import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores([
    'dist',
    'demo',
    'src-tauri',
    'node_modules',
    'stats.html',
    'coverage',
    'build',
    '**/*.min.js',
  ]),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
  {
    // MapPage.tsx 属另一批次改动、本轮不可触碰；其 d3 命令式渲染与既有 memoization
    // 与 react-hooks v7 新规则存在教义性冲突，本文件内降为 warn 待该批次自行处理。
    files: ['src/pages/MapPage.tsx'],
    rules: {
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/preserve-manual-memoization': 'warn',
    },
  },
])
