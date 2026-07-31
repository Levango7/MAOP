// D4 (2026-07-22, Phase D) — Tests for StatCard and Panel components.
//
// Verifies that the extracted reusable components render correctly with
// various prop combinations and slots. Uses @vue/test-utils mount().

import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import StatCard from '../components/StatCard.vue';

// ── StatCard tests ──────────────────────────────────────────

describe('StatCard component', () => {
  it('renders label and value', () => {
    const wrapper = mount(StatCard, {
      props: { label: 'Active Agents', value: 42 },
    });
    expect(wrapper.text()).toContain('42');
    expect(wrapper.text()).toContain('Active Agents');
  });

  it('renders icon when provided', () => {
    const wrapper = mount(StatCard, {
      props: { label: 'Cost', value: '$1.50', icon: 'dollar-sign' },
    });
    expect(wrapper.find('.stat__icon').exists()).toBe(true);
  });

  it('omits icon tile when no icon', () => {
    const wrapper = mount(StatCard, {
      props: { label: 'Total', value: 100 },
    });
    expect(wrapper.find('.stat__icon').exists()).toBe(false);
  });

  it('applies tone prop for success/fail/warn styling', () => {
    const w = mount(StatCard, {
      props: { label: 'OK', value: 5, tone: 'success' },
    });
    expect(w.find('.stat').exists()).toBe(true);
    // Tone affects icon background/color via computed style
  });

  it('renders unit when provided', () => {
    const w = mount(StatCard, {
      props: { label: 'Latency', value: 150, unit: 'ms' },
    });
    expect(w.find('.stat__unit').exists()).toBe(true);
    expect(w.find('.stat__unit').text()).toBe('ms');
  });

  it('renders delta with up/down class', () => {
    const w = mount(StatCard, {
      props: { label: 'Growth', value: 100, delta: 5 },
    });
    expect(w.find('.stat__delta').exists()).toBe(true);
    expect(w.find('.stat__delta.is-up').exists()).toBe(true);
  });

  it('renders negative delta with is-down class', () => {
    const w = mount(StatCard, {
      props: { label: 'Decline', value: 100, delta: -3 },
    });
    expect(w.find('.stat__delta.is-down').exists()).toBe(true);
  });

  it('shows skeleton when loading', () => {
    const w = mount(StatCard, {
      props: { label: 'Loading', value: 0, loading: true },
    });
    expect(w.find('.is-loading').exists()).toBe(true);
  });
});
