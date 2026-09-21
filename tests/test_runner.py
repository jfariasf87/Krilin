import unittest

from krilin.demo import DemoDecider, DemoDriver, demo_task, PACKAGE
from krilin.models import (Action, Assertion, Decision, Element, Input, InputState, KrilinError, Selector, Snapshot,
                           StaleSnapshot, Task, candidates)
from krilin.runner import Limits, Runner, model_history


class FixedDecider:
    def __init__(self, action="wait", confidence=1, goal=0):
        self.action, self.confidence, self.goal = action, confidence, goal
        self.states = []

    def decide(self, state, actions, timeout):
        self.states.append(state)
        return Decision(self.action, self.confidence, 1, self.goal, "test")


class CountingDecider(DemoDecider):
    def __init__(self):
        self.states = []

    def decide(self, state, actions, timeout):
        self.states.append(state)
        return super().decide(state, actions, timeout)


class RecordingDriver(DemoDriver):
    def __init__(self):
        super().__init__()
        self.executions = []

    def execute(self, snapshot, action, timeout):
        self.executions.append(action)
        super().execute(snapshot, action, timeout)


class RunnerTests(unittest.TestCase):
    def test_verifies_effect_after_final_allowed_action(self):
        result = Runner(DemoDriver(), DemoDecider(), Limits(max_steps=2)).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.snapshot["elements"][2]["text"], "Saved: Krilin")

    def test_already_satisfied_needs_no_model(self):
        driver = DemoDriver()
        driver.name, driver.saved = "Krilin", True
        decider = FixedDecider()
        result = Runner(driver, decider).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(decider.states, [])

    def test_confidence_and_unoffered_action_never_execute(self):
        for decision in (FixedDecider("click:e2", .1), FixedDecider("click:invented"), FixedDecider("wait", float("nan"))):
            with self.subTest(decision=decision.action):
                driver = RecordingDriver()
                result = Runner(driver, decision).run(demo_task())
                self.assertEqual(result.status, "escalated")
                self.assertFalse(driver.executions)

    def test_model_completion_claim_is_not_a_test_oracle(self):
        result = Runner(DemoDriver(), FixedDecider(goal=1), sleep=lambda _: None).run(demo_task())
        self.assertEqual(result.status, "escalated")
        self.assertIn("Waited", result.reason)

    def test_changing_snapshot_tokens_do_not_hide_a_stall(self):
        class TokenDriver(DemoDriver):
            def observe(self, timeout):
                self.version += 1
                return super().observe(timeout)
        result = Runner(TokenDriver(), FixedDecider(), Limits(max_waits=3), sleep=lambda _: None).run(demo_task())
        self.assertEqual((result.steps, result.reason), (3, "Waited 3 times without a UI change"))

    def test_waiting_on_a_pending_transition_is_not_a_cycle(self):
        class LateDriver(DemoDriver):
            observations = 0
            def observe(self, timeout):
                self.observations += 1
                if self.observations >= 6:  # Content appears after five identical observations.
                    self.name, self.saved = "Krilin", True
                return super().observe(timeout)
        result = Runner(LateDriver(), FixedDecider(), sleep=lambda _: None).run(demo_task())
        self.assertEqual((result.status, result.steps), ("succeeded", 5))
        self.assertEqual({h["action"] for h in result.history}, {"wait"})

    def test_an_action_revisiting_a_state_still_counts_as_a_cycle_after_waits(self):
        class Decider(FixedDecider):
            def decide(self, state, actions, timeout):
                self.action = "click:e2" if len(self.states) % 2 else "wait"
                return super().decide(state, actions, timeout)
        class NoOpDriver(DemoDriver):
            def execute(self, snapshot, action, timeout):
                self.version += 1  # The click changes nothing visible.
        result = Runner(NoOpDriver(), Decider(), sleep=lambda _: None).run(demo_task())
        self.assertEqual(result.status, "escalated")
        self.assertIn("Repeated", result.reason)
        self.assertLess(result.steps, 8)

    def test_input_mode_survives_every_decision_and_invalidates_state(self):
        class ModeDriver(DemoDriver):
            def observe(self, timeout):
                base = super().observe(timeout)
                return Snapshot(base.id, base.elements,
                                InputState(self.version > 0, ("talkback/service",) if self.version > 0 else ()), PACKAGE)
        class RecordingDecider(DemoDecider):
            states = []
            def decide(self, state, actions, timeout):
                self.states.append(state)
                return super().decide(state, actions, timeout)
        decider = RecordingDecider()
        result = Runner(ModeDriver(), decider).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertFalse(decider.states[0]["ui"]["input"]["touch_exploration"])
        self.assertTrue(decider.states[1]["ui"]["input"]["touch_exploration"])
        self.assertEqual(decider.states[1]["goal"], demo_task().goal)
        self.assertTrue(decider.states[1]["recent_history"])

    def test_stale_action_on_identical_state_is_retried_without_a_second_decision(self):
        class StaleDriver(DemoDriver):
            stale = True
            def execute(self, snapshot, action, timeout):
                if self.stale:
                    self.stale = False
                    raise StaleSnapshot()
                super().execute(snapshot, action, timeout)
        decider = CountingDecider()
        result = Runner(StaleDriver(), decider).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual([h["outcome"] for h in result.history], ["stale_snapshot", "accepted", "accepted"])
        self.assertEqual(result.history[1]["retry_after_stale"], 1)
        self.assertEqual(result.history[1]["action"], result.history[0]["action"])
        self.assertEqual(len(decider.states), 2)  # set_text once, click once; the retry asked nobody.

    def test_stale_then_changed_state_gets_a_fresh_decision_that_never_sees_the_stale(self):
        class MovingDriver(DemoDriver):
            stale = True
            def execute(self, snapshot, action, timeout):
                if self.stale:
                    self.stale = False
                    self.name = "typed by the app meanwhile"
                    raise StaleSnapshot()
                super().execute(snapshot, action, timeout)
        decider = CountingDecider()
        result = Runner(MovingDriver(), decider).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.history[0]["outcome"], "stale_snapshot")
        self.assertEqual(len(decider.states), 3)
        self.assertEqual(decider.states[1]["recent_history"], [])
        self.assertEqual([h["action"] for h in decider.states[2]["recent_history"]], ["set_text:e1"])
        self.assertNotIn("retry_after_stale", result.history[1])

    def test_stale_retries_are_bounded_then_decided_again(self):
        class StaleDriver(DemoDriver):
            def execute(self, snapshot, action, timeout):
                raise StaleSnapshot()
        decider = CountingDecider()
        result = Runner(StaleDriver(), decider, Limits(max_steps=6)).run(demo_task())
        self.assertEqual((result.reason, result.steps), ("Step budget exhausted", 6))
        self.assertEqual(len(decider.states), 2)
        self.assertEqual([h.get("retry_after_stale") for h in result.history], [None, 1, 2, 3, None, 1])

    def test_model_history_hides_runner_internals(self):
        current = {"touch_exploration": False, "accessibility_services": [], "ime_visible": False, "execution_mode": "semantic"}
        earlier = {**current, "touch_exploration": True}
        history = [{"step": 1, "action": "click:e2", "outcome": "stale_snapshot", "input": current, "decision": {"confidence": .9}},
                   {"step": 2, "action": "click:e2", "outcome": "accepted", "input": earlier, "before": "abc", "observe_ms": 3},
                   {"step": 3, "action": "wait", "outcome": "waited", "input": current}]
        self.assertEqual(model_history(history, current), [
            {"step": 2, "action": "click:e2", "outcome": "accepted", "input": earlier},
            {"step": 3, "action": "wait", "outcome": "waited"}])
        self.assertEqual(len(model_history([history[2]] * 20, current)), 8)

    def test_empty_transition_does_not_call_model_or_spend_steps(self):
        now = [0.0]
        class EmptyDriver(DemoDriver):
            def observe(self, timeout):
                now[0] += .5
                return Snapshot("s", (), InputState(), PACKAGE)
        decider = FixedDecider()
        result = Runner(EmptyDriver(), decider, Limits(max_steps=2, max_seconds=5), clock=lambda: now[0],
                        sleep=lambda _: None).run(demo_task())
        self.assertEqual((result.reason, result.steps), ("Run deadline reached", 0))
        self.assertFalse(decider.states)
        self.assertTrue(result.history and all(h["action"] == "wait_for_ui" for h in result.history))

    def test_late_effect_of_an_accepted_action_is_awaited_without_a_decision(self):
        class SlowDriver(DemoDriver):
            pending = None
            def execute(self, snapshot, action, timeout):
                self.pending = (action, 2)  # Visible only after two more observations.
            def observe(self, timeout):
                if self.pending:
                    action, left = self.pending
                    if left == 0:
                        super().execute(None, action, timeout)
                        self.pending = None
                    else:
                        self.pending = (action, left - 1)
                return super().observe(timeout)
        decider = CountingDecider()
        result = Runner(SlowDriver(), decider, sleep=lambda _: None).run(demo_task())
        self.assertEqual((result.status, result.steps, len(decider.states)), ("succeeded", 2, 2))
        self.assertEqual([h["action"] for h in result.history],
                         ["set_text:e1", "wait_for_effect", "wait_for_effect", "click:e2", "wait_for_effect", "wait_for_effect"])

    def test_screen_without_actions_is_polled_before_any_decision(self):
        class DialogDriver(DemoDriver):
            transitional = 0
            def execute(self, snapshot, action, timeout):
                super().execute(snapshot, action, timeout)
                self.transitional = 2  # The next two trees show only a title while a dialog is built.
            def observe(self, timeout):
                if self.transitional:
                    self.transitional -= 1
                    return Snapshot(f"t{self.transitional}", (Element("e1", PACKAGE, text="Please wait"),), InputState(), PACKAGE)
                return super().observe(timeout)
        decider = CountingDecider()
        result = Runner(DialogDriver(), decider, sleep=lambda _: None).run(demo_task())
        self.assertEqual((result.status, len(decider.states)), ("succeeded", 2))
        self.assertEqual([h["action"] for h in result.history],
                         ["set_text:e1", "wait_for_actions", "wait_for_actions", "click:e2", "wait_for_actions", "wait_for_actions"])

    def test_polling_a_screen_without_actions_is_bounded_then_jev_decides(self):
        class StuckDriver(DemoDriver):
            def observe(self, timeout):
                return Snapshot("s", (Element("e1", PACKAGE, text="Please wait"),), InputState(), PACKAGE)
        decider = FixedDecider("escalate")
        result = Runner(StuckDriver(), decider, Limits(max_effect_waits=2), sleep=lambda _: None).run(demo_task())
        self.assertEqual((result.reason, len(decider.states)), ("Decider requested help from the calling agent", 1))
        self.assertEqual([h["action"] for h in result.history], ["wait_for_actions", "wait_for_actions"])

    def test_effect_grace_is_bounded_then_jev_decides_again(self):
        class NoOpDriver(DemoDriver):
            def execute(self, snapshot, action, timeout):
                self.version += 1  # Accepted, changes nothing visible.
        decider = CountingDecider()
        result = Runner(NoOpDriver(), decider, Limits(max_effect_waits=2), sleep=lambda _: None).run(demo_task())
        self.assertEqual(result.status, "escalated")
        self.assertIn("Repeated", result.reason)
        self.assertEqual([h["action"] for h in result.history][:4],
                         ["set_text:e1", "wait_for_effect", "wait_for_effect", "set_text:e1"])
        self.assertGreaterEqual(len(decider.states), 2)

    def test_repeated_stale_races_exhaust_steps_without_ui_loop_claim(self):
        class StaleDriver(DemoDriver):
            def execute(self, snapshot, action, timeout):
                raise StaleSnapshot()
        result = Runner(StaleDriver(), DemoDecider(), Limits(max_steps=4)).run(demo_task())
        self.assertEqual(result.reason, "Step budget exhausted")
        self.assertEqual(result.steps, 4)

    def test_deadline_after_slow_model_prevents_action(self):
        now = [0.0]
        class SlowDecider(FixedDecider):
            def decide(self, state, actions, timeout):
                now[0] += 10
                return super().decide(state, actions, timeout)
        driver = RecordingDriver()
        result = Runner(driver, SlowDecider("click:e2"), Limits(max_seconds=1), clock=lambda: now[0]).run(demo_task())
        self.assertEqual(result.reason, "Run deadline reached")
        self.assertFalse(driver.executions)

    def test_unknown_execution_outcome_is_not_retried(self):
        class BrokenDriver(RecordingDriver):
            def execute(self, snapshot, action, timeout):
                self.executions.append(action)
                raise KrilinError("Connection lost; outcome unknown")
        driver = BrokenDriver()
        result = Runner(driver, DemoDecider()).run(demo_task())
        self.assertEqual(result.status, "escalated")
        self.assertEqual(len(driver.executions), 1)

    def test_scope_password_and_text_capabilities(self):
        snapshot = Snapshot("s", (
            Element("private", PACKAGE, password=True, actions=("click", "set_text")),
            Element("disabled", PACKAGE, enabled=False, actions=("click",)),
            Element("other", "other.app", actions=("click",)),
            Element("missing_value", PACKAGE, "missing", actions=("set_text",)),
            Element("save", PACKAGE, actions=("click",)),
        ), InputState(touch_exploration=True), PACKAGE)
        self.assertEqual({a.id for a in candidates(snapshot, demo_task())}, {"wait", "escalate", "click:save"})
        with self.assertRaises(KrilinError):
            candidates(Snapshot("s", (), InputState(), "other.app"), demo_task())
        # No active window yet (mid-launch): nothing to do, but not out of scope either.
        self.assertEqual({a.id for a in candidates(Snapshot("s", (), InputState(), ""), demo_task())}, {"wait", "escalate"})

    def test_truncated_tree_never_passes_or_acts(self):
        class TruncatedDriver(DemoDriver):
            def observe(self, timeout):
                s = super().observe(timeout)
                return Snapshot(s.id, s.elements, s.input, s.active_package, True)
        result = Runner(TruncatedDriver(), DemoDecider()).run(demo_task())
        self.assertIn("observation limit", result.reason)

    def test_navigation_cycle_is_bounded(self):
        class CyclingDriver(DemoDriver):
            def execute(self, snapshot, action, timeout):
                self.name = "a" if self.name != "a" else "b"
        result = Runner(CyclingDriver(), FixedDecider("click:e2")).run(demo_task())
        self.assertEqual(result.status, "escalated")
        self.assertLess(result.steps, 7)

    def test_usage_totals_are_summed_and_jev_state_is_compact(self):
        class UsageDecider(DemoDecider):
            states = []
            def decide(self, state, actions, timeout):
                self.states.append(state)
                decision = super().decide(state, actions, timeout)
                return Decision(decision.action_id, 1, 1, 0, "test", {"cost": .0001, "input_tokens": 300})
        decider = UsageDecider()
        result = Runner(DemoDriver(), decider).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.usage, {"calls": 2, "cost": .0002, "input_tokens": 600, "output_tokens": 0})
        self.assertEqual(set(decider.states[0]["ui"]), {"active_package", "input", "elements"})
        self.assertEqual(decider.states[0]["success_assertions"], [{"package": PACKAGE, "resource_id": f"{PACKAGE}:id/demo_status", "text": "Saved: Krilin"}])
        self.assertEqual(Runner(DemoDriver(), DemoDecider()).run(demo_task()).usage["calls"], 2)

    def test_escalation_carries_diagnostics_and_success_does_not(self):
        driver = DemoDriver()
        driver.name = "Krilin"
        result = Runner(driver, FixedDecider("escalate")).run(Task(
            "save", (PACKAGE,), (Assertion(PACKAGE, f"{PACKAGE}:id/demo_status", "Saved: Krilin"),
                                 Assertion(PACKAGE, text="Save", absent=True)),
            inputs=(Input(Selector(role="EditText"), "x"), Input(Selector(text_contains="save"), "y"))))
        self.assertEqual(result.status, "escalated")
        diag = result.diagnostics
        self.assertEqual((diag["candidates"], diag["screens"]), (3, 1))
        self.assertEqual(diag["ambiguous_inputs"], [])
        self.assertEqual([u["matches"] for u in diag["unmet_assertions"]], [0, 1])
        self.assertIn("no element matches", diag["unmet_assertions"][0]["hint"])
        self.assertEqual(diag["unmet_assertions"][0]["nearest"], [f"e3 View id={PACKAGE}:id/demo_status text='Not saved'"])
        self.assertIn("still match", diag["unmet_assertions"][1]["hint"])
        self.assertEqual(Runner(DemoDriver(), DemoDecider()).run(demo_task()).diagnostics, {})

    def test_assertions_are_exact_unique_and_package_scoped(self):
        snapshot = Snapshot("s", (Element("e1", "other.app", text="Done"),), InputState(), PACKAGE)
        self.assertFalse(Assertion(PACKAGE, text="Done").satisfied(snapshot))
        snapshot = Snapshot("s", (Element("e1", PACKAGE, text="Done"), Element("e2", PACKAGE, text="Done")), InputState(), PACKAGE)
        self.assertFalse(Assertion(PACKAGE, text="Done").satisfied(snapshot))
        with self.assertRaises(ValueError):
            Task("test", (PACKAGE,), ())


if __name__ == "__main__":
    unittest.main()
