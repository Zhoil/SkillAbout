import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import push_digest


class ShellAdapterTest(unittest.TestCase):
    def test_message_passed_via_stdin(self):
        with patch("push_digest.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            push_digest.adapter_shell("hello world", {"command": "cat"})
            call_args = mock_run.call_args
            self.assertEqual(call_args.kwargs["input"], "hello world")

    def test_missing_command_raises(self):
        with self.assertRaisesRegex(ValueError, "command"):
            push_digest.adapter_shell("msg", {})

    def test_nonzero_exit_raises(self):
        with patch("push_digest.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            with self.assertRaisesRegex(RuntimeError, "exited 1"):
                push_digest.adapter_shell("msg", {"command": "false"})


class AdapterRegistryTest(unittest.TestCase):
    def test_all_expected_adapters_registered(self):
        expected = {"shell", "wecom_bot", "slack_webhook", "bark", "feishu_bot", "dingtalk_bot"}
        self.assertEqual(set(push_digest.ADAPTERS.keys()), expected)


class MainEntryTest(unittest.TestCase):
    def _write_config(self, directory: str, adapter: str, extra: dict | None = None) -> Path:
        cfg = {
            "limits": {"last30days": 1, "weekly": 1, "all_time": 1},
            "push": {"adapter": adapter, "target": extra or {}},
        }
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def _write_message(self, directory: str, content: str = "test message") -> Path:
        path = Path(directory) / "msg.txt"
        path.write_text(content, encoding="utf-8")
        return path

    def test_unknown_adapter_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, "unknown_adapter")
            msg = self._write_message(d)
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("push_digest.py")),
                 "--config", str(cfg), "--message-file", str(msg)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("Unknown adapter", result.stderr)

    def test_empty_message_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, "shell", {"command": "echo"})
            msg = self._write_message(d, "   ")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("push_digest.py")),
                 "--config", str(cfg), "--message-file", str(msg)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("empty", result.stderr)

    def test_shell_echo_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            # Use a no-output command so stdout contains only the JSON status line.
            cfg = self._write_config(d, "shell", {"command": "true"})
            msg = self._write_message(d, "hello digest")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("push_digest.py")),
                 "--config", str(cfg), "--message-file", str(msg)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
            # Find the JSON status line (starts with '{') among all output lines.
            json_lines = [l for l in result.stdout.splitlines() if l.strip().startswith("{")]
            self.assertTrue(json_lines, f"No JSON line in stdout: {result.stdout!r}")
            output = json.loads(json_lines[-1])
            self.assertEqual(output["status"], "sent")
            self.assertEqual(output["adapter"], "shell")


if __name__ == "__main__":
    unittest.main()
