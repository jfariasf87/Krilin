import unittest

from krilin.demo import DemoDecider, DemoDriver, demo_task, PACKAGE
from krilin.models import Action, Assertion, Decision, Element, InputState, KrilinError, Snapshot, StaleSnapshot, Task, candidates
from krilin.runner import Limits, Runner


class FixedDecider:
    def __init__(self, action="wait", confidence=1, goal=0):
        self.action, self.confidence, self.goal = action, confidence, goal
        self.states = []

    def decide(self, state, actions, timeout):
        self.states.append(state)
        return Decision(self.action, self.confidence, 1, self.goal, "test")


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
        self.assertIn("Repeated", result.reason)

    def test_changing_snapshot_tokens_do_not_hide_a_stall(self):
        class TokenDriver(DemoDriver):
            def observe(self, timeout):
                self.version += 1
                return super().observe(timeout)
        result = Runner(TokenDriver(), FixedDecider(), sleep=lambda _: None).run(demo_task())
        self.assertEqual(result.steps, 2)
        self.assertIn("Repeated", result.reason)

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

    def test_stale_action_is_reobserved_then_decided_again(self):
        class StaleDriver(DemoDriver):
            stale = True
            def execute(self, snapshot, action, timeout):
                if self.stale:
                    self.stale = False
                    raise StaleSnapshot()
                super().execute(snapshot, action, timeout)
        result = Runner(StaleDriver(), DemoDecider()).run(demo_task())
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.history[0]["outcome"], "stale_snapshot")

    def test_empty_transition_does_not_call_model(self):
        class EmptyDriver(DemoDriver):
            def observe(self, timeout):
                return Snapshot("s", (), InputState(), PACKAGE)
        decider = FixedDecider()
        result = Runner(EmptyDriver(), decider, Limits(max_steps=2), sleep=lambda _: None).run(demo_task())
        self.assertEqual(result.reason, "Step budget exhausted")
        self.assertFalse(decider.states)

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

    def test_assertions_are_exact_unique_and_package_scoped(self):
        snapshot = Snapshot("s", (Element("e1", "other.app", text="Done"),), InputState(), PACKAGE)
        self.assertFalse(Assertion(PACKAGE, text="Done").satisfied(snapshot))
        snapshot = Snapshot("s", (Element("e1", PACKAGE, text="Done"), Element("e2", PACKAGE, text="Done")), InputState(), PACKAGE)
        self.assertFalse(Assertion(PACKAGE, text="Done").satisfied(snapshot))
        with self.assertRaises(ValueError):
            Task("test", (PACKAGE,), ())


if __name__ == "__main__":
    unittest.main()
