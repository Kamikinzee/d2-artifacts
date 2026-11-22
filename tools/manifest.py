"""Download and cache the Bungie Destiny 2 manifest (no API key needed).

Keeps a slim, pre-processed copy in .cache/manifest/ (gitignored) and only
re-downloads when Bungie's manifest version changes.

    python tools/manifest.py            # refresh if the version changed
    python tools/manifest.py --force    # always re-download
"""
from __future__ import annotations

import gzip
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "manifest"
SLIM = CACHE / "slim.json.gz"
VERSION_FILE = CACHE / "version.txt"

BUNGIE = "https://www.bungie.net"
TABLES = ["DestinyInventoryItemDefinition", "DestinyPlugSetDefinition", "DestinySocketTypeDefinition"]
SLIM_FORMAT = 4  # bump when _slim() output changes so caches rebuild
UA = {"User-Agent": "d2-wishlist-pipeline/1.0", "Accept-Encoding": "gzip"}

# Bucket hashes
BUCKET_KINETIC, BUCKET_ENERGY, BUCKET_POWER = 1498876634, 2465295065, 953998645
BUCKET_CLASS_ITEM = 1585787867
ARMOR_BUCKETS = {3448274439: "Helmet", 3551918588: "Arms", 14239492: "Chest", 20886954: "Legs", 1585787867: "Class item"}


# Certificates are still fully verified against the OS store. Python 3.13+ also turns on
# VERIFY_X509_STRICT, which rejects some real-world CA chains (e.g. AV/TLS-inspection roots)
# that curl/browsers accept, so only that extra strictness flag is dropped.
_SSL = ssl.create_default_context()
_SSL.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120, context=_SSL) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def live_index() -> dict:
    d = json.loads(_get(f"{BUNGIE}/Platform/Destiny2/Manifest/"))
    if d.get("ErrorStatus") != "Success":
        raise RuntimeError(f"Bungie manifest error: {d.get('ErrorStatus')} {d.get('Message')}")
    return d["Response"]


def _slim(items: dict, plugsets: dict, sockettypes: dict, version: str) -> dict:
    weapons, armor, plugs, subclasses = {}, {}, {}, {}
    for h, d in items.items():
        name = d.get("displayProperties", {}).get("name", "")
        if not name:
            continue
        inv = d.get("inventory", {}) or {}
        if d.get("plug"):
            plugs[h] = {
                "n": name,
                "pci": d["plug"].get("plugCategoryIdentifier", ""),
                "t": d.get("itemTypeDisplayName", ""),
            }
            cap = ((d["plug"].get("energyCapacity") or {}).get("capacityValue"))
            cost = ((d["plug"].get("energyCost") or {}).get("energyCost"))
            if cap:
                plugs[h]["cap"] = cap      # aspects: fragment slots granted
            if cost:
                plugs[h]["cost"] = cost    # fragments / armor mods: energy cost
            if plugs[h]["pci"].startswith("v400.plugs.weapons.masterworks.stat"):
                inv_stats = [s["statTypeHash"] for s in d.get("investmentStats", []) if s.get("value")]
                if inv_stats:
                    plugs[h]["stat"] = inv_stats[0]
        if d.get("itemType") == 3 and d.get("sockets"):
            if 3109687656 in (d.get("itemCategoryHashes") or []):  # Dummies
                continue
            weapons[h] = {
                "n": name,
                "type": d.get("itemTypeDisplayName", ""),
                "tier": inv.get("tierTypeName", ""),
                "bucket": inv.get("bucketTypeHash"),
                "dmg": d.get("defaultDamageType"),
                "ammo": (d.get("equippingBlock") or {}).get("ammoType"),
                "wm": d.get("iconWatermark") or "",
                "coll": d.get("collectibleHash"),
                "stats": [int(k) for k in ((d.get("stats") or {}).get("stats") or {})],
                "cats": d["sockets"].get("socketCategories", []),
                "sockets": [
                    {
                        "st": s.get("socketTypeHash"),
                        "init": s.get("singleInitialItemHash"),
                        "rand": s.get("randomizedPlugSetHash"),
                        "reuse": s.get("reusablePlugSetHash"),
                        "items": [p["plugItemHash"] for p in s.get("reusablePlugItems", [])],
                    }
                    for s in d["sockets"].get("socketEntries", [])
                ],
            }
        elif d.get("itemType") == 16 and d.get("sockets"):  # subclass: for DIM loadout socketOverrides
            subclasses[h] = {
                "n": name,
                "class": d.get("classType"),
                "dmg": d.get("talentGrid", {}).get("hudDamageType") or d.get("defaultDamageType"),
                "cats": d["sockets"].get("socketCategories", []),
                "sockets": [
                    {"st": s.get("socketTypeHash"), "init": s.get("singleInitialItemHash"),
                     "reuse": s.get("reusablePlugSetHash"),
                     "items": [p["plugItemHash"] for p in s.get("reusablePlugItems", [])]}
                    for s in d["sockets"].get("socketEntries", [])
                ],
            }
        elif d.get("itemType") == 2 and inv.get("tierTypeName") == "Exotic":
            armor.setdefault(name, []).append(
                {"hash": int(h), "class": d.get("classType"), "slot": ARMOR_BUCKETS.get(inv.get("bucketTypeHash"), "?")}
            )
    ps = {
        h: [[p["plugItemHash"], p.get("currentlyCanRoll", True)] for p in d.get("reusablePlugItems", [])]
        for h, d in plugsets.items()
    }
    st = {h: [w.get("categoryIdentifier", "") for w in d.get("plugWhitelist", [])] for h, d in sockettypes.items()}
    return {"version": version, "format": SLIM_FORMAT, "weapons": weapons, "armor": armor, "plugs": plugs, "plugsets": ps, "sockettypes": st,
            "subclasses": subclasses}


def refresh(force: bool = False, quiet: bool = False) -> str:
    """Make sure the slim cache matches Bungie's live manifest version. Returns the version."""
    log = (lambda *a: None) if quiet else (lambda *a: print(*a, file=sys.stderr))
    try:
        idx = live_index()
    except Exception as e:
        if SLIM.exists():
            log(f"[manifest] couldn't reach Bungie ({e}); using cached {VERSION_FILE.read_text().strip()}")
            return VERSION_FILE.read_text().strip()
        raise
    version = idx["version"]
    stamp = f"{version}|fmt{SLIM_FORMAT}"
    if not force and SLIM.exists() and VERSION_FILE.exists() and VERSION_FILE.read_text().strip() == stamp:
        log(f"[manifest] cache is current ({version})")
        return version
    log(f"[manifest] downloading {version} ...")
    paths = idx["jsonWorldComponentContentPaths"]["en"]
    raw = {}
    for t in TABLES:
        t0 = time.time()
        raw[t] = json.loads(_get(BUNGIE + paths[t]))
        log(f"[manifest]   {t}: {len(raw[t])} rows ({time.time() - t0:.1f}s)")
    slim = _slim(raw["DestinyInventoryItemDefinition"], raw["DestinyPlugSetDefinition"],
                 raw["DestinySocketTypeDefinition"], version)
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = SLIM.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(slim, f, separators=(",", ":"))
    tmp.replace(SLIM)
    VERSION_FILE.write_text(stamp)
    log(f"[manifest] cached {len(slim['weapons'])} weapons, {len(slim['plugs'])} plugs -> {SLIM.relative_to(ROOT)}")
    return version


_loaded: dict | None = None


def load(refresh_first: bool = True) -> dict:
    global _loaded
    if _loaded is None:
        if refresh_first or not SLIM.exists():
            refresh()
        with gzip.open(SLIM, "rt", encoding="utf-8") as f:
            _loaded = json.load(f)
    return _loaded


if __name__ == "__main__":
    print(refresh(force="--force" in sys.argv))
