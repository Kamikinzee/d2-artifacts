"""Rewrite the God Roll Codex's perk-pool text (p1 / p2 / o) and element from the manifest.

The codex cards list each gun's column 3 / column 4 / origin pools. Those were typed from
guides and drift when Bungie reissues a gun. This rewrites them from the current version(s)
so the page always shows the real pool.

    python tools/sync_codex_pools.py          # rewrite src/godroll-codex.html in place
    python tools/sync_codex_pools.py --dry    # just list what would change
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve import Resolver, norm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CODEX = ROOT / "src" / "godroll-codex.html"
SECTIONS = ["TRIALS", "IRONBANNER", "RENEGADES", "DUNGEONS"]
ELEMENTS = ["Kinetic", "Arc", "Solar", "Void", "Stasis", "Strand"]


def pool_text(res: Resolver, name: str, col: str) -> str | None:
    names = []
    for v in res.current(name):
        for p in v.cols.get(col, []):
            if p.can_roll and not p.enhanced and not p.name.startswith("Empty ") and p.name not in names:
                names.append(p.name)
    return ", ".join(names) if names else None


def card_spans(text: str):
    """Yield (section, name, start, end) for each card."""
    for sec in SECTIONS:
        m = re.search(rf"const {sec}\s*=\s*\[", text)
        if not m:
            continue
        end_arr = text.index("\n];", m.end()) if "\n];" in text[m.end():] else len(text)
        starts = [x.start() for x in re.finditer(r'\{n:"', text[m.end():end_arr])]
        starts = [m.end() + s for s in starts] + [end_arr]
        for a, b in zip(starts, starts[1:]):
            n = re.match(r'\{n:"((?:[^"\\]|\\.)*)"', text[a:b])[1].replace('\\"', '"')
            yield sec, n, a, b


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry" in sys.argv
    res = Resolver()
    text = CODEX.read_text(encoding="utf-8")
    edits = []  # (start, end, new)
    for sec, name, a, b in card_spans(text):
        block = text[a:b]
        cur = res.current(name)
        if not cur:
            print(f"!! {name} ({sec}): not in manifest")
            continue
        for key, col in (("p1", "trait1"), ("p2", "trait2"), ("o", "origin")):
            m = re.search(rf'\b{key}:"([^"]*)"', block)
            actual = pool_text(res, name, col)
            if not m or not actual:
                continue
            act = actual.split(", ")
            old = [x.strip() for x in m[1].split(",") if x.strip()]
            if {norm(x) for x in old} == {norm(x) for x in act}:
                continue  # same pool (order/case only)
            amap = {norm(x): x for x in act}
            keep = [amap[norm(x)] for x in old if norm(x) in amap]
            new = ", ".join(keep + [x for x in act if x not in keep])
            if m[1] != new:
                edits.append((a + m.start(1), a + m.end(1), new))
                print(f"{name} ({sec}) {key}: {m[1]!r} -> {new!r}")
        m = re.search(r'\bt:"([^"]*)"', block)
        if m:
            parts = m[1].split(" · ")
            el = cur[0].element
            if parts and parts[-1] in ELEMENTS and parts[-1] != el:
                new = " · ".join(parts[:-1] + [el])
                edits.append((a + m.start(1), a + m.end(1), new))
                print(f"{name} ({sec}) t: {m[1]!r} -> {new!r}")
    if not dry and edits:
        for s, e, new in sorted(edits, reverse=True):
            text = text[:s] + new + text[e:]
        CODEX.write_text(text, encoding="utf-8")
    print(f"{len(edits)} change(s){' (dry run)' if dry else ''}")


if __name__ == "__main__":
    main()
