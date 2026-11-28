"""Extract every god roll and loadout from src/*.html into data/rolls.json.

data/rolls.json is generated: the HTML sheets are the source of truth (they are what
gets published). For a roll that doesn't live on any sheet, add it to
data/manual_rolls.json (same shape as a "rolls" entry); it is merged in here.

    python tools/extract_rolls.py
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
OUT = ROOT / "data" / "rolls.json"
MANUAL = ROOT / "data" / "manual_rolls.json"

CODEX_SECTIONS = {"TRIALS": "Trials", "IRONBANNER": "Iron Banner", "RENEGADES": "Renegades", "DUNGEONS": "Dungeons"}
CODEX_COLS = ["col1", "col2", "trait1", "trait2", "masterwork", "origin", "mod"]  # TSLOTS order
SHEETS = {
    "titan-loadout.html": "Titan",
    "titan-nocannon-loadout.html": "Titan No-Cannon",
    "warlock-loadout.html": "Warlock",
    "hunter-loadout.html": "Hunter",
    "trials-loadout.html": "Trials",
    "hunter-starter.html": "Hunter Starter",
}
ROLL_KEYS = {
    "barrel": "col1", "blade": "col1", "bowstring": "col1", "launcher": "col1", "launcher barrel": "col1",
    "haft": "col1", "scope": "col1", "sight": "col1",
    "mag": "col2", "magazine": "col2", "guard": "col2", "arrow": "col2", "arrows": "col2", "battery": "col2",
    "trait 1": "trait1", "trait 2": "trait2",
    "mw": "masterwork", "masterwork": "masterwork",
    "origin": "origin", "mod": "mod",
}


def page_data(path: Path) -> dict:
    out = subprocess.run(["node", str(ROOT / "tools" / "extract_page_data.js"), str(path)],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def roll_from_pairs(pairs: list) -> tuple[dict, dict]:
    perks, info = {}, {}
    for k, v in pairs:
        col = ROLL_KEYS.get(k.strip().lower())
        (perks if col else info)[col or k] = v
    return perks, info


def extract_codex(rolls: list, cards: list) -> None:
    data = page_data(SRC / "godroll-codex.html")
    for key, section in CODEX_SECTIONS.items():
        for c in data.get(key, []):
            cards.append({"weapon": c["n"], "section": section, "src": c["src"], "type": c["t"],
                          "p1": c.get("p1", ""), "p2": c.get("p2", ""), "o": c.get("o", "")})
            for mode in ("pvp", "pve"):
                vals = c.get(mode) or []
                rolls.append({
                    "id": f"codex:{slug(section)}:{slug(c['n'])}:{mode}",
                    "weapon": c["n"], "mode": mode, "file": "src/godroll-codex.html",
                    "context": f"Codex {section}: {c['src']} ({c.get('tier', '')})".replace(" ()", ""),
                    "perks": {col: v for col, v in zip(CODEX_COLS, vals) if v},
                    "info": {},
                })


def resolve_weapons(ws: list, shared: dict | None) -> list:
    return [shared[w] if isinstance(w, str) else w for w in ws]


def extract_sheets(rolls: list, loadouts: list) -> None:
    for fname, sheet in SHEETS.items():
        data = page_data(SRC / fname)
        shared = data.get("SHARED_PVP_WEAPONS")
        if sheet == "Trials":
            groups = [(c["key"], "pvp", f"Trials ({c['label']})") for c in data.get("CLASSES", [])]
        else:
            groups = [(m, m, sheet) for m in ("pvp", "pve")]
        for bkey, mode, label in groups:
            for b in data["BUILDS"].get(bkey, []):
                sets = [("main", resolve_weapons(b.get("weapons", []), shared))]
                if b.get("alt"):
                    sets.append(("alt", resolve_weapons(b["alt"].get("weapons", []), shared)))
                for kind, ws in sets:
                    lo = {
                        "id": f"{slug(sheet)}:{b['id']}" + (":alt" if kind == "alt" else ""),
                        "file": f"src/{fname}", "sheet": label, "mode": mode, "build": b["name"],
                        "variant": kind, "element": b.get("el", ""), "exotic_armor": b.get("exotic", ""),
                        "has_artifact": bool(b.get("artifact")), "mods": b.get("mods", []), "weapons": [],
                    }
                    for w in ws:
                        perks, info = roll_from_pairs(w.get("roll", []))
                        lo["weapons"].append({"slot": w.get("slot", ""), "name": w["name"], "type": w.get("type", ""),
                                              "perks": perks, "info": info})
                        ctx = f"{label} sheet: {b['name']}" + (" (no-Praxic alt)" if kind == "alt" else "")
                        for wname in [n.strip() for n in w["name"].split(" / ")]:
                            rolls.append({"id": f"{lo['id']}:{slug(wname)}", "weapon": wname, "mode": mode,
                                          "file": lo["file"], "context": ctx, "perks": perks, "info": info})
                        # Alternate guns for the same slot, each with its own roll
                        for a in w.get("alts", []):
                            ap, ai = roll_from_pairs(a.get("roll", []))
                            lo["weapons"][-1].setdefault("alts", []).append({"name": a["name"], "perks": ap})
                            rolls.append({"id": f"{lo['id']}:{slug(a['name'])}", "weapon": a["name"], "mode": mode,
                                          "file": lo["file"], "context": ctx, "perks": ap, "info": ai})
                    loadouts.append(lo)


def main() -> dict:
    rolls, loadouts, cards = [], [], []
    extract_codex(rolls, cards)
    extract_sheets(rolls, loadouts)
    for extra in [MANUAL, *sorted((ROOT / "data" / "imports").glob("*.json"))]:
        if not extra.exists():
            continue
        for r in json.loads(extra.read_text(encoding="utf-8")):
            r.setdefault("file", extra.relative_to(ROOT).as_posix())
            r.setdefault("info", {})
            rolls.append(r)
    doc = {"_note": "GENERATED by tools/extract_rolls.py from src/*.html (+ data/manual_rolls.json). Don't hand-edit.",
           "rolls": rolls, "loadouts": loadouts, "codex_cards": cards}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


if __name__ == "__main__":
    d = main()
    print(f"{len(d['rolls'])} rolls, {len(d['loadouts'])} loadouts, {len(d['codex_cards'])} codex cards -> {OUT.relative_to(ROOT)}")
