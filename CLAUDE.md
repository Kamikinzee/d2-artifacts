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
 aspects:[], fragments:[], exoticNote, stats, set?, artifact?,
 weapons:[{slot,name,type,roll:[[k,v]],note}], alt?:{note, weapons:[...]},
 mods:[[Helmet],[Arms],[Chest],[Legs],[Class item]], how}
```
- The Warlock sheet also allows string keys into `SHARED_PVP_WEAPONS`.

## Hard rules (check every build)
- **Exotics:** one exotic weapon and one exotic armor piece.
- **Slots:** Kinetic, Stasis and Strand weapons go in the Kinetic slot. Arc, Solar and Void go in the Energy slot. Never pair two of the same slot.
  - M-17 Fast Talker and Modified B-7 are Stasis, so they're Kinetic slot.
  - **Praxic Blade is a Kinetic-slot exotic sword that uses special ammo, not heavy.** So it can't pair with Mint Retrograde or any other Kinetic weapon.
  - Mint Retrograde is a Strand micro-missile pulse in the Kinetic slot, using special ammo. It is not a grenade launcher.
- **Spec mods** (Major Spec, Boss Spec) no longer exist on weapons. Use Backup Mag, Targeting Adjuster, Counterbalance Stock, Quick Access Sling and similar instead.
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
  - Origin traits on some Trials, Iron Banner and Renegades guns.
  - Riiswalker's element.
  - Finite Impactor's version.
  - Whether The Epicurean got a tiered reissue.
  - Necrochasm's catalyst steps.

## Verify a page before publishing
```
NODE_PATH=$(npm root -g) node -e "const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:390,height:900}});const e=[];p.on('pageerror',x=>e.push(x.message));await p.setContent('<!doctype html><html><head><meta charset=utf-8></head><body>'+require('fs').readFileSync(process.argv[1],'utf8')+'</body></html>');console.log(await p.\$\$eval('article',a=>a.length),await p.evaluate(()=>document.documentElement.scrollWidth),e);await b.close()})()" src/titan-loadout.html
```
Check that it shows no errors and a scroll width of 390 (no horizontal scroll).
