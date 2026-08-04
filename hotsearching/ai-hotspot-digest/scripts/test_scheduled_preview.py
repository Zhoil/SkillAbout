from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scheduled_preview


class ScheduledPreviewTest(unittest.TestCase):
    def test_next_run_same_day(self):
        now = datetime(2026, 7, 30, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            scheduled_preview.next_run(now, "09:00:00").isoformat(),
            "2026-07-30T09:00:00+08:00",
        )

    def test_next_run_rolls_to_tomorrow(self):
        now = datetime(2026, 7, 30, 2, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            scheduled_preview.next_run(now, "09:00:00").isoformat(),
            "2026-07-31T09:00:00+08:00",
        )

    def test_send_reminder_mentions_preview_path(self):
        preview = Path("/tmp/previews/latest.txt")
        reminder = scheduled_preview.send_reminder(preview)
        self.assertIn(str(preview), reminder)
        self.assertIn("预览已生成", reminder)
        self.assertNotIn("京ME", reminder)

    def test_load_schedule_fetch_last30days_requires_skill_dir(self):
        """fetch_last30days=true without skill_dir must raise."""
        import json, tempfile
        cfg = {
            "enabled": True,
            "time": "10:00:00",
            "timezone": "GMT+8",
            "digest_config": "references/config.example.json",
            "last30days_file": "/tmp/last30days.json",
            "annotations_file": "/tmp/annotations.json",
            "output_dir": "/tmp/output",
            "fetch_last30days": True,
            "last30days_skill_dir": "",   # <-- empty, should fail
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "schedule.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "last30days_skill_dir"):
                scheduled_preview.load_schedule(path)

    def test_load_schedule_fetch_last30days_ok_with_skill_dir(self):
        """fetch_last30days=true with a non-empty skill_dir should not raise."""
        import json, tempfile
        cfg = {
            "enabled": True,
            "time": "10:00:00",
            "timezone": "GMT+8",
            "digest_config": "references/config.example.json",
            "last30days_file": "/tmp/last30days.json",
            "annotations_file": "/tmp/annotations.json",
            "output_dir": "/tmp/output",
            "fetch_last30days": True,
            "last30days_skill_dir": "/some/path/last30days",
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "schedule.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            result = scheduled_preview.load_schedule(path)
            self.assertTrue(result.get("fetch_last30days"))

    def test_load_schedule_without_fetch_does_not_require_skill_dir(self):
        """fetch_last30days=false (default) must not require last30days_skill_dir."""
        import json, tempfile
        cfg = {
            "enabled": True,
            "time": "10:00:00",
            "timezone": "GMT+8",
            "digest_config": "references/config.example.json",
            "last30days_file": "/tmp/last30days.json",
            "annotations_file": "/tmp/annotations.json",
            "output_dir": "/tmp/output",
            # fetch_last30days omitted → defaults to false
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "schedule.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            result = scheduled_preview.load_schedule(path)
            self.assertFalse(result.get("fetch_last30days"))

    def test_generate_preview_updates_latest_text_and_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schedule_file = root / "references" / "schedule.json"
            schedule_file.parent.mkdir()
            config_path = root / "config.json"
            hotspots_path = root / "hotspots.json"
            annotations_path = root / "annotations.json"
            output_dir = root / "previews"
            for path in (config_path, hotspots_path, annotations_path):
                path.write_text("{}", encoding="utf-8")
            config = {
                "digest_config": str(config_path),
                "last30days_file": str(hotspots_path),
                "annotations_file": str(annotations_path),
                "output_dir": str(output_dir),
            }
            commands = []

            def fake_run(command, check):
                commands.append(command)
                output = Path(command[command.index("--output") + 1])
                output.write_text("terminal preview", encoding="utf-8")
                output.with_suffix(".html").write_text("<html>dashboard</html>", encoding="utf-8")

            with patch.object(scheduled_preview.subprocess, "run", side_effect=fake_run):
                scheduled_preview.generate_preview(
                    schedule_file,
                    config,
                    datetime(2026, 7, 30, 3, 3, 0, tzinfo=timezone.utc),
                )

            self.assertEqual((output_dir / "latest.txt").read_text(), "terminal preview")
            self.assertEqual((output_dir / "latest.html").read_text(), "<html>dashboard</html>")
            self.assertIn("--no-open-dashboard", commands[0])

    def test_open_preview_dashboard_uses_default_browser(self):
        preview = Path("/tmp/digest.txt")
        with patch.object(scheduled_preview, "open_dashboard", return_value=True) as browser_open:
            self.assertTrue(scheduled_preview.open_preview_dashboard(preview))
        self.assertEqual(browser_open.call_args.args[0], Path("/tmp/digest.html"))


if __name__ == "__main__":
    unittest.main()
