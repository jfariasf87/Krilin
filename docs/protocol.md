# Bridge protocol 1

The development companion binds only `127.0.0.1:8765` inside Android. `adb forward tcp:0 tcp:8765` exposes an allocated host port. Requests and responses are UTF-8 JSON objects delimited by a single newline, one request per TCP connection. No HTTP server, shell execution, coordinates, or model key lives on Android.

Every request includes `protocol: 1`, a provisioned 64-character hex `token`, `method`, and `timeout_ms`. The host caps incoming frames at 1 MiB; the companion accepts requests up to 16 KiB. The companion serializes device commands through its main thread. `setup` stores the bridge credential in Android private preferences and in ignored local host configuration.

## Observe

```json
{"protocol":1,"token":"<local-token>","method":"observe","timeout_ms":5000}
```

The response includes `protocol`, `snapshot_id`, `active_package`, `truncated`, `input`, and `elements`. Each element has a snapshot-local `id`, package, resource ID, text, description, role, enabled/checked/focused/password flags, and an explicit list of supported actions. Empty fields use empty strings. Input state contains `execution_mode`, `touch_exploration`, `accessibility_services`, and `ime_visible`.

The tree is limited to 512 represented elements, 2,048 visited nodes, and 64 levels. Text fields are bounded to 2,000 characters. A truncated tree causes the host to escalate. Password values and descriptions are blanked and password nodes cannot be acted on. Text in a visible UI is untrusted data, not an instruction from the caller.

## Act

```json
{"protocol":1,"token":"<local-token>","method":"act","snapshot_id":"<snapshot>","kind":"click","target":"e8","text":null,"timeout_ms":5000}
```

`kind` may be `click`, `set_text`, `scroll_forward`, `scroll_backward`, or `back`. `set_text` requires exact replacement text; `back` is a global semantic operation. Wait and escalation are handled by the host. Success is `{"ok":true}` and means Android accepted the action, not that the task succeeded.

Actions require the most recently issued snapshot, under 15 seconds old. Before execution the companion captures the tree again and compares semantic properties, input state, window/path identities, and geometry. Events drive settling; redundant notifications alone do not invalidate a semantically unchanged snapshot. The driver resolves the target to a live node and confirms the action is supported. A dispatched token is single-use.

Errors return `{"error":"code"}`. `stale_snapshot` means no action executed and permits a new observation/decision. `unauthorized`, `unknown_target`, `invalid_target`, `unsupported_action`, `action_rejected`, `expired_request`, and `timeout_outcome_unknown` stop the current host run. A transport loss after dispatch is also treated as an unknown outcome. There is no automatic action replay.

The API cannot atomically freeze all app rendering while acting. Revalidation reduces stale-target risk; it does not prove a selected element is semantically the right one. Verification after execution remains mandatory.

## Trust and concurrency

This is a developer bridge for a trusted host/emulator, not a multi-tenant boundary. Local apps with sufficient privileges and local host processes are outside its protection model. The exported provisioning activity exists for ADB setup in this development APK. A public production-device companion would need a pairing flow, stricter provisioning lifecycle, and a separately reviewed distribution policy.

The host builds package-scoped candidates and validates model outputs; the device enforces live-node/action membership and token freshness. The bridge itself does not interpret goals, allowed package policy, or model confidence. CLI and MCP acquire the same OS file lock for a shared configuration path. Using multiple independent configurations for one device is unsupported; snapshot invalidation still prevents tokens from different observations being reused.
