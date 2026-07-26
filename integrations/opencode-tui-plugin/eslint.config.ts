import tseslint from "typescript-eslint"
import solid from "eslint-plugin-solid"

export default [
  {
    ignores: ["node_modules/", "dist/"],
  },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    ...solid.configs["flat/typescript"],
  },
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // OpenTUI's `style` prop takes renderer-specific keys (e.g. `fg`), not CSS properties.
      "solid/style-prop": "off",
    },
  },
]
