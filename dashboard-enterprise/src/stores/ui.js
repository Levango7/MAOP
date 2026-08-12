import { defineStore } from 'pinia';
import { ref } from 'vue';

/**
 * useUiStore — single source of truth for global presentation state that
 * several views need to read AND write (theme, density, sidebar rail).
 *
 * Persisted to localStorage under the same keys App.vue used previously
 * (maop_theme / maop_density / maop_rail) so existing user preferences are
 * preserved. The <html> element carries data-theme / data-density attributes
 * that tokens.css + themes.css react to.
 */
function readTheme() {
  const v = typeof localStorage !== 'undefined' ? localStorage.getItem('maop_theme') : null;
  // Previously: absent or 'light' => light. Only explicit 'dark' => dark.
  return v === 'dark' ? 'dark' : 'light';
}
function readDensity() {
  const v = typeof localStorage !== 'undefined' ? localStorage.getItem('maop_density') : null;
  return v === 'compact' ? 'compact' : 'comfortable';
}
function readRail() {
  return typeof localStorage !== 'undefined' && localStorage.getItem('maop_rail') === '1';
}
function readLocale() {
  const v = typeof localStorage !== 'undefined' ? localStorage.getItem('maop_locale') : null;
  // Default to English so the existing UI shows no partial-translation regressions;
  // the user can switch to 中文 from Settings at any time.
  return v === 'zh' ? 'zh' : 'en';
}

export const useUiStore = defineStore('ui', () => {
  const theme = ref(readTheme());
  const density = ref(readDensity());
  const rail = ref(readRail());
  const locale = ref(readLocale());

  function persist() {
    try {
      localStorage.setItem('maop_theme', theme.value);
      localStorage.setItem('maop_density', density.value);
      localStorage.setItem('maop_rail', rail.value ? '1' : '0');
      localStorage.setItem('maop_locale', locale.value);
    } catch {
      /* storage may be unavailable (private mode); ignore */
    }
  }

  function applyAttrs() {
    if (typeof document === 'undefined') return;
    const el = document.documentElement;
    el.setAttribute('data-theme', theme.value);
    el.setAttribute('data-density', density.value);
    el.setAttribute('data-lang', locale.value);
  }

  function setTheme(t) {
    theme.value = t === 'dark' ? 'dark' : 'light';
    persist();
    applyAttrs();
  }
  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark');
  }
  function setDensity(d) {
    density.value = d === 'compact' ? 'compact' : 'comfortable';
    persist();
    applyAttrs();
  }
  function setRail(v) {
    rail.value = !!v;
    persist();
  }
  function toggleRail() {
    rail.value = !rail.value;
    persist();
  }
  function setLocale(l) {
    locale.value = l === 'zh' ? 'zh' : 'en';
    persist();
    applyAttrs();
  }

  // Apply on store init so the first paint already matches the saved state.
  applyAttrs();

  return { theme, density, rail, locale, setTheme, toggleTheme, setDensity, setRail, toggleRail, setLocale };
});
