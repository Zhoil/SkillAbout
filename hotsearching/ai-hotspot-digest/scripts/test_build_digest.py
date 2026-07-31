import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import build_digest


class BuildDigestTest(unittest.TestCase):
    def test_parse_star_history_and_limits(self):
        page = """<button>Weekly</button><button>All-time</button><ol>
        <li class="relative group"><a href="/foo/one"><span class="text-xs text-gray-400 w">1</span><span title="Up 2">▲</span><span class="text-xs shrink-0 accent-text">+1,234</span></a></li>
        <li class="relative group"><a href="/bar/two"><span class="text-xs text-gray-400 w">2</span><span>–</span><span class="text-xs shrink-0 accent-text">+42</span></a></li></ol>
        {"name":"all/top","stars_total":999,"rank":1}"""
        weekly, all_time = build_digest.parse_star_history(page)
        self.assertEqual([x["title"] for x in weekly], ["foo/one", "bar/two"])
        self.assertEqual([x["title"] for x in all_time], ["all/top"])
        self.assertEqual(weekly[0]["trend"], "▲")
        self.assertEqual(weekly[1]["trend"], "–")
        self.assertEqual(weekly[0]["stars_delta"], "1234")
        self.assertEqual(all_time[0]["stars"], "999")

    def test_parse_last30days_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text(json.dumps({
                "clusters": [{"title": "Summary without URL"}],
                "results": [{"title": "Agent trend", "url": "https://example.com"}],
            }))
            item = build_digest.parse_last30days(path)[0]
            self.assertEqual(item["title"], "Agent trend")
            self.assertEqual(item["url"], "https://example.com")

    def test_negative_limit_rejected(self):
        with self.assertRaises(ValueError):
            build_digest.positive_limit(-1, "weekly")

    def test_stable_render_shape(self):
        recent = [{"title": "AI debt", "url": "https://example.com/a"}]
        weekly = [{"title": "a/b", "rank": "1", "trend": "–", "stars_delta": "51", "url": "https://github.com/a/b"}]
        all_time = [{"title": "c/d", "stars": "1000", "url": "https://github.com/c/d"}]
        sections = [
            build_digest.render_recent(recent, 1, {"https://example.com/a": "人工智能公司债务问题受到关注"}),
            build_digest.render_weekly(weekly, 1),
            build_digest.render_all_time(all_time, 1),
        ]
        actual = "\n\n".join(sections)
        expected = """【近30天 AI 热点】
1. AI debt ：人工智能公司债务问题受到关注
   🌐 https://example.com/a

【Star History Weekly】
1. – a/b +51
   🔎 https://github.com/a/b

【Star History All-time】
1. ：c/d ：1,000 🌟
   🔎 https://github.com/c/d"""
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "\n\n".join(sections))

    def test_message_timestamp_format_is_stable(self):
        generated_at = datetime(2026, 7, 30, 3, 3, 0, tzinfo=timezone.utc)
        message = build_digest.render_message(["SECTION"], generated_at)
        self.assertEqual(
            message,
            "🕒 2026-07-30 11:03:00 GMT+8\n\nAI 热点与开源趋势汇总\n\nSECTION",
        )

    def test_missing_chinese_description_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing Chinese description"):
            build_digest.render_recent([{"title": "AI", "url": "https://example.com"}], 1, {})


if __name__ == "__main__":
    unittest.main()
