#!/usr/bin/env bash
# MAOP required-checks 漂移对账：仓库清单 .github/ci-required-checks.json
#                     ↔ GitHub 分支保护实况，逐条双向。
#
# 为什么在本机/带外侧跑，而不是在 CI 里：能读
# `/repos/{repo}/branches/{branch}/protection` 的凭据只有仓库管理员。CI 的默认
# GITHUB_TOKEN 结构性地读不到它（403，实测记录见 docs/ci-gates.md §7.6），
# 而给 workflow 的 permissions: 加 administration / metadata 会让**整条 workflow
# 失效**（0 作业，同样是实测）。CI 侧能做的只有"清单 vs PR 实况"那半边
# （§7.7 的金丝雀），它证不了"平台要求得更少"（保护被放松/删除）——那半边靠本脚本。
#
# 本脚本用调用者自己的凭据：gh CLI 的登录令牌（管理员身份即可，**不要求**新建 PAT，
# 也不要求仓库里配任何 secret）。
#
# 环境变量（全部有默认值，就地跑无需设置）：
#   MAOP_DRIFT_WT      对账用的检出；默认 = 本脚本所属仓库的顶层
#   MAOP_DRIFT_UPDATE  =1 时先 fetch + --ff-only，保证清单取自 origin/<branch>
#                      （默认不更新：不假设你允许脚本动工作树）
#   MAOP_DRIFT_BRANCH  受保护分支；默认取清单里的 branch 字段
#   MAOP_DRIFT_REPO    owner/name；默认从 origin 远端 URL 解析
#   MAOP_DRIFT_TOKEN   令牌来源覆盖；默认 `gh auth token`
#   MAOP_DRIFT_LOG     结论日志；默认 ${TMPDIR:-/tmp}/maop-drift-audit.log
#   MAOP_DRIFT_GAP_H   缺勤阈值（小时）；默认 36
#
# 三态语义（与 py/scripts/check_required_checks_drift.py 一致）：
#   0 MATCH / 1 DRIFT / 3 UNVERIFIED —— 3 **不是**通过，是"这轮没看到"。

set -uo pipefail

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(git -C "$SELF_DIR" rev-parse --show-toplevel 2>/dev/null)"
WT="${MAOP_DRIFT_WT:-$ROOT}"
LOG="${MAOP_DRIFT_LOG:-${TMPDIR:-/tmp}/maop-drift-audit.log}"
GAP_H="${MAOP_DRIFT_GAP_H:-36}"
MAX_LINES=2000
MANIFEST="$WT/.github/ci-required-checks.json"
DRIFT_PY="$WT/py/scripts/check_required_checks_drift.py"

mkdir -p "$(dirname "$LOG")"
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
verdict() { printf '%s %s sha=%s\n' "$(ts)" "$1" "${2:-?}" >> "$LOG"; }

fail_unverified() {
  echo "UNVERIFIED: $1"
  verdict "UNVERIFIED($1)"
  exit 3
}

# ── 缺勤可见性自检 ────────────────────────────────────────────────────
# 放在任何可能退出的检查之前：这样后续步骤崩了，这一行也已经落盘。
#
# 为什么需要它：这份日志只记得住**跑过的轮次**，漏跑的那些不会留下任何痕迹。
# 2026-10-15 真撞上了——定时任务的会话层 125ms 就失败，本脚本根本没执行，
# audit.log 一片空白，和"今天还没到点"完全同形；而更早 audit.log 还被删过一次。
# 这条守卫对外的唯一凭据就是这份日志，所以"缺勤"必须比"没跑"更显眼，
# 否则日志的沉默会被读成持续的绿。
RUN_LINE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z '

note_gap() {
  printf '%s GAP(%s)\n' "$(ts)" "$1" >> "$LOG"
  echo "GAP: $1"
}

last_run_ts=""
if [ -f "$LOG" ]; then
  # grep 的退出码必须单独拿：管道 `grep | tail` 的 $? 是 tail 的，会把
  # "grep 根本跑不了"（缺工具，127）误当成"日志里没有历史"（真缺勤，1）。
  hits="$(grep -E "$RUN_LINE_RE" "$LOG")"
  scan_rc=$?
  if [ "$scan_rc" -eq 0 ]; then
    # 纯 bash 取最后一行＋前 20 字符：不依赖 cut/tail 在场，否则工具缺失会
    # 伪装成"日志没有历史"（自测的 stripped-PATH 农场就是这么把它试出来的）。
    last_line="${hits##*$'\n'}"
    last_run_ts="${last_line:0:20}"
  elif [ "$scan_rc" -ne 1 ]; then
    note_gap "SCAN-FAILED 无法扫描日志（grep 退出码 $scan_rc）—— 不能据此说'一直在守'"
    last_run_ts="SCAN-FAILED"
  fi
fi

if [ "$last_run_ts" = "SCAN-FAILED" ]; then
  :
elif [ -z "$last_run_ts" ]; then
  # 没有历史本身就是缺勤：从没跑过、或日志被删——两种都不能当成"一直在守"。
  note_gap "NO-PRIOR-RUN 日志里没有任何此前的一轮（从没跑过 / 日志被删）"
else
  prev_epoch="$(date -u -d "$last_run_ts" +%s 2>/dev/null || true)"
  if [ -z "$prev_epoch" ]; then
    note_gap "PARSE-FAILED 上一轮时间戳解不开: $last_run_ts"
  else
    elapsed_h=$(( ( $(date -u +%s) - prev_epoch ) / 3600 ))
    if [ "$elapsed_h" -gt "$GAP_H" ]; then
      note_gap "${elapsed_h}h 距上一轮已超 ${GAP_H}h —— 中间那轮缺失，本轮之前存在未覆盖窗口"
    fi
  fi
fi

# ── 前置条件 ─────────────────────────────────────────────────────────
[ -n "$ROOT" ] || fail_unverified "本脚本必须在某个 git 检出内运行（或显式给 MAOP_DRIFT_WT）"
[ -f "$MANIFEST" ] || fail_unverified "找不到清单 $MANIFEST"
[ -f "$DRIFT_PY" ] || fail_unverified "找不到对账脚本 $DRIFT_PY"
command -v gh >/dev/null 2>&1 || fail_unverified "找不到 gh（或改用 MAOP_DRIFT_TOKEN 传令牌）"
PY="$(command -v python || command -v py || true)"
[ -n "$PY" ] || fail_unverified "找不到 python"

SHA="$(git -C "$WT" rev-parse --short HEAD)"

# 清单必须取自受保护分支，而不是任何人正在写的分支。默认信任调用者已经把检出
# 停在正确位置；只有显式 MAOP_DRIFT_UPDATE=1 才动工作树（脚本不擅自改你的树）。
if [ "${MAOP_DRIFT_UPDATE:-0}" = "1" ]; then
  BRANCH="${MAOP_DRIFT_BRANCH:-$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8')).get('branch','master'))" "$MANIFEST" 2>/dev/null)}"
  [ -n "$BRANCH" ] || fail_unverified "清单里读不到 branch，且未给 MAOP_DRIFT_BRANCH"
  git -C "$WT" fetch -q origin "$BRANCH" || fail_unverified "git fetch 失败（网络？）"
  git -C "$WT" merge --ff-only -q "origin/$BRANCH" || fail_unverified "工作树无法快进 origin/$BRANCH（本地有提交？）"
  SHA="$(git -C "$WT" rev-parse --short HEAD)"
fi

REPO="${MAOP_DRIFT_REPO:-$(git -C "$WT" remote get-url origin 2>/dev/null | sed -E 's#.*[:/]([^/]+/[^/.]+)(\.git)?$#\1#')}"
case "$REPO" in
  */*.git) REPO="${REPO%.git}" ;;
esac
case "$REPO" in
  */*) : ;;
  *) fail_unverified "无法从 origin 解析 owner/name（得到 '$REPO'），请设 MAOP_DRIFT_REPO=owner/name" ;;
esac

TOKEN="${MAOP_DRIFT_TOKEN:-$(gh auth token 2>/dev/null)}"
[ -n "$TOKEN" ] || fail_unverified "gh 未登录（gh auth login），或设 MAOP_DRIFT_TOKEN"

# ── 对账 ─────────────────────────────────────────────────────────────
OUT="$(cd "$WT" && GITHUB_TOKEN="$TOKEN" "$PY" py/scripts/check_required_checks_drift.py --repo "$REPO" --branch "${MAOP_DRIFT_BRANCH:-}" 2>&1)"
RC=$?
case "$RC" in
  0) V=MATCH ;;
  1) V=DRIFT ;;
  3) V=UNVERIFIED ;;
  *) V="UNEXPECTED(rc=$RC)" ;;
esac

{
  echo "-----------------------------------------------------------"
  printf '%s %s sha=%s\n' "$(ts)" "$V" "$SHA"
  echo "$OUT" | sed 's/^/    /'
} >> "$LOG"

if [ "$(wc -l < "$LOG")" -gt "$MAX_LINES" ]; then
  tail -n "$MAX_LINES" "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

echo "$V"
echo "$OUT"
exit "$RC"
