"""Resolve weapon names -> itemHash and perk names -> plugHash against the live manifest.

    python tools/resolve.py "Igneous Hammer"          # versions + current perk pools
    python tools/resolve.py "Igneous Hammer" --all    # also show older versions' pools

Versions: a weapon name usually maps to several items (original, 9.7.0 reissue, Adept...).
The *current* version is the one with the "Gear Tier Upgrade" socket (Edge of Fate tiered
loot). Adept/Timelost/Harrowed copies are separate items with their own hashes.
"""
from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest  # noqa: E402

CAT_PERKS = 4241085061
CAT_INTRINSIC = 3956125808
CAT_MODS = 2685412949
DMG = {1: "Kinetic", 2: "Arc", 3: "Solar", 4: "Void", 6: "Stasis", 7: "Strand"}
SLOT = {manifest.BUCKET_KINETIC: "Kinetic", manifest.BUCKET_ENERGY: "Energy", manifest.BUCKET_POWER: "Power"}
AMMO = {1: "Primary", 2: "Special", 3: "Heavy"}
VARIANT_RE = re.compile(r"^(?P<base>.+?) \((?P<v>Adept|Timelost|Harrowed)\)$")
COLS = ["col1", "col2", "trait1", "trait2", "origin"]
COL_LABEL = {"col1": "Barrel (col 1)", "col2": "Magazine (col 2)", "trait1": "Trait 1 (col 3)",
             "trait2": "Trait 2 (col 4)", "origin": "Origin", "masterwork": "Masterwork", "mod": "Mod",
             "intrinsic": "Intrinsic"}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s+", " ", s.strip().lower())
    return s


@dataclass
class Plug:
    hash: int
    name: str
    can_roll: bool = True
    enhanced: bool = False

    @property
    def base(self) -> str:
        return norm(re.sub(r"\s*Enhanced$", "", self.name))


@dataclass
class Version:
    hash: int
    name: str
    variant: str          # "" | "Adept" | ...
    current: bool         # has the Gear Tier Upgrade socket
    tier: str
    type: str
    slot: str             # Kinetic / Energy / Power
    element: str
    ammo: str
    cols: dict = field(default_factory=dict)       # col -> [Plug]
    masterworks: set = field(default_factory=set)  # stat names
    mod_cats: set = field(default_factory=set)     # allowed plugCategoryIdentifiers
    mods: set = field(default_factory=set)         # legal weapon mod names
    intrinsic: str = ""

    @property
    def label(self) -> str:
        bits = [self.variant] if self.variant else []
        if not self.current:
            bits.append("old version")
        return f"{self.name}" + (f" ({', '.join(bits)})" if bits else "")


class Resolver:
    def __init__(self, data: dict | None = None):
        self.d = data or manifest.load()
        self.plugs = self.d["plugs"]
        self.by_name: dict[str, list[str]] = {}
        for h, w in self.d["weapons"].items():
            self.by_name.setdefault(norm(w["n"]), []).append(h)
        self.mods_by_name: dict[str, list[tuple[int, str]]] = {}
        for h, p in self.plugs.items():
            if ".weapon.mod_" in p["pci"] or p["pci"].startswith("v4") and "weapon.mod" in p["pci"]:
                self.mods_by_name.setdefault(norm(p["n"]), []).append((int(h), p["pci"]))
        self._cache: dict[str, Version] = {}

    # ---------- weapons ----------
    def versions(self, name: str, include_variants: bool = True) -> list[Version]:
        key = norm(name)
        hashes = list(self.by_name.get(key, []))
        if include_variants:
            for n, hs in self.by_name.items():
                m = VARIANT_RE.match(n)
                if m and m["base"] == key:
                    hashes += hs
        vs = [self.version(h) for h in hashes]
        # Drop shells with no randomized or fixed perk columns at all (ornament/collection copies)
        vs = [v for v in vs if any(v.cols.get(c) for c in ("col1", "col2", "trait1", "trait2"))]
        # Never-reissued weapons (most exotics) have no tiered version: all of them count as current.
        for variant in {v.variant for v in vs}:
            group = [v for v in vs if v.variant == variant]
            if not any(v.current for v in group):
                for v in group:
                    v.current = True
        return sorted(vs, key=lambda v: (not v.current, v.variant != "", v.hash))

    def current(self, name: str) -> list[Version]:
        """Current (gear-tier) non-variant versions; falls back to all base versions if none are tiered."""
        vs = [v for v in self.versions(name) if not v.variant]
        cur = [v for v in vs if v.current]
        return cur or vs

    def _plugs_for(self, s: dict) -> list[Plug]:
        out = []
        if s.get("rand"):
            src = self.d["plugsets"].get(str(s["rand"]), [])
            pairs = [(h, cr) for h, cr in src]
        elif s.get("reuse"):
            pairs = [(h, cr) for h, cr in self.d["plugsets"].get(str(s["reuse"]), [])]
        elif s.get("items"):
            pairs = [(h, True) for h in s["items"]]
        elif s.get("init"):
            pairs = [(s["init"], True)]
        else:
            pairs = []
        seen = set()
        for h, cr in pairs:
            p = self.plugs.get(str(h))
            if not p or (h, cr) in seen:
                continue
            seen.add((h, cr))
            out.append(Plug(int(h), p["n"], bool(cr), p["t"].startswith("Enhanced") or p["n"].endswith(" Enhanced")))
        return out

    def version(self, h: str) -> Version:
        if h in self._cache:
            return self._cache[h]
        w = self.d["weapons"][h]
        st = self.d["sockettypes"]
        m = VARIANT_RE.match(w["n"])
        v = Version(int(h), m["base"] if m else w["n"], m["v"] if m else "", False, w["tier"], w["type"],
                    SLOT.get(w["bucket"], "?"), DMG.get(w["dmg"], "?"), AMMO.get(w["ammo"], "?"))
        cats = {c["socketCategoryHash"]: c["socketIndexes"] for c in w["cats"]}
        sockets = w["sockets"]
        for s in sockets:
            if "weapon_tiering.plugs.mods.enhancers" in st.get(str(s["st"]), []):
                v.current = True
        generic = []
        traits = []
        for i in cats.get(CAT_PERKS, []):
            s = sockets[i]
            wl = st.get(str(s["st"]), [])
            if any("trackers" in x for x in wl) or not wl:
                continue
            plugs = self._plugs_for(s)
            if "origins" in wl:
                v.cols["origin"] = plugs
            elif "frames" in wl:
                traits.append(plugs)
            else:
                generic.append(plugs)
        for col, plugs in zip(("col1", "col2"), generic):
            v.cols[col] = plugs
        for col, plugs in zip(("trait1", "trait2"), traits):
            v.cols[col] = plugs
        for i in cats.get(CAT_INTRINSIC, []):
            ps = self._plugs_for(sockets[i])
            if ps:
                v.intrinsic = ps[0].name
        for i in cats.get(CAT_MODS, []):
            s = sockets[i]
            wl = st.get(str(s["st"]), [])
            if "v400.plugs.weapons.masterworks" in wl:
                # The socket lists every stat masterwork; only ones for stats this weapon has apply.
                wstats = set(w.get("stats", []))
                cand = list(s.get("items", [])) + [h2 for h2, _ in self.d["plugsets"].get(str(s.get("reuse")), [])]
                for ph in cand:
                    p = self.plugs.get(str(ph))
                    mm = p and re.match(r"^Tier \d+: (.+)$", p["n"])
                    if mm and p.get("stat") in wstats:
                        v.masterworks.add(norm(mm[1]))
            elif any("weapon.mod_" in x for x in wl):
                v.mod_cats.update(x for x in wl if "weapon.mod_" in x)
                for p in self._plugs_for(s):
                    pc = self.plugs[str(p.hash)]["pci"]
                    if pc in v.mod_cats and not p.name.startswith("Empty") and "Enhanced" not in self.plugs[str(p.hash)]["t"]                             and "confetti" not in pc:
                        v.mods.add(p.name)
        self._cache[h] = v
        return v

    # ---------- perks ----------
    @staticmethod
    def find_in(plugs: list[Plug], perk: str) -> list[Plug]:
        """Plugs in a column matching a perk name (base + enhanced share a name)."""
        k = norm(re.sub(r"\s*\(?Enhanced\)?$", "", perk, flags=re.I))
        return [p for p in plugs if p.base == k]

    @staticmethod
    def prefix_in(plugs: list[Plug], perk: str) -> list[Plug]:
        k = norm(perk)
        return [p for p in plugs if p.base.startswith(k)]

    def mod(self, name: str) -> list[tuple[int, str]]:
        return self.mods_by_name.get(norm(name), [])


def describe(r: Resolver, name: str, show_all: bool = False) -> None:
    vs = r.versions(name)
    if not vs:
        print(f"!! no weapon named {name!r}")
        return
    print(f"{name}: {len(vs)} version(s)")
    for v in vs:
        print(f"  {v.hash:>10}  {v.label:40} {v.tier} {v.type} | {v.slot} slot, {v.element}, {v.ammo} ammo | "
              f"{v.intrinsic}{'  <- CURRENT' if v.current and not v.variant else ''}")
    for v in vs if show_all else [x for x in vs if x.current and not x.variant] or vs[:1]:
        print(f"\n  Pools for {v.hash} ({v.label}):")
        for c in COLS:
            ps = v.cols.get(c, [])
            names = [p.name + ("" if p.can_roll else " (can't roll)") for p in ps if not p.enhanced]
            print(f"    {COL_LABEL[c]:18} {', '.join(dict.fromkeys(names))}")
        print(f"    {'Masterworks':18} {', '.join(sorted(v.masterworks))}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    res = Resolver()
    for n in args:
        describe(res, n, "--all" in sys.argv)
        print()
