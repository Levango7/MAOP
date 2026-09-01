"""MAOP 分布式 KV 后端 —— 基于 etcd3 的键值存储实现。

本模块提供 ``EtcdKVBackend``，作为 ``backends.KVBackend`` ABC 的分布式实现，
用于在多实例 / 企业部署场景下替换默认的 SQLite KV 存储，提供跨节点共享的、
强一致（raft 共识）的键值读写与 watch / CAS 能力。

设计要点
--------
- 连接：通过 ``MAOP_ETCD_HOST`` / ``MAOP_ETCD_PORT`` 环境变量配置，默认
  ``localhost:2379``。
- 认证（P0 安全修复）：通过 ``username`` / ``password`` 参数传入，缺省读取
  ``MAOP_ETCD_USERNAME`` / ``MAOP_ETCD_PASSWORD`` 环境变量。生产环境必须
  启用 etcd auth（``etcdctl user add root && etcdctl auth enable``）并提供
  凭证；两个变量必须成对出现，只设其一会抛出 ``ValueError``。
- TLS（P0 安全修复）：通过 ``ca_cert`` / ``cert_key`` / ``cert_cert`` 参数
  传入客户端 CA / 私钥 / 证书文件路径，缺省读取 ``MAOP_ETCD_CA_CERT`` /
  ``MAOP_ETCD_CERT_KEY`` / ``MAOP_ETCD_CERT_CERT``。生产环境建议启用 mTLS。
- 命名空间：通过 key prefix 实现，所有 key 实际存储为 ``/{namespace}/{key}``；
  ``namespace`` 默认 ``maop``，可通过 ``MAOP_ETCD_NAMESPACE`` 覆盖。
  ``list_keys`` 返回时自动剥离前缀，对外暴露逻辑 key。
- TTL：通过 etcd lease 实现，``set(key, value, ttl=N)`` 时创建租约并绑定；
  租约到期后 etcd 自动删除该 key。
- CAS（compare-and-swap）：通过 etcd 事务（``transaction``）实现，比较当前
  value 与 ``expected`` 相等才写入 ``new_value``，原子操作，无需锁。

依赖
----
需要 ``etcd3 >= 0.12.0``（``pip install etcd3``）。未安装时模块导入即失败，
``backends.py`` 的工厂函数会捕获 ``ImportError`` 并降级到 SQLite。
"""

from __future__ import annotations

import logging
import os

# 顶层导入 etcd3 —— 未安装时抛出带清晰提示的 ImportError，
# 由 backends.py 工厂函数的 try/except ImportError 捕获后降级。
try:
    import etcd3
except ImportError as _e:  # pragma: no cover - 仅在缺包时触发
    raise ImportError(
        "etcd3 is required for EtcdKVBackend. "
        "Install the optional dependency with: pip install etcd3>=0.12.0"
    ) from _e

from maop.core.backends.backends import KVBackend

logger = logging.getLogger(__name__)

# 默认 etcd 连接参数
_DEFAULT_ETCD_HOST = "localhost"
_DEFAULT_ETCD_PORT = 2379
# 默认命名空间
_DEFAULT_NAMESPACE = "maop"


class EtcdKVBackend(KVBackend):
    """基于 etcd3 的分布式键值存储后端。

    Parameters
    ----------
    host : str
        etcd 主机地址，为空时读取 ``MAOP_ETCD_HOST`` 环境变量。
    port : int
        etcd 端口，为 0 时读取 ``MAOP_ETCD_PORT`` 环境变量。
    namespace : str
        键命名空间，所有 key 存储为 ``/{namespace}/{key}``；
        为空时读取 ``MAOP_ETCD_NAMESPACE`` 环境变量，默认 ``maop``。
    username : str
        etcd 认证用户名，为空时读取 ``MAOP_ETCD_USERNAME`` 环境变量。
        生产环境必须启用认证（见 SECURITY.md）。
    password : str
        etcd 认证密码，为空时读取 ``MAOP_ETCD_PASSWORD`` 环境变量。
        必须与 username 成对提供，否则抛出 ``ValueError``。
    ca_cert : str
        CA 证书文件路径（TLS），为空时读取 ``MAOP_ETCD_CA_CERT``。
    cert_key : str
        客户端私钥文件路径（mTLS），为空时读取 ``MAOP_ETCD_CERT_KEY``。
    cert_cert : str
        客户端证书文件路径（mTLS），为空时读取 ``MAOP_ETCD_CERT_CERT``。

    Raises
    ------
    ValueError
        username / password 只提供了其中一个时抛出（认证凭证必须成对）。
    RuntimeError
        连接 etcd 失败时抛出（包含原始异常链）。
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        namespace: str = "",
        username: str = "",
        password: str = "",
        ca_cert: str = "",
        cert_key: str = "",
        cert_cert: str = "",
    ) -> None:
        self._host = host or os.getenv("MAOP_ETCD_HOST", _DEFAULT_ETCD_HOST)
        self._port = port or int(os.getenv("MAOP_ETCD_PORT", str(_DEFAULT_ETCD_PORT)))
        self._namespace = namespace or os.getenv("MAOP_ETCD_NAMESPACE", _DEFAULT_NAMESPACE)
        # P0 安全修复：认证凭证 —— 显式参数优先，回退到环境变量。
        # etcd3 客户端要求 user/password 成对生效，此处提前校验避免
        # 半配置状态静默退化为无认证连接。
        self._username = username or os.getenv("MAOP_ETCD_USERNAME", "")
        self._password = password or os.getenv("MAOP_ETCD_PASSWORD", "")
        if bool(self._username) != bool(self._password):
            raise ValueError(
                "etcd 认证配置不完整：MAOP_ETCD_USERNAME 与 MAOP_ETCD_PASSWORD "
                "必须成对提供（只设置了其中一个）。请补全凭证或同时清空以使用匿名访问。"
            )
        # P0 安全修复：TLS 证书路径 —— 用于加密传输与 mTLS 双向认证。
        self._ca_cert = ca_cert or os.getenv("MAOP_ETCD_CA_CERT", "")
        self._cert_key = cert_key or os.getenv("MAOP_ETCD_CERT_KEY", "")
        self._cert_cert = cert_cert or os.getenv("MAOP_ETCD_CERT_CERT", "")
        # key 前缀，形如 ``/maop``
        self._prefix = f"/{self._namespace}"
        try:
            self._client = etcd3.client(
                host=self._host,
                port=self._port,
                user=self._username or None,
                password=self._password or None,
                ca_cert=self._ca_cert or None,
                cert_key=self._cert_key or None,
                cert_cert=self._cert_cert or None,
            )
            # 探活：调用 status() 触发实际连接，便于在初始化阶段暴露问题
            self._client.status()
        except Exception as e:
            logger.error(
                "[etcd] 连接 etcd 失败 (host=%s port=%s): %s",
                self._host, self._port, e,
            )
            raise RuntimeError(
                f"无法连接 etcd (host={self._host}, port={self._port}): {e}. "
                "请检查 MAOP_ETCD_HOST / MAOP_ETCD_PORT 配置及集群是否可达。"
            ) from e
        logger.debug(
            "[etcd] 已连接 host=%s port=%s namespace=%s auth=%s tls=%s",
            self._host, self._port, self._namespace,
            bool(self._username), bool(self._ca_cert),
        )

    # ------------------------------------------------------------------
    # key 前缀处理
    # ------------------------------------------------------------------
    def _full_key(self, key: str) -> str:
        """将逻辑 key 转为带命名空间前缀的物理 key。

        物理 key 形如 ``/maop/{key}``。若 key 已包含前缀则不重复拼接。
        """
        full = f"{self._prefix}/{key}" if not key.startswith(self._prefix + "/") else key
        return full

    def _strip_prefix(self, full_key: bytes | str) -> str:
        """剥离命名空间前缀，返回逻辑 key。"""
        if isinstance(full_key, bytes):
            full_key = full_key.decode("utf-8", errors="replace")
        prefix_with_slash = self._prefix + "/"
        if full_key.startswith(prefix_with_slash):
            return full_key[len(prefix_with_slash):]
        return full_key

    # ------------------------------------------------------------------
    # KVBackend 抽象方法实现
    # ------------------------------------------------------------------
    def get(self, key: str) -> str | None:
        """读取 key 的值。

        Returns
        -------
        str | None
            key 存在返回字符串值；不存在返回 None。
        """
        try:
            value, _meta = self._client.get(self._full_key(key))
        except Exception as e:
            logger.error("[etcd] get 失败 key=%s: %s", key, e)
            raise
        if value is None:
            return None
        # etcd3 以 bytes 返回，解码为 str
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        """写入 key/value，可选 TTL。

        Parameters
        ----------
        key : str
            逻辑 key。
        value : str
            值（字符串）。
        ttl : float | None
            生存时间（秒）。``> 0`` 时创建 etcd lease 并绑定，到期自动删除；
            ``None`` 或 ``<= 0`` 时持久化存储。
        """
        full_key = self._full_key(key)
        try:
            if ttl is not None and ttl > 0:
                # 创建租约，TTL 到期后 etcd 自动删除绑定的 key
                lease = self._client.lease(int(ttl))
                self._client.put(full_key, value, lease=lease)
                logger.debug(
                    "[etcd] set key=%s ttl=%ss (lease_id=%s)",
                    key, int(ttl), lease.id,
                )
            else:
                self._client.put(full_key, value)
                logger.debug("[etcd] set key=%s (持久化)", key)
        except Exception as e:
            logger.error("[etcd] set 失败 key=%s: %s", key, e)
            raise

    def delete(self, key: str) -> bool:
        """删除 key。

        Returns
        -------
        bool
            key 存在并删除成功返回 True；key 不存在返回 False。
        """
        full_key = self._full_key(key)
        try:
            # 先查询是否存在，再删除（避免 etcd3 不同版本 delete 返回值不一致）
            value, _meta = self._client.get(full_key)
            if value is None:
                return False
            self._client.delete(full_key)
            logger.debug("[etcd] delete key=%s", key)
            return True
        except Exception as e:
            logger.error("[etcd] delete 失败 key=%s: %s", key, e)
            raise

    def list_keys(self, prefix: str = "") -> list[str]:
        """列出符合前缀的所有逻辑 key。

        Parameters
        ----------
        prefix : str
            逻辑前缀（不含命名空间）。为空时列出整个命名空间下的所有 key。

        Returns
        -------
        list[str]
            剥离命名空间前缀后的逻辑 key 列表，按 etcd 返回顺序。
        """
        if prefix:
            full_prefix = self._full_key(prefix)
        else:
            # 列出整个命名空间：前缀以 ``/`` 结尾，避免误匹配 ``/maop_xxx``
            full_prefix = self._prefix + "/"
        try:
            keys: list[str] = []
            for _value, meta in self._client.get_prefix(full_prefix):
                keys.append(self._strip_prefix(meta.key))
            logger.debug(
                "[etcd] list_keys prefix=%s 返回 %d 个", prefix, len(keys)
            )
            return keys
        except Exception as e:
            logger.error("[etcd] list_keys 失败 prefix=%s: %s", prefix, e)
            raise

    def cas(self, key: str, expected: str, new_value: str) -> bool:
        """原子 compare-and-swap。

        通过 etcd 事务实现：当且仅当 key 当前 value 等于 ``expected`` 时，
        写入 ``new_value``。整个操作原子完成，无需外部锁。

        Parameters
        ----------
        key : str
            逻辑 key。
        expected : str
            期望的当前值。key 不存在时 etcd 视作空字符串，因此
            ``cas(key, "", new_value)`` 可实现"仅当不存在时创建"语义。
        new_value : str
            替换后的新值。

        Returns
        -------
        bool
            交换成功返回 True；当前值不等于 expected 返回 False。
        """
        full_key = self._full_key(key)
        tx = self._client.transactions
        try:
            # etcd3 事务：compare value 相等才执行 put
            status, _responses = self._client.transaction(
                compare=[tx.value(full_key) == expected.encode("utf-8")],
                success=[tx.put(full_key, new_value)],
                failure=[],
            )
            logger.debug(
                "[etcd] cas key=%s status=%s", key, status,
            )
            return bool(status)
        except Exception as e:
            logger.error("[etcd] cas 失败 key=%s: %s", key, e)
            raise

    # ------------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------------
    def close(self) -> None:
        """显式关闭 etcd 客户端（etcd3 客户端通常无需显式关闭，此处保留接口）。"""
        # etcd3 客户端基于 grpc，Python 侧无显式 close API；
        # 保留方法以与其它 backend 的生命周期管理保持一致。
        client = getattr(self, "_client", None)
        if client is not None:
            transport = getattr(client, "transport", None)
            if transport is not None and hasattr(transport, "close"):
                try:
                    transport.close()
                except Exception as exc:
                    logger.warning(
                        "[backends_distributed] etcd transport.close() failed during "
                        "backend shutdown (best-effort cleanup, ignored): %s",
                        exc, exc_info=True,
                    )
