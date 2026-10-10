// Guard: every statically-extractable t() key in .vue files must exist in
// the merged i18n dictionary (src/i18n/index.js — the runtime source of
// truth, built by import.meta.glob over view-*.js).
//
// Why this exists: PR #73 found Chat.vue referencing 8 keys absent from the
// dictionary — the UI rendered the raw key strings (e.g. placeholder
// "view.chat.inputPlaceholder"). Nothing caught it before merge. This guard
// turns that review method into a permanent CI gate (npm test runs in the
// Frontend Build job).
//
// Scan rules (each one exists because of a real pattern in this repo):
//  - quotes: both 'key' and "key" forms
//  - terminator: the key must be followed by , or ) — this naturally skips
//    dynamic keys built by concatenation (t('view.quotas.' + r.key)) and
//    bare identifiers (t(someVar))
//  - comments stripped first: <!-- -->, /* */, // — McpTopology.vue has a
//    commented anti-pattern example t('key', '中文兜底') that would
//    otherwise false-positive
//  - template literals (t(`view.x.${v}`)) never match a quoted-literal
//    regex, so they are skipped by construction

import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { messages } from '../i18n/index.js';

const SRC_DIR = join(dirname(fileURLToPath(import.meta.url)), '..');

function walkVueFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...walkVueFiles(full));
    } else if (entry.endsWith('.vue')) {
      out.push(full);
    }
  }
  return out;
}

function stripComments(source) {
  return source
    .replace(/<!--[\s\S]*?-->/g, ' ') // Vue template comments
    .replace(/\/\*[\s\S]*?\*\//g, ' ') // JS block comments
    .replace(/\/\/[^\n]*/g, ' '); // JS line comments
}

// Quoted key + mandatory , or ) terminator (see header).
const KEY_RE = /\bt\(\s*(['"])([\w.]+)\1\s*(,|\))/g;

function extractKeys(source) {
  return [...source.matchAll(KEY_RE)].map((m) => m[2]);
}

describe('i18n key completeness', () => {
  it('scans a non-trivial number of .vue files (anti-vacuous)', () => {
    expect(walkVueFiles(SRC_DIR).length).toBeGreaterThan(50);
  });

  it('the runtime dictionary is loaded (anti-vacuous)', () => {
    // Known-good keys that exist in the merged dictionary.
    expect(Object.keys(messages.en)).toContain('view.chat.title');
    expect(Object.keys(messages.en)).toContain('nav.overview');
  });

  it('every static t() key in .vue files exists in the merged dictionary', () => {
    const defined = new Set([
      ...Object.keys(messages.en),
      ...Object.keys(messages.zh),
    ]);
    const files = walkVueFiles(SRC_DIR);
    const missing = [];
    for (const file of files) {
      const src = stripComments(readFileSync(file, 'utf8'));
      for (const key of extractKeys(src)) {
        if (!defined.has(key)) {
          missing.push(`${relative(SRC_DIR, file)}: t('${key}')`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it('the scan itself detects a missing key (mutation self-test)', () => {
    // If the regex/scan silently regresses, the main test above would pass
    // vacuously. This probe proves the extraction + comparison path fires:
    // both call forms are extracted, and an unknown key is flagged.
    const probe = stripComments(
      `<div :title="t('view.__guard_probe__')">{{ t('view.__guard_probe__', {n: 1}) }}</div>`,
    );
    expect(extractKeys(probe)).toEqual([
      'view.__guard_probe__',
      'view.__guard_probe__',
    ]);
    const defined = new Set(Object.keys(messages.en));
    expect(defined.has('view.__guard_probe__')).toBe(false);
  });

  it('dynamic keys are skipped by construction', () => {
    // Concatenated keys, bare identifiers and template literals must not be
    // extracted (they are resolved at runtime and cannot be statically
    // checked).
    const probe = stripComments(
      [
        "t('view.quotas.' + row.key)", // concat
        't(someVar)', // bare identifier
        't(`view.scope.${x}`)', // template literal
      ].join('\n'),
    );
    expect(extractKeys(probe)).toEqual([]);
  });
});
