#!/usr/bin/env bash
# drift-audit 工具的自测：先跑故意坏的合成样本，证明它不是"恒绿的空跑"。
#
# 十一个探针（每条都断言**具体**文案，不只看退出码）：
#   P1  正向对照：真实清单 → MATCH / 0
#   P2  合成清单多一条 → DRIFT / 1，且必须点名那条
#   P3  合成清单少一条 → DRIFT / 1，且必须报"GitHub 上多出"
#   P4  令牌不可用 → UNVERIFIED / 3（**不能**读成通过）
#   P5  找不到 gh → UNVERIFIED / 3（农场里 git 也起不来，故断言"缺依赖必 fail-closed"
#       这条性质，不锁死是哪一道守卫先响）
#   P5b gh 存在但令牌为空 → 精确命中 "gh 未登录"
#   P6  上一轮距今 40h → 必须显式打 GAP 并落盘（缺勤要比"没跑"显眼）
#   P7  上一轮距今 2h  → 必须**不**打 GAP（没这条，P6 的正例什么都不证明）
#   P8  日志不存在      → 必须打 GAP(NO-PRIOR-RUN)，不能静默当"正常"
#   P9  时间戳解不开    → 必须打 GAP(PARSE-FAILED)，不许猜"应该是新鲜的"
#   P10 扫描工具缺失    → 必须打 GAP(SCAN-FAILED)，不能误判成"没历史"
#
# P6–P10 都通过 MAOP_DRIFT_LOG 指向临时目录里的合成日志；**所有**探针都不写
# 使用者的持久日志（默认落 ${TMPDIR}/maop-drift-audit.log）。
#
# 用法：bash tools/drift-audit/selftest.sh      期望最后打印 ALL PROBES PASSED

set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$SELF_DIR" rev-parse --show-toplevel)"
RUN="$SELF_DIR/run.sh"
TMP="$SELF_DIR/selftest-tmp"
PY="$(command -v python || command -v py)"

PASS=0
FAIL=0
ok()   { echo "  PASS  $1"; PASS=$((PASS + 1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL + 1)); }
need() {   # need <haystack> <needle> <label>
  case "$1" in
    *"$2"*) ok "$3（命中：$2）" ;;
    *) bad "$3（未见：$2）" ;;
  esac
}
noneed() {   # 负对照：缺了它，正例就可能只是空转
  case "$1" in
    *"$2"*) bad "$3（不该出现却出现：$2）" ;;
    *) ok "$3（确未出现：$2）" ;;
  esac
}

rm -rf "$TMP"
mkdir -p "$TMP"
# 塞进 PATH 的路径必须是 MSYS 认得的 POSIX 形式：Windows 形式的 "F:/..." 在
# PATH 元素里会被解析错，导致"假的那份 gh 根本没被选中"、探针悄悄测了真凭据。
TMPX="$(cd "$TMP" && pwd)"

echo "P1 正向对照：真实清单（$ROOT）"
out="$(MAOP_DRIFT_LOG="$TMPX/p1.log" bash "$RUN" 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && ok "exit=0" || bad "exit=$rc（期望 0）"
need "$out" "OK: GitHub 分支保护与仓库清单逐条一致" "判词为 MATCH"

# 合成树：形状与仓库内一致（守卫脚本按 parents[2] 找清单）
mk_tree() {   # mk_tree <dir> <json-body>
  mkdir -p "$1/py/scripts" "$1/.github"
  cp "$ROOT/py/scripts/check_required_checks_drift.py" "$1/py/scripts/"
  printf '%s' "$2" > "$1/.github/ci-required-checks.json"
}

echo "P2 合成清单多一条（凭空要求 ghost-context-xyz）"
EXTRA="$("$PY" -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
d['contexts'].append({'name':'ghost-context-xyz','workflow':'ci.yml','job':'ghost','kind':'always'})
print(json.dumps(d,ensure_ascii=False))" "$ROOT/.github/ci-required-checks.json")"
mk_tree "$TMP/p2" "$EXTRA"
out="$(cd "$TMP/p2" && GITHUB_TOKEN="$(gh auth token)" "$PY" py/scripts/check_required_checks_drift.py --repo Levango7/MAOP 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && ok "exit=1" || bad "exit=$rc（期望 1）"
need "$out" "ghost-context-xyz" "点名了幽灵条目"
need "$out" "FAIL: 1 处漂移" "漂移计数正确"

echo "P3 合成清单少一条（去掉 CI merge gate）"
LESS="$("$PY" -c "
import json,sys
d=json.load(open(sys.argv[1],encoding='utf-8'))
d['contexts']=[c for c in d['contexts'] if c['name']!='CI merge gate']
print(json.dumps(d,ensure_ascii=False))" "$ROOT/.github/ci-required-checks.json")"
mk_tree "$TMP/p3" "$LESS"
out="$(cd "$TMP/p3" && GITHUB_TOKEN="$(gh auth token)" "$PY" py/scripts/check_required_checks_drift.py --repo Levango7/MAOP 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && ok "exit=1" || bad "exit=$rc（期望 1）"
need "$out" "GitHub 上多出、清单里没有" "反方向漂移被识别"
need "$out" "CI merge gate" "点名了被漏掉的上下文"

echo "P4 gh 只给坏令牌（run.sh 必须判 UNVERIFIED 而非通过）"
mkdir -p "$TMP/fakebin"
printf '#!/usr/bin/env bash\nif [ "$1" = "auth" ] && [ "$2" = "token" ]; then echo garbage-token; exit 0; fi\nexit 0\n' > "$TMP/fakebin/gh"
chmod +x "$TMP/fakebin/gh"
echo "  选中的 gh: $(PATH="$TMPX/fakebin:$PATH" command -v gh)"
out="$(PATH="$TMPX/fakebin:$PATH" MAOP_DRIFT_LOG="$TMPX/p4.log" bash "$RUN" 2>&1)"; rc=$?
[ "$rc" -eq 3 ] && ok "exit=3" || bad "exit=$rc（期望 3）"
need "$out" "UNVERIFIED" "判词为 UNVERIFIED"

echo "P5 PATH 被剥空（任何缺失依赖都必须 fail-closed，不能静默或判通过）"
mkdir -p "$TMP/nogh"
for b in bash date dirname git mkdir mv sed wc tail python; do
  p="$(command -v "$b" || true)"
  [ -n "$p" ] && ln -sf "$p" "$TMP/nogh/$b"
done
echo "  nogh bin: $(ls "$TMP/nogh" | tr '\n' ' ')"
out="$(PATH="$TMPX/nogh" /usr/bin/bash "$RUN" 2>&1)"; rc=$?
[ "$rc" -eq 3 ] && ok "exit=3" || bad "exit=$rc（期望 3）"
need "$out" "UNVERIFIED" "判词为 UNVERIFIED"
# 农场里 git 自己也起不来，所以先撞到的守卫可能是"不在 git 检出内"而不是"缺 gh"——
# 两条都是"缺依赖 ⇒ 拒绝放行"，这里断言的是这条性质，不锁死具体措辞。
case "$out" in
  *"找不到 gh"* | *"必须在某个 git 检出内运行"* | *"找不到清单"*)
    ok "点名了缺失的依赖" ;;
  *)
    bad "没有点名任何缺失依赖：$(printf '%s' "$out" | tail -1)" ;;
esac

echo "P5b gh 在但令牌为空（必须精确打 'gh 未登录'，不能当成通过）"
mkdir -p "$TMP/notok"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/notok/gh"
chmod +x "$TMP/notok/gh"
echo "  选中的 gh: $(PATH="$TMPX/notok:$PATH" command -v gh)"
out="$(PATH="$TMPX/notok:$PATH" MAOP_DRIFT_LOG="$TMPX/p5b.log" bash "$RUN" 2>&1)"; rc=$?
[ "$rc" -eq 3 ] && ok "exit=3" || bad "exit=$rc（期望 3）"
need "$out" "gh 未登录" "精确命中空令牌分支"

echo "P6 上一轮距今 40h（缺勤必须比『没跑』显眼）"
old="$(date -u -d '40 hours ago' +%Y-%m-%dT%H:%M:%SZ)"
printf '%s MATCH sha=seed000\n' "$old" > "$TMP/log6"
out="$(MAOP_DRIFT_LOG="$TMPX/log6" bash "$RUN" 2>&1)"; rc=$?
need "$out" "GAP:" "stdout 报了缺勤窗口"
need "$out" "40h" "把间隔小时数说出来了"
grep -qE ' GAP\(' "$TMP/log6" && ok "GAP 已落盘（下一轮读得到）" || bad "日志里没有 GAP 行"
[ "$rc" -eq 0 ] && ok "本轮判定仍完成（缺勤不改变本轮结论）" || bad "exit=$rc（期望 0）"

echo "P7 上一轮距今 2h（新鲜时不得误报）"
recent="$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)"
printf '%s MATCH sha=seed001\n' "$recent" > "$TMP/log7"
out="$(MAOP_DRIFT_LOG="$TMPX/log7" bash "$RUN" 2>&1)"; rc=$?
noneed "$out" "GAP:" "新鲜时不报缺勤"
grep -qE ' GAP\(' "$TMP/log7" && bad "日志被写了 GAP 行（阈值或判定写错）" || ok "日志无 GAP 行"

echo "P8 日志压根不存在（被删 / 从没跑过）"
out="$(MAOP_DRIFT_LOG="$TMPX/log-missing" bash "$RUN" 2>&1)"; rc=$?
need "$out" "NO-PRIOR-RUN" "日志缺失被判成缺勤，而不是'正常'"
[ -f "$TMPX/log-missing" ] && ok "缺勤记录已落盘（文件被重建）" || bad "什么都没写下——缺勤仍然隐形"

echo "P9 上一轮时间戳解不开（不许反过来猜'应该是新鲜的'）"
printf '%s MATCH sha=seed002\n' "2026-13-45T99:99:99Z" > "$TMP/log9"
out="$(MAOP_DRIFT_LOG="$TMPX/log9" bash "$RUN" 2>&1)"; rc=$?
need "$out" "PARSE-FAILED" "解不开时判缺勤而非通过"

echo "P10 扫描工具本身缺失（grep 不在 PATH）—— 必须报 SCAN-FAILED，不能误判成'没有历史'"
mkdir -p "$TMP/nogrep"
for b in bash date dirname git mkdir mv sed python; do
  p="$(command -v "$b" || true)"
  [ -n "$p" ] && ln -sf "$p" "$TMP/nogrep/$b"
done
echo "  nogrep bin: $(ls "$TMP/nogrep" | tr '\n' ' ')"
recent2="$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)"
printf '%s MATCH sha=seed010\n' "$recent2" > "$TMP/log10"
out="$(PATH="$TMPX/nogrep" /usr/bin/bash "$RUN" 2>&1)"; rc=$?
need "$out" "SCAN-FAILED" "工具缺失被如实报成扫描失败"
noneed "$out" "NO-PRIOR-RUN" "没把'扫不动'误报成'没历史'"

rm -rf "$TMP"
echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && echo "ALL PROBES PASSED" || echo "SOME PROBES FAILED"
