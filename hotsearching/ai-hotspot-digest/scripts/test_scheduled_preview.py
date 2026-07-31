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


if __name__ == "__main__":
    unittest.main()
