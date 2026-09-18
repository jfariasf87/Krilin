"""Opt-in development validation. Only interacts with the companion's demo screen.

python scripts/validate_emulator.py           # deterministic driver checks
python scripts/validate_emulator.py --live    # also uses the key from .env
"""

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

from krilin.cli import configured_decider
from krilin.demo import DemoDecider, demo_task
from krilin.device import adb, device_lock, load_driver
from krilin.models import Action, Assertion, StaleSnapshot
from krilin.runner import Runner


@contextmanager
def talkback_enabled(serial, driver):
    """Temporarily enable an installed TalkBack service and restore original state."""
    component = "com.google.android.marvin.talkback/.TalkBackService"
    previous = adb(serial, "shell", "settings", "get", "secure", "enabled_accessibility_services")
    services = [] if previous in {"", "null"} else previous.split(":")
    if not any("com.google.android.marvin.talkback/" in s for s in services):
        services.append(component)
    try:
        adb(serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", ":".join(services))
        deadline = time.monotonic() + 8
        while not driver.observe().input.touch_exploration:
            if time.monotonic() > deadline:
                raise AssertionError("TalkBack did not enable touch exploration; install/enable it first")
            time.sleep(.1)
        yield
    finally:
        if previous == "null":
            adb(serial, "shell", "settings", "delete", "secure", "enabled_accessibility_services")
        else:
            adb(serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--talkback", action="store_true", help="Temporarily enable installed TalkBack, verify semantic control, then restore")
    parser.add_argument("--bridge-config", type=Path, default=Path(".local/bridge.json"))
    args = parser.parse_args()
    config = json.loads(args.bridge_config.read_text(encoding="utf-8"))
    with device_lock(args.bridge_config):
        driver = load_driver(args.bridge_config)

        def reset():
            adb(config["serial"], "shell", "am", "start", "-W", "-f", "0x10008000", "-n", "dev.krilin.bridge/.DemoActivity")

        reset()
        scripted = Runner(driver, DemoDecider()).run(demo_task())
        results = {"scripted": asdict(scripted)}
        if scripted.status != "succeeded":
            print(json.dumps(results, indent=2))
            return 2

        # A second observer invalidates the first token before any action executes.
        first = driver.observe()
        driver.observe()
        try:
            driver.execute(first, Action("back", "back"))
        except StaleSnapshot:
            results["stale_token_rejected"] = True
        else:
            raise AssertionError("A superseded token executed")

        # Unicode must go through ACTION_SET_TEXT, with no adb shell input encoding.
        value = "Krilin café 日本語"
        deadline = time.monotonic() + 5
        while True:
            snapshot = driver.observe()
            field = next(e for e in snapshot.elements if e.resource_id.endswith("/demo_name"))
            try:
                driver.execute(snapshot, Action(f"set_text:{field.id}", "set_text", field.id, value))
                break
            except StaleSnapshot:
                if time.monotonic() > deadline:
                    raise
        observed = driver.observe()
        assert Assertion("dev.krilin.bridge", "dev.krilin.bridge:id/demo_name", value).satisfied(observed)
        results["unicode_verified"] = True

        if args.talkback:
            with talkback_enabled(config["serial"], driver):
                reset()
                results["talkback"] = asdict(Runner(driver, DemoDecider()).run(demo_task()))
                if results["talkback"]["status"] == "succeeded":
                    assert results["talkback"]["snapshot"]["input"]["touch_exploration"]

        if args.live:
            reset()
            decider = configured_decider("openrouter", None, Path(".env"))
            try:
                results["live"] = asdict(Runner(driver, decider, trace=Path(".local/live.jsonl")).run(demo_task()))
            finally:
                decider.close()
        Path(".local").mkdir(exist_ok=True)
        Path(".local/validation.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        summary = {name: {k: value[k] for k in ("status", "reason", "steps", "elapsed_ms", "history")}
                   if isinstance(value, dict) else value for name, value in results.items()}
        print(json.dumps(summary, indent=2))
        return 0 if all(v["status"] == "succeeded" for v in results.values() if isinstance(v, dict)) else 2


if __name__ == "__main__":
    sys.exit(main())
