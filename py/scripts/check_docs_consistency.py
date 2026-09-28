"""文档-代码一致性检查脚本（P1-4 专项；2026-09-29 接入 CI 的 docs-gate 作业）。

对应质量审计报告 P1-4 的三类文档漂移，独立可运行，仅用 Python 标准库：

1. 路径存在性校验：扫描 README.md 与 docs/ 下所有 .md 中的内联代码块路径
   （如 py/maop/delegate/dispatcher.py、core/xxx.py），检查仓库中是否存在；
   含 py/ 前缀的路径相对仓库根解析，否则依次尝试 py/ 目录、仓库根、
   py/maop/ 目录、dashboard-enterprise/ 目录（README 中 core/、config/ 为
   包内简写，docs 中 src/、views/ 为前端仓内简写）。
2. 模块计数校验：解析 "N modules" / "N files" / "N subpackages" 表述
   （如 ``core/ (289 modules)``），按名词选口径计数（见下方第 3 层）。
3. Markdown 表格列数校验：校验每个表格行（含分隔行）列数一致，破版则报告。

## 门禁模式（--gate，CI 用）与全量报告模式的区别

默认（无参数）= 人工工具，扫描 README.md + docs/ 全部 .md 并如实报告，
包括历史记录文档；`--gate` = CI 门禁，只对"当前权威文档"判定失败。范围与
假阳性按以下三层收敛，每层都会打印计数（不静默）：

1. **范围**：文档索引 docs/README.md 第 1–6 章所列文档 + README.md。口径来自
   索引自己的声明（第 7 章："归档文档仅作历史参考……当前权威文档以上述
   第1–6章所列为准"），因此新文档要受门禁约束须先被索引列入当前章节。
   此外，头部含 ``<!-- docs-gate: exempt=<理由> -->`` 的文档被跳过——那是
   自声明的路径断言不成立于今天的文档（盘点快照 / HLD·LLD / PRD / 修复计划 /
   版本历史 / 面向 MAOS 仓的指南），理由会被逐个打印，不静默。
2. **简写与必然不可解析的分型**：跨仓（MAOS）代码引用、仓库内可按"路径后缀"
   命中的简写（``views/Tenants.vue``、``agents/crud.py`` 等）、裸文件名提及
   （``regression.py`` 只指代文件、未声明路径）。后缀匹配按路径段对齐，
   ``core/worker_pool.py`` 不会因为存在 ``core/reliability/worker_pool.py``
   而放行，所以真漂移（如 ``core/security/tenant.py``）仍会被报告。
3. **模块计数口径**与 CI 的 py/scripts/doc_reconcile.py 对齐（files=顶层非
   `__init__.py` 文件、subpackages=含 `__init__.py` 的真子包、modules=递归
   `*.py`），否则两个检查器会对同一句声明给出相反结论。

两种自声明豁免（都必须写理由，门禁模式逐条打印，不做静默放过）：

- **文件级**：``<!-- docs-gate: exempt=<理由> -->`` 放在文档前 15 行内。
  用于"本文的路径是当时/未来的事实"——盘点快照、HLD/LLD、PRD、修复计划、
  版本历史。
- **行级**：``<!-- docs-gate: skip=<理由> -->`` 放在该行末尾（表格行请放在
  最后一个单元格内，否则会多出一次列）。用于删除台账、"该路径不存在"的更正
  说明、路线图项等单行断言。

Usage: python scripts/check_docs_consistency.py [--gate]
       （--gate 为 CI 门禁模式；无参数为全量人工报告）

退出码: 0 = 范围内全部通过；1 = 发现不一致。
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
DASH_DIR = REPO_ROOT / "dashboard-enterprise"
DOC_INDEX = REPO_ROOT / "docs" / "README.md"

# MAOS（企业版仓库）独有的代码路径：本仓必然缺失，属跨仓引用而非文档漂移
CROSS_REPO_RE = re.compile(r"(?:^|/)maop/enterprise/|^enterprise/[\w.\-]+\.(?:py|json)$")

# 文档索引第 7 章起为"归档/历史"，其链接不计入门禁范围
ARCHIVE_CHAPTER_RE = re.compile(r"^##+\s*第\s*7\s*章")
MD_LINK_RE = re.compile(r"\]\(\./([^)#]+\.md)\)")

# 文档头部自声明的门禁豁免：`<!-- docs-gate: exempt=<理由> -->`
# 用于"本文的路径断言本就不该按今天的事实成立"的文档（时间点快照、
# 面向 MAOS 仓的指南等）。门禁模式跳过并打印理由，全量模式仍报告。
# 理由必须非空（`\S` 起始），否则"贴个空标记就免检"会成为绕过口。
GATE_EXEMPT_RE = re.compile(r"<!--\s*docs-gate:\s*exempt=(\S.*?)\s*-->")

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


def load_maintained_docs() -> set[str]:
    """解析 docs/README.md 第 1–6 章的 .md 链接，返回"当前权威文档"集合。

    口径来自文档索引自己的声明（第 7 章：归档文档仅作历史参考，
    "当前权威文档以上述第1–6章所列为准"），故门禁范围随索引自动伸缩：
    新文档要受门禁约束，就必须先被索引列入当前章节。
    返回相对仓库根的路径集合（含 README.md 本身）。
    """
    docs: set[str] = set()
    if DOC_INDEX.is_file():
        for line in DOC_INDEX.read_text(encoding="utf-8", errors="ignore").splitlines():
            if ARCHIVE_CHAPTER_RE.match(line.strip()):
                break
            for m in MD_LINK_RE.finditer(line):
                docs.add(f"docs/{m.group(1)}")
    # 索引缺失也不能把 README 放掉：它是产品门面，范围只应收缩不应清空
    docs.add("README.md")
    return docs


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
    # 只认常规路径字符：排除排版/状态记号（如 `✓/✗/○`）与 shell 风格
    # 交替写法（如 `requirements.lock|txt`），它们看着像路径但不是路径声明
    if not re.fullmatch(r"[\w./+\-]+", token, re.UNICODE):
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
    （含子路径前缀）；以 / 结尾的目录规则按**任意深度**的祖先目录匹配
    （git 的 `htmlcov/` 会命中 `py/htmlcov/index.html`，此前只比仓库根前缀，
    导致这类运行时产物被误报为文档漂移）。
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
            if "/" not in p:
                # 无斜杠目录规则：任一目录段命中（含深层，如 py/htmlcov/）
                if any(fnmatch.fnmatch(part, p) for part in parts[:-1]):
                    return True
            else:
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


# MAOS（企业版仓库）里才有的脚本/产物：文档按裸名引用它们属跨仓而非漂移
# 依据：F:\Nexus\MAOS\scripts\{issue_license,ci_generate_test_key}.py、
#       F:\Nexus\MAOS\maop\enterprise\_integrity_manifest.json、
#       F:\Nexus\MAOS\maop\enterprise\crl.py:128（写 data/crl_cache.json）
CROSS_REPO_NAMES = {
    "issue_license.py",
    "ci_generate_test_key.py",
    "_integrity_manifest.json",
}
CROSS_REPO_PATHS = {"data/crl_cache.json"}

# 配置项默认值里的占位符组合路径（如 `root_dir/data`、`data_dir/maop.db`），
# 描述的是运行时拼接结果，不是仓库文件
PLACEHOLDER_DIR_RE = re.compile(r"^[a-z_]+_dir/")

# 行级豁免：`<!-- docs-gate: skip=<理由> -->`（用于"本文故意提到不存在的路径"，
# 如删除台账、说明"该路径不存在"的更正注记、路线图项）。理由同样不许留空。
GATE_LINE_SKIP_RE = re.compile(r"<!--\s*docs-gate:\s*skip=(\S.*?)\s*-->")


def is_cross_repo(token: str) -> bool:
    """判断引用是否指向 MAOS（企业版仓库）的代码路径或产物。"""
    t = token.strip().rstrip("/").replace("\\", "/").lstrip("./")
    if CROSS_REPO_RE.search(t) or t in CROSS_REPO_PATHS:
        return True
    return t.rsplit("/", 1)[-1] in CROSS_REPO_NAMES


def is_placeholder_path(token: str) -> bool:
    """判断是否为配置默认值中的占位符拼接路径。"""
    return bool(PLACEHOLDER_DIR_RE.match(token.strip().lstrip("./")))


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
    其余依次尝试 py/、仓库根、py/maop/、dashboard-enterprise/（README 中
    core/、config/ 为包内简写，docs 中 src/ 等为前端仓内简写）。
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
        # 依次尝试：py/ 包内简写、仓库根、py/maop/ 包内简写、前端仓内简写
        candidates = [PY_DIR / p, REPO_ROOT / p, PY_DIR / "maop" / p, DASH_DIR / p]
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


def resolve_by_suffix(token: str, all_paths: set[str]) -> bool:
    """token 是否作为某个仓库内真实路径的**段对齐后缀**存在。

    用于放行文档里的包内/前端简写（``views/Tenants.vue`` 实为
    ``dashboard-enterprise/src/views/Tenants.vue``、``agents/crud.py`` 实为
    ``py/maop/dashboard/routers/agents/crud.py``）。按段对齐意味着
    ``core/worker_pool.py`` 不会因 ``core/reliability/worker_pool.py`` 而放行
    ——真漂移仍会被报告。
    """
    t = token.strip().rstrip("/").lstrip("./").replace("\\", "/")
    if not t:
        return False
    needle = "/" + t
    return any(p == t or p.endswith(needle) for p in all_paths)


# ---------------------------------------------------------------- 三类校验

def check_paths(
    md_files: list[pathlib.Path],
    file_index: dict[str, list[str]],
    ignore_patterns: list[str],
    all_paths: set[str],
) -> tuple[list[str], Counter]:
    """校验 1：内联代码块路径存在性。

    返回 (问题列表, 跳过分型计数)。两类"必然不可解析"按语义跳过并计数，
    避免噪声淹没真漂移：
      - 跨仓（MAOS）代码引用：本仓不存在属预期
      - 段对齐后缀命中：包内/前端简写（含裸文件名提及）
    """
    issues: list[str] = []
    skipped: Counter = Counter()
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
            if GATE_LINE_SKIP_RE.search(line):
                skipped["行级豁免（文档内已标注理由）"] += 1
                continue
            for match in INLINE_CODE_RE.finditer(line):
                token = match.group(1).strip()
                if not looks_like_path(token):
                    continue
                if token.endswith("/"):
                    continue  # 目录引用由模块计数校验负责
                resolved, _rel = resolve_path(token, base_dir=md.parent)
                if resolved is not None:
                    continue
                if is_cross_repo(token):
                    skipped["跨仓（MAOS）引用"] += 1
                    continue
                if is_placeholder_path(token):
                    skipped["配置默认值占位符路径"] += 1
                    continue
                if is_gitignored(token, ignore_patterns):
                    continue  # 命中 .gitignore，缺失属预期，不算漂移
                if resolve_by_suffix(token, all_paths):
                    skipped["包内/前端简写（后缀命中）"] += 1
                    continue
                base = token.rsplit("/", 1)[-1]
                hint = find_actual_hint(base, file_index)
                rel = md.relative_to(REPO_ROOT).as_posix()
                issues.append(
                    f"[{rel}:{lineno}] 引用的路径不存在: {token}{hint}"
                )
    return issues, skipped


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
                    issues, rel, lineno, match.group(1), int(match.group(2)),
                    match.group(3).rstrip("s"),
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
                    issues, rel, lineno, path_token, int(match.group(1)),
                    match.group(2).rstrip("s"),
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


def count_dir_stat(kind: str, directory: pathlib.Path) -> int:
    """按声明名词计数：files=顶层 .py（不含 __init__.py），
    subpackages=含 __init__.py 的顶层子目录，modules=递归 .py。

    口径与 CI 门禁 py/scripts/doc_reconcile.py 一致（"files" 指顶层文件，
    不是递归计数），否则 README 的 `core/` (5 files + 16 subpackages)
    会被误判为漂移（递归数是 259）。
    """
    k = kind.lower()
    if k.startswith("subpackage"):
        return sum(
            1 for e in directory.iterdir()
            if e.is_dir() and (e / "__init__.py").is_file() and not is_excluded(e)
        )
    if k.startswith("file"):
        # 与 CI 门禁同口径：顶层非 __init__.py 文件（含 .md 等），
        # 否则两个检查器会对同一句 README 声明给出相反结论
        return sum(
            1 for e in directory.iterdir()
            if e.is_file() and e.name != "__init__.py" and not is_excluded(e)
        )
    return count_py_modules(directory)


def _report_count_issue(
    issues: list[str], rel: str, lineno: int, dir_token: str, declared: int, kind: str
) -> None:
    """对比声明值与目录实际数量（按 kind 选口径），不一致则记一条问题。"""
    actual_dir = resolve_dir(dir_token)
    if actual_dir is None:
        return
    actual = count_dir_stat(kind, actual_dir)
    if actual != declared:
        issues.append(
            f"[{rel}:{lineno}] {kind} 声明 {declared}，"
            f"实际目录 {actual_dir.relative_to(REPO_ROOT).as_posix()}/ "
            f"按口径统计为 {actual}"
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


def main(argv: list[str] | None = None) -> int:
    """执行全部校验并按结果输出、返回退出码。"""
    gate = "--gate" in (argv if argv is not None else sys.argv)
    all_files = collect_md_files(REPO_ROOT)
    file_index = build_file_index(REPO_ROOT)
    all_paths = {p for paths in file_index.values() for p in paths}
    ignore_patterns = load_gitignore_patterns(REPO_ROOT)

    scope = all_files
    if gate:
        maintained = load_maintained_docs()
        scope = [
            f for f in all_files
            if f.relative_to(REPO_ROOT).as_posix() in maintained
        ]
        exempt: list[tuple[pathlib.Path, str]] = []
        kept: list[pathlib.Path] = []
        for f in scope:
            m = GATE_EXEMPT_RE.search("\n".join(read_lines(f)[:15]))
            if m:
                exempt.append((f, m.group(1)))
            else:
                kept.append(f)
        scope = kept
        print(
            f"门禁模式：范围为文档索引第 1–6 章所列当前权威文档 "
            f"{len(scope)}/{len(all_files)} 个"
            f"（{len(all_files) - len(scope) - len(exempt)} 个未列入当前章节、"
            f"{len(exempt)} 个自声明豁免）"
        )
        for f, reason in exempt:
            print(f"  豁免: {f.relative_to(REPO_ROOT).as_posix()} —— {reason}")
    else:
        print(f"扫描 {len(all_files)} 个 Markdown 文件（README.md + docs/）")
    print(f"建立文件索引 {len(file_index)} 个条目")

    path_issues, skipped = check_paths(scope, file_index, ignore_patterns, all_paths)
    issues: list[str] = list(path_issues)
    issues += check_module_counts(scope)
    issues += check_tables(scope)
    if skipped:
        print("按语义跳过（非漂移）：" + "、".join(
            f"{name} {count} 处" for name, count in skipped.most_common()
        ))

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
