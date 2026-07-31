// Tests for i18n t() interpolation — {var} placeholder replacement.
//
// Covers the newly-fixed t(key, params) behaviour in src/i18n/index.js:
//   - {var} placeholders are replaced with params values
//   - missing params leave the placeholder intact
//   - missing keys return the key itself
//   - no params returns the raw string

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useI18n, messages } from '../i18n/index.js';

describe('i18n t() interpolation', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    // Inject test-only keys into the en dictionary so we control the template
    // strings exactly. Cleaned up in afterEach to avoid polluting other suites.
    messages.en.hello = 'Hello {name}';
    messages.en.multi = '{greeting}, {name}! You have {count} messages.';
    messages.en.staticText = 'Static text';
  });

  afterEach(() => {
    delete messages.en.hello;
    delete messages.en.multi;
    delete messages.en.staticText;
  });

  it('replaces {name} placeholder with params.name', () => {
    const { t } = useI18n();
    expect(t('hello', { name: 'world' })).toBe('Hello world');
  });

  it('returns the raw string when no params are given', () => {
    const { t } = useI18n();
    expect(t('hello')).toBe('Hello {name}');
    expect(t('staticText')).toBe('Static text');
  });

  it('returns the key itself when the key is missing from the dictionary', () => {
    const { t } = useI18n();
    expect(t('nonexistent.key.xyz')).toBe('nonexistent.key.xyz');
  });

  it('keeps placeholder as-is when the matching param is missing', () => {
    const { t } = useI18n();
    expect(t('hello', {})).toBe('Hello {name}');
    expect(t('multi', { greeting: 'Hi' })).toBe('Hi, {name}! You have {count} messages.');
  });

  it('replaces multiple placeholders in one string', () => {
    const { t } = useI18n();
    expect(t('multi', { greeting: 'Hi', name: 'Bob', count: 3 }))
      .toBe('Hi, Bob! You have 3 messages.');
  });

  it('stringifies non-string param values during replacement', () => {
    const { t } = useI18n();
    expect(t('hello', { name: 42 })).toBe('Hello 42');
  });

  it('treats null param value as missing (keeps placeholder)', () => {
    const { t } = useI18n();
    expect(t('hello', { name: null })).toBe('Hello {name}');
  });
});