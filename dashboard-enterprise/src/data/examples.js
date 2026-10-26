/**
 * 示例任务库 —— 产品内容，不是用户数据。
 *
 * 为什么放在前端而不是后端 API：
 *  - 示例是静态内容，改一次发一次版本即可，不需要 DB / 迁移 / 读写权限；
 *  - 展示文案需要中英双语，放前端可直接走既有 i18n 机制；
 *  - 不引入新的只读端点，就不会有新的鉴权面与 CI 门禁面。
 *
 * `task` 字段是**可以直接执行的 prompt**，与 README 里
 * `maop run --task "<task>"` 的用法一致 —— 示例必须能真的跑，否则只是装饰。
 */
export const EXAMPLE_CATEGORIES = ['refactor', 'test', 'review', 'migrate', 'docs', 'fix'];

export const EXAMPLES = [
  {
    id: 'refactor-jwt',
    category: 'refactor',
    task: 'refactor auth module to use JWT',
  },
  {
    id: 'add-unit-tests',
    category: 'test',
    task: 'add unit tests for the credential vault service, covering expiry and rotation',
  },
  {
    id: 'review-pr-diff',
    category: 'review',
    task: 'review the staged diff and report only issues that would break production',
  },
  {
    id: 'migrate-api',
    category: 'migrate',
    task: 'migrate callers from the deprecated list_runs_v1 helper to list_runs',
  },
  {
    id: 'generate-module-docs',
    category: 'docs',
    task: 'generate API docs for the dashboard routers, one section per module',
  },
  {
    id: 'fix-failing-test',
    category: 'fix',
    task: 'find why tests/test_share_service.py fails on windows and fix the root cause',
  },
];
