"""Build wishlists/spinz.txt (DIM wish list) from the codex + loadout sheets.

    python tools/build_wishlist.py          # refresh manifest, extract, validate, write
    python tools/build_wishlist.py --check  # same, but don't write anything (CI-style)

Pipeline: manifest.refresh() -> extract_rolls (src/*.html -> data/rolls.json)
          -> validate (manifest + hard rules) -> wishlists/spinz.txt + reports/validation.md

Only validated perks are written. " / " alternates expand into every valid combination.
Per weapon version DIM gets:
  1. full-roll lines (barrel + mag + both traits)   note ends "[full roll]"
  2. trait-only lines (trait 1 + trait 2)           note ends "[traits only]"
DIM uses the first matching line, so full rolls come first. Masterwork, mod and origin
aren't written (DIM ignores masterworks/mods; origins are validated but left out so a roll
isn't rejected over a 2-option origin). Older versions and Adept copies get lines only
for combinations whose perks all exist on that version.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract_rolls  # noqa: E402
import manifest  # noqa: E402
from resolve import Resolver  # noqa: E402
from validate import report, run_all  # noqa: E402
import build_dim_loadouts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "wishlists" / "spinz.txt"
REPORT = ROOT / "reports" / "validation.md"
PERK_COLS = ["col1", "col2", "trait1", "trait2"]
MODE_LABEL = {"pvp": "PvP", "pve": "PvE"}


def plug_for(res: Resolver, v, col: str, name: str) -> int | None:
    ps = res.find_in(v.cols.get(col, []), name)
    if not ps:
        return None
    base = [p for p in ps if not p.enhanced] or ps   # DIM matches enhanced via the base perk
    return base[0].hash


def compact(contexts: list[str]) -> str:
    """'Titan sheet: A', 'Titan sheet: B (no-Praxic alt)' -> 'Titan sheet: A, B (no-Praxic alt)'."""
    groups: dict[str, list[str]] = {}
    for c in contexts:
        head, sep, tail = c.partition(" sheet: ")
        key, item = (head + " sheet", tail) if sep else (c, "")
        groups.setdefault(key, [])
        if item and item not in groups[key]:
            groups[key].append(item)
    out = []
    for k, items in groups.items():
        items = [i for i in items if not (i.endswith(" (no-Praxic alt)") and i[:-16] in items)]
        if not items:
            out.append(k)
        elif len(items) > 3:
            out.append(f"{k}: {len(items)} builds")
        else:
            out.append(f"{k}: {', '.join(items)}")
    return "; ".join(out)


def clean(s: str) -> str:
    return s.replace("|", "/").replace("\n", " ").strip()


def build(res: Resolver, results: dict) -> tuple[list[str], dict]:
    # (weapon name) -> (version hash) -> {kind: {perk tuple: [entries]}}
    table: dict = {}
    vlabel: dict[int, str] = {}
    for rr in results["rolls"]:
        if rr.status not in ("PASS", "PARTIAL"):
            continue
        r = rr.roll
        cols = [c for c in PERK_COLS if rr.valid.get(c)]
        for v in rr.versions:
            vlabel[v.hash] = v.label
            for kind, use in (("full roll", cols), ("traits only", [c for c in ("trait1", "trait2") if c in cols])):
                if kind == "traits only" and (len(use) < 2 or use == cols):
                    continue
                for combo in itertools.product(*[rr.valid[c] for c in use]):
                    hashes = [plug_for(res, v, c, n) for c, n in zip(use, combo)]
                    if None in hashes:
                        continue
                    key = tuple(sorted(set(hashes)))
                    (table.setdefault(r["weapon"], {}).setdefault(v.hash, {})
                     .setdefault(kind, {}).setdefault(key, [])
                     .append((r["mode"], r["context"])))
    lines, stats = [], {"weapons": 0, "lines": 0, "items": 0}
    for weapon in sorted(table, key=str.lower):
        stats["weapons"] += 1
        lines.append(f"// {weapon}")
        for vh, kinds in table[weapon].items():
            stats["items"] += 1
            label = vlabel[vh]
            suffix = label[len(weapon):].strip()  # "(old version)" / "(Adept)" / ""
            for kind in ("full roll", "traits only"):
                for perks, entries in kinds.get(kind, {}).items():
                    by_mode: dict[str, list[str]] = {}
                    for mode, ctx in entries:
                        by_mode.setdefault(mode, [])
                        if ctx not in by_mode[mode]:
                            by_mode[mode].append(ctx)
                    note = " · ".join(f"{MODE_LABEL.get(m, m)} — {compact(c)}" for m, c in by_mode.items())
                    note += f" [{kind}]" + (f" {suffix}" if suffix else "")
                    tags = ",".join(sorted(by_mode))
                    lines.append(f"dimwishlist:item={vh}&perks={','.join(map(str, perks))}#notes:{clean(note)}|tags:{tags}")
                    stats["lines"] += 1
        lines.append("")
    return lines, stats


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    a = ap.parse_args()
    version = manifest.refresh().split("|")[0]
    doc = extract_rolls.main()
    res = Resolver()
    results = run_all(res, doc)
    body, stats = build(res, results)
    rolls = results["rolls"]
    cnt = {s: sum(r.status == s for r in rolls) for s in ("PASS", "PARTIAL", "FAIL", "SKIP")}
    lo_fail = sum(l.status == "FAIL" for l in results["loadouts"])
    header = [
        "title:Spinz's D2 god rolls",
        f"description:Generated from Spinz's God Roll Codex and loadout sheets, validated against Bungie manifest "
        f"{version} on {dt.date.today().isoformat()}. {stats['weapons']} weapons, {stats['lines']} rolls.",
        "",
        "// GENERATED by tools/build_wishlist.py — edit the src/*.html sheets (or data/manual_rolls.json), not this file.",
        "// Notes: '[full roll]' = barrel+mag+traits match, '[traits only]' = both traits match.",
        "",
    ]
    if not a.check:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text("\n".join(header + body), encoding="utf-8", newline="\n")
        REPORT.parent.mkdir(exist_ok=True)
        REPORT.write_text(f"_Manifest {version}, {dt.date.today().isoformat()}_\n\n" + report(results), encoding="utf-8")
    # DIM loadouts (subclass, mods, exotic armor) — catches illegal armor mods / subclass picks too
    g = build_dim_loadouts.Gen([])
    dim_items = g.run()
    for prob in g.problems:
        print("DIM LOADOUT FAIL:", prob)
    if not a.check:
        build_dim_loadouts.write(dim_items, g.problems, False)
    lo_fail += len(g.problems)
    print(f"Rolls: {cnt['PASS']} PASS, {cnt['PARTIAL']} PARTIAL, {cnt['FAIL']} FAIL, {cnt['SKIP']} SKIP | "
          f"Loadouts: {lo_fail} FAIL | DIM loadouts: {len(dim_items)}")
    print(f"Wishlist: {stats['weapons']} weapons, {stats['items']} item versions, {stats['lines']} lines"
          + ("" if a.check else f" -> {OUT.relative_to(ROOT)} (report: {REPORT.relative_to(ROOT)})"))
    sys.exit(1 if cnt["FAIL"] or lo_fail else 0)


if __name__ == "__main__":
    main()
