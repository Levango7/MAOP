import { test, expect } from '@playwright/test'

// T-UI (2026-09-30) 顶栏/侧栏布局契约——用户拍板的三条铁律:
//   1. 顶栏是顶栏: 通栏 fixed, 不被侧栏覆盖, 品牌大标题常驻可见
//   2. 侧栏从顶栏下方开始, 只占顶栏与页脚之间的内容带
//   3. 移动端 drawer 同样从顶栏下方滑入
// 几何断言读 boundingBox 动态值, 不钉 --topbar-h 具体数(随密度切换 56/64px)。

test.describe('T-UI desktop layout', () => {
  test.use({ viewport: { width: 1280, height: 800 } })

  test('sidebar starts below topbar and never covers it', async ({ page }) => {
    await page.goto('/')
    const topbar = page.locator('.topbar')
    const sidebar = page.locator('.sidebar')
    await expect(topbar).toBeVisible()
    await expect(sidebar).toBeVisible()

    const tb = await topbar.boundingBox()
    const sb = await sidebar.boundingBox()
    expect(tb.x).toBe(0)
    // 铁律 2: 侧栏顶缘 ≥ 顶栏底缘 —— 两者互不覆盖
    expect(sb.y).toBeGreaterThanOrEqual(tb.y + tb.height - 1)
  })

  test('content (and footer) sit to the right of the sidebar', async ({ page }) => {
    await page.goto('/')
    const sb = await page.locator('.sidebar').boundingBox()
    const shell = await page.locator('.content-shell').boundingBox()
    expect(shell.x).toBeGreaterThanOrEqual(sb.x + sb.width - 1)
  })

  test('brand big title is inside the topbar and visible', async ({ page }) => {
    await page.goto('/')
    const brand = page.locator('.topbar__brandname')
    await expect(brand).toBeVisible()
    const box = await brand.boundingBox()
    const tb = await page.locator('.topbar').boundingBox()
    // 大标题完整落在顶栏内(不被侧栏盖、不被裁切)
    expect(box.y).toBeGreaterThanOrEqual(tb.y)
    expect(box.y + box.height).toBeLessThanOrEqual(tb.y + tb.height + 1)
  })

  test('rail collapse keeps the no-overlap contract', async ({ page }) => {
    await page.goto('/')
    await page.locator('.sidebar-toggle').click()
    const topbar = page.locator('.topbar')
    const tb = await topbar.boundingBox()
    const sb = await page.locator('.sidebar').boundingBox()
    expect(sb.y).toBeGreaterThanOrEqual(tb.y + tb.height - 1)
    expect(sb.width).toBeLessThan(100) // rail 收窄态
  })
})

test.describe('T-UI mobile drawer', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('drawer slides in below topbar; topbar stays visible', async ({ page }) => {
    await page.goto('/')
    const topbar = page.locator('.topbar')
    const tb = await topbar.boundingBox()
    const hamburger = page.locator('.hamburger-btn')
    await expect(hamburger).toBeVisible()
    await hamburger.click()

    const sidebar = page.locator('.sidebar')
    await expect(sidebar).toBeVisible()
    const sb = await sidebar.boundingBox()
    expect(sb.y).toBeGreaterThanOrEqual(tb.y + tb.height - 1)
    await expect(topbar).toBeVisible()
  })
})
