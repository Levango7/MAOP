import { describe, it, expect, beforeEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import EvolutionTimeline from '../components/EvolutionTimeline.vue';

const items = [
  {
    agent: 'codex',
    version: '1.0',
    from_config: 'old config',
    to_config: 'new routing strategy',
    applied_at: 1700000000,
    improved: false,
  },
  {
    agent: 'codex',
    version: '1.1',
    from_config: 'new routing strategy',
    to_config: 'prompt tuned + tool set expanded',
    applied_at: 1700000100,
    improved: true,
  },
  {
    agent: 'claude',
    version: '2.0',
    from_config: '-',
    to_config: 'switched to newer model',
    applied_at: 1700000200,
    improved: true,
  },
];

describe('EvolutionTimeline', () => {
  beforeEach(() => setActivePinia(createPinia()));

  it('renders a node per item (3 nodes)', () => {
    const w = mount(EvolutionTimeline, { props: { items } });
    const nodes = w.findAll('.evo-timeline__node');
    expect(nodes.length).toBe(3);
  });

  it('shows versions and agent names', () => {
    const w = mount(EvolutionTimeline, { props: { items } });
    const text = w.text();
    expect(text).toContain('v1.0');
    expect(text).toContain('v1.1');
    expect(text).toContain('codex');
    expect(text).toContain('claude');
  });

  it('marks improved nodes with is-gain class', () => {
    const w = mount(EvolutionTimeline, { props: { items } });
    const gains = w.findAll('.evo-timeline__dot.is-gain');
    expect(gains.length).toBe(2);
  });

  it('renders empty state when no items', () => {
    const w = mount(EvolutionTimeline, { props: { items: [] } });
    expect(w.find('.evo-timeline').exists()).toBe(false);
    expect(w.text()).toBeTruthy();
  });
});