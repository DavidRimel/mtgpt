# Commander brackets

WotC's bracket system describes what a deck is trying to do, so players can
match expectations before shuffling. The machine-readable rules are in
`mtgpt/brackets.py` (`RULES`); this table mirrors it exactly.

| Bracket | Name | Game Changers max | Mass land denial | Extra turns | Tutors | Two-card combos |
|---|---|---|---|---|---|---|
| 1 | Exhibition | 0 | excluded | watched | minimal | excluded |
| 2 | Core | 0 | excluded | watched | sparse | excluded |
| 3 | Upgraded | 3 | excluded | watched | unrestricted | excluded |
| 4 | Optimized | unrestricted | allowed | unrestricted | unrestricted | allowed |
| 5 | cEDH | unrestricted | allowed | unrestricted | unrestricted | allowed |

"Watched" means mtgpt raises a warning once extra-turn spell count reaches
`EXTRA_TURN_WARN_AT` (3); it does not mean extra turns are banned outright at
brackets 1-3, only that chaining them is against the bracket's intent.
"Minimal"/"sparse" tutor guidance triggers a warning once `tutor_count`
reaches `TUTOR_WARN_AT` (4). A Game Changers count over the bracket's max, or
mass land denial present where it isn't allowed, is an **error** (fails
`compliant`); extra-turn density and tutor density are **warnings** (do not
fail `compliant` on their own).

## The Game Changers list

Maintained by WotC and revised over time, so mtgpt fetches it from Scryfall's
`is:gamechanger` rather than hardcoding it. A deck that goes over its
bracket's allowance is reported as an error (`code: "game_changers"`) naming
the offending cards.

## What mtgpt checks, and what it does not

Checked from card data alone (via `mtgpt bracket` / `mtgpt report`):

- Game Changer count (`game_changers`)
- Mass land denial (`mass_land_denial`), from the `MASS_LAND_DENIAL` function tag
- Extra-turn spell density (`extra_turns`) — a count, which approximates
  "not chained," not a detection of chaining
- Tutor density (`tutor_count`), with land fetches excluded (they are tagged
  `ramp`, not `tutor`, in `mtgpt/classify.py`)

Not checked unless `report --combos` is passed:

- **Two-card infinite combos.** This needs Commander Spellbook data. Without
  `--combos`, every `bracket`/`report` call lists
  `"Two-card infinite combo detection requires Commander Spellbook (not
  supplied)."` in `deferred_checks`. A deck that passes at bracket 1 or 2
  without `--combos` may still be running an infinite combo.
- **Repeatable extra turns.** Even with `--combos` supplied, counting
  extra-turn spells is always a proxy for chaining, not a detection of it —
  `deferred_checks` names this gap (`EXTRA_TURN_APPROXIMATION`)
  unconditionally, in every report.

Say so when reporting a verdict. A bracket claim that hides a skipped rule is
worse than no claim.
