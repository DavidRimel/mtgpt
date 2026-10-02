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

## Measuring `classify` against Tagger: use the bulk data, not a sample

Scryfall publishes the **entire Tagger vocabulary** as bulk data, which makes this
measurable rather than sampleable. Two files, both listed at
`api.scryfall.com/bulk-data`:

- `oracle_tags` — all 4,559 oracle tags, each with its `slug`, `aliases`,
  `child_ids` and the `taggings` (oracle ids) attached to it.
- `oracle_cards` — one card object per oracle id.

`otag:<slug>` is **hierarchical**: `otag:recursion` has no taggings of its own and
resolves through 19 children. To reproduce a search, walk the child graph from the
root tag and union the taggings. Aliases are normalised, which is why
`otag:graveyard-hate` works for the tag whose slug is `hate-graveyard`.

**Recall and precision fail independently, and a sample of `otag:` hits can only
measure recall.** `mtgpt find` samples cards the community tagged and asks whether
`classify` agrees — it is blind to cards `classify` tagged that nobody else did.
A recursion regex once scored recall 0.73 and was reported as sound; scored over
all 32,116 commander-legal cards it had 135 false positives at precision 0.92,
including graveyard-hate and graveyard-cost cards, which are the semantic inverse.
`mtgpt cross-check <function>` reports both directions; this corpus method is what
settles an argument.

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

`mass-land-denial` resolves too — 106 commander-legal cards (Armageddon,
Apocalypse, Acid Rain, Ajani Vengeant). It was recorded here as having no
equivalent, which was wrong: four *other* spellings 404 and the one that works is
our own `Function` value. The tag is **broader than our regex** — it includes land
locks (Winter Orb, Blood Moon, Back to Basics) where `classify` matches only
destruction, so the two score 0.21 recall at 0.69 precision against each other by
design.

Rejected — every one 404: `stax`, `token-generation`, `tokens`, `token`,
`cost-reduction`, `land-destruction`, `mass-land-destruction`, `mld`, `land-hate`,
`resource-denial`, `mana-denial`, `taxing`, `haste-enabler`, `card-selection`,
`creature-tutor`, `land-tutor`, `free-spell`, `infinite-combo`, `combo`, `wincon`,
`extra-turns`, `mana-ritual`, `treasure`, `reanimation`, `proliferate`,
`stack-interaction`, `indestructible`, `hexproof-granter`,
`counterspell-protection`, `fixing`, `mana-fixing`, `color-fixing`.

Note that a 404 means "no such tag OR no matching cards" — the two are
indistinguishable, which is why `tagger.TAGS` ships only probed tags and why
`mass-land-denial` was wrongly written off on four failed guesses.

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

Scored against all 32,116 commander-legal cards (recall / precision):

| function | recall | precision | FP | function | recall | precision | FP |
|---|---|---|---|---|---|---|---|
| extra_turns | 0.94 | 1.00 | 0 | spot_removal | 0.60 | 0.89 | 402 |
| counterspell | 0.83 | 1.00 | 1 | sweeper | 0.67 | 0.84 | 115 |
| tutor | 0.41 | 0.99 | 5 | ramp | 0.72 | 0.81 | 365 |
| recursion | 0.72 | 0.98 | 25 | mass_land_denial | 0.21 | 0.69 | 10 |
| draw | 0.88 | 0.96 | 133 | protection | 0.55 | 0.66 | 356 |
| | | | | wincon | 0.86 | 0.57 | 40 |

`protection` (0.66) and `wincon` (0.57) are the weakest and are the next things
worth fixing: a third of what we call protection, and nearly half of what we call
a win condition, the community does not. `tutor`'s low recall is by design — land
fetches are ramp here, for the bracket rule — and its precision of 0.99 shows the
regex is narrow rather than loose.

Two structural sources of disagreement, both in `classify` and both deliberate:
a land is tagged `land` and nothing else, so channel and utility lands never carry
their other function; and only the FRONT face is read. Front-face-only is right
for a modal DFC, whose back is a land, but it loses a real half of a **split** or
**adventure** card — `Dusk // Dawn` loses Dawn's recursion and
`Bonecrusher Giant // Stomp` loses Stomp's removal.
