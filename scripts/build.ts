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

// Compiles src/ file by file into a mirrored dist/. OpenCode never runs the
// Solid transform on node_modules, so the published files must already be
// Solid's universal-renderer output (docs/opencode-v2-integration.md, V4/V5).
// Not bundled: solid-js and @opentui/* must stay imports, since the host
// provides them. Relative specifiers get their `.js` (or `/index.js`), which
// the host's ESM loader needs and the TypeScript sources leave out.

import { existsSync, statSync } from "node:fs";
import { mkdir, readdir, rm } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { transformSolidSource } from "../node_modules/@opentui/solid/scripts/solid-transform.js";

const root = resolve(import.meta.dir, "..");
const sourceDir = join(root, "src");
const targetDir = join(root, "dist");

function resolveRelative(specifier: string, file: string): string {
  if (!specifier.startsWith("./") && !specifier.startsWith("../")) return specifier;
  const base = resolve(dirname(file), specifier);
  if (existsSync(`${base}.ts`) || existsSync(`${base}.tsx`)) return `${specifier}.js`;
  if (existsSync(base) && statSync(base).isDirectory() && (existsSync(join(base, "index.ts")) || existsSync(join(base, "index.tsx")))) {
    return `${specifier}/index.js`;
  }
  throw new Error(`${relative(root, file)}: cannot resolve ${specifier}`);
}

async function sources(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true });
  const nested = await Promise.all(
    entries.map((entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return sources(path);
      return Promise.resolve(/\.tsx?$/.test(entry.name) ? [path] : []);
    })
  );
  return nested.flat().sort();
}

await rm(targetDir, { recursive: true, force: true });
for (const file of await sources(sourceDir)) {
  const code = await Bun.file(file).text();
  const compiled = await transformSolidSource(code, {
    filename: file,
    resolvePath: (specifier: string) => resolveRelative(specifier, file)
  });
  if (file.endsWith(".tsx") && !compiled.includes("@opentui/solid")) {
    throw new Error(`${relative(root, file)}: Solid transform produced unexpected output`);
  }
  const target = join(targetDir, relative(sourceDir, file)).replace(/\.tsx?$/, ".js");
  await mkdir(dirname(target), { recursive: true });
  await Bun.write(target, compiled);
  console.log(`built ${relative(root, target)} (${compiled.length} bytes)`);
}
