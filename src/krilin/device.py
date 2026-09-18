from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
from typing import Iterator

from .bridge import BridgeDriver
from .models import KrilinError

COMPONENT = "dev.krilin.bridge/.BridgeService"


def find_adb() -> str:
    if found := shutil.which("adb"):
        return found
    roots = [os.getenv("ANDROID_HOME"), os.getenv("ANDROID_SDK_ROOT")]
    if os.name == "nt":
        roots.append(str(Path(os.getenv("LOCALAPPDATA", "")) / "Android/Sdk"))
    else:
        roots.extend([str(Path.home() / "Android/Sdk"), str(Path.home() / "Library/Android/sdk")])
    for root in filter(None, roots):
        path = Path(root) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        if path.is_file():
            return str(path)
    raise KrilinError("ADB not found. Add Android platform-tools to PATH or set ANDROID_HOME")


def adb(serial: str, *args: str) -> str:
    command = [find_adb(), "-s", serial, *args]
    if args and args[0] == "shell":
        command = [find_adb(), "-s", serial, "shell", shlex.join(args[1:])]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        # Command arguments can contain the bridge token; never include them in errors.
        raise KrilinError(f"ADB command failed ({type(exc).__name__}); check device connection") from exc


def setup(serial: str, apk: Path, config: Path) -> None:
    if not apk.is_file():
        raise KrilinError("Build the companion APK first; see android/README.md")
    if adb(serial, "get-state") != "device":
        raise KrilinError("The selected device is not ready")
    adb(serial, "install", "-r", str(apk.resolve()))
    token = secrets.token_hex(32)
    adb(serial, "shell", "am", "start", "-W", "-n", "dev.krilin.bridge/.MainActivity", "--es", "token", token)
    previous = adb(serial, "shell", "settings", "get", "secure", "enabled_accessibility_services")
    enabled = [] if previous in {"", "null"} else previous.split(":")
    # Android can normalize a component to its fully qualified class name.
    equivalents = {COMPONENT, "dev.krilin.bridge/dev.krilin.bridge.BridgeService"}
    if not equivalents.intersection(enabled):
        enabled.append(COMPONENT)
    if any(not re.fullmatch(r"[A-Za-z0-9_.$]+/[A-Za-z0-9_.$]+", s) for s in enabled):
        raise KrilinError("Unexpected accessibility service setting; enable Krilin manually")
    adb(serial, "shell", "settings", "put", "secure", "enabled_accessibility_services", ":".join(enabled))
    adb(serial, "shell", "settings", "put", "secure", "accessibility_enabled", "1")
    port = int(adb(serial, "forward", "tcp:0", "tcp:8765"))
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"serial": serial, "port": port, "token": token}, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        config.chmod(0o600)


def load_driver(config: Path) -> BridgeDriver:
    try:
        settings = json.loads(config.read_text(encoding="utf-8"))
        return BridgeDriver(settings["token"], int(settings["port"]))
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise KrilinError("Missing or invalid bridge configuration; run krilin setup") from exc


@contextmanager
def device_lock(config: Path) -> Iterator[None]:
    """Fail promptly when two processes try to control the same configured session."""
    config.parent.mkdir(parents=True, exist_ok=True)
    with config.with_suffix(".lock").open("a+b") as handle:
        handle.write(b"\0")
        handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise KrilinError("This device session is busy in another Krilin process") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
