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

/*
 * Compile src/tui.tsx to dist/tui.js with OpenTUI's own Solid transform.
 *
 * OpenCode's TUI registers @opentui/solid's Bun transform plugin, whose source
 * filter deliberately excludes any path under node_modules: published packages
 * are expected to ship precompiled code. A raw .tsx payload therefore loads
 * through Bun's generic JSX runtime instead of Solid's compile-time transform,
 * which renders once and wires no reactivity at all - the widget appears
 * frozen (or not at all) while commands keep working. Shipping the compiled
 * output of the exact same transform closes that gap.
 */
import { transformSolidSource } from "../node_modules/@opentui/solid/scripts/solid-transform.js";

const root = new URL("..", import.meta.url).pathname;
const source = `${root}src/tui.tsx`;
const target = `${root}dist/tui.js`;

const code = await Bun.file(source).text();
const compiled = await transformSolidSource(code, { filename: source });

if (!compiled || !compiled.includes("@opentui/solid")) {
  throw new Error("Solid transform produced unexpected output; refusing to write dist/tui.js");
}

await Bun.write(target, compiled);
console.log(`built ${target} (${compiled.length} bytes)`);
