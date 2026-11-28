"""Import blueberries.gg god rolls into a DIM wish list. Anyone can run this.

    python tools/import_blueberries.py --fetch                       # download new/changed pages, then import
    python tools/import_blueberries.py                               # import from the pages already downloaded
    python tools/import_blueberries.py --fetch --wishlist my.txt     # also write a standalone DIM wish list
    python tools/import_blueberries.py --wishlist pve.txt --mode pve # PvE rolls only (or --mode pvp)

--fetch reads the site's sitemaps and downloads every god-roll page that's new or has a newer
lastmod than the cached copy, waiting 10 s between requests (the site's robots.txt Crawl-delay).
The first run downloads ~400 pages, so it takes about 70 minutes. Later runs only fetch what changed.

Every "PvE Roll" / "PvP Roll" table (and the older list-style pages) becomes a roll. Each option is
checked against the Bungie manifest: guide spellings are corrected, a trait listed under the wrong
column is moved, and perks that aren't on the weapon's current version are dropped. A roll is kept
only if at least one trait column survives. Output:
  data/imports/blueberries.json    rolls (merged into the main pipeline by extract_rolls.py)
  reports/blueberries_import.md    what was dropped or skipped, and why
  --wishlist PATH                  a DIM wish list built from these rolls only
"""
from __future__ import annotations

import difflib
import html
import json
import re
import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_rolls import ROLL_KEYS  # noqa: E402
from resolve import Resolver, norm  # noqa: E402
from validate import validate_roll  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "bb"
OUT = ROOT / "data" / "imports" / "blueberries.json"
REPORT = ROOT / "reports" / "blueberries_import.md"

KEYS = dict(ROLL_KEYS)
KEYS.update({"perk 1": "trait1", "perk 2": "trait2", "trait": "trait1", "origin trait": "origin",
             "origin": "origin", "mod": "mod", "weapon mod": "mod", "masterwork": "masterwork",
             "frame": None, "intrinsic": None, "catalyst": None, "tube": "col1", "rail": "col1",
             "projectile": "col2", "rounds": "col2", "ammo": "col2", "barrel/sight": "col1"})


def text(fragment: str) -> str:
    t = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def options(v: str) -> list[str]:
    """'A or B (also good: C, D)' -> [A, B, C, D]."""
    v = re.sub(r"(?i)\(\s*(also \w+|alt(ernative)?s?|or|or even)\s*:?\s*", ", ", v).replace(")", "")
    parts = re.split(r"\s*(?:,|/|\bor\b|;)\s*", v)
    out = []
    for p in parts:
        p = p.strip(" .:-–")
        if p and not re.match(r"(?i)^(any|n/?a|none|your choice|personal preference)\b", p):
            out.append(p)
    return list(dict.fromkeys(out))


def weapon_name(page: str) -> str | None:
    m = re.search(r"Name:\s*</[^>]+>\s*(?:<[^>]+>\s*)*([^<]+)<", page)
    if m and text(m.group(1)):
        return text(m.group(1))
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    if m:
        t = text(m.group(1))
        t = re.sub(r"(?i)^destiny 2\s*", "", t)
        return re.split(r"(?i):|\s+god rolls?", t)[0].strip()
    return None


def page_date(page: str) -> str:
    m = re.search(r'"dateModified"\s*:\s*"([0-9-]{10})', page)
    return m.group(1) if m else ""


def respell(res: Resolver, name: str, perks: dict) -> dict:
    """Fix guide spellings ("One-for-All", "Crystallised") to the manifest name in that column."""
    cur = [v for v in res.versions(name) if v.current] or res.versions(name)
    out = {}
    for col, val in perks.items():
        pool = sorted({p.name for v in cur for p in v.cols.get(col, [])})
        if not pool:
            out[col] = val
            continue
        fixed = []
        for o in val.split(" / "):
            if o in pool:
                fixed.append(o)
                continue
            m = difflib.get_close_matches(o, pool, n=1, cutoff=0.8) or                 difflib.get_close_matches(o.replace("-", " "), pool, n=1, cutoff=0.8)
            fixed.append(m[0] if m else o)
        out[col] = " / ".join(dict.fromkeys(fixed))
    # Guides sometimes list a trait under the wrong perk column: move it to the column it rolls in.
    pools = {c: {p.name for v in cur for p in v.cols.get(c, [])} for c in ("trait1", "trait2")}
    moved = {"trait1": [], "trait2": []}
    for col, other in (("trait1", "trait2"), ("trait2", "trait1")):
        keep = []
        for o in filter(None, out.get(col, "").split(" / ")):
            (moved[other] if o not in pools[col] and o in pools[other] else keep).append(o)
        if col in out:
            out[col] = keep
    for col in ("trait1", "trait2"):
        vals = list(dict.fromkeys((out.get(col) or []) + moved[col]))
        if vals:
            out[col] = " / ".join(vals)
        else:
            out.pop(col, None)
    return out


MODE_RE = re.compile(r"(?i)\b(PvE|PvP)\b[^:]*?\b(?:god\s+)?rolls?\b\s*:?\s*(.*)")


def cells(row: str) -> list[tuple[str, int]]:
    """(text, colspan) for every th/td in a row."""
    out = []
    for attrs, body in re.findall(r"<t[hd]([^>]*)>(.*?)</t[hd]>", row, re.S):
        m = re.search(r'colspan="?(\d+)', attrs)
        out.append((text(body), int(m.group(1)) if m else 1))
    return out


def add_perk(perks: dict, info: dict, k: str, v: str) -> None:
    k = k.lower().strip().rstrip(":")
    col = KEYS.get(k, "?")
    if col is None or not v:
        return
    if col == "?":
        info[k] = v
    else:
        perks[col] = " / ".join(options(v))


def label_of(s: str) -> str:
    s = re.sub(r"(?i)^roll\s*#?\d+\s*:?\s*", "", s).strip()
    return s or "God roll"


def parse_tables(page: str) -> list[dict]:
    rolls = []
    for tbl in re.findall(r"<table[^>]*>(.*?)</table>", page, re.S):
        rows = [cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", tbl, re.S)]
        if not rows or not rows[0]:
            continue
        head = rows[0]
        m = MODE_RE.match(head[0][0])
        if not m:
            continue
        mode = m.group(1).lower()
        if len(head) > 1:        # "PvE Rolls | Roll #1: All-Around | Roll #2: DPS"
            labels = [label_of(h) for h, _ in head[1:]]
        else:                    # "PvE Roll: All-Around" or "PvE God Roll"
            labels = [label_of(m.group(2))]
        found = [({}, {}) for _ in labels]
        for row in rows[1:]:
            if len(row) < 2:
                continue
            key = row[0][0]
            vals = []
            for v, span in row[1:]:
                vals += [v] * span
            if len(vals) == 1:
                vals *= len(labels)
            for (perks, info), v in zip(found, vals):
                add_perk(perks, info, key, v)
        rolls += [{"mode": mode, "label": lab, "perks": p, "info": i}
                  for lab, (p, i) in zip(labels, found) if p]
    return rolls


def parse_lists(page: str) -> list[dict]:
    """Older pages: <h3>X PvE god roll</h3><ul><li><strong>Barrel</strong>: ...</li></ul>."""
    rolls = []
    for hm in re.finditer(r"<h[2-4][^>]*>(.*?)</h[2-4]>", page, re.S):
        h = text(hm.group(1))
        m = re.search(r"(?i)\b(PvE|PvP)\s+god\s+rolls?\b", h)
        if not m:
            continue
        nxt = re.search(r"<h[1-4][^>]*>", page[hm.end():])
        body = page[hm.end(): hm.end() + nxt.start()] if nxt else page[hm.end():]
        for ul in re.findall(r"<ul[^>]*>(.*?)</ul>", body, re.S)[:3]:
            perks, info = {}, {}
            for li in re.findall(r"<li[^>]*>(.*?)</li>", ul, re.S):
                km = re.match(r"\s*<strong>(.*?)</strong>\s*:?\s*(.*)", li, re.S)
                if km:
                    add_perk(perks, info, text(km.group(1)), text(km.group(2)).lstrip(": "))
            if perks:
                rolls.append({"mode": m.group(1).lower(), "label": "God roll", "perks": perks, "info": info})
    return rolls


def parse(page: str) -> list[dict]:
    return parse_tables(page) or parse_lists(page)


def resolve_name(res: Resolver, name: str) -> str | None:
    """Exact manifest name, else the closest one ('Sovereignity', 'Suspectum 4fr', 'M-17 Fast Talker')."""
    for cand in (name, re.sub(r"\s*\(.*?\)\s*$", "", name)):
        if res.versions(cand):
            return cand
        key = norm(cand)
        loose = {re.sub(r"[^a-z0-9]", "", k): k for k in res.by_name}
        hit = loose.get(re.sub(r"[^a-z0-9]", "", key))
        hit = [hit] if hit else difflib.get_close_matches(key, list(res.by_name), n=1, cutoff=0.85)
        if hit and res.versions(hit[0]):
            return res.d["weapons"][res.by_name[hit[0]][0]]["n"]
    return None


SITEMAPS = ["https://www.blueberries.gg/post-sitemap.xml", "https://www.blueberries.gg/page-sitemap.xml"]
UA = "Mozilla/5.0 (compatible; d2-wishlist-importer)"
DELAY = 10  # seconds, robots.txt Crawl-delay


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    from manifest import _SSL  # relaxed X509 strict flag (some Windows CA chains fail without it)
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
        return r.read()


def fetch() -> None:
    """Refresh the sitemap index and download new or updated god-roll pages, politely."""
    (CACHE / "pages").mkdir(parents=True, exist_ok=True)
    index = {}
    for i, sm in enumerate(SITEMAPS):
        if i:
            time.sleep(DELAY)
        x = _download(sm).decode("utf-8", "replace")
        for loc, mod in re.findall(r"<loc>([^<]*god-roll[^<]*)</loc>\s*<lastmod>([^<]*)</lastmod>", x):
            index[loc] = mod
    (CACHE / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    got_f = CACHE / "fetched.json"
    got = json.loads(got_f.read_text(encoding="utf-8")) if got_f.exists() else {}
    for url, mod in index.items():   # pages downloaded before fetched.json existed count as current
        if url not in got and (CACHE / "pages" / f"{url.rstrip('/').split('/')[-1]}.html").exists():
            got[url] = mod
    todo = [u for u, mod in index.items() if got.get(u) != mod]
    mins = len(todo) * DELAY / 60
    print(f"{len(index)} god-roll pages, {len(todo)} new or updated" + (f" (~{mins:.0f} min at {DELAY} s each)" if todo else ""), flush=True)
    for i, url in enumerate(todo, 1):
        time.sleep(DELAY)
        try:
            (CACHE / "pages" / f"{url.rstrip('/').split('/')[-1]}.html").write_bytes(_download(url))
            got[url] = index[url]
            got_f.write_text(json.dumps(got, indent=1), encoding="utf-8")
        except Exception as e:  # keep going; it is retried next run
            print(f"  failed {url}: {e}", flush=True)
        if i % 25 == 0 or i == len(todo):
            print(f"  {i}/{len(todo)}", flush=True)


def write_wishlist(res: Resolver, rolls: list[dict], path: Path, mode: str) -> dict:
    """Standalone DIM wish list from imported rolls (same line format as wishlists/spinz.txt)."""
    import datetime as dt
    from build_wishlist import build  # lazy: build_wishlist imports this module too
    picked = [r for r in rolls if mode == "all" or r["mode"] == mode]
    body, stats = build(res, {"rolls": [validate_roll(res, r) for r in picked]})
    what = {"all": "PvE + PvP", "pve": "PvE", "pvp": "PvP"}[mode]
    version = res.d.get("version", "")
    head = [f"title:blueberries.gg god rolls ({what})",
            f"description:Every {what} god roll from blueberries.gg, checked against the Bungie manifest "
            f"{str(version).split('|')[0]} on {dt.date.today().isoformat()}. {stats['weapons']} weapons, {stats['lines']} rolls. "
            "Notes show each page's last-updated date.",
            "", "// GENERATED by tools/import_blueberries.py. Source: https://www.blueberries.gg",
            "// '[full roll]' = barrel+mag+traits match, '[traits only]' = both traits match.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(head + body), encoding="utf-8", newline="\n")
    return stats


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Import blueberries.gg god rolls into a DIM wish list.")
    ap.add_argument("--fetch", action="store_true", help="download new/updated pages first (10 s between requests)")
    ap.add_argument("--wishlist", type=Path, help="also write a standalone DIM wish list to this path")
    ap.add_argument("--mode", choices=["all", "pve", "pvp"], default="all", help="which rolls go in --wishlist")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    if not (CACHE / "index.json").exists():
        print("No pages downloaded yet: run with --fetch first.")
        return 1
    index = json.loads((CACHE / "index.json").read_text(encoding="utf-8"))
    res = Resolver()
    out, dropped, skipped = [], [], []
    for url, lastmod in sorted(index.items()):
        slug = url.rstrip("/").split("/")[-1]
        f = CACHE / "pages" / f"{slug}.html"
        if not f.exists():
            skipped.append((url, "not fetched"))
            continue
        page = f.read_text(encoding="utf-8", errors="replace")
        raw = weapon_name(page)
        parsed = parse(page)
        if not raw or not parsed:
            skipped.append((url, "no roll tables"))
            continue
        name = resolve_name(res, raw) or resolve_name(res, re.sub(r"-god-rolls?.*$", "", slug).replace("-", " "))
        if not name:
            skipped.append((url, f"'{raw}' not in the manifest"))
            continue
        date = (lastmod or page_date(page))[:10]
        seen = set()
        for i, r in enumerate(parsed):
            roll = {"id": f"blueberries:{slug}:{r['mode']}:{i}", "weapon": name, "mode": r["mode"],
                    "file": "data/imports/blueberries.json",
                    "context": f"blueberries.gg {r['mode'].upper()}: {r['label']} (page updated {date})",
                    "perks": respell(res, name, r["perks"]), "info": {"source": url, **r["info"]}}
            rr = validate_roll(res, roll)
            if rr.status == "SKIP":
                skipped.append((url, f"{r['mode']} '{r['label']}': {rr.warnings[0] if rr.warnings else 'skip'}"))
                continue
            clean = {}
            for col, v in roll["perks"].items():
                good = rr.valid.get(col)
                if good is None:
                    continue
                if good:
                    clean[col] = " / ".join(dict.fromkeys(good))
            for e in rr.errors:
                dropped.append((name, r["mode"], r["label"], e))
            if not (clean.get("trait1") or clean.get("trait2")):
                skipped.append((url, f"{r['mode']} '{r['label']}': no valid trait perks"))
                continue
            key = (r["mode"], json.dumps(clean, sort_keys=True))
            if key in seen:
                continue
            seen.add(key)
            roll["perks"] = clean
            out.append(roll)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    weapons = len({r["weapon"] for r in out})
    L = ["# blueberries.gg import", "",
         f"{len(out)} rolls on {weapons} weapons from {len(index)} god-roll pages.", "",
         "Perks that aren't legal on the weapon's current version were dropped (listed below). "
         "A roll is kept only if at least one trait column survives.", "",
         "## Dropped options", ""]
    L += [f"- **{n}** ({m.upper()} {lab}): {e}" for n, m, lab, e in dropped] or ["- none"]
    L += ["", "## Skipped pages / rolls", ""]
    L += [f"- {u}: {why}" for u, why in skipped] or ["- none"]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{len(out)} rolls on {weapons} weapons; {len(dropped)} options dropped; {len(skipped)} skipped -> {OUT.relative_to(ROOT)}")
    if a.wishlist:
        st = write_wishlist(res, out, a.wishlist, a.mode)
        print(f"Wish list ({a.mode}): {st['weapons']} weapons, {st['lines']} lines -> {a.wishlist}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
