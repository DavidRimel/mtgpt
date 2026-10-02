# Stage 3 — the tuning loop

## The scorecard

    python3 -m mtgpt.cli scorecard --file decks/<slug>/<best>.txt --goal decks/<slug>/goal.json --bracket <N>

reports wins by the target round (primary), win rate, commander on curve,
interaction held while live (`covered`), opponent win attempts answered and
losses to them, protection, mulligan rate and its causes, untapped-land share,
short colors, `weaknesses` (worst first), and `card_impact` per card.

| Target | Level |
|---|---|
| Commander on curve | ≥ 70% |
| Interaction while live (`covered`) | ≥ 50% |
| Mulligan rate | ≤ 25% |
| Untapped lands | ≥ 80%, no short color |

## Judging a swap

    python3 -m mtgpt.cli scorecard --file decks/<slug>/<best>.txt --file decks/<slug>/<new>.txt \
        --goal decks/<slug>/goal.json --bracket <N> --combos decks/<slug>/combos.json

- **rejected** — the floors failed (a category below its audit minimum, or a new bracket
  error). Nothing was simmed. Pick a different cut.
- **keep** — primary up ≥ 1.5 points, no guard broken. `project best <slug> <new> --primary <value>`.
- **revert** — primary down, or a guard broken (on-curve −3, covered −3, opponent-win
  losses +2, mulligan +3 points, or a color newly short).
- **mixed** — anything else. Decide, and write why in the log: prefer multi-job cards and
  the weakest target.

Close calls (primary moved < 3 points) are re-run at 3000 games automatically;
`games` in the output says which count the verdict used. Bracket warnings (a
two-card combo at bracket 3) come back under `floors.warnings`: mention them.

## Choosing the cut

From the best version's `card_impact`: the lowest `win_delta` / highest
`dead_rate` card that is single-job, `measurable: true`, not a combo piece, and
not a land a short color needs. A card with `measurable: false` is never cut on
its numbers — its value (removal, counters) is invisible to a goldfish.

## The loop

1. Baseline: scorecard the best version; name the top weakness.
2. Pick a swap: in — a `research.md` candidate for that weakness, multi-job first;
   out — chosen as above.
3. Write the new list, `project save <slug> --file new.txt --note "+In -Out (why)"`.
4. Judge it against the best version; act on the verdict.
5. Log it: `project log <slug> "v12 vs v8: keep +2.1 (on-curve 68→72) — +In -Out"`.
6. One swap at a time; pairs only when the cards need each other.
7. Once per checkpoint round, test ±1 land the same way.
8. **Checkpoint** after 10 swaps tried or 3 non-keeps in a row: show the best
   version's scorecard against the round's starting one, the kept swaps and why,
   sim gaps hit; ask continue, change direction, or stop.

## Finishing

1. Land pass: swap tapped lands for untapped ones of the same colors (and fix
   short colors), each judged by the scorecard.
2. Pilot spot-check: pilot games 0–2 of seed 1 (`goldfish-new --seed 1 --game N`,
   `references/goldfish.md`) and report where your line beat or lost to the
   heuristic on the same deal. A big gap is a likely heuristic blind spot: log it.
3. Final report: best version against `v1`, every kept swap and why, sim gaps,
   and the list in paste-ready form for Moxfield/Archidekt import.
4. `project stage <slug> finish`.

## Noise floor

(Filled in by the implementation's calibration run — Task 12.)
