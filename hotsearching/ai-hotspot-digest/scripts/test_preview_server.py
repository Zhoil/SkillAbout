import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import preview_server


class PreviewServerTest(unittest.TestCase):
    def test_refresh_uses_fixed_runner_and_manifest_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule = root / "schedule.json"
            schedule.write_text("{}", encoding="utf-8")
            (root / preview_server.REFRESH_MANIFEST).write_text(json.dumps({
                "token": "test-token",
                "schedule_file": str(schedule),
            }), encoding="utf-8")
            controller = preview_server.RefreshController(root)
            self.assertTrue(controller.authorized("test-token"))
            self.assertFalse(controller.authorized("wrong"))
            with patch.object(preview_server.subprocess, "run") as run:
                run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
                controller._run_refresh()
            command = run.call_args.args[0]
            self.assertIn(str(schedule.resolve()), command)
            self.assertIn("--no-open-dashboard", command)
            self.assertEqual(controller.refresh_state, {"state": "complete"})

    def test_missing_schedule_fails_without_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / preview_server.REFRESH_MANIFEST).write_text(json.dumps({
                "token": "test-token",
                "schedule_file": str(root / "missing.json"),
            }), encoding="utf-8")
            controller = preview_server.RefreshController(root)
            with patch.object(preview_server.subprocess, "run") as run:
                controller._run_refresh()
            run.assert_not_called()
            self.assertEqual(controller.refresh_state["state"], "failed")


if __name__ == "__main__":
    unittest.main()
