# Foundation validation

Validation performed locally on Windows with Python 3.14, JDK 17, and one running Android emulator. These results describe the small bundled fixture and are not a general performance or reliability benchmark.

## Verified

- Host tests cover task assertions, scope, live candidate constraints, invalid model outputs, confidence checks, time/step limits, repeated states, stale recovery, input-mode persistence, Unicode-safe wire transport, device ownership, and preservation of enabled accessibility services.
- The actual MCP stdio client/server handshake, tool schemas, and device-configuration failure response were exercised.
- The Android debug APK builds and Android lint reports no errors. Its report currently contains two compatibility warnings: targeting API 35 instead of the newest API level, and backup metadata across Android versions. The manifest explicitly disables backup, with separate rules excluding shared preferences from transfer.
- On-device checks enter a name, save it, verify the visible result, reject a superseded snapshot token, and replace text with `Krilin café 日本語`.
- The same semantic test passed with installed TalkBack enabled and `touch_exploration: true`. The original enabled-service setting was restored afterward. Gesture navigation and spoken feedback were not tested.
- The Python wheel and source distribution build locally. Packaging is checked for exclusion of `.env`, local device credentials, SDK paths, and build output.

## One successful live OpenRouter run

The provider returned model ID `typesafe/jev-1.13-20260917`. The controller completed the demo goal and verified `Saved: Krilin` in **1,748 ms**. It used three Jev calls, two accepted UI actions, one stale-snapshot recovery, and two short waits for the app tree to appear.

| Decision | Model call time | Outcome |
| --- | ---: | --- |
| Replace Name with Krilin | 430 ms | Snapshot became stale; no action |
| Replace Name with Krilin | 235 ms | Accepted |
| Click Save | 308 ms | Accepted; next observation verified success |

The three responses reported a combined cost of **$0.000194124** for that run. This excludes earlier development requests. Confidence and selected-choice probability thresholds remained at 0.8 throughout validation.

Earlier integration runs exposed empty trees during transitions, redundant accessibility notifications, and low-confidence escalation after stale actions. Fixes added deterministic empty-tree waiting, live-state revalidation, and explicit stale-outcome context. The successful run demonstrates the integration path, not a measured success rate across apps.

## Reproduce

```sh
python -m unittest discover -s tests -v
python -m krilin demo
python scripts/validate_emulator.py --talkback --live
```

The final command requires a configured emulator, an installed TalkBack service, and `OPENROUTER_API_KEY` in `.env`. It temporarily changes the enabled-service list and restores it in `finally`. Run without `--talkback` to avoid that change and without `--live` to avoid provider calls. Detailed local output is stored in ignored `.local/validation.json` and `.local/live.jsonl`.

CI is defined for Windows, Linux, and macOS with Python 3.11/3.14, plus Android build/lint. That remote matrix has not been run as part of this local implementation. The optional direct TypeSafe route has not been exercised with a live TypeSafe key.
