# Contributing to Krilin

Krilin is structured as a reusable public project. Keep provider APIs, controller policy, and Android internals behind their existing boundaries rather than adding app-specific branches to the shared loop. Put reusable task examples under `examples/` and test-specific behavior in fixtures.

## Checks

```sh
python -m pip install -e ".[mcp]"
python -m unittest discover -s tests -v
python -m krilin demo
bash android/gradlew -p android :app:assembleDebug :app:lintDebug
```

Use `android\gradlew.bat` on Windows. CI defines a host matrix for Windows, Linux, and macOS with Python 3.11 and 3.14, and an Android build/lint job. The matrix is a validation target; local results on one platform do not prove every job has passed.

For driver changes, build/install on an emulator and run `python -m krilin smoke`. For provider changes, an opt-in live run requires a developer-supplied key and spends API credits. Never require keys for the default test suite. Distinguish scripted fixture results from live model results in reports.

`python scripts/validate_emulator.py` checks the demo task, stale-token rejection, and Unicode entry on the connected emulator. Add `--talkback` to temporarily enable an installed TalkBack service, verify semantic control, and restore the prior service list. Add `--live` for a bounded OpenRouter run. Results are written to ignored `.local/validation.json`; provider traces go to `.local/live.jsonl`. These are developer smoke checks, not a statistical benchmark.

`python scripts/evaluate.py --live --runs N` runs every `examples/fixture/*.json` scenario N times with Jev and reports success rate, steps, calls and cost per scenario into `.local/eval.json`; `--record` keeps replayable traces under `.local/eval/`. Loop-policy changes (settling, waits, retries, thresholds) must be motivated by these runs or by a recording under `tests/fixtures/`, and the change plus the numbers go into `docs/validation.md`. Report escalations and environmental failures as they happened.

Cover failures that can lead to wrong UI actions: stale tokens, input-mode changes, unknown targets, ambiguous assertions, exhausted budgets, transport uncertainty, and duplicate device ownership. Do not silently lower execution thresholds to make a test pass. Calibrate policy changes against an evaluation corpus.

Document bridge-contract changes in `docs/protocol.md` and increment the protocol when compatibility breaks. Keep model identifiers and provider endpoints isolated in the provider adapter. Do not commit `.env`, `.local`, device traces, API keys, APK signing keys, SDK paths, or build output.
