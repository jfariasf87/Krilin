from dataclasses import asdict
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch

from krilin.bridge import BridgeDriver
from krilin.demo import DemoDriver
from krilin.device import COMPONENT, device_lock, setup
from krilin.models import Action, KrilinError, Snapshot, StaleSnapshot


class BridgeTests(unittest.TestCase):
    def roundtrip(self, response, call):
        captured = []
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            def handle():
                conn, _ = listener.accept()
                with conn, conn.makefile("rb") as reader:
                    captured.append(json.loads(reader.readline()))
                    conn.sendall(json.dumps(response).encode() + b"\n")
            thread = Thread(target=handle, daemon=True)
            thread.start()
            try:
                return call(BridgeDriver("a" * 64, listener.getsockname()[1])), captured
            finally:
                thread.join(2)

    def test_live_snapshot_wire_and_auth(self):
        wire = asdict(DemoDriver().observe(1))
        wire["protocol"] = 1
        wire["snapshot_id"] = wire.pop("id")
        result, captured = self.roundtrip(wire, lambda d: d.observe())
        self.assertEqual(len(result.elements), 3)
        self.assertEqual(captured[0]["token"], "a" * 64)
        self.assertEqual(captured[0]["method"], "observe")

    def test_stale_is_distinct_from_uncertain_transport(self):
        with self.assertRaises(StaleSnapshot):
            self.roundtrip({"error": "stale_snapshot"}, lambda d: d.execute(DemoDriver().observe(1), Action("click:e2", "click", "e2")))
        with self.assertRaises(KrilinError):
            self.roundtrip({"error": "unauthorized"}, lambda d: d.observe())

    def test_invalid_snapshot_rejected(self):
        with self.assertRaises(KrilinError):
            Snapshot.from_dict({"protocol": 999})

    def test_setup_preserves_talkback_and_allocates_forward(self):
        calls = []
        def fake_adb(serial, *args):
            calls.append(args)
            if args == ("get-state",):
                return "device"
            if args[:4] == ("shell", "settings", "get", "secure"):
                return "com.google.android.marvin.talkback/.TalkBackService"
            if args[0] == "forward":
                return "12345"
            return ""
        with TemporaryDirectory() as tmp, patch("krilin.device.adb", fake_adb):
            apk = Path(tmp) / "test.apk"
            apk.touch()
            config = Path(tmp) / "bridge.json"
            setup("emulator-1", apk, config)
            updated = next(a for a in calls if a[:5] == ("shell", "settings", "put", "secure", "enabled_accessibility_services"))
            self.assertIn("talkback/.TalkBackService", updated[5])
            self.assertTrue(updated[5].endswith(COMPONENT))
            self.assertEqual(json.loads(config.read_text())["port"], 12345)

    def test_two_process_sessions_cannot_share_lock(self):
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "bridge.json"
            with device_lock(config):
                with self.assertRaises(KrilinError):
                    with device_lock(config):
                        self.fail("Second device owner acquired the lock")
