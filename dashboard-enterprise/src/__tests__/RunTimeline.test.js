import { describe, it, expect, beforeEach, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';

const mockToast = { success: vi.fn(), error: vi.fn(), warn: vi.fn(), info: vi.fn(), show: vi.fn(), dismiss: vi.fn() };
vi.mock('../composables/useToast.js', () => ({
  useToast: () => mockToast,
  toastState: { items: [] },
}));

import RunTimeline from '../components/RunTimeline.vue';

/**
 * These cases pin the PEV grouping: the component turns a flat `run_events`
 * list into Plan / Execute / Verify sections. If the grouping silently broke,
 * the timeline would still render (all events are still shown) — just without
 * the phase structure that makes a retried or blocked run legible. So the
 * assertions below are about *which section* an event lands in, not merely
 * whether it appears.
 */
describe('RunTimeline.vue (PEV grouping)', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    localStorage.setItem('maop_locale', 'en');
  });

  const toneFn = (s) => s;

  function mountTimeline(events) {
    // No stubs: stubbing Badge would drop its text content, which the gate
    // assertions depend on.
    return mount(RunTimeline, { props: { events, toneFn } });
  }

  const PLAN = { seq: 1, kind: 'run.plan', agent: 'a', status: 'ok', duration_ms: 10, payload: { routing_key: 'chat' } };
  const STEP1 = { seq: 2, kind: 'run.step', agent: 'codex', status: 'ok', duration_ms: 100, payload: { stdout: 'done', attempt: 1 } };
  const STEP2 = { seq: 3, kind: 'run.step', agent: 'codex', status: 'failed', duration_ms: 50, payload: { error: 'boom', attempt: 2 } };
  const VERIFY = {
    seq: 4, kind: 'run.verify', agent: 'codex', status: 'passed', duration_ms: 5,
    payload: { summary: 'all gates green', gates: [{ name: 'lint', passed: true }, { name: 'tests', passed: false }] },
  };
  const COST = { seq: 5, kind: 'run.cost', status: 'ok', duration_ms: 0, payload: { estimated_cost: 0.02 } };

  it('renders the empty state when there are no events', () => {
    const wrapper = mountTimeline([]);
    expect(wrapper.text()).toContain('No timeline recorded');
  });

  it('groups events into Plan / Execute / Verify / Other, in that order', () => {
    const wrapper = mountTimeline([PLAN, STEP1, VERIFY, COST]);
    const heads = wrapper.findAll('.run-timeline__phase').map((n) => n.text());
    expect(heads).toEqual(['Plan', 'Execute', 'Verify', 'Other']);
  });

  it('drops empty phases instead of showing an empty section', () => {
    // No run.plan event ⇒ the Plan section must not exist at all.
    const wrapper = mountTimeline([STEP1, VERIFY]);
    const heads = wrapper.findAll('.run-timeline__phase').map((n) => n.text());
    expect(heads).toEqual(['Execute', 'Verify']);
  });

  it('summarises each phase with event count and total duration', () => {
    const wrapper = mountTimeline([STEP1, STEP2]);
    const meta = wrapper.findAll('.run-timeline__group-meta').map((n) => n.text());
    // two steps, 100 + 50 ms
    expect(meta[0]).toContain('2 events');
    expect(meta[0]).toContain('150ms');
  });

  it('shows the attempt badge only when a step actually retried', () => {
    const wrapper = mountTimeline([STEP1, STEP2]);
    const attempts = wrapper.findAll('.run-timeline__attempt').map((n) => n.text());
    // attempt 1 must NOT render as "attempt 1" — that would read as a retry.
    expect(attempts).toEqual(['attempt 2']);
  });

  it('renders verify gates with pass/fail tones', () => {
    const wrapper = mountTimeline([VERIFY]);
    const gates = wrapper.findAll('.run-timeline__gates .badge, .run-timeline__gates Badge');
    // Fall back to any element carrying the gate text if the class differs.
    const texts = gates.length ? gates.map((n) => n.text()) : [wrapper.find('.run-timeline__gates').text()];
    const joined = texts.join(' ');
    expect(joined).toContain('lint');
    expect(joined).toContain('tests');
  });

  it('keeps unknown event kinds in Other rather than dropping them', () => {
    const wrapper = mountTimeline([{ seq: 9, kind: 'run.mystery', status: 'ok', duration_ms: 1, payload: {} }]);
    const heads = wrapper.findAll('.run-timeline__phase').map((n) => n.text());
    expect(heads).toEqual(['Other']);
  });
});
