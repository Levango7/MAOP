// D4 (2026-07-22, Phase D) — Tests for StatCard and Panel components.
//
// Verifies that the extracted reusable components render correctly with
// various prop combinations and slots. Uses @vue/test-utils mount().

import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import StatCard from '../components/StatCard.vue';
import Panel from '../components/Panel.vue';

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
      props: { label: 'Cost', value: '$1.50', icon: '💰' },
    });
    expect(wrapper.find('.stat-icon').exists()).toBe(true);
    expect(wrapper.find('.stat-icon').text()).toBe('💰');
  });

  it('omits icon tile when no icon', () => {
    const wrapper = mount(StatCard, {
      props: { label: 'Total', value: 100 },
    });
    expect(wrapper.find('.stat-icon').exists()).toBe(false);
  });

  it('applies variant class for success/fail/warn', () => {
    const w = mount(StatCard, {
      props: { label: 'OK', value: 5, variant: 'success' },
    });
    // w.element is the Vue app container; query for the actual root.
    const root = w.element.querySelector('.stat-card');
    expect(root.classList.contains('success')).toBe(true);
  });

  it('applies accent border color via style', () => {
    const w = mount(StatCard, {
      props: { label: 'X', value: 1, accent: 'var(--accent)' },
    });
    const root = w.element.querySelector('.stat-card');
    expect(root.classList.contains('has-accent')).toBe(true);
    expect(root.style.borderLeftColor).toContain('var(--accent)');
  });

  it('renders sparkline SVG when sparkline prop is set', () => {
    const w = mount(StatCard, {
      props: { label: 'RPM', value: 100, sparkline: '0,10 50,20 100,5' },
    });
    expect(w.find('.sparkline svg').exists()).toBe(true);
    expect(w.find('polyline').attributes('points')).toBe('0,10 50,20 100,5');
  });

  it('supports value slot override', () => {
    const w = mount(StatCard, {
      props: { label: 'Count', value: 0 },
      slots: { value: '<strong>99+</strong>' },
    });
    expect(w.find('.stat-value strong').exists()).toBe(true);
    expect(w.find('.stat-value strong').text()).toBe('99+');
  });

  it('centered layout applies centered class', () => {
    const w = mount(StatCard, {
      props: { label: 'Centered', value: 5, centered: true },
    });
    const root = w.element.querySelector('.stat-card');
    expect(root.classList.contains('centered')).toBe(true);
  });

  it('renders footer slot when provided', () => {
    const w = mount(StatCard, {
      props: { label: 'X', value: 1 },
      slots: { footer: '<span>delta +2</span>' },
    });
    expect(w.find('.stat-footer').exists()).toBe(true);
    expect(w.find('.stat-footer').text()).toContain('delta +2');
  });
});

// ── Panel tests ────────────────────────────────────────────

describe('Panel component', () => {
  it('renders title in h3', () => {
    const w = mount(Panel, {
      props: { title: 'System Health' },
      slots: { default: '<p>content</p>' },
    });
    expect(w.find('h3.panel-title').text()).toBe('System Health');
    expect(w.find('.panel-body').html()).toContain('content');
  });

  it('omits header when no title and no actions slot', () => {
    const w = mount(Panel, {
      slots: { default: '<p>no header</p>' },
    });
    expect(w.find('.panel-header').exists()).toBe(false);
    expect(w.find('.panel-body').exists()).toBe(true);
  });

  it('shows header when actions slot is provided even without title', () => {
    const w = mount(Panel, {
      slots: {
        default: '<p>body</p>',
        actions: '<span class="count">5</span>',
      },
    });
    expect(w.find('.panel-header').exists()).toBe(true);
    expect(w.find('.panel-actions').text()).toContain('5');
  });

  it('renders actions slot alongside title', () => {
    const w = mount(Panel, {
      props: { title: 'Logs' },
      slots: {
        default: '<div>log lines</div>',
        actions: '<button>Clear</button>',
      },
    });
    expect(w.find('.panel-title').text()).toBe('Logs');
    expect(w.find('.panel-actions button').exists()).toBe(true);
  });

  it('applies no-shadow class when shadow=false', () => {
    const w = mount(Panel, {
      props: { title: 'Flat', shadow: false },
    });
    const root = w.element.querySelector('.panel');
    expect(root.classList.contains('no-shadow')).toBe(true);
  });

  it('applies margin-bottom style', () => {
    const w = mount(Panel, {
      props: { title: 'X', marginBottom: 16 },
    });
    const root = w.element.querySelector('.panel');
    expect(root.style.marginBottom).toBe('16px');
  });

  it('applies overflow-x auto for wide content', () => {
    const w = mount(Panel, {
      props: { title: 'Table', overflow: 'auto' },
    });
    const root = w.element.querySelector('.panel');
    expect(root.style.overflowX).toBe('auto');
  });

  it('applies custom body padding', () => {
    const w = mount(Panel, {
      props: { title: 'Compact', bodyPadding: 12 },
    });
    const root = w.element.querySelector('.panel');
    expect(root.style.padding).toBe('12px');
  });

  it('renders footer slot when provided', () => {
    const w = mount(Panel, {
      props: { title: 'X' },
      slots: { footer: '<span>footer text</span>' },
    });
    expect(w.find('.panel-footer').exists()).toBe(true);
    expect(w.find('.panel-footer').text()).toContain('footer text');
  });

  it('preserves root class "panel" for light-theme compatibility', () => {
    const w = mount(Panel, { props: { title: 'X' } });
    // App.vue's .light-theme .panel override relies on this class name.
    const root = w.element.querySelector('.panel');
    expect(root).not.toBeNull();
    expect(root.classList.contains('panel')).toBe(true);
  });
});
