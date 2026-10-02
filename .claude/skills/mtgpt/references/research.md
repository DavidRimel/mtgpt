# Stage 2 — research how this commander is built

Every candidate comes from the toolkit, never from memory, and is verified on
Scryfall before it goes in `research.md`.

## Sources, in order

1. **EDHREC**
   - `python3 -m mtgpt.cli synergy "<commander>" --limit 60` — high-synergy cards and inclusion rates.
   - `python3 -m mtgpt.cli themes "<commander>"` — how the commander is usually built.
   - `python3 -m mtgpt.cli compare --file decks/<slug>/<best>.txt` — what the average deck
     plays that this list does not, tagged by function.
2. **Commander Spellbook — the commander only.**
   `python3 -m mtgpt.cli card-combos "<commander>" > decks/<slug>/combos.json`.
   This file feeds the floor check. Never submit a deck to Spellbook, and do not
   run `combos --file` here.
3. **Function packages** — `python3 -m mtgpt.cli find <function> --identity <colors> --limit 15`
   for each job the deck needs (`ramp`, `protection`, `sacrifice_outlet`, `landfall`, …;
   `find --help` lists them).
4. **Primers and tuned lists** — web search for "<commander> primer" and recent tuned
   lists, from the sources in `references/sources.md`. Sites that block scripts are read
   through the Chrome MCP (load the `claude-in-chrome` skill first).

## The deck's jobs

Before listing candidates, write at the top of `research.md` the jobs this deck
needs, from the goal, the commander's text, and what the sources show. For a
landfall commander: ramp, land recursion, landfall triggers, protection, removal.
For Hapatra: -1/-1 counter placers, token-on-counter payoffs, drains, sac
outlets, protection, removal.

## The multi-job flag

A candidate is **multi-job** when it covers two or more of *this deck's* jobs. It
is a judgment against the jobs list, informed by `classify` tags — not a test for
"modal" or "MDFC". A creature that replays lands from the graveyard and has a
landfall trigger is multi-job in a landfall deck and single-job in a
spellslinger deck. Multi-job candidates are listed first in every group and are
preferred whenever the loop picks a card to add.

## Verify every candidate

- `python3 -m mtgpt.cli classify "<name>" ...` — real card, its functions.
- In the commander's color identity.
- Bracket: no mass land denial below bracket 4; count Game Changers against the
  bracket's allowance (`references/brackets.md`).

## Pre-scan

Run stage 1 on the candidates now (`goldfish-scan` a scratch list of them, then
`card-rule` each `needs_review` card), so the tuning loop never stops on an unmodeled
card. Leave out `--goal`: a goal file names cards that must be in the scanned list, so
it fails on a candidates-only list.

## research.md format

    # <name> — research
    ## Deck jobs
    - ramp; -1/-1 placers; token payoffs; drains; sac outlets; protection; removal
    ## Candidates
    ### Protection
    - **Card Name** — multi-job (protection, sac outlet) — EDHREC 41% — sources: EDHREC, primer X — cut: Weak Card

Every candidate line names its sources and a suggested cut.
