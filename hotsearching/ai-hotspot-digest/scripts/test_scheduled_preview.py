from datetime import datetime, timezone
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
