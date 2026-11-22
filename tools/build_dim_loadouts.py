"""Turn every loadout sheet build into a DIM loadout (import link + JSON).

    python tools/build_dim_loadouts.py                      # writes data/dim_loadouts.json + reports/dim_loadouts.md
    python tools/build_dim_loadouts.py --inventory destinyWeapons.csv destinyArmor.csv
        # match your real copies (DIM Settings > Spreadsheets exports) -> .cache/dim_loadouts_mine.md
    python tools/build_dim_loadouts.py --check              # resolve only; exit 1 on any unresolved name

A DIM loadout link is https://app.destinyitemmanager.com/loadouts?loadout=<json>. Opening it in DIM
opens the loadout drawer; Save puts it in your DIM profile (synced). Subclasses and mods resolve by
hash. Weapons and armor resolve by *instance id*, so without --inventory they show as "missing"
placeholders you swap for your own copy in the drawer.

Armor mods: weapon-type mods (Hand Cannon Targeting, Auto Rifle Loader...) no longer exist. The
current pool is element-based (Kinetic Targeting, Void Loader...), so a weapon-type mod is mapped to
the element of the build's gun of that type.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest  # noqa: E402
from extract_rolls import SHEETS, SRC, page_data, resolve_weapons, roll_from_pairs, slug  # noqa: E402
from resolve import Resolver, norm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "data" / "dim_loadouts.json"
OUT_MD = ROOT / "reports" / "dim_loadouts.md"
DIM = "https://app.destinyitemmanager.com/loadouts?loadout="

CLASS = {"titan": 0, "hunter": 1, "warlock": 2}
DMG = {"arc": 2, "solar": 3, "void": 4, "stasis": 6, "strand": 7}
STATS = {"weapons": 2996146975, "health": 392767087, "class": 1943323491,
         "grenade": 1735777505, "super": 144602215, "melee": 4244567218}
ELEMENTS = ["Kinetic", "Arc", "Solar", "Void", "Stasis", "Strand"]
# Weapon-type armor mod -> element mod. Kinetic-slot guns that are Stasis/Strand use that element.
TYPE_MOD = re.compile(r"^(?:(Unflinching) )?(.+?) (Targeting|Loader|Dexterity|Scavenger|Holster|Aim)$", re.I)
TYPE_ALIASES = {"smg": "submachine gun", "sniper": "sniper rifle", "sword": "sword", "glaive": "glaive",
                "linear fusion": "linear fusion rifle", "bow": "combat bow", "gl": "grenade launcher"}
RENAMES = {"weapon font": "Weapons Font", "harmonic reloader": "Harmonic Loader"}
FIXED = {"Heavy Reserves": None, "Ammo Finder / Scout": "Heavy Ammo Finder"}   # gone from the game; my picks
STAT_MOD = {"health": "Health Mod", "class": "Class Mod", "grenade": "Grenade Mod", "melee": "Melee Mod",
            "super": "Super Mod", "weapon": "Weapons Mod", "weapons": "Weapons Mod"}
PLACEHOLDER = re.compile(r"^(any |legendary )", re.I)   # "Any Void legendary primary": no specific gun
SKIP_HINTS = ("resistance mods", "resistance for", "plus resistance")


class Gen:
    def __init__(self, inventory: list[Path]):
        self.r = Resolver()
        self.d = self.r.d
        self.P = self.d["plugs"]
        self.armor_mods: dict[str, int] = {}
        for h, p in self.P.items():
            if p["pci"].startswith("enhancements.v2") and p["t"].endswith("Armor Mod") \
                    and not p["n"].startswith(("Empty", "Locked")):
                self.armor_mods.setdefault(norm(p["n"]), int(h))
        self.inv = self._load_inventory(inventory)
        self.problems: list[str] = []

    # ---------- inventory (DIM CSV exports) ----------
    @staticmethod
    def _load_inventory(paths: list[Path]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for p in paths:
            with open(p, newline="", encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    h, iid = row.get("Hash"), (row.get("Id") or "").strip('"')
                    if h and iid:
                        perks = " | ".join(v for k, v in row.items() if k and k.startswith("Perks") and v)
                        out.setdefault(int(h), []).append({"id": iid, "perks": perks.lower(),
                                                           "power": int(row.get("Power") or 0)})
        return out

    def pick_copy(self, hashes: list[int], wanted: list[str]) -> tuple[str, int, str]:
        """Best owned copy across all versions: most wanted perks, then power. ("0", hash, "") if none."""
        best = None
        for h in hashes:
            for c in self.inv.get(h, []):
                score = sum(1 for w in wanted if w and w.lower() in c["perks"])
                key = (score, c["power"])
                if best is None or key > best[0]:
                    best = (key, c["id"], h)
        if best:
            return best[1], best[2], f"{best[0][0]}/{len([w for w in wanted if w])} perks"
        return "0", hashes[0], ""

    # ---------- subclass ----------
    def subclass(self, cls: str, el: str) -> tuple[int, dict]:
        el0 = el.split()[0].lower()
        for h, s in self.d["subclasses"].items():
            if s["class"] != CLASS[cls]:
                continue
            if (el0 == "prismatic" and s["n"].startswith("Prismatic")) or s["dmg"] == DMG.get(el0):
                return int(h), s
        raise KeyError(f"no {cls} {el} subclass")

    def socket_plugs(self, s: dict) -> list[tuple[int, str]]:
        hs = [h for h, _ in self.d["plugsets"].get(str(s.get("reuse")), [])] + s["items"] + ([s["init"]] if s["init"] else [])
        out = []
        for h in dict.fromkeys(hs):
            p = self.P.get(str(h))
            if p and not p["n"].startswith("Empty"):
                out.append((int(h), p["n"]))
        return out

    @staticmethod
    def first_mentioned(text: str, plugs: list[tuple[int, str]]) -> tuple[int, str] | None:
        """The plug whose name appears earliest in free text ("Thundercrash (one-shot) or Fists...")."""
        t = norm(text)
        hits = []
        for h, n in plugs:
            for alias in dict.fromkeys([norm(n), norm(re.sub(r" Grenade$", "", n)), norm(n.split(":")[0])]):
                if alias and re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", t):
                    hits.append((t.find(alias), -len(alias), h, n))
        return (min(hits)[2], min(hits)[3]) if hits else None

    @staticmethod
    def mention_pos(text: str, n: str) -> int:
        t = norm(text)
        ps = [t.find(a) for a in (norm(n), norm(re.sub(r" Grenade$", "", n)), norm(n.split(":")[0])) if a in t]
        return min(ps) if ps else 10**6

    def subclass_overrides(self, b: dict, cls: str, ctx: str) -> tuple[int, dict]:
        sh, s = self.subclass(cls, b.get("el", ""))
        cats = {c["socketCategoryHash"]: c["socketIndexes"] for c in s["cats"]}
        idx_plugs = {i: self.socket_plugs(sock) for i, sock in enumerate(s["sockets"])}
        ov: dict[str, int] = {}
        # Abilities + super: every kit line is matched against every ability-ish socket
        ability_idx = [i for c, ix in cats.items() for i in ix if len(ix) <= 5 and c not in ()]
        aspect_idx = fragment_idx = []
        names = {i: {n for _, n in idx_plugs[i]} for i in idx_plugs}
        for c, ix in cats.items():
            pl = names[ix[0]]
            if any(n.startswith(("Spark", "Echo", "Ember", "Whisper", "Thread", "Facet")) for n in pl) and len(ix) >= 4:
                fragment_idx = ix
            elif len(ix) == 2 and len(pl) >= 3 and not any("Grenade" in n or "Barricade" in n for n in pl):
                aspect_idx = ix
        ability_idx = [i for i in range(len(s["sockets"])) if i not in aspect_idx and i not in fragment_idx]
        for label, text in b.get("kit", []):
            hit = None
            for i in ability_idx:
                m = self.first_mentioned(text, idx_plugs[i])
                if m:
                    pos = self.mention_pos(text, m[1])
                    if hit is None or pos < hit[3]:
                        hit = (i, m[0], m[1], pos)
            if hit and str(hit[0]) not in ov:
                ov[str(hit[0])] = hit[1]
            elif not hit:
                self.problems.append(f"{ctx}: kit {label} '{text}' not in {s['n']}")
        for kind, want, idx in (("aspect", b.get("aspects", []), aspect_idx), ("fragment", b.get("fragments", []), fragment_idx)):
            slots = list(idx)
            for a in want:
                m = self.first_mentioned(re.sub(r"\(.*?\)", "", a), idx_plugs[idx[0]]) if idx else None
                if not m:
                    self.problems.append(f"{ctx}: {kind} '{a}' not in {s['n']}")
                    continue
                if not slots:
                    self.problems.append(f"{ctx}: too many {kind}s ('{a}')")
                    continue
                ov[str(slots.pop(0))] = m[0]
        # Fragment slots come from the aspects' energy capacity; each fragment costs energy (1 today)
        cap = sum(self.P[str(ov[str(i)])].get("cap", 0) for i in aspect_idx if str(i) in ov)
        used = sum(self.P[str(ov[str(i)])].get("cost", 1) for i in fragment_idx if str(i) in ov)
        if cap and used != cap:
            self.problems.append(f"{ctx}: {used} fragments but the aspects give {cap} slots "
                                 + ("(too many)" if used > cap else "(empty slots)"))
        return sh, ov

    # ---------- armor mods ----------
    def weapon_element(self, w: dict) -> str | None:
        vs = self.r.current(w["name"])
        return vs[0].element if vs else None

    def element_of(self, weapons: list[dict], pred) -> str | None:
        for w in weapons:
            vs = self.r.current(w["name"])
            if vs and pred(vs[0]):
                return vs[0].element
        return None

    def fix_token(self, raw: str, weapons: list[dict]) -> tuple[str | None, str]:
        """Sheet mod phrase -> current-manifest phrase. (None = drop it). Second value: why it changed."""
        m = raw.strip()
        mm = re.match(r"^(.*?)(\s*x\d)$", m)
        base, mult = (mm[1], mm[2]) if mm else (m, "")
        if base in FIXED:
            return (FIXED[base] + mult if FIXED[base] else None), "doesn't exist; my pick"
        if base.lower() in RENAMES:
            return RENAMES[base.lower()] + mult, "renamed"
        if base in ("Weapon Surge", "Special Weapon Surge"):
            want_ammo = "Special" if base.startswith("Special") else None
            el = self.element_of(weapons, lambda v: v.ammo == want_ammo) if want_ammo else                 self.element_of(weapons, lambda v: v.slot == "Power") or self.element_of(weapons, lambda v: v.slot == "Energy")
            return (f"{el} Weapon Surge{mult}" if el else raw), "element-based now"
        tm = TYPE_MOD.match(base)
        if tm and tm[2].capitalize() not in ELEMENTS and tm[2].lower() != "harmonic" and not self.armor_mods.get(norm(base)):
            wtype = norm(TYPE_ALIASES.get(tm[2].lower(), tm[2].lower()))
            el = self.element_of(weapons, lambda v: norm(v.type).endswith(wtype))
            if el:
                return (f"Unflinching {el} Aim" if tm[1] else f"{el} {tm[3]}") + mult, "element-based now"
        return raw, ""

    def map_mod(self, raw: str, weapons: list[dict], ctx: str) -> list[tuple[str, int]]:
        m, _ = self.fix_token(raw, weapons)
        if not m or any(h in m.lower() for h in SKIP_HINTS):
            return []
        n = 1
        mm = re.match(r"^(.*?)\s*x(\d)$", m)
        if mm:
            m, n = mm[1], int(mm[2])
        m = re.sub(r"\s*\(.*?\)\s*", " ", m).strip()           # "Arc Siphon (Harmonic Siphon)"
        m = re.split(r"\s+or\s+", m)[0].strip()               # "Grenade Font or Melee Font"
        st = re.match(r"^(Health|Class|Grenade|Melee|Super|Weapons?) stat mod$", m, re.I)
        if st:
            m = STAT_MOD[st[1].lower()]
        h = self.armor_mods.get(norm(m))
        if h is None:
            self.problems.append(f"{ctx}: armor mod '{raw}' doesn't exist in the manifest")
            return []
        return [(m, h)] * n

    # ---------- build -> loadout ----------
    def loadout(self, b: dict, cls: str, sheet: str, mode: str, ws: list[dict], variant: str) -> dict:
        ctx = f"{sheet} {mode.upper()}: {b['name']}" + (" (alt)" if variant == "alt" else "")
        sh, ov = self.subclass_overrides(b, cls, ctx)
        equipped = [{"id": "0", "hash": sh, "socketOverrides": ov}]
        lines, matched = [], []
        for w in ws:
            if PLACEHOLDER.match(w["name"]):
                lines.append(f"- {w.get('slot', '')}: {w['name']} (your pick)")
                continue
            vs = self.r.current(w["name"])
            if not vs:
                self.problems.append(f"{ctx}: weapon '{w['name']}' not in manifest")
                continue
            perks, _ = roll_from_pairs(w.get("roll", []))
            wanted = [p.split(" / ")[0] for k, p in perks.items() if k in ("col1", "col2", "trait1", "trait2")]
            all_hashes = [v.hash for v in vs] + [v.hash for v in self.r.versions(w["name"]) if v.hash not in [x.hash for x in vs]]
            iid, h, how = self.pick_copy(all_hashes, wanted)
            equipped.append({"id": iid, "hash": h})
            matched.append((w["name"], iid != "0", how))
            roll = ", ".join(f"{v}" for k, v in perks.items() if k in ("col1", "col2", "trait1", "trait2", "masterwork"))
            alts = "; or " + ", ".join(a["name"] for a in w["alts"]) if w.get("alts") else ""
            lines.append(f"- {w.get('slot', '')}: {w['name']}" + (f" ({roll})" if roll else "") + alts)
        params: dict = {}
        ex = re.sub(r"\s*\(.*\)$", "", b.get("exotic", "")).strip()
        arm = [a for a in self.d["armor"].get(ex, []) if a["class"] == CLASS[cls]]
        if arm:
            params["exoticArmorHash"] = arm[0]["hash"]
            iid, h, _ = self.pick_copy([a["hash"] for a in arm], [])
            equipped.append({"id": iid, "hash": h})
            matched.append((ex, iid != "0", ""))
        else:
            self.problems.append(f"{ctx}: exotic armor '{b.get('exotic')}' not found for {cls}")
        mods, mod_names = [], []
        for slot, txt in b.get("mods", []):
            for raw in re.split(r",\s*", txt):
                for name, h in self.map_mod(raw, ws, ctx):
                    mods.append(h)
                    mod_names.append(name)
        params["mods"] = mods
        order = [s for s in re.findall(r"[A-Za-z]+", (b.get("stats") or "").lower()) if s in STATS or s == "weapon"]
        order = list(dict.fromkeys("weapons" if s == "weapon" else s for s in order))
        if order:
            params["statConstraints"] = [{"statHash": STATS[s]} for s in order + [s for s in STATS if s not in order]]
            params["assumeArmorMasterwork"] = 3
        notes = "\n".join(filter(None, [
            b.get("pitch", ""), f"Stats: {b['stats']}" if b.get("stats") else "",
            "Weapons:", *lines,
            f"Mods: {', '.join(n for n in mod_names if '(was' in n)}" if any("(was" in n for n in mod_names) else "",
            b.get("how", ""), f"#spinz #{slug(sheet).replace('-', '')} #{mode}",
        ]))
        name = f"{sheet} {mode.upper()} · {b['name']}" + (" (alt)" if variant == "alt" else "")
        lo = {"id": f"spinz-{slug(sheet)}-{mode}-{b['id']}" + ("-alt" if variant == "alt" else ""),
              "name": name[:120], "classType": CLASS[cls], "clearSpace": False,
              "equipped": equipped, "unequipped": [], "parameters": params, "notes": notes[:2000]}
        return {"loadout": lo, "matched": matched, "mod_names": mod_names, "ctx": ctx,
                "link": DIM + urllib.parse.quote(json.dumps(lo, separators=(",", ":")), safe="")}

    def fix_sheets(self) -> list[str]:
        """Rewrite each build's mods:[...] on the sheets to current mod names. Returns change log lines."""
        log = []
        for fname, sheet in SHEETS.items():
            path = SRC / fname
            html = path.read_text(encoding="utf-8")
            data = page_data(path)
            shared = data.get("SHARED_PVP_WEAPONS")
            for bkey, builds in data["BUILDS"].items():
                for b in builds:
                    ws = resolve_weapons(b.get("weapons", []), shared)
                    new_mods, changed = [], []
                    for slot, txt in b.get("mods", []):
                        toks = []
                        for raw in re.split(r",\s*", txt):
                            new, why = self.fix_token(raw, ws)
                            if new != raw:
                                changed.append(f"{raw} -> {new or '(removed)'} ({why})")
                            if new:
                                toks.append(new)
                        new_mods.append([slot, ", ".join(toks)])
                    if not changed:
                        continue
                    anchor = f'id:"{b["id"]}"'
                    if html.count(anchor) != 1:
                        log.append(f"SKIP {fname} {b['id']}: id not unique")
                        continue
                    start = html.index("mods:[", html.index(anchor))
                    end = html.index("]]", start) + 2
                    html = html[:start] + "mods:" + json.dumps(new_mods, ensure_ascii=False, separators=(",", ":")) + html[end:]
                    log += [f"{sheet} {bkey} {b['name']}: {c}" for c in changed]
            path.write_text(html, encoding="utf-8")
        return log

    def run(self) -> list[dict]:
        out = []
        for fname, sheet in SHEETS.items():
            data = page_data(SRC / fname)
            shared = data.get("SHARED_PVP_WEAPONS")
            if sheet == "Trials":
                groups = [(c["key"], "pvp", f"Trials {c['label']}", c["key"]) for c in data.get("CLASSES", [])]
            else:
                cls = sheet.split()[0].lower()
                groups = [(m, m, sheet, cls) for m in ("pvp", "pve")]
            for bkey, mode, label, cls in groups:
                for b in data["BUILDS"].get(bkey, []):
                    out.append(self.loadout(b, cls, label, mode, resolve_weapons(b.get("weapons", []), shared), "main"))
                    if b.get("alt"):
                        out.append(self.loadout(b, cls, label, mode, resolve_weapons(b["alt"]["weapons"], shared), "alt"))
        return out


MINE = ROOT / ".cache" / "dim_loadouts_mine.md"   # inventory-matched links stay out of git (instance ids)


def write(items: list[dict], problems: list[str], have_inv: bool) -> None:
    if not have_inv:
        OUT_JSON.write_text(json.dumps([i["loadout"] for i in items], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = ["# DIM loadouts", "",
          "GENERATED by `tools/build_dim_loadouts.py`. Click a link with DIM signed in, check the drawer, then **Save**.",
          "Weapons and armor marked missing are placeholders: click them in the drawer to pick your copy." if not have_inv else
          "Items were matched to your copies from the DIM CSV export (best perk match).", ""]
    if problems:
        md += ["## Problems", ""] + [f"- {p}" for p in problems] + [""]
    md += ["## Loadouts", ""]
    for i in items:
        own = sum(1 for _, ok, _ in i["matched"] if ok)
        md.append(f"- [{i['loadout']['name']}]({i['link']}) ({own}/{len(i['matched'])} items owned)")
    OUT_MD.parent.mkdir(exist_ok=True)
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", nargs="*", type=Path, default=[], help="DIM weapons/armor CSV exports")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix-sheets", action="store_true", help="rewrite sheet mod text to current mod names")
    a = ap.parse_args()
    g = Gen(a.inventory)
    if a.fix_sheets:
        for line in g.fix_sheets():
            print("FIXED", line)
    items = g.run()
    for p in g.problems:
        print("PROBLEM", p)
    if not a.check:
        write(items, g.problems, bool(a.inventory))
        print(f"{len(items)} loadouts -> " + (str(MINE.relative_to(ROOT)) if a.inventory else
              f"{OUT_JSON.relative_to(ROOT)}, {OUT_MD.relative_to(ROOT)}"))
    return 1 if a.check and g.problems else 0


if __name__ == "__main__":
    sys.exit(main())
