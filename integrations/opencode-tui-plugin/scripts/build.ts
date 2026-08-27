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
