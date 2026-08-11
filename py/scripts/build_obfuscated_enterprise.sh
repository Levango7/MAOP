#!/usr/bin/env bash
# MAOP Enterprise — 混淆发布流水线
#
# 严格遵守顺序：混淆 → 签名 → 打 wheel(若顺序反了,L3 完整性校验会误报全部篡改)
#
# 前置条件(发布机):
#   pip install pyarmor>=8.0 wheel build
#
# 用法:
#   bash py/scripts/build_obfuscated_enterprise.sh
#
# 输出:
#   py/dist/obf/maop/enterprise/         混淆后的企业版模块
#   py/dist/wheels/maop-*-py3-none-any.whl   完整 wheel

set -euo pipefail

# ── 定位 ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PY_DIR}/.." && pwd)"
ENT_SRC="${PY_DIR}/maop/enterprise"
OBF_DIR="${PY_DIR}/dist/obf"
WHEEL_DIR="${PY_DIR}/dist/wheels"

# 生产私钥路径(不提交到 git)
PRIVATE_KEY="${MAOP_SIGNING_KEY:-${HOME}/.maop/keys/prod_private_key.pem}"

if [[ ! -f "${PRIVATE_KEY}" ]]; then
    echo "ERROR: production signing key not found at ${PRIVATE_KEY}" >&2
    echo "  Set MAOP_SIGNING_KEY env var or place key at default location." >&2
    exit 1
fi

if ! python -c "import pyarmor" 2>/dev/null; then
    echo "ERROR: pyarmor not installed. Run: pip install 'pyarmor>=8.0'" >&2
    exit 1
fi

echo "==> [1/5] Cleaning previous obfuscation output"
rm -rf "${OBF_DIR}"
mkdir -p "${OBF_DIR}" "${WHEEL_DIR}"

# ── 2. 复制 maop 源到 dist/obf(混淆只覆盖 enterprise/,其余保持明文)──
echo "==> [2/5] Staging maop package to dist/obf/"
mkdir -p "${OBF_DIR}/maop"
# rsync 不在 Windows Git Bash 默认 delivering;fallback to cp+删除
if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude '__pycache__' --exclude '*.pyc' "${PY_DIR}/maop/" "${OBF_DIR}/maop/"
else
    (cd "${PY_DIR}/maop" && find . -type d -name __pycache__ -prune -o -type f -print | \
        grep -v '\.pyc$' | while read -r f; do
            mkdir -p "${OBF_DIR}/maop/$(dirname "$f")"
            cp "$f" "${OBF_DIR}/maop/$f"
        done)
fi

# ── 3. PyArmor 混淆 enterprise/*.py(排除 __init__.py 与 keys/)──
echo "==> [3/5] Obfuscating enterprise modules with PyArmor"

# 先移除待混淆的明文文件, PyArmor 会生成同名混淆文件
ENTERPRISE_PY=(rbac tenant audit sso saml_handler license crl ha container pg_persist n8n tls_auto)
for mod in "${ENTERPRISE_PY[@]}"; do
    f="${OBF_DIR}/maop/enterprise/${mod}.py"
    if [[ -f "${f}" ]]; then
        rm "${f}"
    fi
done

pyarmor gen \
    --output "${OBF_DIR}/maop/enterprise" \
    --enable-jit \
    --mix-str \
    --private \
    $(for m in "${ENTERPRISE_PY[@]}"; do echo "${ENT_SRC}/${m}.py"; done) \
    2>&1 | grep -vE "^INFO" | tail -20

# ── 4. 对混淆产物重新签名完整性 manifest ────────────────────────
echo "==> [4/5] Re-signing integrity manifest against OBFUSCATED code"
python "${SCRIPT_DIR}/sign_enterprise_modules.py" \
    --private-key "${PRIVATE_KEY}" \
    --root "${OBF_DIR}"

# 验证 manifest 与混淆产物同目录
if [[ ! -f "${OBF_DIR}/maop/enterprise/_integrity_manifest.json" ]]; then
    echo "ERROR: integrity manifest missing after signing" >&2
    exit 1
fi

# ── 5. 构建 wheel ─────────────────────────────────────────────
echo "==> [5/5] Building wheel"
cd "${PY_DIR}"
python -m build --wheel --outdir "${WHEEL_DIR}" 2>&1 | tail -5

echo ""
echo "✓ Obfuscated enterprise wheel built:"
ls -lh "${WHEEL_DIR}"/*.whl 2>/dev/null || echo "  (check ${WHEEL_DIR} for output)"
echo ""
echo "Verify: python -c \"from maop.config.edition import get_edition; print(get_edition())\""
echo "  Without MAOP_LICENSE_KEY: expect Edition.PERSONAL"
echo "  With valid MAOP_LICENSE_KEY: expect Edition.ENTERPRISE"
