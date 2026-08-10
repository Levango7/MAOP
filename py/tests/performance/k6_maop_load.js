// ─────────────────────────────────────────────────────────────────────
// MAOP API 负载压测脚本 (k6)
//
// 用法:
//   k6 run k6_maop_load.js
//   k6 run k6_maop_load.js --env MAOP_HOST=http://localhost:9079
//   k6 run k6_maop_load.js --env STAGE_RAMP=2m --env STAGE_HOLD=5m
//
// 场景:
//   1. 控制面 API (GET /api/agents, /api/models) — 高 QPS, 低延迟
//   2. 编排执行 API (POST /api/execute) — 中 QPS, 高延迟
//   3. 向量检索 API (POST /api/search) — 中 QPS, 中延迟
//   4. 健康检查 (GET /api/health) — 低 QPS, 极低延迟
//
// 自定义指标 (snake_case):
//   biz_success_rate    — 业务成功率 (2xx + 部分 4xx)
//   biz_error_rate      — 业务错误率 (5xx)
//   api_latency_p95     — API P95 延迟
//   execute_duration    — 编排执行耗时
// ─────────────────────────────────────────────────────────────────────

import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { SharedArray } from 'k6/data';

// ── 配置 ────────────────────────────────────────────────────────────
const maopHost = __ENV.MAOP_HOST || 'http://localhost:9079';
const apiToken = __ENV.MAOP_API_TOKEN || '';
const stageRamp = __ENV.STAGE_RAMP || '1m';
const stageHold = __ENV.STAGE_HOLD || '3m';
const stageDown = __ENV.STAGE_DOWN || '1m';
const maxVus = parseInt(__ENV.MAX_VUS || '100');

// ── 自定义指标 (snake_case per convention) ──────────────────────────
const bizSuccessRate = new Rate('biz_success_rate');
const bizErrorRate = new Rate('biz_error_rate');
const apiLatencyP95 = new Trend('api_latency_p95', true);
const executeDuration = new Trend('execute_duration', true);
const llmFallbackCount = new Counter('llm_fallback_count');

// ── 负载阶段 ────────────────────────────────────────────────────────
export const options = {
  stages: [
    { duration: stageRamp, target: maxVus },     // ramp-up
    { duration: stageHold, target: maxVus },     // hold
    { duration: stageDown, target: 0 },           // ramp-down
  ],
  thresholds: {
    // SLO 门禁 (与 docs/sla.md §2.2 对齐)
    'http_req_duration{endpoint=control}': ['p(95)<200'],   // 控制面 P95 < 200ms
    'http_req_duration{endpoint=execute}': ['p(95)<800'],   // 编排 P95 < 800ms
    'http_req_duration{endpoint=search}': ['p(95)<150'],    // 检索 P95 < 150ms
    'http_req_duration{endpoint=health}': ['p(95)<100'],    // 健康检查 P95 < 100ms
    'http_req_failed': ['rate<0.01'],                       // 错误率 < 1%
    'biz_success_rate': ['rate>0.99'],                      // 业务成功率 > 99%
  },
  noConnectionReuse: false,
  userAgent: 'k6-maop-loadtest/1.0',
};

// ── 请求头 ──────────────────────────────────────────────────────────
const headers = {
  'Content-Type': 'application/json',
  'User-Agent': 'k6-maop-loadtest/1.0',
};
if (apiToken) {
  headers['Authorization'] = `Bearer ${apiToken}`;
}

// ── 测试数据 ────────────────────────────────────────────────────────
const agentModels = new SharedArray('models', () => [
  'gpt-4o', 'gpt-4o-mini', 'claude-3.5-sonnet', 'claude-3.5-haiku',
  'gemini-1.5-pro', 'gemini-1.5-flash',
]);

const searchQueries = new SharedArray('queries', () => [
  'agent orchestration plan', 'memory consolidation strategy',
  'MCP tool discovery', 'budget guard threshold',
  'circuit breaker fallback', 'vector search ANN',
  'DAG progress streaming', 'knowledge graph entity',
  'multi-tenant RLS', 'SAML SSO callback',
]);

// ── 辅助函数 ────────────────────────────────────────────────────────
function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function recordBizMetrics(response, endpointTag) {
  const isSuccess = response.status >= 200 && response.status < 400;
  const isServerError = response.status >= 500;
  bizSuccessRate.add(isSuccess);
  bizErrorRate.add(isServerError);
  apiLatencyP95.add(response.timings.duration, { endpoint: endpointTag });
  if (isServerError) {
    llmFallbackCount.add(1);
  }
}

// ── 主测试场景 ──────────────────────────────────────────────────────
export default function () {
  // 场景权重: 控制面 40%, 编排 20%, 检索 30%, 健康检查 10%
  const scenario = Math.random();

  if (scenario < 0.4) {
    // ── 控制面 API ─────────────────────────────────────────────
    group('control_plane', () => {
      const endpoints = ['/api/agents', '/api/models', '/api/config'];
      const ep = randomItem(endpoints);
      const res = http.get(`${maopHost}${ep}`, {
        headers,
        tags: { endpoint: 'control' },
      });
      check(res, {
        'control 2xx': (r) => r.status >= 200 && r.status < 300,
        'control has body': (r) => r.body && r.body.length > 0,
      });
      recordBizMetrics(res, 'control');
    });
  } else if (scenario < 0.6) {
    // ── 编排执行 API ───────────────────────────────────────────
    group('execute', () => {
      const model = randomItem(agentModels);
      const payload = JSON.stringify({
        model,
        task: `Load test task ${Date.now()}`,
        maxTurns: 3,
        tools: ['mcp.search'],
      });
      const res = http.post(`${maopHost}/api/execute`, payload, {
        headers,
        tags: { endpoint: 'execute' },
      });
      check(res, {
        'execute accepted': (r) => r.status === 200 || r.status === 202,
      });
      recordBizMetrics(res, 'execute');
      if (res.status === 200) {
        executeDuration.add(res.timings.duration);
      }
    });
  } else if (scenario < 0.9) {
    // ── 向量检索 API ───────────────────────────────────────────
    group('search', () => {
      const query = randomItem(searchQueries);
      const payload = JSON.stringify({
        query,
        topK: 10,
        threshold: 0.7,
      });
      const res = http.post(`${maopHost}/api/search`, payload, {
        headers,
        tags: { endpoint: 'search' },
      });
      check(res, {
        'search 2xx': (r) => r.status >= 200 && r.status < 300,
        'search returns results': (r) => {
          if (r.status !== 200) return false;
          try {
            const body = JSON.parse(r.body);
            return Array.isArray(body.results) || Array.isArray(body);
          } catch (e) {
            return false;
          }
        },
      });
      recordBizMetrics(res, 'search');
    });
  } else {
    // ── 健康检查 ───────────────────────────────────────────────
    group('health', () => {
      const res = http.get(`${maopHost}/api/health`, {
        headers,
        tags: { endpoint: 'health' },
      });
      check(res, {
        'health 200': (r) => r.status === 200,
        'health has status': (r) => {
          try {
            const body = JSON.parse(r.body);
            return body.status !== undefined;
          } catch (e) {
            return false;
          }
        },
      });
      recordBizMetrics(res, 'health');
    });
  }

  // 思考时间: 100-500ms (模拟真实用户节奏)
  sleep(Math.random() * 0.4 + 0.1);
}

// ── setup/teardown ──────────────────────────────────────────────────
export function setup() {
  // 验证目标服务可达
  const res = http.get(`${maopHost}/api/health`, { headers });
  if (res.status !== 200) {
    console.error(`MAOP not reachable at ${maopHost} (status ${res.status})`);
    return { reachable: false };
  }
  console.log(`MAOP reachable at ${maopHost}, starting load test...`);
  return { reachable: true, host: maopHost };
}

export function teardown(data) {
  if (data.reachable) {
    console.log('Load test completed.');
  }
}