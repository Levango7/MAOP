// ESLint 9.x Flat Config
//
// 由 .eslintrc.json 迁移而来，适配 ESLint 9.x flat config 格式。
// 参考：
//   - https://eslint.org/docs/latest/use/configure/configuration-files
//   - https://eslint.vuejs.org/recommended-rules.html
//
// 说明：
//   - 依赖 @eslint/js（ESLint 9.x 自带）、eslint-plugin-vue（已声明于 devDependencies）
//   - 依赖 globals 包（ESLint 传递依赖，用于声明标准环境全局变量）
//   - 依赖 eslint-config-prettier（关闭与 Prettier 冲突的格式化规则）

import js from '@eslint/js';
import pluginVue from 'eslint-plugin-vue';
import eslintConfigPrettier from 'eslint-config-prettier';
import globals from 'globals';

export default [
  // ── 忽略文件（放在最前面以提高匹配效率）──────────────────────
  {
    ignores: [
      'dist/**',
      'dist-enterprise/**',
      'node_modules/**',
      'e2e/**',
      'coverage/**',
      'test-results/**',
      '*.config.js',
      'api_audit.json',
      'orphan_audit.json',
    ],
  },

  // ── 基础推荐规则 ──────────────────────────────────────────────
  js.configs.recommended,

  // ── Vue 3 推荐规则（flat 格式）──────────────────────────────
  ...pluginVue.configs['flat/recommended'],

  // ── 项目特定配置 ─────────────────────────────────────────────
  {
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: {
        // 浏览器环境
        ...globals.browser,
        // ES2022 标准全局
        ...globals.es2022,
        // Node.js 全局（vite.config.js 等构建脚本可能用到）
        ...globals.node,
        // Vue 3 <script setup> 编译宏
        defineProps: 'readonly',
        defineEmits: 'readonly',
        defineExpose: 'readonly',
        withDefaults: 'readonly',
        // Vite 通过 define 注入的全局常量（见 vite.config.js）
        __APP_VERSION__: 'readonly',
      },
    },
    rules: {
      // ── 从 .eslintrc.json 迁移的通用规则 ─────────────────────
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-debugger': 'error',
      'prefer-const': 'error',
      'no-var': 'error',
      'eqeqeq': ['error', 'always'],

      // ── Vue 规则 ─────────────────────────────────────────────
      'vue/multi-word-component-names': 'off',
      // no-v-html 关闭: 项目中 v-html 用于受控 SVG 图标 (AppIcon.vue glyph, DagGraph.vue statusSvg)
      // 及经 DOMPurify 消毒的 HTML/Markdown 内容 (Chat/Docs/EvolutionHistory), 已在 utils/sanitize.js 统一防护, 详见安全审查经验。
      'vue/no-v-html': 'off',
      'vue/require-default-prop': 'warn',
      'vue/attribute-hyphenation': 'error',
      'vue/v-on-event-hyphenation': 'error',
      // Vue 最佳实践规则（R4审查补充）
      'vue/no-mutating-props': 'warn',
      'vue/no-side-effects-in-computed-properties': 'warn',
      'vue/require-explicit-emits': 'warn',
    },
  },

  // ── 关闭与 Prettier 冲突的格式化规则 ─────────────────────────
  eslintConfigPrettier,
];