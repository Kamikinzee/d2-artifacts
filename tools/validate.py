"""Validate god rolls and loadouts against the live manifest + Spinz's hard rules.

    python tools/validate.py                      # validate everything in data/rolls.json
    python tools/validate.py --weapon "Igneous Hammer" \
        --roll "Smallbore; Ricochet Rounds; Rapid Hit / Slideshot; Opening Shot; Range; Roar of Battle; Counterbalance Stock"
        # one roll, slots in codex order: Barrel; Mag; Trait 1; Trait 2; Masterwork; Origin; Mod

Exit code 1 if anything FAILs.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve import COL_LABEL, COLS, Resolver, Version, norm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ROLLS = ROOT / "data" / "rolls.json"
CODEX_ORDER = ["col1", "col2", "trait1", "trait2", "masterwork", "origin", "mod"]
PLACEHOLDER_RE = re.compile(r"^(any\b|legendary\b|your\b)", re.I)
SPEC_RE = re.compile(r"\b(major|minor|boss|taken)\s+spec\b", re.I)
ELEMENTS = ["Kinetic", "Arc", "Solar", "Void", "Stasis", "Strand"]
KINETIC_SLOT = {"Kinetic", "Stasis", "Strand"}


@dataclass
class RollResult:
    roll: dict
    status: str = "PASS"                 # PASS | PARTIAL | FAIL | SKIP
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # col -> list of option names that are valid on at least one current version
    valid: dict = field(default_factory=dict)
    versions: list = field(default_factory=list)   # all Version objects for the weapon
    fixes: list = field(default_factory=list)      # (wrong, right) name corrections

    def err(self, msg: str, fatal: bool = False):
        self.errors.append(msg)
        if fatal:
            self.status = "FAIL"
        elif self.status == "PASS":
            self.status = "PARTIAL"


def split_opts(v: str) -> list[str]:
    """'A / B (note)' -> ['A', 'B']. Parenthetical notes on the sheets are descriptions, not names."""
    out = []
    for o in str(v).split(" / "):
        o = re.sub(r"\s*\([^)]*\)\s*$", "", o).strip()
        if o:
            out.append(o)
    return out


def _only_empty(plugs) -> bool:
    return all(p.name.startswith("Empty ") for p in plugs)


def is_placeholder(name: str) -> bool:
    return bool(PLACEHOLDER_RE.match(name.strip()))


def check_option(res: Resolver, cur: list[Version], col: str, opt: str, rr: RollResult) -> bool:
    """Is `opt` legal in `col` on at least one current version? Records errors/fixes."""
    if col == "origin" and all(_only_empty(v.cols.get("origin", [])) for v in cur):
        rr.warnings.append(f"Origin: '{opt}' not checked — current version has no fixed origin list (craftable copy)")
        return True
    if col in COLS:
        hits = [v for v in cur if res.find_in(v.cols.get(col, []), opt)]
        rollable = [v for v in hits if any(p.can_roll for p in res.find_in(v.cols.get(col, []), opt))]
        if rollable:
            return True
        if hits:
            rr.err(f"{COL_LABEL[col]}: '{opt}' is in the pool but can no longer roll on the current version")
            return False
        # Wrong column?
        for other in COLS:
            if other != col and any(res.find_in(v.cols.get(other, []), opt) for v in cur):
                rr.err(f"{COL_LABEL[col]}: '{opt}' is not in this column; it rolls in {COL_LABEL[other]}")
                return False
        # Abbreviation (e.g. "Corkscrew" -> "Corkscrew Rifling")?
        pre = {p.name for v in cur for p in res.prefix_in(v.cols.get(col, []), opt) if not p.enhanced}
        if len(pre) == 1:
            full = pre.pop()
            rr.fixes.append((opt, full))
            rr.warnings.append(f"{COL_LABEL[col]}: '{opt}' resolved as '{full}' (abbreviated name)")
            return True
        old = [v for v in rr.versions if not v.current and res.find_in(v.cols.get(col, []), opt)]
        where = f" (only on old version {old[0].hash})" if old else ""
        pool = ", ".join(dict.fromkeys(p.name for v in cur for p in v.cols.get(col, [])
                                       if not p.enhanced and p.can_roll and not p.name.startswith("Empty ")))
        rr.err(f"{COL_LABEL[col]}: '{opt}' isn't in the current pool{where}. Current pool: {pool or '(fixed/none)'}")
        return False
    if col == "masterwork":
        k = norm(opt)
        if any(k in v.masterworks for v in cur):
            return True
        if not any(v.masterworks for v in cur):
            return True  # exotics etc. with no stat masterwork socket: nothing to check
        pre = sorted({m for v in cur for m in v.masterworks if m.startswith(k)})
        if len(pre) == 1:
            rr.fixes.append((opt, pre[0].title()))
            rr.warnings.append(f"Masterwork: '{opt}' resolved as '{pre[0].title()}'")
            return True
        rr.err(f"Masterwork: '{opt}' isn't a masterwork for this weapon. Options: "
               f"{', '.join(sorted({m.title() for v in cur for m in v.masterworks}))}")
        return False
    if col == "mod":
        if SPEC_RE.search(opt):
            rr.err(f"Mod: '{opt}' — Spec mods no longer exist on weapons", fatal=False)
            return False
        hits = res.mod(opt)
        if not hits:
            rr.err(f"Mod: '{opt}' isn't a weapon mod in the manifest")
            return False
        if not any(pci in v.mod_cats for v in cur for _, pci in hits):
            legal = sorted({m for v in cur for m in v.mods})
            rr.err(f"Mod: '{opt}' can't go in this weapon's mod socket. Legal: {', '.join(legal)}")
            return False
        return True
    return True


def validate_roll(res: Resolver, roll: dict) -> RollResult:
    rr = RollResult(roll)
    name = roll["weapon"]
    if is_placeholder(name):
        rr.status = "SKIP"
        rr.warnings.append(f"'{name}' is a placeholder, not a specific weapon")
        return rr
    rr.versions = res.versions(name)
    if not rr.versions:
        rr.err(f"No weapon named '{name}' in the manifest", fatal=True)
        return rr
    cur = [v for v in rr.versions if v.current and not v.variant] or [v for v in rr.versions if v.current]
    if len(cur) > 1:
        rr.warnings.append(f"{len(cur)} current versions ({', '.join(str(v.hash) for v in cur)}); "
                           "each perk only needs to exist on one of them")
    perks = roll.get("perks", {})
    if not any(perks.get(c) for c in ("col1", "col2", "trait1", "trait2")):
        rr.status = "SKIP"
        rr.warnings.append("No perk columns listed (exotic/fixed roll) — nothing to wishlist")
        return rr
    for col in CODEX_ORDER:
        if not perks.get(col):
            continue
        good = []
        for opt in split_opts(perks[col]):
            if check_option(res, cur, col, opt, rr):
                fixed = dict(rr.fixes).get(opt, opt)
                good.append(fixed)
        rr.valid[col] = good
        if not good and col in ("col1", "col2", "trait1", "trait2"):
            rr.status = "FAIL"
    return rr


# ---------------- loadouts ----------------

@dataclass
class LoadoutResult:
    lo: dict
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def status(self) -> str:
        return "FAIL" if self.errors else "PASS"


def element_in(text: str) -> str | None:
    text = re.sub(r"\b(Kinetic|Energy)[- ]slot\b", "", text, flags=re.I)
    for e in ELEMENTS:
        if re.search(rf"\b{e}\b", text):
            return e
    return None


def validate_loadout(res: Resolver, lo: dict) -> LoadoutResult:
    lr = LoadoutResult(lo)
    slots: dict[str, list[str]] = {}
    exotic_weapons = []
    for w in lo["weapons"]:
        names = [n.strip() for n in w["name"].split(" / ")]
        label = w.get("slot", "")
        typ = w.get("type", "")
        if is_placeholder(names[0]):
            e = element_in(names[0]) or element_in(typ)
            if e:
                slots.setdefault("Kinetic" if e in KINETIC_SLOT else "Energy", []).append(names[0])
            elif "heavy" in names[0].lower() or label.lower().startswith("heavy"):
                slots.setdefault("Power", []).append(names[0])
            continue
        v0 = None
        for n in names:
            vs = [v for v in res.versions(n) if v.current and not v.variant] or res.versions(n)
            if not vs:
                lr.errors.append(f"{n}: not found in the manifest")
                continue
            v = vs[0]
            v0 = v0 or v
            if v.tier == "Exotic" and n == names[0]:
                exotic_weapons.append(n)
            # sheet label vs manifest
            for want, have, what in (("Kinetic", v.slot, "slot"), ("Energy", v.slot, "slot"),
                                     ("Primary", v.ammo, "ammo"), ("Special", v.ammo, "ammo"), ("Heavy", v.ammo, "ammo")):
                if re.search(rf"\b{want}\b", label) and have != want and not (want == "Heavy" and have == "Heavy"):
                    lr.errors.append(f"{n}: sheet says '{label}' but it's {v.slot} slot / {v.ammo} ammo")
                    break
            te = element_in(typ)
            if te and te != v.element and n == names[0]:
                lr.errors.append(f"{n}: sheet type says {te} but the manifest says {v.element}")
            for opt in split_opts(w.get("perks", {}).get("mod", "")):
                if SPEC_RE.search(opt):
                    lr.errors.append(f"{n}: '{opt}' — Spec mods no longer exist on weapons")
        if v0:
            slots.setdefault(v0.slot, []).append(names[0])
    for slot, ws in slots.items():
        if len(ws) > 1:
            lr.errors.append(f"Two weapons in the {slot} slot: {', '.join(ws)}")
    if len(exotic_weapons) > 1:
        lr.errors.append(f"More than one exotic weapon: {', '.join(exotic_weapons)}")
    # exotic armor
    ex = lo.get("exotic_armor", "")
    found = []
    for aname, pieces in res.d["armor"].items():
        if aname and re.search(rf"(^|\W){re.escape(aname)}(\W|$)", ex, re.I):
            found.append((aname, pieces))
    found = [f for f in found if not any(f[0] != g[0] and f[0].lower() in g[0].lower() for g in found)]
    # Exotic class items carry perks named after other exotics (e.g. "Stoicism (Inmost Light + Synthoceps)")
    ci = [f for f in found if any(p["slot"] == "Class item" for p in f[1])]
    if ci:
        found = ci[:1]
    if not ex:
        lr.warnings.append("No exotic armor listed")
    elif not found:
        lr.errors.append(f"Exotic armor '{ex}' isn't an exotic armor piece in the manifest")
    elif len(found) > 1:
        lr.errors.append(f"More than one exotic armor piece named: {', '.join(f[0] for f in found)}")
    else:
        aname, pieces = found[0]
        if any(p["slot"] == "Class item" for p in pieces) and not lo.get("element", "").startswith("Prismatic"):
            lr.errors.append(f"{aname} is an exotic class item; its perks only work on Prismatic (build is {lo.get('element')})")
    # Trials/Competitive: artifact perks are off
    if lo["sheet"].startswith("Trials") and lo.get("has_artifact"):
        lr.errors.append("Trials build lists artifact perks, but artifact perks are off in Trials")
    for slot_mods in lo.get("mods", []):
        text = " ".join(map(str, slot_mods))
        if SPEC_RE.search(text):
            lr.errors.append(f"Armor mods mention a Spec mod: {text}")
    return lr


def codex_pool_check(res: Resolver, card: dict) -> list[str]:
    """Compare the codex's claimed col3/col4/origin pools and element against the manifest."""
    out = []
    cur = res.current(card["weapon"])
    if not cur:
        return out
    v = cur[0]
    te = element_in(card.get("type", ""))
    if te and te != v.element:
        out.append(f"type line says {te}, manifest says {v.element}")
    for key, col in (("p1", "trait1"), ("p2", "trait2"), ("o", "origin")):
        claimed = {norm(re.sub(r"\s*\([^)]*\)", "", x)) for x in re.split(r",\s*", card.get(key, "")) if x.strip()}
        if not claimed:
            continue
        actual_names = {p.base: p.name for vv in cur for p in vv.cols.get(col, [])
                        if p.can_roll and not p.enhanced and not p.name.startswith("Empty ")}
        if not actual_names:
            continue
        missing = sorted(claimed - set(actual_names))
        extra = sorted(set(actual_names) - claimed)
        if missing or extra:
            out.append(f"{COL_LABEL[col]} pool: codex lists {sorted(claimed)} but manifest has "
                       f"{sorted(actual_names.values())}")
    return out


def run_all(res: Resolver, doc: dict) -> dict:
    rolls = [validate_roll(res, r) for r in doc["rolls"]]
    loadouts = [validate_loadout(res, lo) for lo in doc["loadouts"]]
    pools = {}
    for c in doc.get("codex_cards", []):
        p = codex_pool_check(res, c)
        if p:
            pools[f"{c['weapon']} ({c['section']})"] = p
    return {"rolls": rolls, "loadouts": loadouts, "pools": pools}


def report(results: dict) -> str:
    L = []
    rolls, los = results["rolls"], results["loadouts"]
    cnt = {s: sum(r.status == s for r in rolls) for s in ("PASS", "PARTIAL", "FAIL", "SKIP")}
    L.append(f"# Validation report\n\nRolls: {cnt['PASS']} PASS, {cnt['PARTIAL']} PARTIAL, {cnt['FAIL']} FAIL, {cnt['SKIP']} SKIP  ")
    L.append(f"Loadouts: {sum(l.status == 'PASS' for l in los)} PASS, {sum(l.status == 'FAIL' for l in los)} FAIL\n")
    L.append("PARTIAL = some ' / ' alternates are invalid; only the valid combinations are wishlisted.\n")
    for st in ("FAIL", "PARTIAL"):
        sel = [r for r in rolls if r.status == st]
        if sel:
            L.append(f"## Rolls: {st}\n")
            for r in sel:
                L.append(f"- **{r.roll['weapon']}** [{r.roll['mode'].upper()}] — {r.roll['context']} (`{r.roll['file']}`)")
                for e in r.errors:
                    L.append(f"  - {e}")
            L.append("")
    fixes = sorted({(r.roll["weapon"], a, b, r.roll["file"]) for r in rolls for a, b in r.fixes})
    if fixes:
        L.append("## Abbreviated perk names (accepted, but should be spelled out)\n")
        L += [f"- {w}: '{a}' → '{b}' (`{f}`)" for w, a, b, f in fixes]
        L.append("")
    bad = [l for l in los if l.status == "FAIL"]
    if bad:
        L.append("## Loadouts: FAIL\n")
        for l in bad:
            L.append(f"- **{l.lo['sheet']} — {l.lo['build']}**{' (alt)' if l.lo['variant'] == 'alt' else ''} (`{l.lo['file']}`)")
            L += [f"  - {e}" for e in l.errors]
        L.append("")
    if results["pools"]:
        L.append("## Codex pool / element mismatches vs manifest\n")
        for k, v in results["pools"].items():
            L.append(f"- **{k}**")
            L += [f"  - {x}" for x in v]
        L.append("")
    multi = sorted({(r.roll["weapon"], w) for r in rolls for w in r.warnings if "current versions" in w})
    if multi:
        L.append("## Weapons with more than one current version\n")
        L += [f"- {w}: {msg}" for w, msg in multi]
        L.append("")
    skips = sorted({(r.roll["weapon"], r.warnings[0]) for r in rolls if r.status == "SKIP"})
    if skips:
        L.append("## Skipped (not wishlistable)\n")
        L += [f"- {w}: {m}" for w, m in skips]
    return "\n".join(L) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--weapon")
    ap.add_argument("--roll", help="'Barrel; Mag; Trait 1; Trait 2; Masterwork; Origin; Mod' (blank to skip a slot)")
    ap.add_argument("--report", help="write the markdown report here")
    a = ap.parse_args()
    res = Resolver()
    if a.weapon:
        vals = [x.strip() for x in (a.roll or "").split(";")]
        roll = {"weapon": a.weapon, "mode": "test", "context": "cli", "file": "-",
                "perks": {c: v for c, v in zip(CODEX_ORDER, vals) if v}}
        r = validate_roll(res, roll)
        print(f"{r.status}: {a.weapon}")
        for e in r.errors:
            print(f"  FAIL  {e}")
        for w in r.warnings:
            print(f"  note  {w}")
        sys.exit(1 if r.status == "FAIL" else 0)
    doc = json.loads(ROLLS.read_text(encoding="utf-8"))
    results = run_all(res, doc)
    text = report(results)
    if a.report:
        Path(a.report).write_text(text, encoding="utf-8")
    print(text)
    bad = any(r.status == "FAIL" for r in results["rolls"]) or any(l.status == "FAIL" for l in results["loadouts"])
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
