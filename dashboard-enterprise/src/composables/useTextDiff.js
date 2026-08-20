/**
 * useTextDiff — 极简逐行文本 diff（基于 LCS 动态规划）。
 *
 * 设计目标:
 *   - 不引入 diff / jsdiff 等外部库，保持 dashboard 零依赖增量。
 *   - 输出逐行分类（added / removed / unchanged），供前端 diff 高亮渲染。
 *   - 复杂度 O(n*m) LCS，对演化周期的 prompt/配置文本（通常 < 500 行）足够。
 *
 * 用法:
 *   const { diff } = useTextDiff();
 *   const rows = diff('a\nb', 'a\nc');
 *   // [{ type: 'unchanged', text: 'a' }, { type: 'removed', text: 'b' }, { type: 'added', text: 'c' }]
 */

/**
 * 计算 LCS 长度矩阵。
 * @param {string[]} a 基准行数组
 * @param {string[]} b 目标行数组
 * @returns {number[][]} LCS 长度矩阵
 */
function lcsMatrix(a, b) {
  const n = a.length;
  const m = b.length;
  const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      if (a[i - 1] === b[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  return dp;
}

/**
 * 回溯 LCS 矩阵生成 diff 行序列。
 * @param {number[][]} dp LCS 矩阵
 * @param {string[]} a 基准行数组
 * @param {string[]} b 目标行数组
 * @returns {{type: string, text: string}[]} diff 行数组
 */
function backtrack(dp, a, b) {
  const result = [];
  let i = a.length;
  let j = b.length;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) {
      result.unshift({ type: 'unchanged', text: a[i - 1] });
      i--;
      j--;
    } else if (dp[i - 1][j] >= dp[i][j - 1]) {
      result.unshift({ type: 'removed', text: a[i - 1] });
      i--;
    } else {
      result.unshift({ type: 'added', text: b[j - 1] });
      j--;
    }
  }
  while (i > 0) {
    result.unshift({ type: 'removed', text: a[i - 1] });
    i--;
  }
  while (j > 0) {
    result.unshift({ type: 'added', text: b[j - 1] });
    j--;
  }
  return result;
}

/**
 * 计算两段文本的逐行 diff。
 * @param {string} baseText 基准文本
 * @param {string} targetText 目标文本
 * @returns {{type: 'added'|'removed'|'unchanged', text: string}[]} diff 行数组
 */
function diffText(baseText, targetText) {
  const a = String(baseText || '').split('\n');
  const b = String(targetText || '').split('\n');
  const dp = lcsMatrix(a, b);
  return backtrack(dp, a, b);
}

/**
 * 计算 diff 统计摘要。
 * @param {{type: string}[]} rows diff 行数组
 * @returns {{added: number, removed: number, unchanged: number}}
 */
function diffStats(rows) {
  const stats = { added: 0, removed: 0, unchanged: 0 };
  for (const r of rows) {
    if (r.type === 'added') stats.added++;
    else if (r.type === 'removed') stats.removed++;
    else stats.unchanged++;
  }
  return stats;
}

export function useTextDiff() {
  return { diff: diffText, stats: diffStats };
}