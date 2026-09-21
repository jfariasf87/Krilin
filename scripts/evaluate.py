"""Run fixture scenarios with Jev several times and report what actually happened.

python scripts/evaluate.py --bridge-config .local/emulator-b.json --live --runs 3
python scripts/evaluate.py --live --scenarios examples/fixture/hide-done.json --runs 5 --max-cost 0.5

Without --live the scenario files are only parsed and the launch precondition is exercised. Results go to
.local/eval.json; traces (with observations when --record) to .local/eval/<scenario>-<run>.jsonl.
Each Jev call bills the configured OpenRouter key; --max-cost stops the session when the reported
cost passes that limit. These are development measurements on one emulator, not a benchmark.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import glob
import json
from pathlib import Path
import sys
import time

from krilin.cli import configured_decider
from krilin.device import configured_serial, device_lock, launch, load_driver
from krilin.models import KrilinError
from krilin.scenario import Scenario, run_scenario


def summarize(runs: list[dict]) -> list[dict]:
    rows = []
    for name in sorted({r["name"] for r in runs}):
        mine = [r for r in runs if r["name"] == name]
        ok = [r for r in mine if r["status"] == "succeeded"]
        reasons = Counter(r["reason"] for r in mine if r["status"] != "succeeded")
        rows.append({
            "scenario": name, "runs": len(mine), "succeeded": len(ok),
            "median_ms": sorted(r["elapsed_ms"] for r in mine)[len(mine) // 2],
            "mean_steps": round(sum(r["runner_steps"] for r in mine) / len(mine), 1),
            "mean_calls": round(sum(r["usage"]["calls"] for r in mine) / len(mine), 1),
            "cost": round(sum(r["usage"]["cost"] for r in mine), 6),
            "failed_steps": Counter(r["failed_step"] for r in mine if r["status"] != "succeeded"),
            "reasons": reasons.most_common(3),
        })
    return rows


def table(rows: list[dict]) -> str:
    lines = ["| Scenario | Runs | Succeeded | Median ms | Mean runner steps | Mean Jev calls | Cost (USD) | Escalations |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for r in rows:
        reasons = "; ".join(f"step {step}: {reason[:70]}" for (reason, count), step in
                            zip(r["reasons"], list(r["failed_steps"].elements())[:3])) or "—"
        lines.append(f"| {r['scenario']} | {r['runs']} | {r['succeeded']} | {r['median_ms']} | {r['mean_steps']} | "
                     f"{r['mean_calls']} | {r['cost']:.4f} | {reasons} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bridge-config", type=Path, default=Path(".local/bridge.json"))
    parser.add_argument("--scenarios", nargs="*", default=sorted(glob.glob("examples/fixture/*.json")))
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--live", action="store_true", help="Use Jev through the key in .env (spends credits)")
    parser.add_argument("--record", action="store_true", help="Also write every observation into the traces")
    parser.add_argument("--max-cost", type=float, default=1.0, help="Stop when the reported cost passes this (USD)")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--out", type=Path, default=Path(".local/eval.json"))
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    scenarios = [Scenario.from_dict(json.loads(Path(p).read_text(encoding="utf-8"))) for p in args.scenarios]
    if not scenarios:
        print("No scenario files found", file=sys.stderr)
        return 1
    serial = configured_serial(args.bridge_config)
    launcher = lambda l: launch(serial, l.package, l.activity, l.clear_task)  # noqa: E731
    if not args.live:
        for scenario in scenarios:
            if scenario.launch:
                launcher(scenario.launch)
        print(f"Parsed {len(scenarios)} scenarios; launch preconditions run. Add --live to run them with Jev.")
        return 0
    runs: list[dict] = []
    spent = 0.0
    trace_dir = args.out.parent / "eval"
    with device_lock(args.bridge_config):
        driver = load_driver(args.bridge_config)
        decider = configured_decider("openrouter", None, args.env_file)
        try:
            for scenario in scenarios:
                for index in range(1, args.runs + 1):
                    if spent > args.max_cost:
                        print(f"Stopping: reported cost {spent:.4f} USD exceeds --max-cost", file=sys.stderr)
                        break
                    trace = trace_dir / f"{scenario.name}-{index}.jsonl"
                    trace.unlink(missing_ok=True)
                    result = run_scenario(scenario, driver, decider, launcher, trace=trace, record=args.record)
                    spent += result.usage["cost"]
                    runs.append({"name": scenario.name, "run": index, "status": result.status, "reason": result.reason,
                                 "failed_step": result.failed_step, "elapsed_ms": result.elapsed_ms,
                                 "runner_steps": sum(s.steps for s in result.steps), "usage": result.usage,
                                 "trace": str(trace), "steps": [asdict(s) for s in result.steps]})
                    print(f"{scenario.name} #{index}: {result.status} in {result.elapsed_ms} ms, "
                          f"{result.usage['calls']} calls, {result.usage['cost']:.5f} USD — {result.reason}")
                    time.sleep(.5)
        finally:
            decider.close()
    rows = summarize(runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "serial": serial,
                                    "summary": rows, "runs": runs}, indent=2, ensure_ascii=False, default=list) + "\n",
                        encoding="utf-8")
    print()
    print(table(rows))
    print(f"\nTotal reported cost: {spent:.5f} USD. Details: {args.out}")
    return 0 if runs and all(r["status"] == "succeeded" for r in runs) else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KrilinError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        sys.exit(1)
