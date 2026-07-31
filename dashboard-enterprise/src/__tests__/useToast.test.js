// Tests for useToast timer cleanup — dismiss() now clears the auto-dismiss
// setTimeout so manual dismiss does not leak a pending timer.
//
// Covers src/composables/useToast.js:
//   - show() schedules an auto-dismiss timer
//   - dismiss() clears that timer (no leak)
//   - success/error/warn/info shortcuts set the correct tone
//   - timeout:null keeps a persistent toast with no timer

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { useToast, toastState } from '../composables/useToast.js';

describe('useToast timer cleanup', () => {
  beforeEach(() => {
    toastState.items.splice(0, toastState.items.length);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    toastState.items.splice(0, toastState.items.length);
  });

  it('show() adds a toast and schedules auto-dismiss via setTimeout', () => {
    const toast = useToast();
    const id = toast.show('hello', { timeout: 1000 });
    expect(id).toBeTruthy();
    expect(toastState.items).toHaveLength(1);
    expect(toastState.items[0].message).toBe('hello');
    expect(toastState.items[0]._timer).toBeTruthy();
    // Advancing fake timers fires the auto-dismiss → toast removed.
    vi.advanceTimersByTime(1000);
    expect(toastState.items).toHaveLength(0);
  });

  it('dismiss() clears the scheduled timer so it does not leak', () => {
    const toast = useToast();
    const clearSpy = vi.spyOn(global, 'clearTimeout');
    const id = toast.show('leak me', { timeout: 5000 });
    expect(toastState.items).toHaveLength(1);
    const scheduledTimerId = toastState.items[0]._timer;
    expect(scheduledTimerId).toBeTruthy();

    toast.dismiss(id);

    // The toast is removed immediately.
    expect(toastState.items).toHaveLength(0);
    // clearTimeout was invoked with the exact timer id that was scheduled.
    expect(clearSpy).toHaveBeenCalledWith(scheduledTimerId);

    // After advancing well past the original timeout, no further callback fires
    // (no leaked timer re-triggering dismiss).
    clearSpy.mockClear();
    vi.advanceTimersByTime(10000);
    expect(clearSpy).not.toHaveBeenCalled();
    expect(toastState.items).toHaveLength(0);
  });

  it('dismiss() is a safe no-op for an unknown id', () => {
    const toast = useToast();
    toast.show('a', { timeout: 0 });
    toast.dismiss(999999);
    expect(toastState.items).toHaveLength(1);
  });

  it('success/error/warn/info shortcuts set the correct tone', () => {
    const toast = useToast();
    toast.success('s');
    toast.error('e');
    toast.warn('w');
    toast.info('i');
    expect(toastState.items).toHaveLength(4);
    expect(toastState.items[0].tone).toBe('success');
    expect(toastState.items[1].tone).toBe('fail');
    expect(toastState.items[2].tone).toBe('warn');
    expect(toastState.items[3].tone).toBe('info');
  });

  it('show() without timeout creates a persistent toast with no timer', () => {
    const toast = useToast();
    // No opts → opts.timeout is undefined → `if (t.timeout)` is falsy → no timer.
    toast.show('persistent');
    expect(toastState.items).toHaveLength(1);
    expect(toastState.items[0]._timer).toBeUndefined();
    vi.advanceTimersByTime(100000);
    expect(toastState.items).toHaveLength(1);
  });

  it('show() with timeout:null falls back to the 3200ms default and auto-dismisses', () => {
    const toast = useToast();
    // opts.timeout === null triggers the 3200 default branch in show().
    toast.show('default', { timeout: null });
    expect(toastState.items[0].timeout).toBe(3200);
    expect(toastState.items[0]._timer).toBeTruthy();
    vi.advanceTimersByTime(3200);
    expect(toastState.items).toHaveLength(0);
  });

  it('shortcuts pass through extra options (e.g. timeout)', () => {
    const toast = useToast();
    toast.success('with opts', { timeout: 500 });
    expect(toastState.items[0].tone).toBe('success');
    expect(toastState.items[0].timeout).toBe(500);
    vi.advanceTimersByTime(500);
    expect(toastState.items).toHaveLength(0);
  });
});