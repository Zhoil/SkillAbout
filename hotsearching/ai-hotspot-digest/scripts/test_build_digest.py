import json
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_digest
import preview_browser


def _make_weekly_items(count: int = 5) -> list[dict[str, str]]:
    """Create mock weekly items for testing."""
    trends = ["▲", "▼", "–", "▲", "▼", "▲", "–", "▼", "▲", "▲",
              "▼", "–", "▲", "▼", "▲", "–", "▼", "▲", "–", "▼"]
    items = []
    for i in range(count):
        items.append({
            "title": f"owner/repo-{i + 1}",
            "rank": str(i + 1),
            "trend": trends[i % len(trends)],
            "stars_delta": str(1000 - i * 50),
            "url": f"https://github.com/owner/repo-{i + 1}",
        })
    return items


class BuildDigestTest(unittest.TestCase):
    def test_hotspot_cooldown_rotates_after_day_boundary(self):
        items = [
            {"title": title, "url": f"https://example.com/{title.lower()}"}
            for title in ("A", "B", "C", "D")
        ]
        shown: dict[str, str] = {}
        first, excluded = build_digest.select_recent_with_cooldown(
            items, 2, shown, date(2026, 8, 4), 7
        )
        self.assertEqual([item["title"] for item in first], ["A", "B"])
        self.assertEqual(excluded, 0)

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "cooldown.json"
            build_digest.save_cooldown_state(
                state_path, shown, first, date(2026, 8, 4), 7
            )
            shown = build_digest.load_cooldown_state(state_path)

        same_day, excluded = build_digest.select_recent_with_cooldown(
            items, 2, shown, date(2026, 8, 4), 7
        )
        self.assertEqual([item["title"] for item in same_day], ["C", "D"])
        self.assertEqual(excluded, 2)

        for item in same_day:
            shown[item["url"]] = "2026-08-04"

        next_day, excluded = build_digest.select_recent_with_cooldown(
            items, 2, shown, date(2026, 8, 5), 7
        )
        self.assertEqual(next_day, [])
        self.assertEqual(excluded, 4)

        after_cooldown, excluded = build_digest.select_recent_with_cooldown(
            items, 2, shown, date(2026, 8, 11), 7
        )
        self.assertEqual([item["title"] for item in after_cooldown], ["A", "B"])
        self.assertEqual(excluded, 0)

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
                "results": [{"title": "Agent trend", "url": "https://example.com", "summary": "智能体获得新的工具调用能力。", "source": "weixin"}],
            }))
            item = build_digest.parse_last30days(path)[0]
            self.assertEqual(item["title"], "Agent trend")
            self.assertEqual(item["url"], "https://example.com")
            self.assertEqual(item["summary"], "智能体获得新的工具调用能力。")
            self.assertEqual(item["source"], "weixin")

    def test_negative_limit_rejected(self):
        with self.assertRaises(ValueError):
            build_digest.positive_limit(-1, "weekly")

    def test_stable_render_shape(self):
        recent = [{"title": "AI debt", "url": "https://example.com/a"}]
        weekly = [{"title": "a/b", "rank": "1", "trend": "–", "stars_delta": "51", "url": "https://github.com/a/b", "description": "每周趋势项目。"}]
        all_time = [{"title": "c/d", "stars": "1000", "url": "https://github.com/c/d", "description": "长期热门项目。"}]
        sections = [
            build_digest.render_recent(recent, 1, {"https://example.com/a": "人工智能公司债务问题受到关注"}),
            build_digest.render_weekly(weekly, 1),
            build_digest.render_all_time(all_time, 1),
        ]
        actual = "\n\n".join(sections)
        expected = """【近30天 研发工具与技能热点】
1. AI debt ：人工智能公司债务问题受到关注
   🌐 https://example.com/a

【Star History Weekly】
1. – a/b +51
   💡 每周趋势项目。
   🔎 https://github.com/a/b

【Star History All-time】
1. ：c/d ：1,000 🌟
   💡 长期热门项目。
   🔎 https://github.com/c/d"""
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "\n\n".join(sections))

    def test_message_timestamp_format_is_stable(self):
        generated_at = datetime(2026, 7, 30, 3, 3, 0, tzinfo=timezone.utc)
        message = build_digest.render_message(["SECTION"], generated_at)
        self.assertEqual(
            message,
            "🕒 2026-07-30 11:03:00 GMT+8\n\n研发工具与技能热点及开源趋势汇总\n\nSECTION",
        )

    def test_dashboard_contains_dynamic_and_terminal_views(self):
        generated_at = datetime(2026, 7, 30, 3, 3, 0, tzinfo=timezone.utc)
        recent = [{"title": "Agent trend", "url": "https://example.com/a"}]
        weekly = [{
            "title": "a/b", "rank": "1", "trend": "▲",
            "stars_delta": "51", "url": "https://github.com/a/b",
            "description": "实用的开发工具。",
        }]
        all_time = [{"title": "c/d", "stars": "1000", "url": "https://github.com/c/d", "description": "热门的开源项目。"}]
        message = build_digest.render_message(["SECTION"], generated_at)
        dashboard = build_digest.render_dashboard(
            recent,
            weekly,
            all_time,
            {"last30days": 1, "weekly": 1, "all_time": 1},
            {"https://example.com/a": "研发智能体获得新能力"},
            message,
            generated_at,
        )
        self.assertIn("动态看板", dashboard)
        self.assertIn("终端内容", dashboard)
        self.assertIn("Agent trend", dashboard)
        self.assertIn("研发智能体获得新能力", dashboard)
        self.assertIn("2026-07-30 11:03:00 GMT+8", dashboard)
        self.assertIn("prefers-reduced-motion", dashboard)
        self.assertIn("实时刷新", dashboard)
        self.assertIn("refreshDashboard(false)", dashboard)
        self.assertIn("triggerGeneration", dashboard)
        self.assertIn("latest preview unavailable", dashboard)
        self.assertIn("实用的开发工具。", dashboard)
        self.assertIn("热门的开源项目。", dashboard)

    def test_repository_descriptions_use_fresh_cache(self):
        weekly = [{"title": "a/b", "url": "https://github.com/a/b"}]
        all_time = [{"title": "c/d", "url": "https://github.com/c/d"}]
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "descriptions.json"
            cache_path.write_text(json.dumps({
                "a/b": {"description": "Weekly tool", "fetched_at": datetime.now(timezone.utc).isoformat()},
                "c/d": {"description": "Popular framework.", "fetched_at": datetime.now(timezone.utc).isoformat()},
            }), encoding="utf-8")
            with patch.object(build_digest, "fetch_repository_description") as fetch:
                stats = build_digest.enrich_repository_descriptions(
                    weekly,
                    all_time,
                    1,
                    1,
                    cache_path,
                    {"a/b": "每周工具。", "c/d": "热门框架。"},
                )
            fetch.assert_not_called()
        self.assertEqual(stats, {"requested": 2, "resolved": 2})
        self.assertEqual(weekly[0]["description"], "每周工具。")
        self.assertEqual(all_time[0]["description"], "热门框架。")

    def test_repository_description_has_truthful_fallback(self):
        description = build_digest.repository_description({"title": "owner/project"})
        self.assertIn("owner/project", description)
        self.assertIn("owner 维护", description)
        self.assertTrue(description.endswith("。"))

    def test_dashboard_escapes_inline_script_breakout(self):
        generated_at = datetime(2026, 7, 30, tzinfo=timezone.utc)
        recent = [{"title": "</script><script>alert(1)</script>", "url": "https://example.com/a"}]
        dashboard = build_digest.render_dashboard(
            recent,
            [],
            [],
            {"last30days": 1, "weekly": 0, "all_time": 0},
            {"https://example.com/a": "安全测试"},
            "terminal",
            generated_at,
        )
        self.assertNotIn("</script><script>alert(1)</script>", dashboard)
        self.assertIn("\\u003c/script\\u003e", dashboard)

    def test_open_dashboard_uses_default_browser(self):
        dashboard = Path("/tmp/digest preview.html")
        with (
            patch.object(preview_browser, "ensure_preview_server", return_value="http://127.0.0.1:8888"),
            patch.object(preview_browser.webbrowser, "open", return_value=True) as browser_open,
        ):
            self.assertTrue(preview_browser.open_dashboard(dashboard))
        url = browser_open.call_args.args[0]
        self.assertTrue(url.startswith("http://127.0.0.1:8888/"))
        self.assertIn("digest%20preview.html", url)
        self.assertEqual(browser_open.call_args.kwargs, {"new": 2})

    def test_missing_chinese_description_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing Chinese description"):
            build_digest.render_recent([{"title": "AI", "url": "https://example.com"}], 1, {})

    def test_chinese_source_summary_is_used_when_annotation_is_missing(self):
        rendered = build_digest.render_recent([{
            "title": "Agent Skill",
            "url": "https://mp.weixin.qq.com/s/example",
            "summary": "文章总结了智能体技能的分层设计、工具调用方式和生产实践。后续内容不应进入首句。",
        }], 1, {})
        self.assertIn("文章总结了智能体技能的分层设计、工具调用方式和生产实践。", rendered)
        self.assertNotIn("后续内容", rendered)

    def test_render_weekly_table_basic(self):
        items = _make_weekly_items(3)
        table = build_digest.render_weekly_table(items, 3)
        self.assertIn("【Weekly 变化对比表】", table)
        self.assertIn("owner/repo-1", table)
        self.assertIn("owner/repo-2", table)
        self.assertIn("owner/repo-3", table)
        self.assertIn("上期排名", table)
        self.assertIn("本期排名", table)
        self.assertIn("Star变化", table)

    def test_render_weekly_table_trend_logic(self):
        """Verify that ▲ means previous rank was worse (higher number)."""
        items = [
            {"title": "a/rising", "rank": "1", "trend": "▲", "stars_delta": "500", "url": "https://github.com/a/rising"},
            {"title": "b/falling", "rank": "2", "trend": "▼", "stars_delta": "100", "url": "https://github.com/b/falling"},
            {"title": "c/stable", "rank": "3", "trend": "–", "stars_delta": "50", "url": "https://github.com/c/stable"},
        ]
        table = build_digest.render_weekly_table(items, 3)
        rows = table.split("\n")
        # Line layout: header, separator, then data rows
        # ▲ rank 1 -> previous rank was 2
        rising_row = rows[3]  # first data row after header + separator
        self.assertIn("2", rising_row)  # previous rank for rising item
        # ▼ rank 2 -> previous rank was 1
        falling_row = rows[4]
        self.assertIn("1", falling_row)  # previous rank for falling item
        # – rank 3 -> previous rank was 3
        stable_row = rows[5]
        self.assertIn("3", stable_row)  # previous rank for stable item

    def test_render_weekly_table_empty(self):
        self.assertEqual(build_digest.render_weekly_table([], 5), "")

    def test_render_weekly_table_limit(self):
        items = _make_weekly_items(10)
        table = build_digest.render_weekly_table(items, 3)
        # Should only contain the first 3 repos
        self.assertIn("owner/repo-1", table)
        self.assertIn("owner/repo-2", table)
        self.assertIn("owner/repo-3", table)
        self.assertNotIn("owner/repo-4", table)

    def test_render_weekly_chart_generates_file(self):
        """Test that render_weekly_chart creates a PNG file when matplotlib is available."""
        if not build_digest.HAS_MATPLOTLIB:
            self.skipTest("matplotlib not installed")
        items = _make_weekly_items(5)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "digest.txt"
            chart_path = build_digest.render_weekly_chart(items, 5, output_path)
            self.assertIsNotNone(chart_path)
            self.assertTrue(chart_path.exists())
            self.assertEqual(chart_path.suffix, ".png")
            # File should have non-trivial size (a real chart)
            self.assertGreater(chart_path.stat().st_size, 1000)

    def test_render_weekly_chart_20_items(self):
        """Test chart generation with 20 items (the new default limit)."""
        if not build_digest.HAS_MATPLOTLIB:
            self.skipTest("matplotlib not installed")
        items = _make_weekly_items(20)
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "digest.txt"
            chart_path = build_digest.render_weekly_chart(items, 20, output_path)
            self.assertIsNotNone(chart_path)
            self.assertTrue(chart_path.exists())
            self.assertGreater(chart_path.stat().st_size, 5000)

    def test_render_weekly_chart_no_matplotlib(self):
        """Test graceful fallback when matplotlib is not available."""
        if build_digest.HAS_MATPLOTLIB:
            # Temporarily simulate missing matplotlib
            original = build_digest.HAS_MATPLOTLIB
            try:
                build_digest.HAS_MATPLOTLIB = False
                items = _make_weekly_items(5)
                with tempfile.TemporaryDirectory() as directory:
                    output_path = Path(directory) / "digest.txt"
                    chart_path = build_digest.render_weekly_chart(items, 5, output_path)
                    self.assertIsNone(chart_path)
            finally:
                build_digest.HAS_MATPLOTLIB = original
        else:
            items = _make_weekly_items(5)
            with tempfile.TemporaryDirectory() as directory:
                output_path = Path(directory) / "digest.txt"
                chart_path = build_digest.render_weekly_chart(items, 5, output_path)
                self.assertIsNone(chart_path)


if __name__ == "__main__":
    unittest.main()
