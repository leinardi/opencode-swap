/*
 * MIT License
 *
 * Copyright (c) 2026 Roberto Leinardi
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

import tseslint from "typescript-eslint";
import solid from "eslint-plugin-solid";

export default [
  {
    ignores: ["node_modules/", "dist/"]
  },
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    ...solid.configs["flat/typescript"]
  },
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "@typescript-eslint/consistent-type-imports": ["error", { fixStyle: "inline-type-imports" }],
      // OpenTUI's `style` prop takes renderer-specific keys (e.g. `fg`), not CSS properties.
      "solid/style-prop": "off"
    }
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      // OpenCode 2 refuses a plugin-local copy of these ("plugins must use the
      // host's ..."); the host's own are reachable as types only.
      "@typescript-eslint/no-restricted-imports": [
        "error",
        {
          patterns: [
            { group: ["effect", "effect/*", "@opencode/*"], allowTypeImports: true, message: "Import types only: OpenCode 2 refuses a plugin-local copy." }
          ]
        }
      ],
      // Logs are not a place for anything this plugin handles.
      "no-console": "error",
      // The plugin only switches credentials: it never creates, edits or
      // removes one, and never persists anything through OpenCode's storage.
      "no-restricted-syntax": [
        "error",
        {
          selector: "MemberExpression[object.property.name='credential'][property.name=/^(create|update|remove)$/]",
          message: "Only credential.list and credential.activate are allowed."
        },
        {
          selector: "MemberExpression[property.name='storage']",
          message: "The plugin never persists state through OpenCode's storage."
        }
      ]
    }
  }
];
