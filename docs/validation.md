# Validation

Everything here was run locally on Windows 11 with Python 3.14, JDK 17 (Temurin), one Android emulator at a time, and the OpenRouter key in `.env`. These are development measurements on a small fixture, not a benchmark: one emulator, one model version, and a provider in alpha.

## Iteration 2 — fixture scenarios with live Jev (2026-09-21)

Emulator `flutter_emulator_b` (`emulator-5556`, API 35, 2.5 GB RAM). Model reported by the provider: `typesafe/jev-1.13-20260917`. Thresholds stayed at 0.8 confidence / 0.8 selected probability throughout.

The companion's `FixtureActivity` is an offline notes app (30 seeded rows, 3 done) with a modal "What's new" dialog on every fresh launch, a Sync button with a 1.5 s spinner, an add-note dialog with validation, a Hide-done filter, an off-screen row, and an ID-less detail screen (Flutter-style: labels only). One scenario per pattern lives in `examples/fixture/`; every scenario starts by dismissing the modal. The fixture runs in its own process (`:fixture`), so launches are real cold/warm starts.

### Results

`python scripts/evaluate.py --bridge-config .local/emulator-b.json --live --record --runs 3`, after the loop-policy changes below, on a freshly booted emulator:

| Scenario | Steps | Runs | Succeeded | Median ms | Mean runner actions | Mean Jev calls | Cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dismiss-and-sync | 2 | 3 | 3 | 4,212 | 6.0 | 5.0 | 0.0013 |
| hide-done | 2 | 3 | 3 | 2,710 | 2.7 | 2.0 | 0.0004 |
| add-note-validation | 4 | 3 | 3 | 5,430 | 9.0 | 5.0 | 0.0008 |
| scroll-to-row | 2 | 3 | 3 | 2,729 | 3.0 | 2.0 | 0.0004 |
| detail-toggle | 4 | 3 | 3 | 4,483 | 6.3 | 4.0 | 0.0006 |
| delete-with-confirm | 4 | 3 | 3 | 4,479 | 7.0 | 4.0 | 0.0006 |

18/18 scenario runs verified all their assertions; total reported cost 0.0041 USD. Across those runs: observation median 155 ms (p90 310 ms), Jev decision median 272 ms (p90 367 ms), device action median 37 ms. Per-call cost ranged 0.000036–0.00010 USD.

A larger sample (`--runs 5`, 30 scenario runs, same evening) finished 18/30. All 12 failures were provider errors — HTTP 503 or a request timeout even after one retry — with **no action executed**; none came from Krilin's own policy. The 69 decisions the provider did answer had a minimum confidence of 0.85 and a minimum selected probability of 0.88 (56 clicks, 5 text replacements, 2 scrolls, 6 deliberate waits). 44 of the 107 device actions were first rejected as `stale_snapshot` because a dialog or screen was still animating; every one was re-dispatched without a model call once the semantic UI proved unchanged.

The recording `tests/fixtures/fixture-scroll-to-row.jsonl` is one of these live runs, reduced to the app's own elements; `tests/test_replay.py` replays it through the real loop.

### What the runs changed, and the evidence

Each change below was made after a reproducible failure on the fixture, then re-measured. The single-run detail is in `.local/eval/*.jsonl` (ignored).

| Finding | Evidence | Change |
| --- | --- | --- |
| A correct decision rejected as stale came back with lower confidence | 5/5 runs: `click GOT IT` at 0.99 → `stale_snapshot` (dialog still animating) → same action re-decided at 0.72–0.77 → escalated | A stale decision is re-dispatched without a new model call while the scoped fingerprint is unchanged (max 3, each a step); stale records are runner-internal and no longer shown to Jev. After: 3/3 |
| Waiting on a spinner counted as a navigation cycle | 2/3 runs: `click SYNC` 0.93, `wait` 0.94, `wait` 0.90 → "Repeated UI state" at the third identical observation | Revisiting the state Jev chose to wait on is a pending transition, bounded by `max_waits` (8) with the reason "Waited N times without a UI change"; action-driven revisits still trip the cycle guard |
| Jev's probabilities are rounded to two decimals | `Probabilities sum to 0.9900 over 20 choices` | Sum tolerance `max(0.001, 0.005·n)`; the selected choice must still be the maximum |
| The tree right after an accepted action predates its effect | Checkbox already `checked` but the list unfiltered; ADD NOTE accepted but no dialog in the tree | Companion: after an accepted action, wait for the first UI event it causes (≤500 ms), then a 150 ms quiet window (Android batches content changes every 100 ms), capped at 500 ms. 3/3 post-click observations correct |
| An observation is not an action | `timeout_outcome_unknown` on observe while the app cold-started | Observe may use the whole request budget; act keeps the 1.5 s dispatch cap. Observe timeouts are named "Observation timed out" |
| Runner polling spent the caller's step budget | Four empty observations during an activity transition exhausted `max_steps: 6` | `wait_for_ui` no longer counts as a step; the deadline bounds it |
| An accepted click processed late looked like a no-op | Click accepted, next observation unchanged, Jev escalated at 0.55 | Up to `max_effect_waits` (3) re-observations before Jev is asked to judge an unchanged screen |
| A dialog window can exist before its content | After "Delete note": one non-actionable element (`Note 04`) — Jev escalated at 0.41–0.55 | A screen with no actionable candidates is re-observed up to `max_effect_waits` times before a decision |
| No active window mid-launch | `Foreground package is outside task scope: ` (empty) | An empty active package is a transition, not out of scope |
| Provider hiccups | HTTP 503/529 and timeouts from the alpha endpoint | One retry after 0.5 s on 502/503/504/529 or timeout, only if ≥2 s of budget remain; 401 is never retried |
| Multi-action goals lower confidence | "Press Delete note, then confirm with Delete…" → `wait` at 0.66; "Open the dialog and press Add without a title" → `wait` 0.35–0.49 | Task authoring, not code: split into one-action steps with their own assertions. Both scenarios then passed 3/3 |
| Material uppercases dialog buttons | The button reads `GOT IT`, not "Got it" | `text_contains` (case- and whitespace-insensitive) exists for exactly this |
| Edge-to-edge hides the first row | `fixture_summary` under the status bar was reported not visible | Fixture layouts fit system windows |

Environmental incidents, reported as they happened: another agent briefly used emulator B mid-session (a foreground Pixan activity and a stray `N` typed into the fixture's title field invalidated one round); the resident Pixan process (440 MB) pushed the 2.5 GB emulator into swap, which produced observation timeouts and one ANR in the fixture process; a cold reboot of the emulator restored 155 ms observations. The OpenRouter Jev endpoint returned 503/529 in bursts during the evening.

### Limits

One emulator image, one model version, a six-pattern fixture, and a provider in alpha. Success here means Jev + Krilin completed these particular scenarios; it says nothing about apps whose accessibility trees are incomplete, about TalkBack gesture semantics, or about success rates on real apps. The Flutter reference app (Pixan) has not been re-run under the new contract.

### Reproduce

```sh
python -m unittest discover -s tests -v          # 65 tests, no device or key
python -m krilin --bridge-config .local/emulator-b.json setup --serial emulator-5556
python -m krilin --bridge-config .local/emulator-b.json smoke
python scripts/evaluate.py --bridge-config .local/emulator-b.json --live --record --runs 3
```

## Foundation (0.1, 2026-09-18)

- Host tests covered task assertions, scope, live candidate constraints, invalid model outputs, confidence checks, time/step limits, repeated states, stale recovery, input-mode persistence, Unicode-safe wire transport, device ownership, and preservation of enabled accessibility services.
- The MCP stdio client/server handshake, tool schemas, and device-configuration failure response were exercised.
- The Android debug APK built and Android lint reported no errors (warnings: target API level, plurals candidates for the fixture strings).
- On-device checks entered a name, saved it, verified the visible result, rejected a superseded snapshot token, and replaced text with `Krilin café 日本語`; the same semantic test passed with TalkBack enabled and `touch_exploration: true`, with the prior service list restored afterwards. Gesture navigation and spoken feedback were not tested.
- One live OpenRouter run completed the demo goal in **1,748 ms** with three Jev calls (235–430 ms each), two accepted actions and one stale-snapshot recovery, at a reported cost of **$0.000194**.

CI is defined for Windows, Linux, and macOS with Python 3.11/3.14, plus Android build/lint; that remote matrix has not yet been run. The optional direct TypeSafe route has not been exercised with a live TypeSafe key.
