// Dump the data globals from a src/*.html page as JSON (used by extract_rolls.py).
// Runs only the data portion of the page script (everything before the first DOM code)
// in a sandbox, so no browser is needed.
//   node tools/extract_page_data.js src/titan-loadout.html
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const NAMES = ['TYPES', 'TRIALS', 'IRONBANNER', 'RENEGADES', 'DUNGEONS', 'POOLS', 'IBPOOLS', 'RPOOLS', 'DPOOLS',
  'TSLOTS', 'SLOTS', 'BUILDS', 'CLASSES', 'SHARED_PVP_WEAPONS', 'W', 'NOTES'];

const out = {};
for (const src of scripts) {
  const lines = src.split('\n');
  // Data is declared as top-level `const UPPER_CASE = ...`; the first top-level
  // let/var/function/lower-case const marks the start of the rendering code.
  const cut = lines.findIndex(l => /^(let|var|function|async|const [a-z]|class)\b/.test(l));
  const data = (cut >= 0 ? lines.slice(0, cut) : lines).join('\n');
  const grab = NAMES.map(n => `${JSON.stringify(n)}: typeof ${n} === "undefined" ? undefined : ${n}`).join(',\n');
  const res = vm.runInNewContext(`${data}\n;({${grab}})`, {}, { timeout: 5000 });
  for (const [k, v] of Object.entries(res)) if (v !== undefined) out[k] = v;
}
process.stdout.write(JSON.stringify(out));
