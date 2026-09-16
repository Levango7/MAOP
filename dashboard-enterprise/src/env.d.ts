/**
 * MAOP Dashboard Enterprise — Type Declarations
 *
 * NOTE: This project is a JavaScript (JS) project, not TypeScript.
 * The `tsconfig.json` with `strict: true` exists solely for IDE
 * IntelliSense and type-checking assistance. The build pipeline
 * (`vite build`) does not invoke `tsc`; `eslint` only lints `.js`
 * and `.vue` files. The `strict` flag therefore does not enforce
 * compile-time type constraints on the codebase — it is advisory.
 */
/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  const component: DefineComponent<{}, {}, any>;
  export default component;
}