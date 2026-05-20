#!/usr/bin/env python3
"""Deterministic fixture checks for refresh_token_data.py."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = TOOLS_DIR / "fixtures"
sys.path.insert(0, str(TOOLS_DIR))

import refresh_token_data  # noqa: E402


@contextmanager
def patched_env(values: dict[str, str]):
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def copy_fixture(name: str, tmp_path: Path) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES_DIR / name, target)
    return target


def strings_in(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)


class RefreshTokenDataTest(unittest.TestCase):
    def build_usage(
        self,
        fixture_name: str,
        codex_relative: str,
        claude_relative: str,
        opencode_db: Path | None = None,
    ) -> tuple[dict, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = copy_fixture(fixture_name, Path(tmp.name))
        codex_dirs = root / codex_relative
        claude_dir = root / claude_relative
        opencode_path = opencode_db if opencode_db is not None else root / "missing-opencode.db"
        with patched_env(
            {
                "DASHBOARD_CODEX_DIRS": str(codex_dirs),
                "DASHBOARD_CLAUDE_PROJECTS_DIR": str(claude_dir),
                "DASHBOARD_OPENCODE_DB": str(opencode_path),
                "DASHBOARD_TIMEZONE": "UTC",
            }
        ):
            return refresh_token_data.build_usage(), root

    def assert_no_absolute_fixture_paths(self, usage: dict, root: Path) -> None:
        root_text = str(root)
        for value in strings_in(usage):
            self.assertNotIn(root_text, value)
            self.assertNotIn(str(Path.home()), value)

    def test_codex_only_fixture_preserves_codex_counting(self):
        usage, root = self.build_usage("codex-only", "codex/sessions", "empty-claude")

        self.assertEqual(usage["schemaVersion"], 2)
        self.assertEqual(usage["timezone"], "UTC")
        self.assertEqual(usage["firstDate"], "2026-01-02")
        self.assertEqual(usage["lastDate"], "2026-01-02")
        self.assertEqual(usage["totals"]["totalTokens"], 150)
        self.assertEqual(usage["totals"]["modelCalls"], 2)
        self.assertEqual(usage["stats"]["countedModelCalls"], 2)
        self.assertEqual(usage["stats"]["duplicateCumulativeEvents"], 1)
        self.assertEqual(usage["stats"]["providers"]["codex"]["countedModelCalls"], 2)
        self.assertEqual(usage["providers"][0]["id"], "codex")
        self.assertEqual(usage["providers"][0]["totalTokens"], 150)
        self.assertEqual(usage["providers"][1]["id"], "claude")
        self.assertEqual(usage["providers"][1]["totalTokens"], 0)
        self.assert_no_absolute_fixture_paths(usage, root)

    def test_claude_duplicate_stream_dedupes_by_message_id(self):
        usage, root = self.build_usage("claude-duplicate-stream", "empty-codex", "claude/projects")

        self.assertEqual(usage["firstDate"], "2026-01-03")
        self.assertEqual(usage["lastDate"], "2026-01-03")
        self.assertEqual(
            usage["totals"],
            {
                "totalTokens": 42,
                "inputTokens": 34,
                "cachedInputTokens": 20,
                "cacheCreationTokens": 5,
                "freshInputTokens": 14,
                "outputTokens": 8,
                "reasoningOutputTokens": 0,
                "modelCalls": 1,
            },
        )
        claude_stats = usage["stats"]["providers"]["claude"]
        self.assertEqual(claude_stats["candidateUsageEvents"], 3)
        self.assertEqual(claude_stats["duplicateMessageEvents"], 2)
        self.assertEqual(claude_stats["nullUsageEvents"], 1)
        self.assertEqual(claude_stats["zeroTokenEvents"], 1)
        self.assertEqual(claude_stats["countedModelCalls"], 1)
        self.assertEqual(usage["models"][0]["name"], "claude-sonnet-4-5")
        self.assert_no_absolute_fixture_paths(usage, root)

    def test_mixed_providers_aggregate_to_frontend_shape(self):
        usage, root = self.build_usage("mixed", "codex/sessions", "claude/projects")

        expected_fields = {
            "generatedAt",
            "ownerHandle",
            "timezone",
            "firstDate",
            "lastDate",
            "methodology",
            "stats",
            "totals",
            "subagentTotals",
            "hoursOfDay",
            "models",
            "days",
        }
        self.assertTrue(expected_fields.issubset(usage.keys()))
        self.assertEqual(len(usage["hoursOfDay"]), 24)
        for bucket in usage["hoursOfDay"]:
            self.assertIn("hour", bucket)
            self.assertIn("totalTokens", bucket)
        for day in usage["days"]:
            self.assertIn("subagentUsage", day)
            self.assertIn("hours", day)
        self.assertEqual(usage["schemaVersion"], 2)
        self.assertEqual(usage["firstDate"], "2026-01-02")
        self.assertEqual(usage["lastDate"], "2026-01-03")
        self.assertEqual(usage["totals"]["totalTokens"], 142)
        self.assertEqual(usage["totals"]["modelCalls"], 2)
        self.assertEqual([day["date"] for day in usage["days"]], ["2026-01-02", "2026-01-03"])
        providers_by_id = {provider["id"]: provider for provider in usage["providers"]}
        self.assertEqual(providers_by_id["codex"]["totalTokens"], 100)
        self.assertEqual(providers_by_id["claude"]["totalTokens"], 42)
        self.assertEqual({model["name"] for model in usage["models"]}, {"gpt-5.1-codex-mini", "claude-sonnet-4-5"})
        self.assertEqual(
            {model["name"]: model["provider"] for model in usage["models"]},
            {"gpt-5.1-codex-mini": "Codex", "claude-sonnet-4-5": "Claude Code"},
        )
        self.assertEqual(
            {model["name"]: model["provider"] for day in usage["days"] for model in day["models"]},
            {"gpt-5.1-codex-mini": "Codex", "claude-sonnet-4-5": "Claude Code"},
        )
        self.assert_no_absolute_fixture_paths(usage, root)

    def test_owner_handle_helpers(self):
        self.assertEqual(
            refresh_token_data.owner_from_github_url("git@github.com:s0pchak/the-maxx-report.git"),
            "s0pchak",
        )
        self.assertEqual(
            refresh_token_data.owner_from_github_url("https://github.com/example-user/the-maxx-report.git"),
            "example-user",
        )
        self.assertIsNone(refresh_token_data.owner_from_github_url("https://gitlab.com/example/repo.git"))

        with patched_env({"DASHBOARD_OWNER_HANDLE": "@receipt-maxxer"}):
            self.assertEqual(refresh_token_data.infer_owner_handle(), "receipt-maxxer")

    def test_main_writes_ai_usage_and_legacy_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_fixture("mixed", Path(tmp))
            output_dir = root / "output"
            original_data_dir = refresh_token_data.DATA_DIR
            refresh_token_data.DATA_DIR = output_dir
            try:
                with patched_env(
                    {
                        "DASHBOARD_CODEX_DIRS": str(root / "codex/sessions"),
                        "DASHBOARD_CLAUDE_PROJECTS_DIR": str(root / "claude/projects"),
                        "DASHBOARD_OPENCODE_DB": str(root / "missing-opencode.db"),
                        "DASHBOARD_TIMEZONE": "UTC",
                    }
                ):
                    with redirect_stdout(StringIO()):
                        refresh_token_data.main()
            finally:
                refresh_token_data.DATA_DIR = original_data_dir

            js_text = (output_dir / "usage.js").read_text(encoding="utf-8")
            self.assertTrue(js_text.startswith("window.AI_TOKEN_USAGE = "))
            self.assertIn("window.CODEX_TOKEN_USAGE = window.AI_TOKEN_USAGE;", js_text)

    def test_subagent_path_routes_to_subagent_usage_and_fills_hour_bucket(self):
        usage, root = self.build_usage(
            "claude-with-subagent",
            "empty-codex",
            "claude/projects",
        )

        days_by_date = {day["date"]: day for day in usage["days"]}
        day = days_by_date["2026-01-04"]
        # Main: input=100, output=50. Subagent: input=200, output=80.
        # totalTokens for the day is 100+50 + 200+80 = 430.
        self.assertEqual(day["totalTokens"], 430)
        # Only the subagent path counts toward subagentUsage.
        self.assertEqual(day["subagentUsage"]["totalTokens"], 280)
        self.assertEqual(day["subagentUsage"]["outputTokens"], 80)
        self.assertEqual(usage["subagentTotals"]["totalTokens"], 280)

        # Hour bucket: both events fall in the same UTC date but different hours
        # (14:30 and 15:00 UTC). Test runs with DASHBOARD_TIMEZONE=UTC so we
        # land in hour buckets 14 and 15.
        self.assertEqual(usage["hoursOfDay"][14]["totalTokens"], 150)
        self.assertEqual(usage["hoursOfDay"][15]["totalTokens"], 280)
        # Per-day hours mirror the same split, keyed by string hour.
        self.assertEqual(day["hours"]["14"]["totalTokens"], 150)
        self.assertEqual(day["hours"]["15"]["totalTokens"], 280)
        self.assert_no_absolute_fixture_paths(usage, root)

    def test_opencode_db_aggregates_into_provider_row(self):
        import sqlite3

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db_path = Path(tmp.name) / "opencode.db"
        connection = sqlite3.connect(db_path)
        try:
            connection.execute("CREATE TABLE message (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            rows = [
                (
                    "msg_real",
                    {
                        "role": "assistant",
                        "modelID": "moonshotai/kimi-k2.6",
                        "providerID": "openrouter",
                        "time": {"created": 1736208000000},
                        "tokens": {
                            "total": 39194,
                            "input": 18827,
                            "output": 240,
                            "reasoning": 671,
                            "cache": {"write": 0, "read": 19456},
                        },
                    },
                ),
                (
                    "msg_zero",
                    {
                        "role": "assistant",
                        "modelID": "moonshotai/kimi-k2.6",
                        "providerID": "openrouter",
                        "time": {"created": 1736208001000},
                        "tokens": {
                            "total": 0,
                            "input": 0,
                            "output": 0,
                            "reasoning": 0,
                            "cache": {"write": 0, "read": 0},
                        },
                    },
                ),
                (
                    "msg_user",
                    {
                        "role": "user",
                        "time": {"created": 1736208002000},
                    },
                ),
            ]
            for msg_id, payload in rows:
                connection.execute(
                    "INSERT INTO message (id, data) VALUES (?, ?)",
                    (msg_id, refresh_token_data.json.dumps(payload)),
                )
            connection.commit()
        finally:
            connection.close()

        usage, root = self.build_usage(
            "mixed",
            "codex/sessions",
            "claude/projects",
            opencode_db=db_path,
        )

        opencode_stats = usage["stats"]["providers"]["opencode"]
        self.assertTrue(opencode_stats["dbPresent"])
        self.assertEqual(opencode_stats["matchedAssistantEvents"], 2)
        self.assertEqual(opencode_stats["zeroTokenEvents"], 1)
        self.assertEqual(opencode_stats["countedModelCalls"], 1)

        providers_by_id = {provider["id"]: provider for provider in usage["providers"]}
        self.assertIn("opencode", providers_by_id)
        self.assertEqual(providers_by_id["opencode"]["totalTokens"], 39194)
        self.assertEqual(providers_by_id["opencode"]["cachedInputTokens"], 19456)
        self.assertEqual(providers_by_id["opencode"]["cacheCreationTokens"], 0)
        self.assertEqual(providers_by_id["opencode"]["reasoningOutputTokens"], 671)

        models_by_name = {model["name"]: model for model in usage["models"]}
        self.assertIn("moonshotai/kimi-k2.6", models_by_name)
        self.assertEqual(models_by_name["moonshotai/kimi-k2.6"]["provider"], "OpenCode")
        self.assert_no_absolute_fixture_paths(usage, root)


if __name__ == "__main__":
    unittest.main()
