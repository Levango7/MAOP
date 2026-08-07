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
      'vue/no-v-html': 'off',
      'vue/require-default-prop': 'warn',
      'vue/attribute-hyphenation': 'error',
      'vue/v-on-event-hyphenation': 'error',
    },
  },

  // ── 关闭与 Prettier 冲突的格式化规则 ─────────────────────────
  eslintConfigPrettier,
];