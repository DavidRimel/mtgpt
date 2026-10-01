# External source reachability — probed 2026-10-01, re-probed on implementation

Every row was probed live. "Reachable" means a real response with usable data, not just a 200.

## Reachable: JSON APIs (preferred — stable shapes, no scraping)

| Source | Endpoint | Gives us |
|---|---|---|
| Scryfall | `api.scryfall.com/cards/search` | Cards, legality, colour identity, prices, `is:gamechanger` |
| **Scryfall Tagger** | same, `otag:<tag>` | **Community-curated function tags.** `otag:ramp` 2286, `otag:removal` 6449, `otag:card-advantage` 6206, `otag:sweeper` 943, `otag:tutor` 1163, `otag:counterspell` 550, `otag:protection` 1322, `otag:extra-turn` 58, `otag:wheel` 149. Combinable with `ci:`, `legal:commander`, `order=edhrec`. |
| EDHREC | `json.edhrec.com/pages/commanders/<slug>.json` | Synergy scores, inclusion rates, themes, bracket spread |
| EDHREC | `.../commanders/<slug>/{upgraded,budget,expensive,cedh}.json` | Bracket/budget variant pools |
| **EDHREC** | `.../average-decks/<slug>.json` | **A full consensus decklist** by card type |
| EDHREC | `.../top/salt.json` | Salt scores |
| Commander Spellbook | `backend.commanderspellbook.com/variants/` | Combos, with `bracketTag` and `salt` |
| **Archidekt** | `archidekt.com/api/decks/<id>/` | **Deck import by URL**, with quantities, `Commander` category, `edhBracket` (often `null` — authors rarely set it) |
| MTGJSON | `mtgjson.com/api/v5/Meta.json` | Set/price metadata (low value here) |

## Reachable: HTML (scrape with care — shapes drift)

| Source | Use |
|---|---|
| MTGGoldfish | `/metagame/commander` — archetype popularity, `/archetype/<slug>` links |
| MTGTop8 | `/format?f=cEDH` — competitive decklists |
| Commander's Herald | Articles and primers |
| EDHREC (HTML) | Fallback if the JSON shape changes |

## Reachable: web research

General `WebSearch` works for blogs, primers, and articles. Use a curated allowlist —
an earlier probe showed SEO farms dominate otherwise (three of ten hits from one content mill).

## NOT reachable — confirmed, not worth retrying

| Source | Status | Why |
|---|---|---|
| **Reddit** | 403 to everything | Anthropic's crawler is banned by Reddit's policy. Script, `WebFetch`, and `WebSearch` all refuse. No workaround exists from here. |
| Moxfield | 403 Cloudflare | Host-level challenge; a real browser is required and Chromium cannot launch here |
| TappedOut | 403 | Bot protection |
| Aetherhub | 403 | Bot protection |
| Deckstats | 403 | Bot protection |
| mtgdecks.net | 403 | Bot protection |
| EDHREC `themes.json`, `tribes.json`, `combos/<ci>.json` | 403 | Only per-commander and `top/` pages are public |

## The `otag:` vocabulary, as actually probed

A tag that does not exist is answered **404 "your query didn't match any cards"** —
the same reply a real tag with an over-narrow filter gets. So an unverified tag ships
as a silent empty result, not as an error. `mtgpt/tagger.py` holds the verified list
with each card count; `tagger.REJECTED` holds the names that 404'd. Do not add a tag
to either without probing it.

Verified (count as of probing): `ramp` 2286, `draw` 4250, `removal` 6449,
`spot-removal` 5401, `creature-removal` 5511, `artifact-removal` 1190,
`enchantment-removal` 1038, `sweeper`/`mass-removal`/`board-wipe` 943 (all the same
set), `card-advantage` 6206, `tutor` 1163, `counterspell` 550, `protection` 1322,
`recursion` 2245, `extra-turn` 58, `extra-combat` 45, `win-condition` 69,
`wheel` 149, `theft` 724, `sacrifice-outlet` 1484, `discard-outlet` 1326,
`mana-rock` 384, `mana-dork` 441, `land-ramp` 621, `mana-filter` 220,
`mana-sink` 1866, `ritual` 69, `untapper` 763, `blink`/`flicker` 198, `clone` 71,
`bounce` 928, `mill` 1294, `self-mill` 1061, `lifegain` 2598, `counters-matter` 1270,
`landfall` 286, `evasion` 5354, `anthem` 536, `graveyard-hate` 419, `hate` 4511,
`hatebear` 66, `tax` 466, `pillowfort` 62, `group-hug` 413, `fog` 93.

Rejected — every one 404: `stax`, `token-generation`, `tokens`, `token`,
`cost-reduction`, `land-destruction`, `mass-land-destruction`, `mld`, `land-hate`,
`resource-denial`, `mana-denial`, `taxing`, `haste-enabler`, `card-selection`,
`creature-tutor`, `land-tutor`, `free-spell`, `infinite-combo`, `combo`, `wincon`,
`extra-turns`, `mana-ritual`, `treasure`, `reanimation`, `proliferate`,
`stack-interaction`, `indestructible`, `hexproof-granter`,
`counterspell-protection`, `fixing`, `mana-fixing`, `color-fixing`.

**There is no mass-land-denial tag.** Every candidate 404s, so `find` cannot serve
that function and `classify.py`'s regex is the only source for it.

## EDHREC slugs: apostrophes are deleted, not hyphenated

`Yuriko, the Tiger's Shadow` is `yuriko-the-tigers-shadow`. Replacing the apostrophe
with a hyphen gives `yuriko-the-tiger-s-shadow`, which the CDN answers **403** — the
same status a genuinely private page returns, so it reads as "endpoint blocked"
rather than "wrong slug". Confirmed against Gishath, Sun's Avatar; K'rrik, Son of
Yawgmoth; and Hanna, Ship's Navigator. Names where the apostrophe is followed by a
space (`Praetors' Voice`) are unaffected either way, which is how the bug hid.

## Design consequence

`otag:` changes how candidates should be found. `classify.py`'s regex exists to tag cards a user
*already has*; for *discovering* cards that fill a gap, a human-curated tag is strictly better and
cross-checks our own classification. Where they disagree, that disagreement is itself a signal.

Measured agreement between `otag:` and `classify.py` at `--limit 60`, `ci:wubrg`:
ramp 0.82, draw 0.83, counterspell 0.82, removal 0.80, protection 0.80, sweeper 0.73,
**recursion 0.37**, **tutor 0.30**. Tutor is mostly by design (land fetches are ramp
here). Recursion is a genuine gap: the regex wants "return ... from a graveyard" and
misses "put target creature card from a graveyard onto the battlefield" — Reanimate,
Animate Dead, Victimize, Rise of the Dark Realms. Lands are the other systematic
source: `classify` short-circuits a land to `land` alone, so channel lands and modal
DFC spell halves never carry the function they also perform.
