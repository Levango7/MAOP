"""SAML 2.0 SP-initiated SSO 处理器（纯 Python，无 pysaml2 依赖）。

使用 lxml + cryptography 实现：
  - IdP metadata 拉取与解析（SSO URL、X.509 证书）
  - AuthnRequest 构造（HTTP-Redirect binding）
  - SAML Response 解析与 XML 签名验证（enveloped signature, exclusive c14n）
  - Conditions 校验（Audience、NotBefore/NotOnOrAfter）
  - AttributeStatement 提取

设计原则：fail-closed —— 任何验证失败均抛 SSOError，绝不返回 stub session。
"""

from __future__ import annotations

import base64
import copy
import datetime
import hashlib
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

from lxml import etree
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_der_x509_certificate

from maop.enterprise.sso import (
    SSOConfig,
    SSOError,
    SSOProvider,
    SSOSession,
    SSOUser,
)

logger = logging.getLogger(__name__)

# SAML / XMLDSig / Metadata 命名空间
NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}

# 命名空间 URI 常量（用于无前缀查找）
_SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
_SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
_DS_NS = "http://www.w3.org/2000/09/xmldsig#"
_MD_NS = "urn:oasis:names:tc:SAML:2.0:metadata"

# 时钟偏移容差（秒）—— 允许 IdP/SP 之间 ±60 秒
CLOCK_SKEW_S = 60

# SAML session 默认有效期（8 小时）
_DEFAULT_SESSION_TTL_S = 28800


class SAMLHandler:
    """SAML 2.0 Service Provider handler.

    实现 SP-initiated SSO 与 XML 签名验证。
    所有验证失败均抛 SSOError（fail-closed），绝不返回 stub session。
    """

    def __init__(self, config: SSOConfig) -> None:
        self._config = config
        self._idp_metadata: dict | None = None  # 缓存解析的 metadata
        self._clock_skew_s = CLOCK_SKEW_S

    # ── 公开接口 ─────────────────────────────────────────────────────

    def get_authorize_url(self, state: str = "") -> str:
        """构造 SAML AuthnRequest 重定向 URL（HTTP-Redirect binding）。

        1. 从 IdP metadata 获取 SSO URL（SingleSignOnService）
        2. 构造 <samlp:AuthnRequest> XML
        3. DEFLATE 压缩 + base64 编码 + URL 编码
        4. 返回 {sso_url}?SAMLRequest={encoded_request}&RelayState={state}
        """
        metadata = self._get_idp_metadata()
        sso_url = metadata.get("sso_url", "")
        if not sso_url:
            raise SSOError(
                "SAML IdP missing SingleSignOnService URL "
                "(set SSOConfig.saml_metadata_url with SSO URL, "
                "or configure IdP metadata)"
            )

        request_id = f"id_{secrets.token_hex(16)}"
        request_xml = self._build_authn_request(request_id)
        # SAML HTTP-Redirect binding：DEFLATE（raw, 无 zlib header）→ base64 → URL 编码
        compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
        deflated = compressor.compress(request_xml) + compressor.flush()
        encoded = base64.b64encode(deflated).decode("ascii")

        params: dict[str, str] = {"SAMLRequest": encoded}
        if state:
            params["RelayState"] = state
        query = urllib.parse.urlencode(params)
        return f"{sso_url}?{query}"

    def handle_response(self, saml_response_b64: str, relay_state: str = "") -> SSOSession:
        """处理 IdP 返回的 SAML Response（HTTP-POST binding）。

        1. base64 解码 SAMLResponse
        2. 解析 XML，提取 Assertion
        3. 验证 XML 签名（使用 IdP 公钥 from metadata 或 config）
        4. 验证 Conditions（Audience、NotBefore/NotOnOrAfter）
        5. 提取 NameID 和 Attribute statements
        6. 构造 SSOSession 返回

        Raises:
            SSOError: 任何验证失败（签名错误、过期、Audience 不匹配等）。
        """
        if not saml_response_b64:
            raise SSOError("SAMLResponse is empty")

        # 1. base64 解码
        try:
            response_xml = base64.b64decode(saml_response_b64)
        except Exception as exc:
            raise SSOError(f"SAMLResponse base64 decode failed: {exc}") from exc

        # 2. 解析 XML（禁用外部实体与网络访问，防 XXE）
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            root = etree.fromstring(response_xml, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise SSOError(f"SAMLResponse XML parse failed: {exc}") from exc

        # 校验根元素是 samlp:Response
        if not root.tag == f"{{{_SAMLP_NS}}}Response":
            raise SSOError(f"Expected samlp:Response root, got {root.tag}")

        # 3. 提取 Assertion（Response > Assertion）
        assertion_elem = root.find(f"{{{_SAML_NS}}}Assertion")
        if assertion_elem is None:
            # 也尝试在子树中查找（某些 IdP 嵌套 EncryptedAssertion 等，此处不支持解密）
            assertion_elem = root.find(f".//{{{_SAML_NS}}}Assertion")
        if assertion_elem is None:
            raise SSOError("SAMLResponse missing <saml:Assertion> element")

        # 4. 获取 IdP 证书
        cert_b64 = self._get_idp_cert_b64()

        # 5. 验证 XML 签名（fail-closed：失败抛 SSOError）
        self._verify_signature(response_xml, cert_b64)

        # 6. 验证 Conditions
        expected_audience = self._config.saml_entity_id or "maop-sp"
        self._validate_conditions(assertion_elem, expected_audience)

        # 7. 提取 NameID 和 Attributes
        name_id = self._extract_name_id(assertion_elem)
        attributes = self._extract_attributes(assertion_elem)

        # 8. 构造 SSOUser 和 SSOSession
        now = time.time()
        # session 有效期：优先用 Conditions.NotOnOrAfter，否则默认 8 小时
        expires_at = now + _DEFAULT_SESSION_TTL_S
        noa = self._get_not_on_or_after(assertion_elem)
        if noa is not None:
            expires_at = min(expires_at, noa.timestamp())

        user = self._build_user_from_saml(name_id, attributes)
        session_id = f"sess_{secrets.token_hex(16)}_{int(now)}"
        session = SSOSession(
            session_id=session_id,
            user=user,
            access_token="",  # SAML 不返回 OAuth access_token
            refresh_token="",
            expires_at=expires_at,
            created_at=now,
        )
        logger.info(
            "[saml] SSO session=%s user=%s",
            session_id, user.external_id,
        )
        return session

    # ── IdP metadata ─────────────────────────────────────────────────

    def _get_idp_metadata(self) -> dict:
        """获取 IdP metadata（带缓存）。

        优先级：
          1. 直接配置的 saml_idp_cert + saml_entity_id（不依赖 metadata URL）
          2. 从 saml_metadata_url 拉取并解析
        """
        if self._idp_metadata is not None:
            return self._idp_metadata

        # 若直接配置了证书，仍需 metadata 提供的 sso_url（用于 AuthnRequest 重定向）
        if self._config.saml_idp_cert:
            self._idp_metadata = {
                "entity_id": self._config.saml_entity_id,
                "sso_url": "",  # 由 metadata URL 解析得到，或单独配置
                "slo_url": "",
                "x509_cert": self._config.saml_idp_cert,
            }
            # 如果有 metadata_url，也拉取以补充 sso_url
            if self._config.saml_metadata_url:
                try:
                    xml_bytes = self._fetch_idp_metadata()
                    parsed = self._parse_idp_metadata(xml_bytes)
                    # 用 metadata 的 sso_url 补充，证书仍用直接配置的
                    self._idp_metadata["sso_url"] = parsed["sso_url"]
                    self._idp_metadata["slo_url"] = parsed.get("slo_url", "")
                    if not self._idp_metadata["entity_id"]:
                        self._idp_metadata["entity_id"] = parsed["entity_id"]
                except SSOError as exc:
                    logger.warning("[saml] metadata fetch failed, using direct cert config: %s", exc)
            return self._idp_metadata

        # 无直接证书，必须从 metadata URL 获取
        xml_bytes = self._fetch_idp_metadata()
        self._idp_metadata = self._parse_idp_metadata(xml_bytes)
        return self._idp_metadata

    def _get_idp_cert_b64(self) -> str:
        """获取 IdP X.509 证书（base64 编码 DER）。

        优先使用 config.saml_idp_cert（直接配置），否则用 metadata 中的证书。
        """
        if self._config.saml_idp_cert:
            return self._config.saml_idp_cert
        metadata = self._get_idp_metadata()
        cert = metadata.get("x509_cert", "")
        if not cert:
            raise SSOError(
                "No IdP X.509 certificate available "
                "(set SSOConfig.saml_idp_cert or provide metadata with X509Certificate)"
            )
        return cert  # type: ignore[no-any-return]

    def _fetch_idp_metadata(self) -> bytes:
        """从 config.saml_metadata_url 获取 IdP metadata XML。"""
        url = self._config.saml_metadata_url
        if not url:
            raise SSOError(
                "SSOConfig.saml_metadata_url is not configured "
                "(and no saml_idp_cert provided)"
            )
        req = urllib.request.Request(
            url, method="GET", headers={"Accept": "application/xml"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()  # type: ignore[no-any-return]
        except urllib.error.URLError as exc:
            raise SSOError(
                f"Failed to fetch IdP metadata from {url}: {exc.reason}"
            ) from exc

    def _parse_idp_metadata(self, xml_bytes: bytes) -> dict:
        """解析 IdP metadata XML。

        返回:
          {
            "entity_id": str,
            "sso_url": str,  # SingleSignOnService（优先 Redirect binding）
            "slo_url": str,  # SingleLogoutService
            "x509_cert": str,  # base64 编码的 X509Certificate（去空白）
          }
        """
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            root = etree.fromstring(xml_bytes, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise SSOError(f"IdP metadata XML parse failed: {exc}") from exc

        # EntityDescriptor @entityID
        entity_id = root.get("entityID", "")

        # SingleSignOnService：优先 HTTP-Redirect，其次 HTTP-POST
        sso_url = ""
        sso_url_post = ""
        for sso in root.iter(f"{{{_MD_NS}}}SingleSignOnService"):
            binding = sso.get("Binding", "")
            if binding.endswith("HTTP-Redirect"):
                sso_url = sso.get("Location", "")
            elif binding.endswith("HTTP-POST"):
                sso_url_post = sso.get("Location", "")
        sso_url = sso_url or sso_url_post

        # SingleLogoutService
        slo_url = ""
        for slo in root.iter(f"{{{_MD_NS}}}SingleLogoutService"):
            slo_url = slo.get("Location", "")
            if slo_url:
                break

        # X509Certificate：优先 KeyDescriptor[@use='signing']
        x509_cert = ""
        for kd in root.iter(f"{{{_MD_NS}}}KeyDescriptor"):
            if kd.get("use", "signing") != "signing":
                continue
            cert_elem = kd.find(f".//{{{_DS_NS}}}X509Certificate")
            if cert_elem is not None and cert_elem.text:
                x509_cert = "".join(cert_elem.text.split())  # 去掉所有空白
                break
        # 兜底：取第一个 X509Certificate
        if not x509_cert:
            for cert_elem in root.iter(f"{{{_DS_NS}}}X509Certificate"):
                if cert_elem.text:
                    x509_cert = "".join(cert_elem.text.split())
                    break

        if not sso_url:
            raise SSOError("IdP metadata missing SingleSignOnService URL")
        if not x509_cert:
            raise SSOError("IdP metadata missing X509Certificate")

        return {
            "entity_id": entity_id,
            "sso_url": sso_url,
            "slo_url": slo_url,
            "x509_cert": x509_cert,
        }

    # ── AuthnRequest 构造 ────────────────────────────────────────────

    def _build_authn_request(self, request_id: str) -> bytes:
        """构造 <samlp:AuthnRequest> XML。"""
        acs_url = self._config.saml_acs_url or self._config.redirect_uri
        if not acs_url:
            raise SSOError(
                "AssertionConsumerServiceURL required "
                "(set SSOConfig.saml_acs_url or redirect_uri)"
            )
        entity_id = self._config.saml_entity_id or "maop-sp"
        issue_instant = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        root = etree.Element(
            f"{{{_SAMLP_NS}}}AuthnRequest",
            nsmap={"samlp": _SAMLP_NS, "saml": _SAML_NS},
            attrib={
                "ID": request_id,
                "Version": "2.0",
                "IssueInstant": issue_instant,
                "ProtocolBinding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                "AssertionConsumerServiceURL": acs_url,
            },
        )
        issuer = etree.SubElement(root, f"{{{_SAML_NS}}}Issuer")
        issuer.text = entity_id
        etree.SubElement(
            root,
            f"{{{_SAMLP_NS}}}NameIDPolicy",
            attrib={
                "Format": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
                "AllowCreate": "true",
            },
        )
        return etree.tostring(root, xml_declaration=False, encoding="utf-8")  # type: ignore[no-any-return]

    # ── XML 签名验证 ─────────────────────────────────────────────────

    def _verify_signature(self, response_xml: bytes, cert_b64: str) -> bool:
        """验证 SAML Response 的 XML 签名（enveloped signature, exclusive c14n）。

        验证步骤：
          1. 解析证书为公钥对象
          2. 提取 <ds:Signature> 元素（Assertion 内优先，其次 Response 内）
          3. 提取 SignedInfo / SignatureValue / Reference / DigestValue
          4. 对被签名元素（去掉 Signature 后）做 exclusive c14n，计算 SHA256 摘要，
             与 Reference.DigestValue 比对
          5. 对 SignedInfo 做 exclusive c14n，用 RSA-SHA256 (PKCS1v15) 验证 SignatureValue

        Args:
            response_xml: 完整 SAML Response XML bytes
            cert_b64: IdP X.509 证书（base64 编码 DER）

        Returns:
            True 如果签名验证通过

        Raises:
            SSOError: 任何验证失败（fail-closed）
        """
        # 1. 解析证书 → 公钥
        try:
            cert_der = base64.b64decode(cert_b64)
        except Exception as exc:
            raise SSOError(f"IdP cert base64 decode failed: {exc}") from exc
        try:
            cert_obj = load_der_x509_certificate(cert_der)
            public_key = cert_obj.public_key()
        except Exception as exc:
            raise SSOError(f"IdP cert parse failed: {exc}") from exc

        # 2. 解析 XML
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True)
            root = etree.fromstring(response_xml, parser=parser)
        except etree.XMLSyntaxError as exc:
            raise SSOError(f"Signature verification: XML parse failed: {exc}") from exc

        # 3. 查找 Signature 元素：Assertion 内的优先，其次 Response 内的
        sig_elem = root.find(f".//{{{_SAML_NS}}}Assertion/{{{_DS_NS}}}Signature")
        if sig_elem is None:
            sig_elem = root.find(f"{{{_DS_NS}}}Signature")
        if sig_elem is None:
            # 也尝试任意位置的 Signature
            sig_elem = root.find(f".//{{{_DS_NS}}}Signature")
        if sig_elem is None:
            raise SSOError("SAML Response missing <ds:Signature> element")

        # 被签名的父元素（Assertion 或 Response）
        signed_elem = sig_elem.getparent()
        if signed_elem is None:
            raise SSOError("Signature element has no parent (cannot determine signed element)")

        # 4. 提取 SignedInfo / SignatureValue / Reference / DigestValue
        signed_info_elem = sig_elem.find(f"{{{_DS_NS}}}SignedInfo")
        signature_value_elem = sig_elem.find(f"{{{_DS_NS}}}SignatureValue")
        if signed_info_elem is None or signature_value_elem is None:
            raise SSOError("Signature element missing SignedInfo or SignatureValue")

        reference_elem = signed_info_elem.find(f"{{{_DS_NS}}}Reference")
        if reference_elem is None:
            raise SSOError("SignedInfo missing Reference element")

        digest_value_elem = reference_elem.find(f"{{{_DS_NS}}}DigestValue")
        if digest_value_elem is None or not (digest_value_elem.text or "").strip():
            raise SSOError("Reference missing DigestValue")
        digest_value_b64 = (digest_value_elem.text or "").strip()

        # 检查 Transforms 是否声明了 enveloped-signature
        transforms_elem = reference_elem.find(f"{{{_DS_NS}}}Transforms")
        _has_enveloped_transform = False  # noqa: F841
        if transforms_elem is not None:
            for t in transforms_elem.findall(f"{{{_DS_NS}}}Transform"):
                if t.get("Algorithm") == "http://www.w3.org/2000/09/xmldsig#enveloped-signature":
                    _has_enveloped_transform = True  # noqa: F841

        # 5. 计算被签名元素的 c14n 摘要
        #    enveloped signature：去掉 Signature 子元素后做 c14n
        #    为不修改原树，对 deepcopy 操作
        signed_copy = copy.deepcopy(signed_elem)
        sig_in_copy = signed_copy.find(f"{{{_DS_NS}}}Signature")
        if sig_in_copy is not None:
            signed_copy.remove(sig_in_copy)

        # exclusive c14n（lxml 的 method="c14n" + exclusive=True）
        c14n_bytes = etree.tostring(
            signed_copy,
            method="c14n",
            exclusive=True,
            with_comments=False,
        )
        computed_digest = hashlib.sha256(c14n_bytes).digest()

        try:
            expected_digest = base64.b64decode(digest_value_b64)
        except Exception as exc:
            raise SSOError(f"DigestValue base64 decode failed: {exc}") from exc

        if computed_digest != expected_digest:
            raise SSOError(
                "SAML signature digest mismatch: Reference.DigestValue does not "
                "match canonicalized signed element"
            )

        # 6. 验证 SignedInfo 的签名
        #    对 SignedInfo 做 exclusive c14n，用 RSA-SHA256 (PKCS1v15) 验证
        signed_info_c14n = etree.tostring(
            signed_info_elem,
            method="c14n",
            exclusive=True,
            with_comments=False,
        )

        sig_value_b64 = (signature_value_elem.text or "").strip()
        try:
            sig_value = base64.b64decode(sig_value_b64)
        except Exception as exc:
            raise SSOError(f"SignatureValue base64 decode failed: {exc}") from exc

        try:
            public_key.verify(  # type: ignore[union-attr,call-arg]
                sig_value,
                signed_info_c14n,
                padding.PKCS1v15(),  # type: ignore[arg-type]
                hashes.SHA256(),
            )
        except Exception as exc:
            raise SSOError(f"SignatureValue RSA-SHA256 verification failed: {exc}") from exc

        logger.debug("[saml] XML signature verification passed")
        return True

    # ── Conditions / Attributes 提取 ─────────────────────────────────

    def _extract_attributes(self, assertion_elem) -> dict:
        """从 Assertion 提取 AttributeStatement。

        返回 {attr_name: [value1, value2, ...]}（多值属性用列表）。
        """
        attributes: dict[str, list[str]] = {}
        attr_stmt = assertion_elem.find(f"{{{_SAML_NS}}}AttributeStatement")
        if attr_stmt is None:
            return attributes
        for attr in attr_stmt.findall(f"{{{_SAML_NS}}}Attribute"):
            name = attr.get("Name", "")
            if not name:
                continue
            values: list[str] = []
            for v in attr.findall(f"{{{_SAML_NS}}}AttributeValue"):
                if v.text:
                    values.append(v.text)
            attributes[name] = values
        return attributes

    def _extract_name_id(self, assertion_elem) -> str:
        """提取 NameID（Subject > NameID）。"""
        subject = assertion_elem.find(f"{{{_SAML_NS}}}Subject")
        if subject is None:
            return ""
        name_id_elem = subject.find(f"{{{_SAML_NS}}}NameID")
        if name_id_elem is None or not name_id_elem.text:
            return ""
        return name_id_elem.text  # type: ignore[no-any-return]

    def _validate_conditions(self, assertion_elem, expected_audience: str) -> None:
        """验证 Conditions：Audience、NotBefore、NotOnOrAfter。

        时钟偏移容差：±CLOCK_SKEW_S 秒。
        """
        conditions = assertion_elem.find(f"{{{_SAML_NS}}}Conditions")
        if conditions is None:
            # 没有 Conditions 也算通过（某些 IdP 不下发 Conditions）
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        skew = datetime.timedelta(seconds=self._clock_skew_s)

        # NotBefore
        nb_str = conditions.get("NotBefore")
        if nb_str:
            try:
                nb = _parse_saml_time(nb_str)
            except Exception as exc:
                raise SSOError(f"Failed to parse NotBefore {nb_str!r}: {exc}") from exc
            if now + skew < nb:
                raise SSOError(
                    f"Assertion NotBefore {nb_str} is in the future "
                    f"(now={now.isoformat()})"
                )

        # NotOnOrAfter
        noa_str = conditions.get("NotOnOrAfter")
        if noa_str:
            try:
                noa = _parse_saml_time(noa_str)
            except Exception as exc:
                raise SSOError(f"Failed to parse NotOnOrAfter {noa_str!r}: {exc}") from exc
            if now - skew >= noa:
                raise SSOError(
                    f"Assertion NotOnOrAfter {noa_str} has passed "
                    f"(now={now.isoformat()})"
                )

        # AudienceRestriction
        audience_restriction = conditions.find(f"{{{_SAML_NS}}}AudienceRestriction")
        if audience_restriction is not None:
            audiences = audience_restriction.findall(f"{{{_SAML_NS}}}Audience")
            if not audiences:
                raise SSOError("AudienceRestriction has no Audience element")
            audience_values = [a.text or "" for a in audiences]
            if expected_audience not in audience_values:
                raise SSOError(
                    f"Audience mismatch: expected {expected_audience!r}, "
                    f"got {audience_values}"
                )

    def _get_not_on_or_after(self, assertion_elem) -> datetime.datetime | None:
        """从 Conditions 提取 NotOnOrAfter（用于 session 过期时间）。"""
        conditions = assertion_elem.find(f"{{{_SAML_NS}}}Conditions")
        if conditions is None:
            return None
        noa_str = conditions.get("NotOnOrAfter")
        if not noa_str:
            return None
        try:
            return _parse_saml_time(noa_str)
        except Exception:
            return None

    # ── SSOUser 构造 ─────────────────────────────────────────────────

    def _build_user_from_saml(self, name_id: str, attributes: dict) -> SSOUser:
        """从 SAML NameID 和 AttributeStatement 构造 SSOUser。"""
        def first_value(key: str) -> str:
            v = attributes.get(key)
            if isinstance(v, list) and v:
                return v[0]  # type: ignore[no-any-return]
            return ""

        # 常见 SAML 属性名（含 Microsoft AD FS / Azure AD claim URI）
        email = (
            first_value("email")
            or first_value("Email")
            or first_value("EmailAddress")
            or first_value("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
        )
        display_name = (
            first_value("displayname")
            or first_value("DisplayName")
            or first_value("cn")
            or first_value("name")
            or first_value("Name")
            or first_value("http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
        )
        # roles / groups：多值属性
        roles: list[str] = []
        for key in (
            "roles",
            "role",
            "groups",
            "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
        ):
            v = attributes.get(key)
            if isinstance(v, list) and v:
                roles = [str(r) for r in v]
                break
        if not roles:
            roles = [self._config.default_role]

        tenant_id = first_value("tenant_id") or first_value("TenantId")

        sub = name_id or "unknown"
        return SSOUser(
            external_id=f"saml:{sub}",
            email=email,
            display_name=display_name,
            roles=roles,
            tenant_id=tenant_id,
            provider=SSOProvider.SAML,
            last_login=time.time(),
        )


def _parse_saml_time(t: str) -> datetime.datetime:
    """解析 SAML UTC 时间字符串（ISO-8601, 'Z' 后缀）。

    支持 '2025-01-01T00:00:00Z' 与 '2025-01-01T00:00:00.123Z' 两种格式。
    返回带时区（UTC）的 datetime。
    """
    s = t.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt
