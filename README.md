# D2 wish lists for DIM

DIM wish lists put a thumbs-up on any weapon in your vault that matches a god roll, so you can tell keepers from shards at a glance. Every roll here is checked against Bungie's live manifest. Perks that can't roll on the current version of a gun are left out, and so are misspelled guide perks that don't exist.

## Use a wish list (no setup)

1. Open [DIM](https://app.destinyitemmanager.com) → **Settings** → **Wish List**.
2. Paste one of these URLs into the wish-list box and click **Update** (or the reload button):

| List | What's in it | URL |
|---|---|---|
| blueberries.gg, all | every PvE and PvP god roll from blueberries.gg | `https://raw.githubusercontent.com/Kamikinzee/d2-artifacts/main/wishlists/blueberries.txt` |
| blueberries.gg, PvE | PvE rolls only | `https://raw.githubusercontent.com/Kamikinzee/d2-artifacts/main/wishlists/blueberries-pve.txt` |
| blueberries.gg, PvP | PvP rolls only | `https://raw.githubusercontent.com/Kamikinzee/d2-artifacts/main/wishlists/blueberries-pvp.txt` |
| Spinz's list | the blueberries rolls plus Spinz's own loadout sheets and God Roll Codex | `https://raw.githubusercontent.com/Kamikinzee/d2-artifacts/main/wishlists/spinz.txt` |

DIM accepts several URLs at once if you separate them with `|`, so you can stack these on top of voltron or any other list.

**Reading the notes.** Hover over the thumbs-up on an item. The note says which mode the roll is for, which blueberries page it came from, and when that page was last updated. Some pages haven't been touched in a season or two, so check the date. `[full roll]` means the barrel, mag and both traits match. `[traits only]` means just the two traits match.

## Build your own copy

You need Python 3.10 or newer. Nothing else gets installed and there's no API key.

```
git clone https://github.com/Kamikinzee/d2-artifacts
cd d2-artifacts
python tools/import_blueberries.py --fetch --wishlist my-wishlist.txt
```

- `--fetch` downloads the god-roll pages from blueberries.gg. The site asks crawlers to wait 10 seconds between requests, so the first run takes about 70 minutes. Later runs only download pages that changed since your last run.
- The Bungie manifest (~200 MB) downloads into `.cache/` on the first run, and again whenever Bungie updates it.
- `--mode pve` or `--mode pvp` limits the list to one mode. Leave it off to get both.
- `reports/blueberries_import.md` lists every perk that was dropped (not on the current version, or not a real perk on that gun) and every page that was skipped (exotics with fixed rolls, armor pages, pages without a roll table).

To use the file you built, open DIM → Settings → Wish List and choose **Load wish list from file**.
