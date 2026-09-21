# CLAUDE.md

Guidance for Claude Code sessions in this repository. Read it before touching code or the emulator.

## What Krilin is

Krilin gives coding agents a **bounded** way to drive Android apps. One loop, four stages:

1. **Observe** — the Kotlin accessibility service (`android/.../BridgeService.kt`) reads the live accessibility tree and returns a structured snapshot: elements `e1..eN` with package, resource ID, text, description, role, flags and the *semantic actions each node supports*, plus input state and a single-use `snapshot_id`.
2. **Decide** — Python builds a **closed candidate set** from that snapshot (`models.candidates()`): `wait`, `escalate`, optional `back`, and one `kind:eN` per supported node action. Jev (`typesafe/jev-1.13` through OpenRouter `POST /api/alpha/decisions`, `jev.py`) picks exactly one candidate and returns confidence and a probability distribution.
3. **Act** — the companion re-captures the tree, rejects the request if anything changed (`stale_snapshot`), resolves the target to a live node and performs `ACTION_CLICK` / `ACTION_SET_TEXT` / scroll / global back.
4. **Verify** — a fresh observation must satisfy the caller's explicit assertions. Only then is a run `succeeded`; everything else is `escalated` with state and history.

The whole point is **Jev doing the driving**. Claude's job is to author tasks (goal, allowed packages, assertions, text values), improve the loop, and report what Jev actually did. Manual bridge driving by Claude hides exactly the capability/limitation data the project exists to surface.

Version 0.2, public, MIT. Authoritative narrative docs: `README.md`, `docs/architecture.md` (why Python host + Kotlin driver), `docs/protocol.md` (bridge contract), `docs/validation.md` (what was actually verified), `CONTRIBUTING.md`.

## Layout

| Path | Role |
| --- | --- |
| `src/krilin/models.py` | Frozen dataclasses (`Element`, `Snapshot`, `Selector`, `Assertion`, `Input`, `Task`, `Action`, `Decision`), `Driver`/`Decider` protocols, `candidates()`, `input_targets()`, `nearest()`, `brief()`. `Snapshot.from_dict` is the strict wire validator; `Snapshot.compact()` is what Jev reads. |
| `src/krilin/runner.py` | The loop. `Limits` (20 steps / 60 s default, hard caps 100 / 300 s; thresholds .8; 3 repeated states; 8 waits; 3 effect waits). Stale retry, wait-aware cycle guard, effect/idle polling, `Result.diagnostics`, optional `--record` observations, JSONL trace events. |
| `src/krilin/scenario.py` | Ordered steps as data: `Launch` (code-executed precondition), `Step`, `Scenario.from_dict`, `run_scenario` (shared budget, stop at first escalation, `failed_step`; 0 = launch failed). |
| `src/krilin/replay.py` | `ReplayDriver`/`ReplayDecider`/`load_recording`: replay a `--record` trace through the real loop offline. |
| `src/krilin/jev.py` | Provider adapter only: `build_request`, `parse_response`, `JevDecider`. Model IDs and endpoints live here and nowhere else. |
| `src/krilin/bridge.py` | One newline-delimited JSON request per TCP connection to the ADB-forwarded companion. Maps `stale_snapshot` to `StaleSnapshot`, everything else to `KrilinError`. |
| `src/krilin/device.py` | `adb` wrapper, `setup` (install APK, provision token, enable service *alongside* existing services, allocate forward), `load_driver`, `device_lock`. |
| `src/krilin/cli.py` | `demo`, `setup`, `observe`, `smoke`, `run`, `serve`. Exit codes: 0 success, 2 escalated, 1 config/input error. |
| `src/krilin/mcp_server.py` | Transport only: `android_observe()`, `android_run(...)` and `android_run_scenario(...)` call the same controller as the CLI; its instructions/docstrings are the weak caller's manual. |
| `src/krilin/demo.py` | Deterministic offline fixture (`DemoDriver`, `DemoDecider`, `demo_task`). Not a Jev simulation. |
| `android/app/src/main/java/dev/krilin/bridge/` | `BridgeService.kt` (observe/act, event-driven settling, snapshot signatures), `DemoActivity.kt` (name/save smoke screen), `FixtureActivity.kt` + `FixtureDetailActivity.kt` + `FixtureState.kt` (offline complexity fixture in its own `:fixture` process; the detail screen deliberately has no view IDs), `MainActivity.kt` (token provisioning + shortcuts). |
| `tests/` | `unittest` suites; no device, key or network required. `tests/fixtures/*.jsonl` are sanitized live recordings replayed by `test_replay.py`. |
| `scripts/validate_emulator.py` | Opt-in on-device checks against the demo screen; `--live` spends OpenRouter credits, `--talkback` flips secure settings. |
| `scripts/evaluate.py` | Runs `examples/fixture/*.json` N times with Jev, prints a per-scenario table, writes `.local/eval.json`; `--record` keeps replayable traces. The only legitimate basis for loop-policy changes. |
| `examples/save-name.json`, `examples/fixture/*.json` | Reference task; one scenario per fixture pattern. |
| `.local/` (ignored) | `bridge.json` / `emulator-b.json` (serial, port, **token**), traces, `eval/`. Never commit; never print the token. |

## Commands

```powershell
.\.venv\Scripts\Activate.ps1                       # venv already exists here (Python 3.14)
python -m pip install -e ".[mcp]"
python -m unittest discover -s tests -v            # 65 tests, ~2 s, no device/key
python -m krilin demo                              # offline fixture through the real Runner
.\android\gradlew.bat -p android :app:assembleDebug :app:lintDebug   # needs JDK 17 + SDK 35
python -m krilin setup --serial emulator-5554      # installs companion; writes .local/bridge.json
python -m krilin smoke                             # demo screen, DemoDecider, no model
python -m krilin observe --brief --package PKG    # one line per element, no model — author selectors from this
python -m krilin run --task examples/save-name.json --trace .local/run.jsonl   # Jev drives one subgoal
python -m krilin run --scenario examples/fixture/hide-done.json --trace .local/run.jsonl --record   # steps + launch
python scripts/evaluate.py --bridge-config .local/emulator-b.json --live --record --runs 3   # the numbers
python -m krilin serve --env-file .env             # MCP stdio server
adb -s emulator-5554 shell am start -W -f 0x10008000 -n dev.krilin.bridge/.DemoActivity   # reset fixture
```

Prefix every device command with `--bridge-config .local/emulator-b.json` when working on emulator B (see below).

`OPENROUTER_API_KEY` is read from `.env` (already present locally) or the environment. Never echo it, paste it, or put it in an error message.

## Invariants — do not break these

- **Closed candidates.** Jev only ever chooses among actions built from the live snapshot. The runner rejects any decision outside the candidate set. Never let the model invent a target, an action kind, or text.
- **Semantic actions only.** The bridge speaks `click`, `set_text`, `scroll_forward`, `scroll_backward`, `back`. No coordinates, taps, swipes, screenshots, OCR, or shell execution on the device — `docs/protocol.md` forbids them by design. "Not implemented" in the README (screenshots, OCR, coordinate gestures, generated text, app-launch planning, resumable workflows) is a scope statement, not a to-do list.
- **Text comes from the caller.** `inputs` (selector → text) and the `text_values` shorthand (resource ID → text) are the only sources of typed text; a `set_text` candidate exists only for the one field a selector uniquely matches. Jev never generates text; there is no small-LLM fallback.
- **Success = explicit assertions, verified on a fresh observation.** The `goal_achieved` noul answer is diagnostic only. A model claiming completion is not a test oracle (`test_model_completion_claim_is_not_a_test_oracle`).
- **Fail closed.** Missing confidence, inconsistent probabilities, thresholds not met, truncated tree, out-of-scope foreground package, transport loss after dispatch — all escalate, none retry blindly. A dropped connection after `act` is an unknown outcome, never replayed.
- **Stale is not failure.** `stale_snapshot` means nothing executed: re-observe, re-dispatch the same decision if the scoped UI is unchanged (bounded), otherwise decide again. It must stay distinct from uncertain-transport errors, and Jev never sees stale records.
- **Budgets are real.** Steps, wall-time, per-call timeout, repeated-state count, waits, effect waits, stale retries, 512 elements / 2048 nodes / 64 depth, 24 KB decision request, 255 candidates, 16 KiB device request, 1 MiB host frame. Do not raise or bypass them to make something pass.
- **Runner-internal waiting is not a decision.** Empty trees, the late effect of an accepted action, a window without content yet, and stale re-dispatch are handled by the loop without spending the caller's steps or a Jev call, each with its own bound (deadline, `max_effect_waits`, `STALE_RETRIES`). Jev is asked only when there is something to decide; stale records are never shown to it.
- **Loop-policy changes need fixture evidence.** Settling, waits, retries, thresholds and tolerances change only after a reproducible failure in `scripts/evaluate.py` runs (or a recording under `tests/fixtures/`), and the finding, the change and the re-measured numbers go into `docs/validation.md`. Provider outages (OpenRouter 503/529 bursts are common) are reported as provider errors, never compensated by loosening anything.
- **Layer boundaries.** Provider details stay in `jev.py`; Android details stay in the companion/`bridge.py`/`device.py`; no app-specific branches in `runner.py` or `models.py`. Reusable tasks go in `examples/`, test-only behaviour in fixtures.
- **Protocol changes** touch `docs/protocol.md`, `bridge.py`, `models.py` (`Snapshot.from_dict`), `BridgeService.kt` and the `protocol` number, together, and bump the number when compatibility breaks. The Kotlin action allow-list and the Python one in `Snapshot.from_dict` must match.
- **Setup preserves other accessibility services** (TalkBack included). Never replace `enabled_accessibility_services` with only Krilin.

## Scope discipline: no irrelevant features, no rigged tests

**History:** an earlier session (GLM) added screen coordinates to the Jev state, a screenshot/vision module, hand-driving helper scripts under `.local/`, and tests shaped to pass. The user reverted all of it to the initial commit. Do not repeat this.

Red flags — if you catch yourself doing any of these, stop and re-read the request:

- **Feeding Jev data that cannot change its decision.** Coordinates are the canonical case: Jev chooses among `kind:eN` candidates, the bridge has no coordinate action, so bounds/x-y in the state are dead weight that inflates the 24 KB budget and proves nothing. Every field in `state` or `criteria` must be justified by a decision it can alter.
- **Building a capability nobody asked for** because it "might help" — new action kinds, new modules, new CLI flags, vision, OCR, gesture paths, planners. Each of these is a user decision; ask, don't ship.
- **Hand-driving the device and calling it validation.** Scripts that click/type through the bridge are Claude doing Jev's job. They are legitimate *only* for selector discovery and fixture reset, and they do not belong in the repo.
- **Tautological tests.** A fake decider that returns `click:save` plus an assertion that `click:save` was executed proves nothing about the runner. Fakes must be dumb (`FixedDecider`, `RecordingDriver`, thin `DemoDriver` subclasses) and must never reimplement runner/candidate logic.
- **Asserting what the code happens to do** instead of what the contract requires. Assert on behaviour: nothing executed, status `escalated`, the reason text, decider never called, service list preserved.
- **Loosening anything to go green** — `Limits`, thresholds, `max_repeated_states`, size caps, assertion strictness, wire validation.
- **Documentation that outruns evidence.** `docs/validation.md` records what was run and what happened, including escalations and costs. Never write "validated" for a run you did not perform or that did not succeed.

Gate before adding code: name (a) the task contract, existing invariant, or one of the two target scenarios it serves, and (b) the concrete failing behaviour it fixes. If either is missing, don't build it — say so in one sentence and move on.

Gate before committing a test: break the code under test and confirm the test fails. A test that cannot fail is not a test.

## Testing rules

- Framework is `unittest`, discovered from `tests/`; keep it that way (CI runs it on Windows/Linux/macOS, Python 3.11 and 3.14). 65 tests as of 0.2.
- Mutation-check new tests: break the code under test (a one-line edit in a scratch copy), confirm at least one test fails, restore. Use the absolute venv interpreter `C:\git\Krilin\.venv\Scripts\python.exe` for subprocess runs.
- The default suite never needs a device, an API key, or the network. Live and on-device checks are opt-in through `scripts/validate_emulator.py` and `krilin smoke`/`run`.
- Cover the failure paths that can cause a wrong UI action: stale tokens, input-mode changes, unknown targets, ambiguous assertions, exhausted budgets, transport uncertainty, duplicate device ownership, invalid model output.
- MCP tests use the real stdio handshake (`test_mcp.py`); skip cleanly when the `mcp` extra is absent.
- Reports distinguish **fixture** results (`DemoDecider`) from **live** results (Jev). One successful live run is an integration proof, not a success rate.

## Working on the emulator

- **Two emulators exist.** `emulator-5554` belongs to a Flutter agent — never touch it. Krilin work runs on AVD `flutter_emulator_b` (`emulator-5556`, API 35, 2.5 GB RAM; launch it with `emulator -avd flutter_emulator_b`, detached) with config `.local/emulator-b.json`. Pixan is installed there too; if its process is resident it starves the emulator's memory (observation timeouts, ANRs) — `am force-stop com.movvem.pixan2` is app-level and fine. A degraded emulator (slow `wait for adding window`, empty trees for seconds) is fixed by a cold reboot, then `krilin setup` again (approved for emulator B).
- **Allowed:** the emulators' installed apps (`dev.krilin.bridge`, Chrome, YouTube, `com.movvem.pixan2`, ...). Building the APK needs `JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17.0.16.8-hotspot` and `ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk` passed inline (the machine's `JAVA_HOME` points at an incomplete JBR); after a rebuild, `adb install -r` keeps the token and the service re-binds in a few seconds.
- **Off limits:** host files/apps/config, Docker, other projects, and emulator system settings beyond what `krilin setup` does on emulator B. `validate_emulator.py --talkback` flips secure settings — ask before using it.
- **Jev drives.** All execution goes through `krilin run` or MCP `android_run`. Use `krilin observe` (and, sparingly, one-off bridge calls) only to find selectors and reset fixtures. When a run stalls, fix the task — goal wording, decomposition into subgoals, assertions, `text_values`, `allow_back` — and rerun. Do not drive the app by hand to unblock it.
- **Live runs cost money.** Each Jev call bills the user's OpenRouter key. Keep runs bounded, keep traces in `.local/`, and record outcomes honestly in `docs/validation.md`.
- **What "complexity" means here:** testing real, ID-less apps (the reference app is Pixan, Flutter) by a weak caller — the fixture patterns in `examples/fixture/` are the yardstick. Chrome/YouTube were only illustrations of complexity, not requirements. Seeking a seekbar would need a semantic progress action through the protocol-change path, only if a scenario ever needs it.

## Style

- Python: `from __future__ import annotations`, frozen dataclasses, type hints, dense code with comments only where the reason is non-obvious. Raise `KrilinError` for anything safe to hand back to an agent, `StaleSnapshot` for the one recoverable case, `ValueError` for caller input mistakes. Errors never include tokens, keys, or provider response bodies.
- Kotlin: one service file; all node work on the main thread via `FutureTask`; recycle every `AccessibilityNodeInfo`; never echo request payloads in errors.
- Windows is the primary dev box: PowerShell, `.\android\gradlew.bat`, `.venv\Scripts\python.exe`. Write files as UTF-8. Long heredocs (100+ lines) get mangled by the shell wrapper here — write edit scripts to the scratchpad and run them. Android string resources need `\'` for apostrophes.
- Commit nothing from `.env*` (except `.env.example`), `.local/`, `*.jsonl`, `*.apk`, `*.jks`, `local.properties`, or build output.
