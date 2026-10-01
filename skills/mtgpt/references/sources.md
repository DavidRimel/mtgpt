# External source reachability — probed 2026-10-01

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
| **Archidekt** | `archidekt.com/api/decks/<id>/` | **Deck import by URL**, with quantities, `Commander` category, `edhBracket` |
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

## Design consequence

`otag:` changes how candidates should be found. `classify.py`'s regex exists to tag cards a user
*already has*; for *discovering* cards that fill a gap, a human-curated tag is strictly better and
cross-checks our own classification. Where they disagree, that disagreement is itself a signal.
