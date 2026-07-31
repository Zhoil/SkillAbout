import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import fetch_hotspots


class LoadConfigTest(unittest.TestCase):
    def _write_config(self, directory: str, section: dict) -> Path:
        cfg = {"last30days": section, "limits": {"last30days": 1, "weekly": 1, "all_time": 1}}
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_defaults_when_section_empty(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {})
            section = fetch_hotspots.load_last30days_config(cfg)
            self.assertEqual(section, {})

    def test_custom_topic_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d, {"topic": "LLM inference", "days": 7})
            section = fetch_hotspots.load_last30days_config(cfg)
            self.assertEqual(section["topic"], "LLM inference")
            self.assertEqual(section["days"], 7)

    def test_invalid_section_type_raises(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"last30days": "bad"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be a JSON object"):
                fetch_hotspots.load_last30days_config(path)


class BuildCommandTest(unittest.TestCase):
    def _fake_skill_dir(self, directory: str) -> Path:
        skill = Path(directory) / "last30days"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "last30days.py").write_text("# stub", encoding="utf-8")
        return skill

    def test_default_topic_and_days(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            cmd = fetch_hotspots.build_command(skill, Path(d) / "out.json", {})
            self.assertIn("AI artificial intelligence", cmd)
            self.assertIn("--days=30", cmd)
            self.assertIn("--emit=json", cmd)

    def test_custom_topic_days_depth(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            cmd = fetch_hotspots.build_command(
                skill, Path(d) / "out.json",
                {"topic": "open source LLM", "days": 14, "depth": "quick"},
            )
            self.assertIn("open source LLM", cmd)
            self.assertIn("--days=14", cmd)
            self.assertIn("--quick", cmd)

    def test_deep_depth(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            cmd = fetch_hotspots.build_command(skill, Path(d) / "out.json", {"depth": "deep"})
            self.assertIn("--deep", cmd)

    def test_subreddits_and_x_handle(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            cmd = fetch_hotspots.build_command(
                skill, Path(d) / "out.json",
                {"subreddits": "MachineLearning,LocalLLaMA", "x_handle": "@AnthropicAI"},
            )
            self.assertIn("--subreddits=MachineLearning,LocalLLaMA", cmd)
            # @ prefix stripped
            self.assertIn("--x-handle=AnthropicAI", cmd)

    def test_search_source_filter(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            cmd = fetch_hotspots.build_command(
                skill, Path(d) / "out.json",
                {"search": "reddit,hackernews"},
            )
            self.assertIn("--search=reddit,hackernews", cmd)

    def test_invalid_depth_raises(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            with self.assertRaisesRegex(ValueError, "depth"):
                fetch_hotspots.build_command(skill, Path(d) / "out.json", {"depth": "ultra"})

    def test_negative_days_raises(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            with self.assertRaisesRegex(ValueError, "days"):
                fetch_hotspots.build_command(skill, Path(d) / "out.json", {"days": -1})

    def test_missing_engine_raises(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "no-skill"
            with self.assertRaises(FileNotFoundError):
                fetch_hotspots.build_command(missing, Path(d) / "out.json", {})


class RunFetchTest(unittest.TestCase):
    def _fake_skill_dir(self, directory: str) -> Path:
        skill = Path(directory) / "last30days"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "last30days.py").write_text("# stub", encoding="utf-8")
        return skill

    def test_writes_json_to_output(self):
        fake_json = json.dumps({"results": [{"title": "AI news", "url": "https://example.com"}]})
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            out = Path(d) / "result.json"
            with patch("fetch_hotspots.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout=fake_json, stderr="")
                fetch_hotspots.run_fetch(skill, out, {})
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(out.read_text()), json.loads(fake_json))

    def test_nonzero_exit_raises(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            out = Path(d) / "result.json"
            with patch("fetch_hotspots.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="bad")
                with self.assertRaisesRegex(RuntimeError, "exited 2"):
                    fetch_hotspots.run_fetch(skill, out, {})

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            skill = self._fake_skill_dir(d)
            out = Path(d) / "result.json"
            with patch("fetch_hotspots.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
                with self.assertRaisesRegex(RuntimeError, "not valid JSON"):
                    fetch_hotspots.run_fetch(skill, out, {})


class MainEntryTest(unittest.TestCase):
    def _write_config(self, directory: str, section: dict | None = None) -> Path:
        cfg = {
            "last30days": section or {},
            "limits": {"last30days": 1, "weekly": 1, "all_time": 1},
        }
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def _fake_skill_dir(self, directory: str) -> Path:
        skill = Path(directory) / "last30days"
        (skill / "scripts").mkdir(parents=True)
        (skill / "scripts" / "last30days.py").write_text("# stub", encoding="utf-8")
        return skill

    def test_missing_engine_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = self._write_config(d)
            out = Path(d) / "out.json"
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("fetch_hotspots.py")),
                 "--config", str(cfg),
                 "--skill-dir", str(Path(d) / "no-such-skill"),
                 "--output", str(out)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("ERROR", result.stderr)


if __name__ == "__main__":
    unittest.main()
