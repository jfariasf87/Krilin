# Architecture decision: Python host, Kotlin driver

Status: implemented foundation. Date: 2026-09-18.

## Language choices

| Choice | Strengths for Krilin | Tradeoff | Decision |
| --- | --- | --- | --- |
| Python host | Accessible testing/automation ecosystem, typed domain models, simple CLI and MCP integration | Runtime validation is required at boundaries | Use for orchestration |
| TypeScript host | Strong MCP ecosystem and web integration | Would still need a native Android layer; no browser UI is needed yet | Viable alternative, no second host implementation |
| Kotlin host and device | Direct Android APIs and static typing | JVM distribution for every agent client increases initial setup | Use only on the device |
| Rust host | Small binary and strong resource control | More implementation work before profiling identifies CPU pressure | Reconsider for distribution or measured bottlenecks |

Python CPU work consists mainly of a bounded tree filter and JSON validation. The network, device tree traversal, UI transitions, and model latency are the performance questions to measure first. There is no promised millisecond target in this foundation.

## Boundaries

1. The coding agent supplies a concrete subgoal, permitted packages, input text, and UI assertions. Long-range planning remains with that agent.
2. The companion produces a bounded structured snapshot and captures nodes locally. No image is sent to Jev.
3. Python builds only executable action candidates from the snapshot. The action and its target are one choice, including caller-supplied text when needed.
4. Jev selects a complete candidate and separately assesses visible goal completion. [TypeSafe evaluates questions independently](https://docs.typesafe.ai/introduction), so a target question cannot depend on an action question in the same call. Similarly, a risk question about an unknown selected action would be invalid. Action-specific risk policy or a second assessment pass can be added explicitly later.
5. Code validates choice membership, the probability distribution, and configured thresholds. Confidence is a decision signal to calibrate on real tasks; it is not proof an action is correct or safe.
6. The companion checks a snapshot token, input state, node properties, and geometry before executing a supported semantic action. Events drive bounded settling; redundant events are not treated as UI changes.
7. A fresh observation must satisfy caller-defined assertions before success. Failure returns control with state and a bounded history.

The `Driver` and `Decider` protocols allow additional implementations without changing task semantics. The current bridge protocol is versioned independently from package versions. Its contract lives in [protocol.md](protocol.md).

## Preventing forgotten state and unbounded loops

The goal, assertions, current input mode, and eight recent steps accompany each decision. Input state is read from Android each time. Snapshot tokens are ephemeral; progress detection hashes content rather than tokens. Non-task packages are excluded from the model state and progress fingerprint so clocks or unrelated notifications cannot count as task progress. Three visits to the same scoped state end a run, including cycles through different screens.

An action that returns a stale-token rejection can be re-observed and selected again. A dropped connection after dispatch has an unknown outcome and stops the run; actions are never blindly retried. Step, wall-time, per-call, node, and request-size limits bound work. New actions are not started after the controller's deadline. An Android operation already accepted before cancellation may still finish; there is no rollback guarantee.

The optional JSONL journal records goals/assertions, decisions, input modes, outcomes, and per-stage timings. It aids diagnosis, but is not a durable resume mechanism. Resume/recovery must always take a new snapshot.

## Why a persistent companion

A fresh `adb shell uiautomator dump` process for every action adds overhead and may perturb accessibility services through instrumentation. The companion stays connected, receives events, and executes node actions directly. Observation waits for an 80 ms event quiet period, capped at 400 ms, instead of assuming a multi-second sleep is necessary. Animated UIs can remain unstable; the driver rejects stale decisions and the host limits retries.

The companion does not request gesture interception or touch exploration. Semantic actions are useful when testing functional outcomes with TalkBack running. They bypass physical gesture semantics, so they cannot certify the TalkBack user journey. A future explicit gesture driver should model focus/select/activate, multi-finger scrolling, spoken feedback, and restoration of temporarily changed settings. It should never silently fall back from gesture testing to semantic execution.

## Initial boundaries to extend

- Replayable evaluation corpus: real app trees, expected candidate choices, mode transitions, stalls, and end-to-end success rates.
- Better observation: hierarchical context, paging large trees, multiple displays, Compose/WebView cases, and stable element identity across snapshots.
- Stronger assertions: application-level test hooks and accessibility-specific evidence, beyond visible text/check state.
- Lifecycle: robust reconnection after device restart, per-device session registry, async cancellation, crash recovery, and explicit resumable tasks.
- Additional drivers: gesture fidelity, OCR/vision on inaccessible UIs, and desktop/browser adapters behind the same host interfaces.
- Provider evolution: OpenRouter's alpha Decisions contract and the optional direct TypeSafe route remain isolated from task logic.
