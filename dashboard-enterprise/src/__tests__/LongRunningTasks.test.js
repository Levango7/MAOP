import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount, flushPromises } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock('../stores/api.js', () => ({
  useApiStore: () => ({ get: mockGet, post: mockPost, put: vi.fn(), delete: vi.fn() }),
}));

import LongRunningTasks from '../views/LongRunningTasks.vue';

/**
 * Shapes mirror `LongRunningTask.model_dump()`: `progress` is 0.0–1.0.
 *
 * Two behaviours are asserted because getting them wrong produces a bad
 * interaction rather than a visible bug:
 *   1. Progress must render as a percentage (0.42 → "42%"), not as raw 0.42.
 *   2. Cancel must be disabled for terminal states — the backend answers 409
 *      for those, so an enabled button would be a guaranteed error.
 */
describe('LongRunningTasks.vue', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_locale', 'en');
    mockGet.mockReset();
    mockPost.mockReset();
  });

  const TASKS = [
    { task_id: 't1', agent_name: 'codex', task: 'refactor auth', status: 'running', progress: 0.42, progress_message: 'step 3/7' },
    { task_id: 't2', agent_name: 'claude', task: 'write docs', status: 'pending', progress: 0, progress_message: '' },
  ];

  function mockList(tasks = TASKS) {
    mockGet.mockResolvedValue({ status: 'ok', tasks, count: tasks.length });
  }

  function mountView() {
    return mount(LongRunningTasks);
  }

  it('renders tasks with agent, description and percentage', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain('codex');
    expect(wrapper.text()).toContain('refactor auth');
    // 0.42 must be shown as 42%, not 0.42
    expect(wrapper.text()).toContain('42%');
  });

  it('drives the progress bar width from progress', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    const fill = wrapper.find('.lrt__bar-fill');
    expect(fill.attributes('style')).toContain('width: 42%');
  });

  it('enables Cancel for running tasks', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    const cancel = wrapper.findAll('button').filter((b) => b.text() === 'Cancel');
    expect(cancel.length).toBeGreaterThan(0);
    expect(cancel[0].attributes('disabled')).toBeUndefined();
  });

  it('disables Cancel for terminal states', async () => {
    mockList([
      { task_id: 't3', agent_name: 'codex', task: 'done thing', status: 'completed', progress: 1, progress_message: '' },
      { task_id: 't4', agent_name: 'codex', task: 'broke thing', status: 'failed', progress: 0.6, progress_message: '' },
    ]);
    const wrapper = mountView();
    await flushPromises();

    const buttons = wrapper.findAll('.lrt__actions button');
    expect(buttons).toHaveLength(2);
    // Both terminal ⇒ both disabled; clicking would only earn a 409.
    buttons.forEach((b) => expect(b.attributes('disabled')).toBeDefined());
  });

  it('asks for confirmation before cancelling', async () => {
    mockList();
    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('.lrt__actions button').trigger('click');
    await flushPromises();

    expect(mockPost).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain('Cancel this task?');
  });

  it('cancels only after the user confirms', async () => {
    mockList();
    mockPost.mockResolvedValue({ status: 'ok' });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.find('.lrt__actions button').trigger('click');
    await flushPromises();
    await wrapper.findAll('button').find((b) => b.text() === 'Cancel task').trigger('click');
    await flushPromises();

    expect(mockPost).toHaveBeenCalledWith('/api/long-running-tasks/t1/cancel', {});
    expect(mockGet).toHaveBeenCalledTimes(2);
  });

  it('shows the empty state when nothing is active', async () => {
    mockList([]);
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('No active long-running tasks');
  });

  it('shows a load error instead of an empty list', async () => {
    mockGet.mockRejectedValue(new Error('boom'));
    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.text()).toContain('Failed to load tasks');
  });
});
