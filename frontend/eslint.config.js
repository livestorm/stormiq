// Bug-focused lint (no stylistic rules): ESLint recommended + Vue essential.
// Run: npm run lint
import js from "@eslint/js";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...pluginVue.configs["flat/essential"],
  {
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.browser },
    },
    rules: {
      // Unused catch bindings and `_`-prefixed names are intentional.
      "no-unused-vars": ["error", { caughtErrors: "none", argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  {
    // The design system's base components have short names (Card, Chip,
    // Icon…); none collides with an HTML element ("Button" is UiButton).
    files: ["src/components/ui/**/*.vue"],
    rules: { "vue/multi-word-component-names": "off" },
  },
];
