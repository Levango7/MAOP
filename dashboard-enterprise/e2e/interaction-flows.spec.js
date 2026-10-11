import { test, expect } from '@playwright/test'

// 交互流 E2E（2026-10-11 补）：渲染契约由 seeded-pages 钉死，本 spec 钉
// **交互**——搜索/筛选触发服务端重取、聊天发送的乐观上屏。防"点了没反应"，
// 这类回归渲染断言抓不到。
//
// 仅 chromium（交互流与浏览器无关；跨浏览器由 layout/knowledge-graph 承担）。
test.skip(({ browserName }) => browserName !== 'chromium', '交互流仅 chromium')

// no-auth 环境下 admin 端点 403 / 企业路由 404 是设计内的浏览器级网络噪声。
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

const SESSIONS = [
  {
    id: 's-a', status: 'completed', agent: 'claude-code',
    created_at: '2026-10-10T12:00:00Z', updated_at: '2026-10-10T12:00:30Z',
    metadata: { task: '日志分析任务' },
  },
  {
    id: 's-b', status: 'failed', agent: 'copilot',
    created_at: '2026-10-10T11:00:00Z', updated_at: '2026-10-10T11:00:05Z',
    metadata: { task: '构建检查任务' },
  },
  {
    id: 's-c', status: 'completed', agent: 'copilot',
    created_at: '2026-10-10T10:00:00Z', updated_at: '2026-10-10T10:00:20Z',
    metadata: { task: '代码审查任务' },
  },
]

async function stubSessions(page) {
  // Query-aware stub: the Tasks page refetches with search/status params
  // (watch → 300ms debounce → loadTasks). Filter server-side like the
  // real handler would.
  await page.route(/\/api\/sessions(\?|$)/, async (route) => {
    const url = new URL(route.request().url())
    const search = (url.searchParams.get('search') || '').toLowerCase()
    let items = SESSIONS
    if (search) {
      items = items.filter(
        (s) => (s.metadata.task + s.agent + s.id).toLowerCase().includes(search),
      )
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        data: { items, total: items.length, page: 1, limit: 20, total_pages: 1 },
      }),
    })
  })
}

test.describe('interaction flows (seeded)', () => {
  test('tasks search triggers refetch and filters rows', async ({ page }) => {
    const problems = collectProblems(page)
    await stubSessions(page)
    await page.goto('/home/tasks')
    // 初始三行（任务名同现表格与执行时间线——作用域到表格）
    const table = page.getByRole('table')
    await expect(table.getByText('日志分析任务')).toBeVisible()
    await expect(table.getByText('构建检查任务')).toBeVisible()
    await expect(table.getByText('代码审查任务')).toBeVisible()
    // 输入搜索词 → 300ms 防抖 → 服务端重取 → 行过滤
    await page.locator('.filterbar__input').fill('copilot')
    await expect(table.getByText('日志分析任务')).toBeHidden()
    await expect(table.getByText('构建检查任务')).toBeVisible()
    await expect(table.getByText('代码审查任务')).toBeVisible()
    // 清空搜索 → 行恢复
    await page.locator('.filterbar__input').fill('')
    await expect(table.getByText('日志分析任务')).toBeVisible()
    expect(problems, 'runtime problems on /home/tasks').toEqual([])
  })

  test('chat send appends the user message', async ({ page }) => {
    const problems = collectProblems(page)
    // agent 下拉数据源 + 会话侧栏 + SSE 流端点（空流 → 前端走错误兜底分支，
    // 但用户消息是乐观上屏，先于任何网络交互）。
    await page.route(/\/api\/agents(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'ok',
          agents: [{
            name: 'claude-code', description: 'seed', model: 'auto',
            driver: 'cli', capabilities: ['codegen'], enabled: true, healthy: true,
          }],
        }),
      }))
    await page.route(/\/api\/chat\/sessions(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', data: [] }),
      }))
    await page.route(/\/api\/chat\/stream(\?|$)/, (route) =>
      route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
        body: '',
      }))
    await page.goto('/run?tab=chat')
    // 选 agent（发送按钮前置条件）
    await page.locator('#chat-agent-select').selectOption('claude-code')
    // 输入并发送
    await page.locator('textarea').fill('你好，这是 E2E 交互测试消息')
    await page.locator('.send-btn').click()
    // 用户消息上屏（乐观追加，不等网络）
    await expect(page.locator('.msg-row.user')).toContainText('你好，这是 E2E 交互测试消息')
    expect(problems, 'runtime problems on /run?tab=chat').toEqual([])
  })
})
