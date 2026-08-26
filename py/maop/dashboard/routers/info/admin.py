"""Admin endpoints: edition switching & ADR listing.

Endpoints:
    POST /edition — switch runtime edition (admin only)
    GET  /adrs    — list Architecture Decision Records from docs/adr/
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from maop.core.security.middleware import require_admin
from maop.dashboard.error_handler import handle_api_errors

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/info", tags=["info"])


@router.post("/edition")
@handle_api_errors
async def set_edition_endpoint(request: Request) -> dict[str, Any]:
    """切换运行时 edition（仅 admin）。

    请求体: {"edition": "personal" | "enterprise"}

    安全要求:
    - 需要 admin 角色（通过 require_admin 守卫）
    - 记录审计日志
    - 切换到 enterprise 时检查 license（如果配置了）

    返回: {"status": "ok", "edition": "新edition", "previous": "旧edition"}
    """
    # 1. admin 权限守卫（未认证由 middleware 拦截返回 401；非 admin 抛 403）
    require_admin(request)

    # 2. 解析请求体
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")
    target = body.get("edition", "")
    if not isinstance(target, str) or not target:
        raise HTTPException(400, "missing 'edition' field")

    # 3. 校验 edition 取值
    from maop.config.edition import Edition, get_edition, set_edition
    try:
        target_edition = Edition(target.lower())
    except ValueError:
        raise HTTPException(400, f"invalid edition: {target!r}; expected 'personal' or 'enterprise'")

    # 4. 记录切换前的 edition
    previous = get_edition()
    previous_value = previous.value

    # 5. 调用 set_edition 切换
    # 注意：切换到 enterprise 时若 license 无效，需要触发降级。
    # set_edition() 本身只是直接覆盖 _current_edition，不做 license 校验；
    # 因此切换到 enterprise 时先 reset 再走 _detect_with_license_check，
    # 让 license 校验有机会将 edition 降级到 personal。
    # P1-2 (edition 切换门禁): 无有效 license 切换到 enterprise 时，不再
    # 静默降级返回 200+degraded，而是明确拒绝并返回 403 + 授权指引消息，
    # 让用户知道需要 MAOS 商业包 + License。
    degraded_to_personal = False
    if target_edition is Edition.ENTERPRISE:
        from maop.config.edition import _detect_with_license_check, reset_edition
        reset_edition()  # 清除当前覆盖，让 detect 重新走完整流程
        actual = _detect_with_license_check(Edition.ENTERPRISE)
        set_edition(actual)  # 将实际检测结果固定下来
        if actual is not Edition.ENTERPRISE:
            degraded_to_personal = True
    else:
        set_edition(target_edition)

    new_edition = get_edition()
    new_value = new_edition.value

    # 6. 记录审计日志（best-effort，失败不影响切换结果）
    actor = getattr(request.state, "auth_identity", "system") or "system"
    try:
        # admin.py 路径：MAOP/py/maop/dashboard/routers/info/admin.py
        # parents[0]=info, [1]=routers, [2]=dashboard, [3]=maop, [4]=py, [5]=MAOP 根
        maop_root = Path(__file__).resolve().parents[5]
        from maop.control.audit import AuditLevel, AuditLog
        AuditLog(maop_root / "logs" / "audit.jsonl").log(
            action="edition.switch",
            actor=actor,
            target=new_value,
            level=AuditLevel.WARN if new_value != target.lower() else AuditLevel.INFO,
            detail={
                "previous": previous_value,
                "requested": target.lower(),
                "actual": new_value,
                "degraded": new_value != target.lower(),
            },
        )
    except Exception as exc:
        logger.warning("[info] Failed to write audit log for edition switch: %s", exc)

    logger.info(
        "[info] Edition switched by %s: %s -> %s (requested=%s)",
        actor, previous_value, new_value, target.lower(),
    )

    # 7. P1-2 门禁：无有效 license 切换到 enterprise 被明确拒绝（403）。
    # 返回标准化授权指引消息，前端据此显示"需 MAOS 商业包 + License"提示。
    if degraded_to_personal:
        raise HTTPException(
            status_code=403,
            detail=(
                "切换到企业版需要：1) 安装 MAOS 商业包（maop-enterprise）"
                "2) 有效的商业 License。请联系管理员或访问 MAOS 获取授权。"
            ),
        )

    return {
        "status": "ok",
        "edition": new_value,
        "previous": previous_value,
        "requested": target.lower(),
        "degraded": new_value != target.lower(),
    }


@router.get("/adrs")
@handle_api_errors
async def list_adrs() -> list[dict[str, str]]:
    """列出所有 Architecture Decision Records（来自 docs/adr/ 目录）。

    解析每个 ADR Markdown 文件的编号、标题和状态，供前端 About 面板动态展示。
    路径计算：admin.py 位于 py/maop/dashboard/routers/info/，回到 MAOP 根需要 parents[5]。
    """
    import re

    # admin.py 路径：MAOP/py/maop/dashboard/routers/info/admin.py
    # parents[0]=info, [1]=routers, [2]=dashboard, [3]=maop, [4]=py, [5]=MAOP 根
    adr_dir = Path(__file__).resolve().parents[5] / "docs" / "adr"
    adrs: list[dict[str, str]] = []
    if not adr_dir.exists():
        return adrs

    for f in sorted(adr_dir.glob("*.md")):
        if f.name == "README.md":
            continue
        # 解析 ADR 编号：NNN-xxx.md
        match = re.match(r"(\d+)-(.+)\.md", f.name)
        if not match:
            continue
        num = match.group(1)
        file_content = f.read_text(encoding="utf-8")
        # 标题：第一行去掉 "# ADR-NNN: " 前缀
        first_line = file_content.split("\n", 1)[0]
        title = re.sub(r"^#\s*ADR-\d+\s*:\s*", "", first_line).strip()
        if not title:
            title = first_line.lstrip("# ").strip()

        # 状态：兼容两种格式 —— "**Status**: xxx" 或 "## Status\nxxx"
        status = "Unknown"
        m = re.search(r"\*\*Status\*\*:\s*(.+)", file_content)
        if not m:
            m = re.search(r"##\s*Status\s*\n\s*(.+)", file_content)
        if m:
            status = m.group(1).strip()

        adrs.append({
            "number": num,
            "filename": f.name,
            "title": title,
            "status": status,
        })
    return adrs