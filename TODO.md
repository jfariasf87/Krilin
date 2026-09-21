# TODO

Iteration 2 (2026-09-21) delivered the plan in `.claude/plans` — status below. Follow the gates in `CLAUDE.md`; every loop-policy item needs `scripts/evaluate.py` evidence and a line in `docs/validation.md`.

## Done in iteration 2

- [x] Selector contract v2: `resource_id | text | text_contains | description | description_contains | role | checked`, package-scoped, exactly-one match; `absent: true`; `inputs: [{target, text}]` with `text_values` kept as shorthand; ambiguity reported, never guessed.
- [x] Compact Jev state (`Snapshot.compact()`), lean `criteria`, usage totals on results.
- [x] Scenarios as data with a code-executed `launch` precondition; CLI `run --scenario`; MCP `android_run_scenario`.
- [x] `observe --brief [--package]`; escalation diagnostics (`unmet_assertions` with nearest elements and hints, `ambiguous_inputs`, `candidates`, `screens`).
- [x] Complexity fixture (`FixtureActivity`, ID-less `FixtureDetailActivity`, own `:fixture` process) and six scenarios under `examples/fixture/`.
- [x] `scripts/evaluate.py` (success rate, steps, calls, cost, escalation reasons; `--record`), `krilin.replay`, one sanitized live recording under `tests/fixtures/`.
- [x] Loop policy, each from a reproducible fixture failure (see `docs/validation.md`): stale re-dispatch without re-deciding; stale records hidden from Jev; wait-aware cycle guard (`max_waits`); rounding-aware probability check; effect grace and idle polling (`max_effect_waits`); polling never spends steps; empty active package = transition; one provider retry on 5xx/timeout.
- [x] Companion settling: wait for the first event an accepted action causes (≤500 ms), then a 150 ms quiet window (≤500 ms); observe uses the full request budget, act keeps the 1.5 s dispatch cap.
- [x] Result: 18/18 fixture runs verified (3 per scenario, medians 2.7–5.4 s, $0.004 total); in a 30-run sample every failure was a provider 503/timeout with no action executed.
- [x] Docs: README (selectors, scenarios, fixture, evaluation), architecture (settling), validation (findings, numbers, incidents), CONTRIBUTING (evaluate workflow), CLAUDE.md; version 0.2.0.

## Next

- [ ] **Pixan under the new contract.** Author `examples/pixan/*.json` from `observe --brief --package com.movvem.pixan2` on emulator B (language switch, time zone, send message — the flows from the reverted session's traces), run them with `evaluate.py`, record results. Needs the app logged in / seeded; ask the user what state it should start from.
- [ ] **Thresholds, with data.** All 69 accepted decisions in the last sample had confidence ≥ 0.85; the escalations that did occur at 0.35–0.78 were on pre-effect or content-less screens that the runner now waits out. Keep 0.8 until Pixan runs say otherwise; if they do, expose `min_confidence`/`min_probability` per step and document the evidence.
- [ ] **Stale rate.** 44 of 107 device actions were first rejected as stale (dialog/screen animations) and re-dispatched silently. Measure whether a longer quiet window after `WINDOWS_CHANGED` events lowers it without hiding real changes; only if the extra latency is worth it.
- [ ] **CI.** The GitHub matrix (Windows/Linux/macOS × 3.11/3.14 + Android build/lint) has never been run remotely. Push and fix what breaks.
- [ ] **Lint.** `PluralsCandidate` on the fixture strings; decide whether to use plurals or suppress.
- [ ] **Protocol 2 only on demand:** `ime_enter` (submit-on-keyboard fields, common in Flutter `onSubmitted`), `long_click`, `set_progress` + `range` — each only when a scenario needs it, through the protocol-change checklist.
- [ ] Optional data point: one Chrome search scenario as the "largest tree" case for the 24 KB budget and the candidate cap.

## Explicitly not planned

Screenshots / vision / OCR, coordinate or gesture input, scripted macro actions, model-generated text, model-planned app launches, hierarchical/paged observation (unless a real tree cannot fit), multi-device registry, resumable workflows, TalkBack gesture/speech driver, migrating off `unittest`.
