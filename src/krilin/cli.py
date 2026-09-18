from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

from .demo import DemoDecider, DemoDriver, demo_task
from .device import adb, device_lock, load_driver, setup
from .jev import JevDecider
from .models import KrilinError, Task
from .runner import Limits, Runner


def configured_decider(provider: str, model: str | None, env_file: Path) -> JevDecider:
    from dotenv import load_dotenv
    load_dotenv(env_file, override=False)
    variable = "OPENROUTER_API_KEY" if provider == "openrouter" else "TYPESAFE_API_KEY"
    key = os.getenv(variable, "")
    if not key:
        raise KrilinError(f"Set {variable} in {env_file} or the environment before using Jev")
    return JevDecider(key, provider, model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded Android UI control for coding agents")
    parser.add_argument("--bridge-config", type=Path, default=Path(".local/bridge.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="Run the offline deterministic fixture; no device or API key")
    install = sub.add_parser("setup", help="Install the companion and enable it alongside existing services")
    install.add_argument("--serial", required=True)
    install.add_argument("--apk", type=Path, default=Path("android/app/build/outputs/apk/debug/app-debug.apk"))
    sub.add_parser("observe", help="Read a structured live snapshot without a model")
    sub.add_parser("smoke", help="Run the companion's isolated demo screen without a model")
    for command in ("run", "serve"):
        run = sub.add_parser(command, help="Run a task with Jev" if command == "run" else "Expose tools over MCP stdio")
        if command == "run":
            run.add_argument("--task", type=Path, required=True)
            run.add_argument("--trace", type=Path)
            run.add_argument("--max-steps", type=int, default=20)
            run.add_argument("--max-seconds", type=float, default=60)
        run.add_argument("--provider", choices=("openrouter", "typesafe"), default="openrouter")
        run.add_argument("--model")
        run.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        if args.command == "demo":
            result = Runner(DemoDriver(), DemoDecider()).run(demo_task())
        elif args.command == "setup":
            with device_lock(args.bridge_config):
                setup(args.serial, args.apk, args.bridge_config)
            print(json.dumps({"status": "configured", "config": str(args.bridge_config)}))
            return
        elif args.command == "serve":
            from .mcp_server import serve
            serve(args.bridge_config, args.provider, args.model, args.env_file)
            return
        else:
            with device_lock(args.bridge_config):
                driver = load_driver(args.bridge_config)
                if args.command == "observe":
                    print(json.dumps(driver.observe().state(), indent=2))
                    return
                if args.command == "smoke":
                    config = json.loads(args.bridge_config.read_text(encoding="utf-8"))
                    adb(config["serial"], "shell", "am", "start", "-W", "-f", "0x10008000", "-n", "dev.krilin.bridge/.DemoActivity")
                    result = Runner(driver, DemoDecider()).run(demo_task())
                else:
                    task = Task.from_dict(json.loads(args.task.read_text(encoding="utf-8")))
                    decider = configured_decider(args.provider, args.model, args.env_file)
                    try:
                        result = Runner(driver, decider, Limits(max_steps=args.max_steps, max_seconds=args.max_seconds), args.trace).run(task)
                    finally:
                        decider.close()
        print(json.dumps(asdict(result), indent=2))
        if result.status != "succeeded":
            sys.exit(2)
    except (KrilinError, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        sys.exit(1)
