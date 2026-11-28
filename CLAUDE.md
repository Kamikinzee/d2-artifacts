# D2 Artifacts: handoff for Claude Code

This repo holds the source for Spinz's Destiny 2 reference pages, which are published as claude.ai artifacts. Every file in `src/` is the page body only. The Artifact publisher adds the doctype, head and body wrapper, so don't add them yourself.

## Who / how to talk
- **Spinz** plays on controller (DualSense Edge). He's PvP and Trials first. Titan is his main, and he also plays Warlock and Hunter.
- Tone: casual, direct, no filler. If a pick is weak, say so.

## Artifact map (update in place, never duplicate)
| File | Artifact URL |
|---|---|
| src/godroll-codex.html | https://claude.ai/artifact/Va6bDwWD9kkgXN2Wy85jNX |
| src/trials-loadout.html | https://claude.ai/artifact/BcctJfS9w3bEtmeXoeBHNV |
| src/titan-loadout.html | https://claude.ai/artifact/GzVN4JXbL1NraWfAvuNTg3 |
| src/titan-nocannon-loadout.html | https://claude.ai/artifact/McpihPg82DnvtXq4hRNtd2 |
| src/warlock-loadout.html | https://claude.ai/artifact/XS2AG67bYVBCB23jBsFgjC |
| src/hunter-loadout.html | https://claude.ai/artifact/ELj9R2Z2A6oUKxkrzw4MeX |
| src/hunter-starter.html (Spinz's friend: newer player, PvE, has One Thousand Voices, no dungeon-key gear) | https://claude.ai/artifact/DmNBdf1vH7ycCfXeYmM8GG |

To publish, use the Artifact tool with `file_path` set to the src file and `url` set to the URL above. If the live version might have changed, run Artifact `read` on the URL first and merge.

There are two stray duplicates from a past mistake. Spinz hasn't decided whether to delete them, so don't touch them unless he asks: https://claude.ai/artifact/C2xDKzMsgCeTHtUA2FNHqe (Titan) and https://claude.ai/artifact/7KzacADANax8ppTUVbhXRi (Warlock).

The full cross-game artifact index lives in the GAMING claude.ai project, in the doc `claude/artifact-index.md`.

## Data shapes
**God Roll Codex cards** (arrays `TRIALS`, `IRONBANNER`, `RENEGADES`, `DUNGEONS`, each grouped by `src`):
```
{n, t:"Type · Frame · Element", src, tier:"A PvP / B PvE",
 pvp:[Barrel, Mag, Trait1, Trait2, Masterwork, Origin, Mod], pve:[same],
 p1:"col3 pool", p2:"col4 pool", o:"origin traits"}
```
- Alternate perks inside a slot are separated by " / ".
- Pool order arrays: `POOLS`, `IBPOOLS`, `RPOOLS`, `DPOOLS`.
- `TYPES` holds the generic frame rolls.
- The nav has two rows: Source (`#srcnav`) and Type (`#types`).

**Loadout sheets** (`BUILDS.pvp` and `BUILDS.pve`; the Trials sheet uses `CLASSES` plus `BUILDS[classKey]`):
```
{id, name, el, color:"--arc|--void|--solar|--stasis|--prism|--strand", exotic, pitch,
 src:{partial:bool, text}, kit:[[Super],[Class],[Jump],[Melee],[Grenade]],
 aspects:[], fragments:[], exoticNote, stats, sets:[[setName, pieces]], set, artifact?,
 weapons:[{slot,name,type,roll:[[k,v]],alts?:[{name,roll}],note}], alt?:{note, weapons:[...]},
 mods:[[Helmet],[Arms],[Chest],[Legs],[Class item]], how}
```
- The Warlock and Hunter sheets also allow string keys into `SHARED_PVP_WEAPONS`.
- `sets` is the machine-readable armor set (manifest name and piece count: one 4-piece or two 2-pieces), and `set` is the text the sheet shows (label guide pick vs my pick). Every build needs both. The DIM generator turns `sets` into `parameters.setBonuses`, so Loadout Optimizer builds the legendary armor around it. A missing set, an unknown name, a piece count with no bonus, or more pieces than the legendary slots left after the exotic is a FAIL.
- `alts` on a weapon means "or run this other gun in the same slot". Each one gets its own roll and renders under the main roll. Never write "Gun A / Gun B" in `name` with one shared roll, because two guns rarely share a perk pool.
- Codex cards can carry `chk:"Manifest check (...): ..."`, which renders as an italic note under the card title. Use it to say what the manifest changed and which picks are yours.
- Roll keys the wish-list extractor understands: Barrel/Blade/Bowstring/Haft/Launcher/Scope (col 1), Mag/Guard/Arrow/Battery (col 2), Trait 1, Trait 2, MW, Origin, Mod. Any other key (Catalyst, Intrinsic...) is treated as info only.

## Wish-list pipeline (DIM)
`wishlists/spinz.txt` is a DIM wish list built from every roll on every sheet plus the codex. DIM reads it from `https://raw.githubusercontent.com/Kamikinzee/d2-artifacts/main/wishlists/spinz.txt`.
- `python tools/build_wishlist.py` runs the whole thing. It refreshes the manifest cache, extracts `src/*.html` into `data/rolls.json`, validates, and writes `wishlists/spinz.txt` and `reports/validation.md`. Add `--check` to validate without writing. The exit code is 1 on any FAIL.
- `python tools/validate.py --weapon "Name" --roll "Barrel; Mag; Trait 1; Trait 2; MW; Origin; Mod"` checks one roll. Leave a slot empty to skip it.
- `python tools/resolve.py "Name" [--all]` lists versions (current = has the Gear Tier socket), slot, element, ammo and the real perk pools.
- `python tools/sync_codex_pools.py` rewrites codex `p1`/`p2`/`o` and the element from the manifest when they've drifted.
- `python tools/build_dim_loadouts.py` turns every sheet build into a DIM loadout link, covering the subclass (super, abilities, aspects, fragments), weapons, exotic armor, armor mods and stat order. It writes `data/dim_loadouts.json` and `reports/dim_loadouts.md`, and `build_wishlist.py` runs it too: any subclass pick or armor mod that isn't in the manifest counts as a FAIL. Add `--inventory destinyWeapons.csv destinyArmor.csv` (the DIM Settings > Spreadsheets exports) to match Spinz's real copies by instance id. That output goes to `.cache/dim_loadouts_mine.md`, which stays out of git. `--fix-sheets` rewrites sheet mod text to the current mod names.
- Public lists for anyone (see `README.md`): `build_wishlist.py` also writes `wishlists/blueberries.txt`, `blueberries-pve.txt` and `blueberries-pvp.txt`, which hold blueberries rolls only and none of Spinz's sheet rolls. `python tools/import_blueberries.py --fetch` re-downloads only new or changed pages (it tracks them in `.cache/bb/fetched.json`). `--wishlist PATH --mode all|pve|pvp` writes a standalone list without the sheet pipeline.
- blueberries.gg god rolls: the god-roll pages are cached in `.cache/bb/pages/` (fetched 10 s apart, per the site's robots.txt Crawl-delay). `python tools/import_blueberries.py` parses every "PvE Roll / PvP Roll" table into `data/imports/blueberries.json`, corrects spellings to the manifest names, and drops perks that aren't on the current version. Dropped perks are listed in `reports/blueberries_import.md`. Every `data/imports/*.json` merges into the wish list the same way manual rolls do.
- Rolls that aren't on any sheet go in `data/manual_rolls.json`, which uses the same shape as a `rolls` entry in `data/rolls.json`. Don't hand-edit `data/rolls.json`, since it's generated.
- The manifest cache lives in `.cache/` (gitignored, about 200 MB on first download). Python on this machine needs the relaxed X509 strict flag, which `tools/manifest.py` already handles.
- Old versions get lines only where every perk is still legal on them, tagged "(old version)". Adept copies get their own lines.

## Standing workflow (every new build or god roll)
1. Research current guides by web search, and check each guide's date.
2. Resolve every gun against the manifest and run `validate.py`. Nothing reaches Spinz or an artifact until it passes, including slots, one exotic weapon and one exotic armor piece, and mod legality.
3. Label what came from a guide and what's your pick.
4. Update the artifact in place, using the map above.
5. Run `python tools/build_wishlist.py`, then commit and push `src/`, `data/`, `wishlists/` and `reports/`.
6. Tell Spinz in one line what was added.

## Hard rules (check every build)
- **Exotics:** one exotic weapon and one exotic armor piece.
- **Slots:** Kinetic, Stasis and Strand weapons go in the Kinetic slot. Arc, Solar and Void go in the Energy slot. Never pair two of the same slot.
  - M-17 Fast Talker and Modified B-7 are Stasis, so they're Kinetic slot.
  - **Praxic Blade is a Kinetic-slot exotic sword that uses special ammo, not heavy.** So it can't pair with Mint Retrograde or any other Kinetic weapon.
  - Mint Retrograde is a Strand micro-missile pulse in the Kinetic slot, using special ammo. It is not a grenade launcher.
- **Spec mods** (Major Spec, Boss Spec) no longer exist on weapons. Use Backup Mag, Targeting Adjuster, Counterbalance Stock, Quick Access Sling and similar instead.
- **Armor mods are element-based now.** Targeting, Loader, Dexterity, Scavenger, Holster and Unflinching Aim come as Kinetic, Arc, Solar, Void, Stasis, Strand or Harmonic. Weapon-type versions like "Hand Cannon Targeting" don't exist anymore, so pick the element of the gun the mod is for. Heavy Reserves is gone too.
- **Fragments:** the number of fragments has to equal the fragment slots the two aspects give (each aspect has 1 to 3), and the pipeline FAILs otherwise.
- **Blink** is Voidwalker only. Stormcaller doesn't get it.
- **Trials and Competitive** turn artifact perks off.
- **Exotic class item perks** (Stoicism and the like) only work on Prismatic.
- **Stats** use Edge of Fate armor 3.0: Health, Weapons, Class, Grenade, Super, Melee. Values above 100 give bonus effects.
- **Dungeon guns** were reissued in 9.7.0 with new perk pools, and many guides still list the old ones. Check the Bungie manifest (`bungie.net/Platform/Destiny2/Manifest`), because light.gg blocks automated fetches.

## Accuracy
- Web search any meta, loot or perk question. Don't answer from memory, and check each guide's date.
- Label what came from a guide and what's your own pick. Mods are usually picks.
- Heat weapons (Voltaic Shade, High Tyrant, Zealous Ideal) are not meta. Bitter End is the exception.

## Open items
- Swap the heat primaries on the sheets. Spinz was offered this but hasn't confirmed:
  - Voltaic Shade → Prosecutor
  - High Tyrant → Gnawing Hunger
  - Zealous Ideal → Darkest Before
- The Trials Loadout Sheet only has Solar Warlock so far, with 3 builds (Ophidian Snap, Rain of Fire, Icarus Air Duelist). Titan and Hunter are still to do, plus the other Warlock subclasses.
- Unverified details:
  - Necrochasm's catalyst steps.
- Settled by the manifest (Nov 2026):
  - Trials, Iron Banner and Renegades origins are synced.
  - Riiswalker is Kinetic.
  - The current Finite Impactor is the Solar reissue (the older copies are Arc).
  - The Epicurean has no tiered reissue (two untiered copies).
  - Gunnora's Axe is Solar with Suros Synergy.
  - The manifest spells the perk "Ancillary Ordinance", not "Ordnance". Use the manifest spelling.

## Verify a page before publishing
```
NODE_PATH=$(npm root -g) node -e "const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:390,height:900}});const e=[];p.on('pageerror',x=>e.push(x.message));await p.setContent('<!doctype html><html><head><meta charset=utf-8></head><body>'+require('fs').readFileSync(process.argv[1],'utf8')+'</body></html>');console.log(await p.\$\$eval('article',a=>a.length),await p.evaluate(()=>document.documentElement.scrollWidth),e);await b.close()})()" src/titan-loadout.html
```
Check that it shows no errors and a scroll width of 390 (no horizontal scroll).

Playwright isn't installed globally on this machine, so that command fails. Instead, wrap the src file in a doctype shell under `.cache/preview/` and open it in the desktop app's Browser pane at 390×900. Check `document.querySelectorAll('article').length`, `document.documentElement.scrollWidth` and the console for errors.
