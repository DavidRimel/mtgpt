# mtgpt — Design

**Date:** 2026-09-30
**Status:** Approved for phased implementation
**Scope:** A Claude Code plugin that builds and tunes Magic: The Gathering Commander decks.

## Problem

Asking an LLM to build or tune a Commander deck fails in predictable ways. It invents
cards that do not exist, misremembers oracle text, breaks color identity, ignores the
banned list, and offers "upgrades" with no evidence behind them. It also reasons about
deck composition by vibes rather than by numbers, so mana bases and interaction counts
drift without anyone noticing.

mtgpt fixes this by separating two kinds of work:

- **Scripts establish facts.** Every card is resolved against Scryfall before it reaches
  the user. A hallucinated or illegal card becomes structurally impossible to recommend,
  not merely unlikely.
- **The model makes decisions.** Archetype reading, ambiguous function-tagging, cut/add
  judgment, and synergy narrative stay with Claude, informed by verified data.

## Non-goals

- Formats other than Commander/EDH.
- Budget ceilings or owned-collection gating. Constraint enforcement is bracket-based only.
- Full game simulation with opponents and interaction.
- Any authenticated write to Moxfield. mtgpt reads decks; the user imports the output.

## Data sources

All endpoints below were probed live on 2026-09-30; status reflects verified behavior,
not documentation.

| Source | Endpoint | Status | Role |
|---|---|---|---|
| Scryfall | `api.scryfall.com` | Works | Card truth: oracle text, legality, color identity, MV, prices, `is:gamechanger` (53 cards) |
| EDHREC | `json.edhrec.com` | Works (unofficial) | Synergy scores, inclusion rates, staples by category, `tag_counts`, `bracket_counts`, bracket variant pages |
| Commander Spellbook | `backend.commanderspellbook.com` | Works (unofficial) | Combo detection with `bracketTag` and `salt` per combo |
| Moxfield | `api2.moxfield.com` | **403 Cloudflare** | Requires Playwright browser fetch; paste-parser fallback |
| Reddit | any | **Crawler banned** | Unavailable to script, WebFetch, and WebSearch alike. Substituted by curated WebSearch. |

### Notes on the two blocked sources

**Moxfield** returns a Cloudflare challenge page for scripted requests, including with a
browser User-Agent. This is host-level, so no header tuning defeats it. The design drives
a real headless browser to perform the export, and always ships a paste-parser that shares
the same parsing core, so deck ingestion never depends on the browser working.

**Reddit** blocks Anthropic's crawler by policy, which rules out scripts, `WebFetch`, and
`WebSearch` equally. The substitute is `WebSearch` restricted to a curated allowlist of
sources that carry discussion-grade content, with SEO-farm domains blocklisted. The
tradeoff is explicit: Reddit's specific voice is lost; the "what do strong builders do
with this commander" signal is retained.

## Interface: an agent-callable toolkit, not a pipeline

**Requirement added 2026-09-30, mid-implementation, at the user's direction.** mtgpt is a set
of utility operations an agent composes, not a single command that runs a fixed script.

The distinction is not cosmetic. A monolithic `audit` command forces one order of operations
and one granularity, so Claude cannot look up a single card, classify three candidate
replacements, or re-check only the bracket after a swap without re-running everything. Tuning
a deck is inherently iterative — measure, hypothesize, check a candidate, re-measure — and an
interface that only does the whole thing at once cannot support that loop.

Every stage is therefore exposed as its own operation, each:

- **independently callable** with the smallest input it needs,
- **JSON by default**, in a predictable envelope, so output feeds the next decision,
- **structurally honest about failure** — errors name the offending values in machine-readable
  form rather than printing prose,
- **stateless**, so no call depends on a previous one having run.

The pure per-stage functions of Tasks 1-7 already satisfy this internally; what the interface
layer adds is a stable facade, consistent serialization, and a documented surface.

Composition is the skill's job, not the code's. `SKILL.md` teaches the loops — audit, read the
gaps, search for candidates, classify them to confirm they fill the gap, re-audit — rather
than hiding them behind one entry point.

A future MCP server could wrap the same facade. It is not part of Layer 1: the CLI-plus-JSON
surface is what a Claude Code agent reaches for natively, and the facade is deliberately
shaped so an MCP layer would be a thin adapter rather than a rewrite.

## Repository layout

```
mtgpt/
├── .claude-plugin/
│   ├── marketplace.json        # installable via /plugin marketplace add DavidRimel/mtgpt
│   └── plugin.json
├── skills/mtgpt/
│   ├── SKILL.md                # router: build | tune | audit
│   └── references/
│       ├── deckbuilding-hygiene.md
│       ├── brackets.md
│       └── sources.md
├── scripts/
│   ├── deckparse.py
│   ├── moxfield.py
│   ├── scryfall.py
│   ├── edhrec.py
│   ├── spellbook.py
│   ├── classify.py
│   ├── audit.py
│   ├── goldfish.py
│   └── validate.py
├── decks/                      # optional snapshots; tuning becomes a git diff
├── tests/
└── README.md
```

A single skill, not one per verb. `SKILL.md` stays short and routes to workflows, loading
reference files on demand. Separate build/tune skills would compete for triggering on the
same phrasing.

## Pipeline

### Tune an existing deck

1. **Ingest** — Moxfield URL via Playwright, or pasted text. Both feed `deckparse.py`.
2. **Resolve** — batch through Scryfall `/cards/collection` (75 names per request). Any
   unresolved name is a hard stop with the offending name echoed, never a silent guess.
3. **Validate** — 100 cards, singleton, color identity against the commander, banned list.
4. **Classify** — function-tag every card: land, ramp, draw, spot removal, sweeper, tutor,
   counterspell, protection, wincon, synergy piece. Oracle-text rules plus EDHREC category
   cross-reference; Claude adjudicates the ambiguous remainder.
5. **Audit** — counts against targets, mana curve histogram, colored-pip demand against
   available sources.
6. **Bracket check** — Game Changer count, tutor density, mass land denial, two-card
   infinite combos, compared against the target bracket.
7. **Combos** — Commander Spellbook scan of the resolved list; each combo's `bracketTag`
   checked against the target.
8. **Ideas** — EDHREC synergy and inclusion data filtered to the bracket variant, plus
   curated `WebSearch`.
9. **Goldfish** — Monte-Carlo opening hands and turns 1-4.
10. **Recommend** — ranked cuts and adds, each with a one-line reason and supporting
    evidence. Every proposed add is pre-validated for legality and color identity.

### Build a new deck

Same spine, entered from a commander instead of a list: `tag_counts` proposes the
archetype, the hygiene template sets target ratios, a candidate pool is drafted to 99,
then steps 4-10 run against the draft and iterate until the numbers land.

## Deckbuilding hygiene

Targets are encoded as checked numbers so drift is visible rather than arguable.
`audit.py` reports each as actual-versus-target with a flag when out of band.

| Category | Target | Flexes with |
|---|---|---|
| Lands | 36-38 | Curve and ramp count |
| Ramp | 10-12 | Curve; higher for expensive decks |
| Card draw / advantage | 8-12 | Commander's own card flow |
| Spot removal | 5-8 | Table expectations |
| Sweepers | 2-3 | Whether the deck goes wide itself |
| Targeted protection | 3-5 | Reliance on the commander |
| Total mana sources | 46-50 | Lands + ramp combined |
| Average mana value | 2.8-3.2 | Archetype |

Colored-source minimums per pip count follow Karsten-derived thresholds, scaled for a
100-card singleton deck, so a deck is flagged when it wants a given color earlier than its
mana base can reliably supply it.

## Error handling

Unofficial endpoints fail loudly. Each script distinguishes:

- **Hard stop** — unresolvable card name, deck not 100 cards, color identity violation.
  The run halts with a specific message.
- **Degraded** — EDHREC or Spellbook unreachable. The audit still completes; the report
  states which layer was unavailable rather than quietly omitting it.
- **Fallback** — Moxfield browser fetch fails. The user is asked to paste the export, and
  the pipeline continues unchanged.

No script returns empty results on failure in a way that reads as a clean bill of health.

## Testing

Scripts are unit-tested against recorded fixtures rather than live endpoints, so the suite
is deterministic and runnable offline. Fixtures are captured from real responses. Coverage
targets the logic most likely to be wrong quietly:

- `deckparse.py` — Moxfield export format, quantities, set codes, category and commander
  tags, sideboard and maybeboard exclusion.
- `validate.py` — color identity edge cases, singleton exceptions for basic lands.
- `classify.py` — cards with several functions, modal spells.
- `audit.py` — ratio math and pip accounting.
- `goldfish.py` — a fixed seed produces stable statistics.

## Phasing

Approved for three sequential layers. Note that build order is not runtime order: the
pipeline step numbers above describe the order a tuning run executes, while the layers
below describe the order the code gets written.

**Layer 1 — deterministic spine.** Pipeline steps 1-6: ingest, resolve, validate, classify,
audit, bracket check. Useful on its own as a tuning tool. Also includes the plugin
scaffold, `SKILL.md`, the hygiene and bracket references, and tests.

**Layer 2 — external ideas.** Pipeline steps 7, 8, and 10: Commander Spellbook combo
detection, EDHREC synergy recommendations, curated `WebSearch`, and the ranked cut/add
recommender.

**Layer 3 — goldfish.** Pipeline step 9: Monte-Carlo simulation of opening hands and
early turns, plus its contribution to the land and ramp verdicts in the report.

## Risks

1. **Playwright on WSL2.** `pip install playwright` needs no elevation, but Chromium's
   system libraries generally do, and `sudo` requires a password in this environment.
   Verified before the Moxfield fetcher is built. If unavailable, the export/paste path
   stands alone and the browser fetcher is deferred rather than half-delivered.
2. **Unofficial endpoints.** EDHREC and Commander Spellbook offer no compatibility
   contract. Mitigated by loud failures, recorded fixtures, and a pipeline that degrades
   to Scryfall-only.
3. **Classification accuracy.** Function-tagging from oracle text is heuristic. Mitigated
   by EDHREC cross-reference and by surfacing tags in the report so a bad tag is visible
   and correctable rather than silently skewing the ratios.
