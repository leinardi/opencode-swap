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

// scripts/check-license-headers.sh, run against a scratch git repository.

import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// A git hook exports GIT_DIR, GIT_INDEX_FILE and friends; the scratch repository must not
// inherit them, or `git add` would write into the real index.
function cleanEnv(): Record<string, string> {
  return Object.fromEntries(Object.entries(process.env).filter(([key, value]) => !key.startsWith("GIT_") && value !== undefined)) as Record<
    string,
    string
  >;
}

function run(command: string[], cwd: string) {
  const result = Bun.spawnSync(command, { cwd, env: cleanEnv(), stdout: "pipe", stderr: "pipe" });
  return { code: result.exitCode, stdout: result.stdout.toString(), stderr: result.stderr.toString() };
}

const ROOT = run(["git", "rev-parse", "--show-toplevel"], import.meta.dir).stdout.trim();
const SCRIPT = join(ROOT, "scripts", "check-license-headers.sh");
// The header exactly as this repository's own files carry it: the hash-comment block of the
// checker itself, and the block-comment form of this file.
const HASH_HEADER = readFileSync(SCRIPT, "utf8").split("\n").slice(2, 23).join("\n") + "\n";
const BLOCK_HEADER = readFileSync(import.meta.path, "utf8").split("\n").slice(0, 23).join("\n") + "\n";

let scratch: string;
let repo: string;

function git(...args: string[]) {
  const result = run(["git", ...args], repo);
  if (result.code !== 0) throw new Error(`git ${args.join(" ")} failed: ${result.stderr}`);
}

function write(path: string, content: string) {
  writeFileSync(join(repo, path), content);
}

function add(path: string, content: string) {
  write(path, content);
  git("add", "--", path);
}

function check() {
  return run(["bash", "scripts/check-license-headers.sh"], repo);
}

beforeEach(() => {
  scratch = mkdtempSync(join(tmpdir(), "license-headers-"));
  repo = join(scratch, "repo");
  for (const dir of ["scripts", ".mk", "src"]) mkdirSync(join(repo, dir), { recursive: true });
  git("init", "-q");
  copyFileSync(SCRIPT, join(repo, "scripts", "check-license-headers.sh"));
  write("Makefile", HASH_HEADER + "\nMK_COMMON_FILES := help.mk\n");
  write(".mk/help.mk", "ifndef X\nendif\n");
  write(".mk/local.mk", HASH_HEADER.replace("2026", "2025-2026") + "\nifndef Y\nendif\n");
  write("scripts/bootstrap-mk-common.sh", "#!/usr/bin/env bash\n");
  write("src/mod.js", BLOCK_HEADER + "\nexport {};\n");
  write("src/ui.tsx", BLOCK_HEADER + "\nexport {};\n");
  write("config.yaml", "key: value\n");
  write("README.md", "# Title\n");
  git("add", "-A");
});

afterEach(() => {
  rmSync(scratch, { recursive: true, force: true });
});

describe("check-license-headers.sh", () => {
  test("a tree with every header passes", () => {
    const result = check();
    // The checker itself, Makefile, .mk/local.mk, src/mod.js and src/ui.tsx; the make-common
    // files, YAML and Markdown are not governed.
    expect(result.stderr).toBe("");
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("5 files checked");
  });

  const wrong: [string, string, string][] = [
    ["missing", "src/bare.ts", "export {};\n"],
    ["other holder", "scripts/other.sh", "#!/usr/bin/env bash\n" + HASH_HEADER.replace("Roberto Leinardi", "Someone Else")],
    ["other licence", "src/gpl.ts", "/*\n * Copyright (c) 2026 Roberto Leinardi\n *\n * This program is free software\n */\n"],
    ["below the window", "src/late.ts", "//\n".repeat(30) + BLOCK_HEADER],
    ["no year", "src/noyear.ts", BLOCK_HEADER.replace("Copyright (c) 2026", "Copyright (c)")]
  ];
  for (const [name, path, content] of wrong) {
    test(`a wrong header fails and names the file: ${name}`, () => {
      add(path, content);
      const result = check();
      expect(result.code).toBe(1);
      expect(result.stderr.split("\n")).toContain(`  ${path}`);
    });
  }

  test("a shebang may precede the header", () => {
    add("scripts/tool.sh", "#!/usr/bin/env bash\n#\n" + HASH_HEADER + "#\n# What it does.\n");
    const result = check();
    expect(result.stderr).toBe("");
    expect(result.code).toBe(0);
  });

  test("a fetched snippet stops being exempt once it is local", () => {
    write("Makefile", HASH_HEADER + "\nMK_COMMON_FILES := other.mk\n");
    const result = check();
    expect(result.code).toBe(1);
    expect(result.stderr.split("\n")).toContain("  .mk/help.mk");
  });

  test("an untracked file is not checked", () => {
    write("src/untracked.ts", "export const x = 1;\n");
    expect(check().code).toBe(0);
  });

  test("Python files are no longer governed", () => {
    add("src/legacy.py", "x = 1\n");
    const result = check();
    expect(result.code).toBe(0);
    expect(result.stdout).toContain("5 files checked");
  });
});
