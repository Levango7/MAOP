import { test, expect } from '@playwright/test'

// 数据渲染 E2E（2026-10-10 补）：no-auth 模式下所有数据端点 403，既有 spec 用
// `{}` 桩——**数据渲染路径从来没被执行过**，"有数据才崩"的页面抓不到。本 spec
// 按**后端真实响应形状**（取自 router handler 构造处）灌种子数据，断言渲染，
// 并把 后端↔前端 的字段契约钉住：任一侧改名字，这里就红。
//
// 形状来源（权威=两端对齐，不凭空造）：
//   /api/sessions            → session.py list_sessions_paginated:
//                               {status, data:{items,total,page,limit,total_pages}}
//   /api/scheduling/failure-stats → scheduling.py get_failure_stats:
//                               {status, data:{agents,config,total_agents}}
//   /api/overview            → system/overview.py api_overview 的 result dict
//   /api/info/activity       → info/activity.py（{title,time} 事件）
//   /api/model/list|providers|registry → model 路由的 models/providers 数组
//
// 仅 chromium（数据形状与浏览器无关；跨浏览器由 layout/knowledge-graph 承担）。
test.skip(({ browserName }) => browserName !== 'chromium', '数据渲染仅 chromium')

// no-auth 环境下 admin 端点 403 / 企业路由 404 是设计内的浏览器级网络噪声
// （P0-4：匿名用户只有 read 角色；企业端点个人版不注册）——与 route-matrix 同款过滤。
const NETWORK_NOISE_RE =
  /^Failed to load resource: the server responded with a status of (403|404)\b/

function collectProblems(page) {
  const problems = []
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`))
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text()
      if (!NETWORK_NOISE_RE.test(text)) problems.push(`console: ${text}`)
    }
  })
  return problems
}

async function stubJson(page, urlPattern, body) {
  await page.route(urlPattern, (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

test.describe('seeded data rendering (real backend shapes)', () => {
  test('tasks page renders session rows (M-1 envelope unwrapped)', async ({ page }) => {
    const problems = collectProblems(page)
    // 真实形状：{status, data:{items,...}}——M-1 统一信封。
    await stubJson(page, /\/api\/sessions(\?|$)/, {
      status: 'ok',
      data: {
        items: [
          {
            id: 'sess-e2e-001',
            status: 'completed',
            created_at: '2026-10-10T12:00:00Z',
            updated_at: '2026-10-10T12:00:42Z',
            agent: 'claude',
            metadata: { task: '日志分析任务-e2e' },
          },
          {
            id: 'sess-e2e-002',
            status: 'failed',
            created_at: '2026-10-10T11:00:00Z',
            updated_at: '2026-10-10T11:00:10Z',
            agent: 'copilot',
            metadata: { task: '构建检查-e2e' },
          },
        ],
        total: 2,
        page: 1,
        limit: 20,
        total_pages: 1,
      },
    })
    await page.goto('/home/tasks')
    // 行渲染：id、展示名（metadata.task 优先）、状态徽章。展示名同时出现在
    // 任务表格与执行时间线两个组件——作用域到表格以避免 strict mode 歧义。
    const table = page.getByRole('table')
    await expect(table.getByText('sess-e2e-001')).toBeVisible()
    await expect(table.getByText('日志分析任务-e2e')).toBeVisible()
    await expect(table.getByText('sess-e2e-002')).toBeVisible()
    // 计数徽章（total → i18n "{n} 个任务"）
    await expect(page.getByText('2 个任务')).toBeVisible()
    expect(problems, `runtime problems on /home/tasks`).toEqual([])
  })

  test('monitor page renders agent health rows (M-1 envelope unwrapped)', async ({ page }) => {
    const problems = collectProblems(page)
    // 全字段形状 = 后端 FailurePatternDetector.get_stats() 的权威结构
    await stubJson(page, /\/api\/scheduling\/failure-stats(\?|$)/, {
      status: 'ok',
      data: {
        agents: [
          {
            agent_id: 'claude', failure_rate: 0.35, avg_latency: 1.84,
            timeout_rate: 0.05, weight: 0.7, status: 'active',
          },
          {
            agent_id: 'copilot', failure_rate: 0.0, avg_latency: 0.9,
            timeout_rate: 0.0, weight: 1.0, status: 'active',
          },
        ],
        config: {
          window_size: 50, failure_rate_threshold: 0.3,
          timeout_threshold: 30.0, recovery_consecutive_successes: 5,
        },
        total_agents: 2,
      },
    })
    // Monitor.vue 挂在 /operate（operate 组落地页；旧 /monitor 重定向到此）
    await page.goto('/operate')
    await expect(page.getByText('claude')).toBeVisible()
    await expect(page.getByText('copilot')).toBeVisible()
    expect(problems, `runtime problems on /operate`).toEqual([])
  })

  test('monitor page survives partial health payloads (no page blank)', async ({ page }) => {
    // 缺字段曾让 render 抛 TypeError → 整页空白（2026-10-10 桩数据实测复现）。
    // 后端恒定返回全字段，但防御性 ?? 0 兜底已加——本条钉住它不回退。
    const problems = collectProblems(page)
    await stubJson(page, /\/api\/scheduling\/failure-stats(\?|$)/, {
      status: 'ok',
      data: {
        agents: [{ agent_id: 'partial-agent' }], // 其余字段全部缺失
        config: {},
        total_agents: 1,
      },
    })
    await page.goto('/operate')
    // 页面必须仍然挂载（不是白页），且 agent 行渲染
    await expect(page.locator('.monitor-page')).toBeVisible()
    await expect(page.getByText('partial-agent')).toBeVisible()
    expect(problems, `runtime problems on /operate (partial)`).toEqual([])
  })

  test('overview page renders seeded KPIs, fail ranking and activity', async ({ page }) => {
    const problems = collectProblems(page)
    await stubJson(page, /\/api\/overview(\?|$)/, {
      status: 'ok',
      agents_total: 31,
      modules_total: 812,
      tests_total: 10408,
      success_rate: 0.92,
      delegations_total: 567,
      avg_latency_ms: 1840,
      recent_delegations: [
        { id: 'dl-1', agent: 'claude', task: '日志分析', status: 'completed' },
      ],
      fail_ranking: [
        { agent: 'copilot', error: 'timeout', count: 7, rate: 0.35 },
      ],
      source_files: 812,
      code_lines: 123456,
      test_files: 342,
      api_endpoints: 72,
      python_ver: '3.12.7',
      platform: 'Windows AMD64',
      version: '5.3.1',
      uptime: '9021s',
      timeseries: [{ ts: '2026-10-10T11:00:00Z', count: 12 }],
    })
    await stubJson(page, /\/api\/info\/activity(\?|$)/, {
      status: 'ok',
      events: [
        { title: '任务完成-e2e', desc: 'seed', time: '12:00', kind: 'delegation' },
        { title: '注册新 agent-e2e', desc: 'seed', time: '11:58', kind: 'system' },
      ],
    })
    await page.goto('/home')
    // KPI：agents_total / version / fail_ranking 行 / activity 事件
    await expect(page.getByText('copilot')).toBeVisible() // fail_ranking agent
    await expect(page.getByText('timeout')).toBeVisible() // fail_ranking error
    await expect(page.getByText('任务完成-e2e')).toBeVisible() // activity
    await expect(page.getByText('5.3.1').first()).toBeVisible() // version
    expect(problems, `runtime problems on /home`).toEqual([])
  })

  test('models page renders model and provider rows', async ({ page }) => {
    const problems = collectProblems(page)
    await stubJson(page, /\/api\/model\/list(\?|$)/, {
      models: [
        {
          name: 'step-5-preview', provider: 'stepfun', family: 'step',
          context_window: 65536, quality_tier: 'high', latency_tier: 'low',
          provider_healthy: true, enabled: true,
        },
      ],
    })
    await stubJson(page, /\/api\/model\/providers(\?|$)/, {
      providers: [
        {
          name: 'stepfun', type: 'openai-compatible', protocol: 'https',
          healthy: true, has_api_key: true, enabled: true,
        },
      ],
    })
    await stubJson(page, /\/api\/model\/registry(\?|$)/, { status: 'ok', data: {} })
    await page.goto('/capability/models')
    // 种子值同时出现在表格与模型下拉（严格模式，取 first）
    await expect(page.getByText('step-5-preview').first()).toBeVisible()
    await expect(page.getByText('stepfun').first()).toBeVisible()
    expect(problems, `runtime problems on /capability/models`).toEqual([])
  })
})
