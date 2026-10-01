---
name: mtgpt
description: Use when building, auditing, or tuning a Magic the Gathering EDH/Commander deck - imports decks from an Archidekt URL, resolves every card against Scryfall, checks legality and color identity, measures ratios/curve/colored sources against deckbuilding targets, reports bracket compliance, finds combos, finds cards by community-curated function tag, diffs a deck against EDHREC's average build, and suggests additions from EDHREC synergy data. Triggers on "EDH", "Commander deck", "decklist", "tune my deck", "deck audit", "is this legal", "what bracket", "combo", "synergy", "archidekt", "what am I missing".
---

# mtgpt

Build and tune Commander decks on verified data: sixteen independently
callable operations (`card`, `search`, `find`, `classify`, `import`, `read`,
`validate`, `audit`, `bracket`, `report`, `compare`, `synergy`, `themes`,
`combos`, `card-combos`, `suggest`). See `python3 -m mtgpt.cli --help` for the
full list.

Every deck operation takes its list from `--file`, `--stdin`, or `--url` (an
Archidekt link).

## The rule that matters

**Never name a card from memory as a recommendation.** Every card you propose
must come back from `mtgpt card`, `mtgpt search`, `mtgpt find`, or
`mtgpt synergy` first.
Card names, oracle text, legality, and color identity are all things a model
misremembers confidently, and a decklist with a fake card in it is worse than
no decklist.

The toolkit makes this structural: a name that does not resolve against
Scryfall raises, and the CLI reports it as
`{"ok": false, "error": {"type": "UnresolvedCards", "names": [...]}}` with
exit code 2. If you see that, stop and ask the user — never substitute a
guess.

Run the tooling. Report what it says.

## The envelope

Every operation prints one JSON object:

```
{"ok": true,  "command": "<name>", "data": {...}}
{"ok": false, "command": "<name>", "error": {"type": "...", "message": "...", ...}}
```

Exit code is 0 on success, 2 on user error. Errors are data, not just prose:
`UnresolvedCards` carries `names`; `SourceUnavailable` (EDHREC or Commander
Spellbook down) carries `source`. `suggest` degrades instead of failing — if
EDHREC is unreachable it still returns the gap analysis, with the outage
named in `degraded`.

## Composition loops

Don't run commands in isolation — chain them. The commands below are real
and were run against this toolkit.

### Audit a deck

1. Get the list. If the user has an **Archidekt** link, fetch it — no pasting:

   ```bash
   python3 -m mtgpt.cli import "https://archidekt.com/decks/2000000/my-deck"
   ```

   Or skip `import` and pass `--url` straight to the operation you wanted.
   `import` also returns `declared_bracket`, the bracket the deck's author
   claimed — compare it against what `bracket` computes and say if they
   disagree. For **Moxfield** and everything else, ask the user to click
   **Export** and paste the text, or point at a saved file. A `--file` must be
   UTF-8; if mtgpt reports a `UnicodeDecodeError`, have the user re-save as
   UTF-8 or pipe the text in with `--stdin`.
2. Get the whole picture at once:

   ```bash
   python3 -m mtgpt.cli report --url "https://archidekt.com/decks/2000000/" --bracket 3 --text
   python3 -m mtgpt.cli report --file deck.txt --bracket 3 --text
   ```

   Drop `--text` for the JSON form when you need to reason over the
   structure rather than read it.
3. Or check one stage when only one question matters:
   `validate` for legality only, `audit` for ratios/curve/colored sources
   only, `bracket --target N` for the bracket verdict only.
4. Interpret, don't restate. The user can read numbers; say what they mean
   for how the deck plays. See
   `references/deckbuilding-hygiene.md` before arguing with a number the
   audit reports.

Default to bracket 3 if the user hasn't said what they're aiming at, and say
that you assumed it.

### Tune a deck

1. `python3 -m mtgpt.cli audit --file deck.txt` to find under-served
   categories (status `low` with a `delta`).
2. `python3 -m mtgpt.cli suggest --file deck.txt --bracket 3 --limit 10` for
   ranked candidates, each with a `reason` citing which gap it fills and its
   EDHREC inclusion rate. `suggest` already filters to color identity,
   legality, and the bracket's Game Changer allowance.
3. Widen the candidate pool past this commander's own EDHREC page with
   `find`, which searches Scryfall's community-curated function tags:

   ```bash
   python3 -m mtgpt.cli find ramp    --identity wubg --limit 10
   python3 -m mtgpt.cli find removal --identity wubg --limit 10
   ```

   `find` answers "what cards do this job in these colours", ordered by how
   often they are played. `synergy` answers "what do players put in *this*
   deck". Use `find` for the gap, `synergy` for the theme. `--query` ANDs in
   extra Scryfall terms (`--query "usd<5"`). Run `find` with no argument you
   have not seen in `--help`: only probed tags are accepted, and a typo is
   answered with the full vocabulary.
4. Diagnose what the typical build plays that this one does not:

   ```bash
   python3 -m mtgpt.cli compare --file deck.txt
   ```

   `compare` diffs the deck against EDHREC's average build of its commander and
   returns `overlap_pct`, `missing_from_yours` (each tagged with the
   `functions` it would fill, so you can cross it with the audit's gaps) and
   `unique_to_yours`. **The average deck is a popularity artefact, not a
   correct deck.** A low overlap is not a fault, and `unique_to_yours` is
   usually where the deck's actual ideas live. Use it to find *omissions worth
   explaining*, not a list to converge on.
5. Vet anything you're considering that `suggest` did not surface:
   `python3 -m mtgpt.cli card "<name>"` or
   `python3 -m mtgpt.cli classify "<name1>" "<name2>"` before naming it.
6. Re-run `audit` (or `report --text`) after a swap to confirm the gap
   actually closed.

### Check a bracket honestly

```bash
python3 -m mtgpt.cli bracket --file deck.txt --target 3
python3 -m mtgpt.cli report  --file deck.txt --bracket 3 --combos
```

Every bracket report carries `deferred_checks`: without `--combos`, it
always includes the two-card infinite combo gap, because that check needs
Commander Spellbook data the plain `bracket`/`report` calls don't fetch.
**Do not claim a deck is bracket-legal while that line is present.** Add
`--combos` (report only; slower — roughly one Spellbook request per distinct
card, about 7 seconds for a 100-card deck) when the combo rule actually
matters, e.g. for brackets 1-3 where two-card infinites are excluded.

### Explore a commander before building

```bash
python3 -m mtgpt.cli themes  "Atraxa, Praetors' Voice"
python3 -m mtgpt.cli synergy "Atraxa, Praetors' Voice" --variant upgraded --limit 20
```

`themes` shows the archetypes this commander is actually built as and the
bracket spread of recorded decks. `synergy` returns Scryfall-verified
candidates with `synergy` score and `inclusion_rate`; `--variant` narrows to
`budget`, `expensive`, `upgraded`, or `cedh` builds.

Then build the packages out by function, in the commander's identity:

```bash
python3 -m mtgpt.cli find sacrifice_outlet --identity bg --limit 15
python3 -m mtgpt.cli find graveyard_hate   --identity bg --limit 10
```

The tag vocabulary covers more than the audit's categories — `wheel`,
`theft`, `sacrifice_outlet`, `untapper`, `blink`, `landfall`, `pillowfort`,
`group_hug` and others are all searchable, which is how you assemble a
*package* rather than a pile of individually good cards. `--help` lists them
all.

### Investigate a combo

```bash
python3 -m mtgpt.cli card-combos "Thassa's Oracle"
python3 -m mtgpt.cli combos --file deck.txt
```

`card-combos` lists every known combo using one card. `combos --file` checks
what a specific decklist actually assembles — only combos whose every piece
is present in the deck.

## Reading the numbers, not restating them

- **A category is LOW** — name the specific cards worth adding (via
  `suggest`, or `search`/`card` to vet your own idea), not just "add more
  removal."
- **The curve is HIGH** — look for expensive cards that don't advance the
  commander's plan, rather than cutting the biggest mana values by reflex.
- **Colored sources are SHORT** — this is a mana base problem, not a spell
  problem. Fix the lands before touching the spell list. See
  `references/deckbuilding-hygiene.md` for the source-count table.
- **`tutor_count` triggers a bracket warning** — this is about combo
  assembly speed, not card selection taste. Land fetches are deliberately
  excluded from the tutor count (they're counted as ramp instead).

## When the human tags and our regex disagree

`find` returns cards a person tagged; `classify` tags by regex. Each card from
`find` carries `agrees_with_classify`. **Say so when they disagree — do not
silently pick one.**

### Two numbers, and never one of them alone

`find` reports `recall_estimate`: of the cards the community tagged, the share
`classify` also tagged. **That number cannot see a false positive.** It samples
what Tagger labelled, so a regex that tags half the format would still score
well on it. Reading an unlabelled 0.67 as "67% accurate" once hid a classifier
sitting at 0.92 precision with 135 false positives.

For the other direction, and for both at once:

```bash
python3 -m mtgpt.cli cross-check recursion --identity wubrg
python3 -m mtgpt.cli cross-check recursion --direction precision
```

`cross-check` reports `recall_estimate` and `precision_estimate` side by side,
each with a `measures` string saying what it is. **If you quote one, name which
one.** Both are small samples; `references/sources.md` gives the bulk-data method
that measures them over all 32,116 commander-legal cards.

### Where `classify` actually stands

Scored over the whole corpus, not a sample:

| function | recall | precision | false positives |
|---|---|---|---|
| extra_turns | 0.94 | 1.00 | 0 |
| counterspell | 0.83 | 1.00 | 1 |
| tutor | 0.41 | 0.99 | 5 |
| recursion | 0.72 | 0.98 | 25 |
| draw | 0.88 | 0.96 | 133 |
| spot_removal | 0.60 | 0.89 | 402 |
| sweeper | 0.67 | 0.84 | 115 |
| ramp | 0.72 | 0.81 | 365 |
| mass_land_denial | 0.21 | 0.69 | 10 |
| **protection** | 0.55 | **0.66** | 356 |
| **wincon** | 0.86 | **0.57** | 40 |

Read that table before trusting a band. `protection` and `wincon` are the two
weak ones: roughly a third of what `classify` calls protection, and nearly half
of what it calls a win condition, the community does not. If the audit says a
deck is well served on protection, check the tags in `report --text` rather than
believing the count.

### Four causes of disagreement, worth telling apart

- **A deliberate difference.** `otag:tutor` includes land fetches; mtgpt counts
  Cultivate as ramp on purpose, because the bracket rules use tutor density as a
  combo-assembly measure. That is most of the tutor recall gap, and tutor
  precision is 0.99 — the regex is not loose, it is narrow by design.
- **A broader tag.** `otag:mass-land-denial` covers land *locks* — Winter Orb,
  Blood Moon, Back to Basics — while `classify` matches only destruction and mass
  sacrifice. Use `find mass_land_denial` to discover these; keep `classify` for
  the bracket rule, which is written about destruction. Do not expect agreement.
- **A land.** `classify` tags a land `land` and nothing else, so channel lands
  (Boseiju, Otawara, Takenuma) and utility lands (Academy Ruins, Buried Ruin)
  never register the function they also perform. Expect them in every
  disagreement list.
- **A split or adventure back face.** `classify` reads the front face only. For a
  modal DFC that is correct — the back is a land. For a **split** or **adventure**
  card it loses a real half: `Dusk // Dawn` is tagged `sweeper` and loses Dawn's
  recursion, `Bonecrusher Giant // Stomp` loses Stomp's removal. If a deck leans
  on adventure creatures for interaction, the removal count is low by that much.

## Research beyond the toolkit

The toolkit gives verified data. It does not give you *why* a card is good in an
archetype, what a commander's standard packages are called, or how a line
actually plays. For that, use `WebSearch` and `WebFetch` — then bring any card
name you find back through `mtgpt card` before repeating it.

Prefer these sources, which are reachable and worth reading:

- `edhrec.com` — the HTML pages behind the JSON, including theme write-ups
- `commandersherald.com` — primers and strategy articles
- `mtggoldfish.com` — metagame and archetype coverage
- `tcgplayer.com/content` — set and archetype articles
- `mtgtop8.com` — competitive (cEDH) decklists

**Distrust SEO content farms.** A probe of ten search results found three from a
single content mill: pages that restate a card's oracle text, rank "top 10
commanders" with no reasoning, and cite nothing. If a page has no author, no
argument, and no decklist, it is not evidence — drop it rather than quoting it.

**Reddit is not reachable.** Anthropic's crawler is banned by Reddit's policy, so
a script, `WebFetch`, and `WebSearch` all refuse — there is no workaround from
here. If the user wants r/EDH or r/CompetitiveEDH discussion taken into account,
say plainly that you cannot fetch it and ask them to paste the thread.

## Honesty requirements

Every bracket report ends with what it did **not** check
(`deferred_checks`). Surface that list to the user rather than hiding it.
When `suggest` or any EDHREC-backed call returns a non-empty `degraded`
list, say so — a partial answer presented as complete is worse than a
visibly partial one. See `references/brackets.md` for exactly what is and
isn't checked and why.

## What does not exist

Do not improvise these by hand:

- **No Moxfield URL fetching.** Moxfield serves scripted requests a
  Cloudflare challenge; the user must paste the Export text or a saved file.
  The blockage is reversible, so say so if the user asks: fetching needs a
  real browser, and Chromium cannot launch here without four system libraries
  that require root to install (`libnspr4`, `libnss3`, `libnssutil3`,
  `libasound2`) — see the README's "Moxfield URL fetching" section.
  **Archidekt links do work** (`import`, or `--url`), as does a bare Archidekt
  deck id. TappedOut, Aetherhub, Deckstats and mtgdecks.net are all bot-blocked.
- **No deck-from-URL for anything but Archidekt**, and `declared_bracket` from
  an import is the author's claim, not a verdict — run `bracket` for that.
- **No corpus scoring from the CLI.** `cross-check` samples; measuring a
  classifier properly means the Scryfall bulk exports, and
  `references/sources.md` says how.
- **No goldfish simulation.**
- **No deck-from-scratch generation** — `synergy`/`themes` inform a build,
  but nothing assembles a full 99 automatically.
- **Classification is heuristic.** `report --text`'s `CARD TAGS` section
  exists precisely so a wrong tag is visible and correctable, not hidden
  inside a ratio.

## Reference material

Load these only when the question calls for them:

- `references/deckbuilding-hygiene.md` — what each target means and why,
  and the colored-source table. Read this before arguing with a number the
  audit reports.
- `references/brackets.md` — the bracket 1-5 table and exactly what mtgpt
  checks versus defers.
- `references/sources.md` — which external sources are reachable, which are
  blocked, and what each one gives us. Read it before trying to fetch anything
  the toolkit does not already wrap.
