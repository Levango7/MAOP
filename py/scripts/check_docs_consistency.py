"""文档-代码一致性检查脚本（P1-4 专项）。

对应质量审计报告 P1-4 的三类文档漂移，独立可运行，仅用 Python 标准库：

1. 路径存在性校验：扫描 README.md 与 docs/ 下所有 .md 中的内联代码块路径
   （如 py/maop/delegate/dispatcher.py、core/xxx.py），检查仓库中是否存在；
   含 py/ 前缀的路径相对仓库根解析，否则依次尝试 py/ 目录、仓库根、
   py/maop/ 目录（README 中 core/、config/、enterprise/ 等均为包内简写）。
2. 模块计数校验：解析 "N modules" / "N files" 表述（如 ``core/ (289 modules)``），
   与解析出的实际目录递归 *.py 计数对比。
3. Markdown 表格列数校验：校验每个表格行（含分隔行）列数一致，破版则报告。

用法: python scripts/check_docs_consistency.py

退出码: 0 = 全部通过；1 = 发现不一致。
"""

from __future__ import annotations

import fnmatch
import pathlib
import re
import sys
from collections import Counter

# 仓库根 = 本脚本所在 py/scripts/ 的上级两级
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PY_DIR = REPO_ROOT / "py"

# 搜索/计数时需要排除的噪声目录（快照、虚拟环境、构建产物等）
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".venv2",
    ".maop-snapshots",
    "node_modules",
    "__pycache__",
    "dist",
    "dist-enterprise",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

# 视为"代码/配置文件"的扩展名；仅含这类后缀（或含 / 的路径）才做存在性校验
FILE_EXTS = {
    ".py", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".sh", ".ps1", ".bat", ".cmd", ".md", ".rst", ".txt", ".sql",
    ".env", ".example", ".xml", ".html", ".js", ".ts", ".jsx", ".tsx",
    ".vue", ".css", ".scss", ".less", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".woff", ".woff2", ".ttf", ".crt", ".pem", ".key",
    ".csv", ".xlsx", ".lock", ".pyd", ".so", ".dll", ".exe", ".proto",
    ".sqlite", ".db", ".gitignore", ".dockerignore", ".editorconfig",
    ".avsc", ".requirements", ".template",
}

# 内联代码块：单个反引号包裹的内容（排除围栏代码块内）
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# 形如 "core/ (289 modules)" / "py/x/ (12 files)"
MODULE_COUNT_PAREN_RE = re.compile(
    r"([A-Za-z0-9_.\-/]+/?)\s*\(\s*(\d+)\s*(modules?|files?)\s*\)",
    re.IGNORECASE,
)
# 裸的 "289 modules" / "12 files"（不带括号）
MODULE_COUNT_BARE_RE = re.compile(r"\b(\d+)\s+(modules?|files?)\b", re.IGNORECASE)
# 表格分隔行，如 |---|---|
TABLE_SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")
# 跳过的不像文件路径的标记（URL、绝对路径、锚点等）
URL_PREFIXES = ("http://", "https://", "ws://", "wss://", "ftp://")


def is_excluded(path: pathlib.Path) -> bool:
    """判断路径是否位于需要排除的噪声目录下。"""
    return any(part in EXCLUDED_DIRS for part in path.parts)


def collect_md_files(root: pathlib.Path) -> list[pathlib.Path]:
    """收集 README.md 与 docs/ 下全部 .md 文件，返回相对仓库根的路径列表。"""
    files = [root / "README.md"]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.rglob("*.md")))
    return [f for f in files if f.is_file() and not is_excluded(f)]


def read_lines(path: pathlib.Path) -> list[str]:
    """读取文件全部行，UTF-8 容错，忽略无法解码的文件。"""
    try:
        return path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []


def is_code_fence(line: str) -> bool:
    """判断该行是否为围栏代码块起始/结束行（``` 或 ~~~）。"""
    return line.lstrip().startswith(("```", "~~~"))


def split_table_row(line: str) -> list[str]:
    """按竖线切分表格行，支持 \\| 转义；返回去除首尾空格的单元格列表。"""
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())]
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def is_separator_row(cells: list[str]) -> bool:
    """判断一行是否为表格分隔行（如 |---|---|）。"""
    return len(cells) > 0 and all(TABLE_SEPARATOR_RE.match(c) for c in cells)


def looks_like_path(token: str) -> bool:
    """粗筛：判断内联代码块内容是否可能是一个代码/配置文件路径。"""
    token = token.strip()
    if not token or len(token) > 500:
        return False
    if token.startswith(URL_PREFIXES) or token.startswith(("/", "#", "@")):
        return False
    if any(ch.isspace() for ch in token):
        return False
    # 占位符/通配符/引号（如 feature/*、<rev>_x.py、{a,b}、"chat"/"claude"）
    if any(ch in token for ch in "*?<>{}\"'@"):
        return False
    # Windows 绝对路径（如 F:\\xxx）
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return False
    if ":" in token and "\\" not in token:
        # 形如 localhost:9079 的 host:port，非文件路径
        return False
    # 纯扩展名（.py、.env 等运行时/临时文件），无目录上下文时不校验
    if "/" not in token and re.fullmatch(r"\.[A-Za-z0-9]{1,4}", token):
        return False
    # 含目录分隔符，或具备代码/配置文件扩展名
    has_ext = token.lower().endswith(tuple(FILE_EXTS))
    if "/" not in token:
        return has_ext
    # 无扩展名且含大写（导航菜单/路由名，如 Overview/Monitor），非文件路径
    return has_ext or not any(c.isupper() for c in token)


def load_gitignore_patterns(root: pathlib.Path) -> list[str]:
    """读取仓库根 .gitignore 的忽略规则，用于识别"缺失属预期"的运行时文件。"""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []
    patterns: list[str] = []
    for line in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue  # 忽略注释与反向规则（!xxx 不处理）
        patterns.append(line)
    return patterns


def is_gitignored(token: str, patterns: list[str]) -> bool:
    """判断文档引用的路径是否命中 .gitignore（命中则缺失属预期，跳过校验）。

    简化语义：无斜杠规则按任意路径段匹配；含斜杠规则相对仓库根匹配
    （含子路径前缀）；以 / 结尾的目录规则按路径的祖先目录匹配。
    """
    if not patterns:
        return False
    t = token.strip().lstrip("/").rstrip("/").replace("\\", "/")
    if not t:
        return False
    parts = t.split("/")
    for pat in patterns:
        p = pat.strip().rstrip("/")
        if not p:
            continue
        if pat.endswith("/"):
            # 目录规则：任一祖先目录命中即视为被忽略
            for i in range(len(parts), 0, -1):
                if "/".join(parts[:i]) == p:
                    return True
        elif "/" in p:
            # 含斜杠规则：精确或子路径命中
            if fnmatch.fnmatch(t, p) or t.startswith(p + "/"):
                return True
        else:
            # 无斜杠规则：任意路径段命中（如 *.pem、.env）
            if any(fnmatch.fnmatch(part, p) for part in parts):
                return True
    return False


def build_file_index(root: pathlib.Path) -> dict[str, list[str]]:
    """构建 basename -> 相对路径列表 的索引，用于缺失路径的实际位置提示。"""
    index: dict[str, list[str]] = {}
    for f in root.rglob("*"):
        if f.is_file() and not is_excluded(f):
            index.setdefault(f.name, []).append(f.relative_to(root).as_posix())
    return index


def resolve_path(token: str, base_dir: pathlib.Path | None = None) -> tuple[pathlib.Path | None, str]:
    """按文档路径解析规则查找实际路径。

    规则：含 py/ 前缀 -> 相对仓库根；./、../ 前缀 -> 相对当前文档目录；
    其余依次尝试 py/、仓库根、py/maop/（README 中 core/、config/ 等包内简写）。
    返回 (命中路径, 相对仓库根路径)。
    """
    p = token.strip().rstrip("/").replace("\\", "/")
    if p.startswith(("./", "../")):
        # markdown 相对链接：先相对文档所在目录，再相对仓库根解析
        for base in (base_dir, REPO_ROOT):
            if base is None:
                continue
            cand = (base / p).resolve()
            if cand.exists() and not is_excluded(cand) and REPO_ROOT in cand.parents:
                return cand, cand.relative_to(REPO_ROOT).as_posix()
        return None, ""
    if p == "py" or p.startswith("py/"):
        candidates = [REPO_ROOT / p]
    else:
        candidates = [PY_DIR / p, REPO_ROOT / p, PY_DIR / "maop" / p]
    for cand in candidates:
        if cand.exists() and not is_excluded(cand):
            return cand, cand.relative_to(REPO_ROOT).as_posix()
    return None, ""


def resolve_dir(token: str) -> pathlib.Path | None:
    """解析模块计数表述中的目录引用，返回实际目录；解析失败返回 None。"""
    p = token.strip().rstrip("/").replace("\\", "/")
    if p == "py" or p.startswith("py/"):
        candidates = [REPO_ROOT / p]
    else:
        candidates = [PY_DIR / p, REPO_ROOT / p, PY_DIR / "maop" / p]
    for cand in candidates:
        if cand.is_dir() and not is_excluded(cand):
            return cand
    return None


def count_py_modules(directory: pathlib.Path) -> int:
    """递归统计目录下 *.py 文件数（排除 __pycache__ 等噪声目录）。"""
    return sum(1 for f in directory.rglob("*.py") if not is_excluded(f))


def find_actual_hint(name: str, index: dict[str, list[str]]) -> str:
    """从索引中查找同名文件作为"实际位置"提示，最多列 3 个。"""
    hits = index.get(name, [])
    if not hits:
        return ""
    shown = hits[:3]
    suffix = " 等" if len(hits) > 3 else ""
    return "，实际位置: " + ", ".join(shown) + suffix


# ---------------------------------------------------------------- 三类校验

def check_paths(
    md_files: list[pathlib.Path],
    file_index: dict[str, list[str]],
    ignore_patterns: list[str],
) -> list[str]:
    """校验 1：内联代码块路径存在性。"""
    issues: list[str] = []
    for md in md_files:
        lines = read_lines(md)
        in_fence = False
        for lineno, line in enumerate(lines, start=1):
            if is_code_fence(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if line.lstrip().startswith("#"):
                continue  # 标题行中的代码引用是标签（如 ### `hook.py`），非路径声明
            for match in INLINE_CODE_RE.finditer(line):
                token = match.group(1).strip()
                if not looks_like_path(token):
                    continue
                if token.endswith("/"):
                    continue  # 目录引用由模块计数校验负责
                resolved, _rel = resolve_path(token, base_dir=md.parent)
                if resolved is not None:
                    continue
                if is_gitignored(token, ignore_patterns):
                    continue  # 命中 .gitignore，缺失属预期，不算漂移
                base = token.rsplit("/", 1)[-1]
                hint = find_actual_hint(base, file_index)
                rel = md.relative_to(REPO_ROOT).as_posix()
                issues.append(
                    f"[{rel}:{lineno}] 引用的路径不存在: {token}{hint}"
                )
    return issues


def check_module_counts(md_files: list[pathlib.Path]) -> list[str]:
    """校验 2：'N modules'/'N files' 声明与实际目录文件数对比。"""
    issues: list[str] = []
    for md in md_files:
        lines = read_lines(md)
        rel = md.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(lines, start=1):
            # 带括号的形如 "core/ (289 modules)"
            for match in MODULE_COUNT_PAREN_RE.finditer(line):
                _report_count_issue(
                    issues, rel, lineno, match.group(1), int(match.group(2))
                )
            # 裸的 "289 modules"，行内需存在路径式标记才做校验
            paren_spans = [
                m.span() for m in MODULE_COUNT_PAREN_RE.finditer(line)
            ]
            for match in MODULE_COUNT_BARE_RE.finditer(line):
                if any(
                    s <= match.start() < e for s, e in paren_spans
                ):
                    continue  # 已被带括号的正则覆盖，避免重复
                path_token = _path_token_on_line(line)
                if path_token is None:
                    continue
                _report_count_issue(
                    issues, rel, lineno, path_token, int(match.group(1))
                )
    return issues


def _path_token_on_line(line: str) -> str | None:
    """取行内第一个像路径/目录的标记，找不到返回 None。"""
    inline = INLINE_CODE_RE.search(line)
    if inline:
        tok = inline.group(1).strip().rstrip("/")
        if "/" in tok or tok.lower().endswith(tuple(FILE_EXTS)):
            return tok
    m = re.search(r"`([A-Za-z0-9_./-]+/)`", line)
    if m:
        return m.group(1).rstrip("/")
    return None


def _report_count_issue(
    issues: list[str], rel: str, lineno: int, dir_token: str, declared: int
) -> None:
    """对比声明值与目录实际 *.py 数量，不一致则记一条问题。"""
    actual_dir = resolve_dir(dir_token)
    if actual_dir is None:
        return
    actual = count_py_modules(actual_dir)
    if actual != declared:
        issues.append(
            f"[{rel}:{lineno}] 模块数声明 {declared}，"
            f"实际目录 {actual_dir.relative_to(REPO_ROOT).as_posix()}/ "
            f"有 {actual} 个 .py 文件"
        )


def check_tables(md_files: list[pathlib.Path]) -> list[str]:
    """校验 3：Markdown 表格列数一致性。"""
    issues: list[str] = []
    for md in md_files:
        lines = read_lines(md)
        rel = md.relative_to(REPO_ROOT).as_posix()
        block: list[tuple[int, list[str]]] = []  # (行号, 单元格列表)

        def flush(rel: str = rel) -> None:
            """结算当前表格块：以分隔行（或首行）列数为基准找破版行。"""
            nonlocal block
            if not block:
                return
            sep_cells = next(
                (cells for _, cells in block if is_separator_row(cells)), None
            )
            expected = len(sep_cells) if sep_cells is not None else len(block[0][1])
            for lineno, cells in block:
                if is_separator_row(cells):
                    continue
                if len(cells) != expected:
                    issues.append(
                        f"[{rel}:{lineno}] 表格列数不一致: "
                        f"该行 {len(cells)} 列，期望 {expected} 列"
                    )
            block = []

        for lineno, line in enumerate(lines, start=1):
            if line.lstrip().startswith("|"):
                block.append((lineno, split_table_row(line)))
            else:
                flush()
        flush()
    return issues


def main() -> int:
    """执行全部校验并按结果输出、返回退出码。"""
    md_files = collect_md_files(REPO_ROOT)
    print(f"扫描 {len(md_files)} 个 Markdown 文件（README.md + docs/）")

    file_index = build_file_index(REPO_ROOT)
    print(f"建立文件索引 {len(file_index)} 个条目")
    ignore_patterns = load_gitignore_patterns(REPO_ROOT)

    issues: list[str] = []
    issues += check_paths(md_files, file_index, ignore_patterns)
    issues += check_module_counts(md_files)
    issues += check_tables(md_files)

    # 按 [文件:行号] 排序后输出
    def sort_key(issue: str) -> tuple[str, int]:
        m = re.match(r"\[([^\]]+):(\d+)\]", issue)
        return (m.group(1), int(m.group(2))) if m else (issue, 0)

    issues.sort(key=sort_key)

    if issues:
        print(f"发现 {len(issues)} 处文档与代码不一致：")
        for issue in issues:
            print("  " + issue)
        # 汇总：按顶层文档/目录统计漂移分布，便于快速定位重灾区
        dist = Counter()
        for issue in issues:
            m = re.match(r"\[([^\]:]+):\d+\]", issue)
            if m:
                rel = m.group(1)
                dist[rel.split("/")[1] if rel.startswith("docs/") else rel] += 1
        print("按文档/目录汇总：")
        for name, count in dist.most_common():
            print(f"  {name}: {count}")
        return 1
    print("OK: docs consistency passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
