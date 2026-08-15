"""F1: 前端硬编码错误兜底文案清零 — 迁移到 i18n。

用法: python fix_hardcoded_i18n.py --dry-run | (执行)
对 views/*.vue 中 catch 兜底文案（'xxx failed' / 'xxx unavailable' 等）
替换为 t('view.<view>.<key>')，并在 view-<view>.js 的 en/zh 段补 key。
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # dashboard-enterprise/
VIEWS = os.path.join(ROOT, "src", "views")
I18N = os.path.join(ROOT, "src", "i18n")

# 视图名 -> (i18n 命名空间, i18n 文件名)；默认命名空间=view.<小写>，文件名=view-<小写>.js
_NS: dict[str, tuple[str, str]] = {
    "EvolutionHistory": ("view.evolutionHistory", "view-evolution-history.js"),
    "Users": ("view.users", "view-users.js"),
}


def _ns_and_file(view: str) -> tuple[str, str]:
    if view in _NS:
        return _NS[view]
    return f"view.{view.lower()}", f"view-{view.lower()}.js"
MAPPING: dict[str, dict[str, tuple[str, str, str]]] = {
    "Agents": {
        "check failed": ("checkFailed", "Check failed", "检查失败"),
        "upgrade failed": ("upgradeFailed", "Upgrade failed", "升级失败"),
    },
    "Audit": {
        "Rules unavailable": ("rulesUnavailable", "Rules unavailable", "规则不可用"),
        "Save failed": ("saveFailed", "Save failed", "保存失败"),
        "Toggle failed": ("toggleFailed", "Toggle failed", "切换失败"),
        "Delete failed": ("deleteFailed", "Delete failed", "删除失败"),
        "History unavailable": ("historyUnavailable", "History unavailable", "历史记录不可用"),
        "Summary unavailable": ("summaryUnavailable", "Summary unavailable", "摘要不可用"),
        "Events unavailable": ("eventsUnavailable", "Events unavailable", "事件不可用"),
    },
    "EvolutionHistory": {
        "approve failed": ("approveFailed", "Approve failed", "批准失败"),
    },
    "Licenses": {
        "Failed to load licenses": ("loadFailed", "Failed to load licenses", "加载许可证失败"),
        "Renew failed": ("renewFailed", "Renew failed", "续期失败"),
        "Revoke failed": ("revokeFailed", "Revoke failed", "吊销失败"),
    },
    "Models": {
        "Registry unavailable": ("registryUnavailable", "Registry unavailable", "模型注册表不可用"),
        "Models unavailable": ("modelsUnavailable", "Models unavailable", "模型不可用"),
        "Providers unavailable": ("providersUnavailable", "Providers unavailable", "供应商不可用"),
        "Agents unavailable": ("agentsUnavailable", "Agents unavailable", "Agent 不可用"),
        "Availability unavailable": ("availabilityUnavailable", "Availability unavailable", "可用性不可用"),
        "Policies unavailable": ("policiesUnavailable", "Policies unavailable", "策略不可用"),
        "Budget unavailable": ("budgetUnavailable", "Budget unavailable", "预算不可用"),
    },
    "Notifications": {
        "Notifications unavailable": ("notificationsUnavailable", "Notifications unavailable", "通知不可用"),
        "Load more failed": ("loadMoreFailed", "Load more failed", "加载更多失败"),
        "Mark read failed": ("markReadFailed", "Mark read failed", "标记已读失败"),
        "Mark all read failed": ("markAllReadFailed", "Mark all read failed", "全部标记已读失败"),
        "Delete failed": ("deleteFailed", "Delete failed", "删除失败"),
        "Save failed": ("saveFailed", "Save failed", "保存失败"),
    },
    "Overview": {
        "Failed to load overview": ("loadFailed", "Failed to load overview", "加载概览失败"),
    },
    "RBAC": {
        "Grant failed": ("grantFailed", "Grant failed", "授权失败"),
        "Revoke failed": ("revokeFailed", "Revoke failed", "撤销失败"),
    },
    "Search": {
        "Search failed": ("searchFailed", "Search failed", "搜索失败"),
    },
    "Tenants": {
        "Failed to load tenants": ("loadFailed", "Failed to load tenants", "加载租户失败"),
        "Create failed": ("createFailed", "Create failed", "创建失败"),
        "Suspend failed": ("suspendFailed", "Suspend failed", "暂停失败"),
        "Activate failed": ("activateFailed", "Activate failed", "激活失败"),
        "Delete failed": ("deleteFailed", "Delete failed", "删除失败"),
    },
    "Users": {
        "Network error": ("networkError", "Network error", "网络错误"),
        "Failed": ("failed", "Failed", "失败"),
    },
}


def _escape(s: str) -> str:
    return re.escape(s)


def process_view(view: str, mapping: dict[str, tuple[str, str, str]]) -> tuple[int, int]:
    """处理单个视图。返回 (vue 替换数, i18n key 数)。"""
    ns, i18n_file = _ns_and_file(view)
    vue_path = os.path.join(VIEWS, f"{view}.vue")
    if not os.path.exists(vue_path):
        print(f"  [SKIP] {view}.vue 不存在")
        return 0, 0
    txt = open(vue_path, encoding="utf-8").read()
    vue_changes = 0
    for old, (key, _en, _zh) in mapping.items():
        # 只替换 `|| 'old'` / `d.error || 'old'` / `('old')` / `= 'old'` 兜底上下文
        pat = re.compile(rf"(\|\s*|\()'{_escape(old)}'")
        new_txt, n = pat.subn(lambda m: m.group(1) + f"t('{ns}.{key}')", txt)
        txt = new_txt
        vue_changes += n
    if vue_changes:
        if "--dry-run" in sys.argv:
            print(f"  [dry-run] {view}.vue: {vue_changes} 处")
        else:
            open(vue_path, "w", encoding="utf-8").write(txt)

    # i18n: 在 <i18n_file> 的 en/zh 段结尾加 key
    i18n_path = os.path.join(I18N, i18n_file)
    if not os.path.exists(i18n_path) and vue_changes == 0:
        return vue_changes, 0
    if not os.path.exists(i18n_path):
        # 新建 i18n 文件（Users.vue 场景）
        en_entries = [f"    '{ns}.{k}': {_q(en)},\n" for k, (_k, en, _zh) in mapping.items()]
        zh_entries = [f"    '{ns}.{k}': {_q(zh)},\n" for k, (_k, _en, zh) in mapping.items()]
        content = (
            "export const messages = {\n"
            "  en: {\n" + "".join(en_entries) +
            "  },\n"
            "  zh: {\n" + "".join(zh_entries) +
            "  },\n"
            "};\n"
        )
        if "--dry-run" in sys.argv:
            print(f"  [dry-run] 新建 {os.path.basename(i18n_path)}")
            return vue_changes, len(mapping)
        open(i18n_path, "w", encoding="utf-8").write(content)
        return vue_changes, len(mapping)

    i18n_txt = open(i18n_path, encoding="utf-8").read()
    added = 0
    # en 段: 在 `  zh: {` 前插入；zh 段: 在文件末尾 `  },` 前插入
    zh_marker = re.search(r"\n  zh: \{", i18n_txt)
    en_entries = "".join(
        f"    '{ns}.{k}': {_q(en)},\n" for k, (_k, en, _zh) in mapping.items()
    )
    zh_entries = "".join(
        f"    '{ns}.{k}': {_q(zh)},\n" for k, (_k, _en, zh) in mapping.items()
    )
    if zh_marker:
        pos = zh_marker.start()
        i18n_txt = i18n_txt[:pos] + en_entries + i18n_txt[pos:]
        added += len(mapping)
    # zh 段结尾: 文件末尾的 `  },` 前
    tail_marker = re.search(r"\n  \},\n\};$", i18n_txt)
    if tail_marker:
        pos = tail_marker.start()
        i18n_txt = i18n_txt[:pos] + "\n" + zh_entries.rstrip("\n") + i18n_txt[pos:]
        added += len(mapping)
    if added and "--dry-run" not in sys.argv:
        open(i18n_path, "w", encoding="utf-8").write(i18n_txt)
    if "--dry-run" in sys.argv:
        print(f"  [dry-run] {os.path.basename(i18n_path)}: {added} 个 key")
    return vue_changes, added


def _q(s: str) -> str:
    """给文案加单引号（若含单引号则用双引号）。"""
    return repr(s)


def main() -> int:
    total_vue = 0
    total_i18n = 0
    for view, mapping in MAPPING.items():
        v, i = process_view(view, mapping)
        total_vue += v
        total_i18n += i
    print(f"总计: vue 替换 {total_vue} 处, i18n key {total_i18n} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
