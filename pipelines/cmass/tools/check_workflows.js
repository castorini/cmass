#!/usr/bin/env node
// Syntax check for Claude Code Workflow scripts. `node --check` is not enough: a Workflow script is a module-like
// file whose body runs as an async function (top-level await), and some Node versions accept a broken one.
// This compiles the body as an async function, after checking that `export const meta = {...}` is a literal.
//
//   node tools/check_workflows.js FILE_OR_DIR ...     (directories: every .js below them; exit 1 on any error)
const fs = require('fs')
const path = require('path')

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const files = []
const walk = (p) => {
  const st = fs.statSync(p)
  if (st.isDirectory()) {
    for (const e of fs.readdirSync(p)) if (!['node_modules', 'run', '.git'].includes(e)) walk(path.join(p, e))
  } else if (p.endsWith('.js') && path.basename(p) !== 'check_workflows.js') files.push(p)
}
process.argv.slice(2).forEach(walk)

let bad = 0
for (const f of files) {
  const src = fs.readFileSync(f, 'utf8')
  const m = src.match(/^export const meta\s*=\s*/m)
  if (!m) { console.log(`skip  ${f} (no export const meta: not a Workflow script)`); continue }
  // the meta literal: from its opening brace to the matching closing brace
  let i = m.index + m[0].length, depth = 0, end = -1, quote = null
  for (let j = i; j < src.length; j++) {
    const c = src[j]
    if (quote) { if (c === '\\') j++; else if (c === quote) quote = null; continue }
    if (c === '"' || c === "'" || c === '`') quote = c
    else if (c === '{') depth++
    else if (c === '}' && --depth === 0) { end = j + 1; break }
  }
  try {
    if (end < 0) throw new Error('meta: unbalanced braces')
    const metaSrc = src.slice(i, end)
    if (/\$\{|\.\.\.|\(/.test(metaSrc.replace(/(["'`])(?:\\.|(?!\1).)*\1/gs, '""'))) throw new Error('meta is not a pure literal')
    new Function(`return (${metaSrc})`)()
    new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', 'workflow',
      src.slice(0, m.index) + 'const meta = ' + src.slice(i))
    console.log(`ok    ${f}`)
  } catch (e) {
    bad++
    console.log(`ERROR ${f}: ${e.message}`)
  }
}
console.log(`${files.length} file(s), ${bad} error(s)`)
process.exit(bad ? 1 : 0)
