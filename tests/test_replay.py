import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from krilin.demo import DemoDecider, DemoDriver, demo_task
from krilin.models import KrilinError
from krilin.replay import load_recording, run_ids
from krilin.runner import Runner

FIXTURE = Path(__file__).parent / "fixtures" / "fixture-scroll-to-row.jsonl"


class ReplayTests(unittest.TestCase):
    def test_recorded_offline_run_replays_to_the_same_verdict(self):
        with TemporaryDirectory() as tmp:
            trace = Path(tmp) / "run.jsonl"
            original = Runner(DemoDriver(), DemoDecider(), trace=trace, record=True).run(demo_task())
            task, driver, decider = load_recording(trace)
            replayed = Runner(driver, decider).run(task)
        self.assertEqual((replayed.status, replayed.steps), (original.status, original.steps))
        self.assertEqual([a.id for a in driver.executed], ["set_text:e1", "click:e2"])
        self.assertEqual([d["action_id"] for d in [h["decision"] for h in replayed.history]], ["set_text:e1", "click:e2"])

    def test_real_fixture_recording_replays_through_the_loop(self):
        """A live Jev run on the companion's fixture screen (emulator, 2026-09-21), sanitized to app elements."""
        ids = run_ids(FIXTURE)
        self.assertEqual(len(ids), 2)  # dismiss the interstitial, then scroll to Note 28
        verdicts = []
        for run_id in ids:
            task, driver, decider = load_recording(FIXTURE, run_id)
            result = Runner(driver, decider, sleep=lambda _: None).run(task)
            verdicts.append((result.status, [a.id for a in driver.executed]))
        self.assertEqual(verdicts, [("succeeded", ["click:e8"]), ("succeeded", ["scroll_forward:e10"])])
        recorded = [json.loads(l)["status"] for l in FIXTURE.read_text(encoding="utf-8").splitlines() if '"event": "result"' in l]
        self.assertEqual(recorded, ["succeeded", "succeeded"])

    def test_unusable_recordings_are_reported(self):
        with TemporaryDirectory() as tmp:
            trace = Path(tmp) / "run.jsonl"
            Runner(DemoDriver(), DemoDecider(), trace=trace).run(demo_task())  # Not recorded: no observations.
            with self.assertRaisesRegex(KrilinError, "no observations"):
                load_recording(trace)
            with self.assertRaisesRegex(KrilinError, "Unreadable"):
                load_recording(Path(tmp) / "missing.jsonl")


if __name__ == "__main__":
    unittest.main()
