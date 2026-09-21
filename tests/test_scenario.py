import unittest

from krilin.demo import DemoDecider, DemoDriver, PACKAGE
from krilin.models import Decision, KrilinError
from krilin.runner import Limits
from krilin.scenario import Launch, Scenario, Step, run_scenario

NAME = f"{PACKAGE}:id/demo_name"
STATUS = f"{PACKAGE}:id/demo_status"


def scenario_dict(**overrides):
    data = {
        "name": "enter-then-save",
        "allowed_packages": [PACKAGE],
        "launch": {"package": PACKAGE, "activity": ".DemoActivity"},
        "steps": [
            {"goal": "Enter Krilin in Name", "assertions": [{"package": PACKAGE, "resource_id": NAME, "text": "Krilin"}],
             "text_values": {NAME: "Krilin"}},
            {"goal": "Press Save", "assertions": [{"package": PACKAGE, "resource_id": STATUS, "text": "Saved: Krilin"}],
             "text_values": {NAME: "Krilin"}, "max_steps": 3},
        ],
    }
    data.update(overrides)
    return data


class NeverDecider:
    def decide(self, state, actions, timeout):
        return Decision("escalate", 1, 1, 0, "test")


class ScenarioTests(unittest.TestCase):
    def test_launch_runs_once_before_ordered_steps(self):
        launches, driver = [], DemoDriver()
        scenario = Scenario.from_dict(scenario_dict())
        result = run_scenario(scenario, driver, DemoDecider(), launcher=lambda l: launches.append((l, driver.version)))
        self.assertEqual(launches, [(Launch(PACKAGE, ".DemoActivity", True), 0)])  # Before any UI action.
        self.assertEqual((result.status, result.failed_step), ("succeeded", None))
        self.assertEqual([r.status for r in result.steps], ["succeeded", "succeeded"])
        self.assertEqual([r.steps for r in result.steps], [1, 1])
        self.assertEqual(result.usage["calls"], 2)
        self.assertEqual(scenario.steps[1].max_steps, 3)

    def test_stops_at_first_escalation_and_reports_the_step(self):
        driver = DemoDriver()
        result = run_scenario(Scenario.from_dict(scenario_dict(launch=None)), driver, NeverDecider())
        self.assertEqual((result.status, result.failed_step, len(result.steps)), ("escalated", 1, 1))
        self.assertIn("Step 1 escalated", result.reason)
        self.assertEqual(driver.version, 0)

    def test_shared_budget_bounds_later_steps(self):
        now = [0.0]

        class SlowDecider(DemoDecider):
            def decide(self, state, actions, timeout):
                now[0] += 20
                return super().decide(state, actions, timeout)

        def run(max_seconds):
            now[0] = 0
            return run_scenario(Scenario.from_dict(scenario_dict(launch=None, max_seconds=max_seconds)),
                                DemoDriver(), SlowDecider(), clock=lambda: now[0], sleep=lambda _: None)

        self.assertEqual(run(200).status, "succeeded")
        short = run(35)  # Step 1 uses 20 s; step 2 gets only the 15 s left, not its own 60 s, and needs 20.
        self.assertEqual((short.status, short.failed_step), ("escalated", 2))
        self.assertIn("deadline", short.reason)

    def test_launch_failures(self):
        scenario = Scenario.from_dict(scenario_dict())
        with self.assertRaises(KrilinError):
            run_scenario(scenario, DemoDriver(), DemoDecider())

        def broken(launch):
            raise KrilinError("Launch of dev.krilin.bridge failed")

        result = run_scenario(scenario, DemoDriver(), DemoDecider(), launcher=broken)
        self.assertEqual((result.status, result.failed_step, result.steps), ("escalated", 0, []))
        self.assertIn("Launch failed", result.reason)

    def test_from_dict_validation_and_inherited_defaults(self):
        parsed = Scenario.from_dict(scenario_dict(allow_back=True))
        self.assertTrue(all(step.task.allow_back and step.task.allowed_packages == (PACKAGE,) for step in parsed.steps))
        self.assertEqual(parsed.launch, Launch(PACKAGE, ".DemoActivity"))
        for bad in (scenario_dict(steps=[]), scenario_dict(extra=1), scenario_dict(launch={"package": "other.app"}),
                    scenario_dict(launch={"package": PACKAGE, "flags": 1}), scenario_dict(max_seconds=0),
                    {"name": "x"}, scenario_dict(steps=["not an object"])):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Scenario.from_dict(bad)
        with self.assertRaises(ValueError):
            Scenario.from_dict(scenario_dict(steps=[{**scenario_dict()["steps"][0], "max_steps": 0}]))
        with self.assertRaises(ValueError):
            Step(parsed.steps[0].task, max_seconds=301)
        Limits(max_steps=1)  # The same range validator backs step budgets.


if __name__ == "__main__":
    unittest.main()
