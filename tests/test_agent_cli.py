from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_cli


class AgentCliTests(unittest.TestCase):
    @staticmethod
    def run_silently(argv):
        with (
            patch.object(agent_cli.sys, "stdout", io.StringIO()),
            patch.object(agent_cli.sys, "stderr", io.StringIO()),
        ):
            return agent_cli.main(argv)

    def test_spec_command_writes_versioned_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "spec.json"
            code = self.run_silently(["spec", "--output-json", str(output)])
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "spec")
        self.assertEqual(payload["interface_version"], "4.0.0")
        self.assertEqual(
            payload["result"]["spec_sha256"],
            payload["result"]["runtime_identity"]["agent_spec_sha256"],
        )

    def test_windowed_mode_without_console_still_writes_output_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capabilities.json"
            with (
                patch.object(agent_cli.sys, "stdout", None),
                patch.object(agent_cli.sys, "stderr", None),
                patch.object(agent_cli.sys, "__stdout__", None),
                patch.object(agent_cli.sys, "__stderr__", None),
            ):
                code = agent_cli.main(
                    ["--output-json", str(output), "capabilities"]
                )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["interface_version"], "4.0.0")

    def test_frozen_mode_ignores_present_but_invalid_console_handle(self):
        class InvalidConsole:
            def write(self, value):
                raise OSError(22, "Invalid argument")

            def flush(self):
                raise OSError(22, "Invalid argument")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "spec.json"
            with (
                patch.object(agent_cli.sys, "stdout", InvalidConsole()),
                patch.object(agent_cli.sys, "stderr", InvalidConsole()),
                patch.object(agent_cli.sys, "frozen", True, create=True),
            ):
                code = agent_cli.main(
                    ["--output-json", str(output), "spec"]
                )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])

    def test_command_failure_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "error.json"
            missing = Path(directory) / "missing-task"
            code = self.run_silently(
                ["--output-json", str(output), "status", str(missing)]
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["command"], "status")
        self.assertEqual(payload["error"]["type"], "AgentTaskError")

    def test_argument_error_is_written_to_requested_json(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "argument-error.json"
            code = self.run_silently(["--output-json", str(output)])
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["type"], "AgentCliArgumentError")


if __name__ == "__main__":
    unittest.main()
