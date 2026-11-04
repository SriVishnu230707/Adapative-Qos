"""Safety checks for privilege gating and ownership-based namespace cleanup."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("preflight", Path(__file__).resolve().parents[1] / "lab/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightSafetyTests(unittest.TestCase):
    def setUp(self):
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)

    def test_existing_namespace_is_never_claimed_or_deleted(self):
        probe = preflight.Probe()
        with patch.object(probe, "command", side_effect=RuntimeError("name already exists")) as command:
            with self.assertRaises(RuntimeError):
                preflight.network_checks(probe)
            probe.cleanup()
        self.assertEqual(probe.owned, [])
        self.assertEqual(command.call_count, 1)

    def test_second_creation_failure_cleans_only_first_owned_namespace(self):
        probe = preflight.Probe()
        calls = []

        def command(args, **_):
            calls.append(args)
            if len(calls) == 2:
                raise RuntimeError("second creation failed")
            return ""

        with patch.object(probe, "command", side_effect=command):
            with self.assertRaises(RuntimeError):
                preflight.network_checks(probe)
            probe.cleanup()
        self.assertEqual(len(probe.owned), 1)
        self.assertEqual(calls[2], ["ip", "netns", "delete", probe.owned[0]])
        self.assertEqual(calls[3], ["ip", "netns", "list"])
        self.assertEqual(probe.checks[-1]["status"], "pass")

    def test_failed_cleanup_does_not_skip_remaining_owned_namespace(self):
        probe = preflight.Probe()
        probe.owned = ["aqpf-owned-a", "aqpf-owned-b"]
        with patch.object(probe, "command", side_effect=[RuntimeError("busy"), "", ""]) as command:
            probe.cleanup()
        self.assertEqual(command.call_args_list[1].args[0], ["ip", "netns", "delete", "aqpf-owned-a"])
        self.assertEqual([item["status"] for item in probe.checks], ["fail", "pass"])

    def test_unprivileged_run_records_failure_without_starting_processes(self):
        workspace = Path(__file__).resolve().parents[1]
        temporary_root = (workspace / "work/test-tmp").resolve()
        self.assertTrue(temporary_root.is_relative_to(workspace))
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="aqpf-test-", dir=temporary_root) as directory:
            destination = Path(directory) / "manifest.json"
            with patch.object(sys, "argv", ["preflight.py", "--output", str(destination)]), patch.object(preflight.platform, "system", return_value="Linux"), patch.object(preflight.platform, "release", return_value="6.8.0-test"), patch.object(preflight.platform, "machine", return_value="x86_64"), patch.object(preflight.os, "geteuid", return_value=1000, create=True), patch.object(preflight.subprocess, "run") as run:
                result = preflight.main()
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(destination.read_text())["checks"][0]["name"], "privileges")
            run.assert_not_called()

    def test_timed_out_command_is_a_failure(self):
        probe = preflight.Probe()
        with patch.object(preflight.subprocess, "run", side_effect=subprocess.TimeoutExpired("ip", 15)):
            self.assertFalse(probe.attempt("timeout", lambda: probe.command(["ip", "netns", "list"])))
        self.assertEqual(probe.checks[-1]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
