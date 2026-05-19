#!/usr/bin/env python3
"""Deterministic fixture checks for refresh_token_data.py."""

from __future__ import annotations

import os
import shutil
import sqlite3
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
    def build_usage(self, fixture_name: str, codex_relative: str, claude_relative: str) -> tuple[dict, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = copy_fixture(fixture_name, Path(tmp.name))
        codex_dirs = root / codex_relative
        claude_dir = root / claude_relative
        with patched_env(
            {
                "DASHBOARD_CODEX_DIRS": str(codex_dirs),
                "DASHBOARD_CLAUDE_PROJECTS_DIR": str(claude_dir),
                "DASHBOARD_CODEX_LOGS_DB": str(root / "missing-logs.sqlite"),
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
        self.assertEqual(usage["stats"]["highlights"]["peakConcurrentTerminals"]["value"], "N/A")
        self.assertEqual(usage["stats"]["highlights"]["longestTaskTurn"]["value"], "N/A")
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
            "models",
            "days",
        }
        self.assertTrue(expected_fields.issubset(usage.keys()))
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
        highlights = usage["stats"]["highlights"]
        self.assertEqual(set(highlights.keys()), {
            "streetValue",
            "peakConcurrentTerminals",
            "peakDay",
            "longestSession",
            "longestTaskTurn",
            "toolCallPileup",
        })
        for item in highlights.values():
            self.assertTrue({"label", "value", "detail"}.issubset(item.keys()))
            self.assertIsInstance(item["label"], str)
            self.assertIsInstance(item["value"], str)
            self.assertIsInstance(item["detail"], str)
        self.assertEqual(highlights["peakDay"]["date"], "2026-01-02")
        self.assertEqual(highlights["peakDay"]["tokens"], 100)
        self.assertEqual(highlights["peakDay"]["value"], "100")
        self.assertEqual(highlights["longestSession"]["date"], "2026-01-02")
        self.assertEqual(highlights["longestSession"]["seconds"], 0)
        self.assertEqual(highlights["toolCallPileup"]["value"], "N/A")
        self.assertTrue(highlights["streetValue"]["value"].startswith("$"))
        self.assertFalse(highlights["streetValue"]["estimated"])
        self.assertEqual(highlights["peakConcurrentTerminals"]["value"], "N/A")
        self.assertEqual(highlights["longestTaskTurn"]["value"], "N/A")
        self.assert_no_absolute_fixture_paths(usage, root)

    def test_codex_highlights_scan_logs_and_task_turns_without_private_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = copy_fixture("codex-only", Path(tmp))
            session_file = root / "codex/sessions/session.jsonl"
            session_file.write_text(
                session_file.read_text(encoding="utf-8")
                + '\n{"type":"event_msg","timestamp":"2026-01-02T12:10:00Z","payload":{"type":"task_started","turn_id":"turn-private-a"}}\n'
                + '{"type":"event_msg","timestamp":"2026-01-02T12:14:30Z","payload":{"type":"task_complete","turn_id":"turn-private-a","last_agent_message":"private body omitted from export"}}\n'
                + '{"type":"response_item","timestamp":"2026-01-02T12:15:00Z","payload":{"type":"function_call","name":"exec_command","call_id":"tool-private-a"}}\n'
                + '{"type":"response_item","timestamp":"2026-01-02T12:16:00Z","payload":{"type":"web_search_call","id":"tool-private-b"}}\n',
                encoding="utf-8",
            )
            logs_db = root / "logs_2.sqlite"
            conn = sqlite3.connect(logs_db)
            try:
                conn.execute("CREATE TABLE logs (ts INTEGER NOT NULL, process_uuid TEXT, thread_id TEXT, feedback_log_body TEXT)")
                conn.executemany(
                    "INSERT INTO logs (ts, process_uuid, thread_id, feedback_log_body) VALUES (?, ?, ?, ?)",
                    [
                        (1767355200, "process-private-a", "thread-private-a", "secret"),
                        (1767355300, "process-private-b", "thread-private-b", "secret"),
                        (1767358800, "process-private-c", "thread-private-c", "secret"),
                    ],
                )
                conn.commit()
            finally:
                conn.close()

            with patched_env(
                {
                    "DASHBOARD_CODEX_DIRS": str(root / "codex/sessions"),
                    "DASHBOARD_CLAUDE_PROJECTS_DIR": str(root / "empty-claude"),
                    "DASHBOARD_CODEX_LOGS_DB": str(logs_db),
                    "DASHBOARD_TIMEZONE": "UTC",
                }
            ):
                usage = refresh_token_data.build_usage()

            highlights = usage["stats"]["highlights"]
            self.assertEqual(highlights["peakConcurrentTerminals"]["value"], "2")
            self.assertEqual(highlights["peakConcurrentTerminals"]["date"], "2026-01-02")
            self.assertEqual(highlights["peakConcurrentTerminals"]["count"], 2)
            self.assertEqual(highlights["longestTaskTurn"]["value"], "4m 30s")
            self.assertEqual(highlights["longestTaskTurn"]["seconds"], 270)
            self.assertEqual(highlights["longestTaskTurn"]["date"], "2026-01-02")
            self.assertEqual(highlights["toolCallPileup"]["value"], "2")
            self.assertEqual(highlights["toolCallPileup"]["count"], 2)
            self.assertEqual(highlights["toolCallPileup"]["date"], "2026-01-02")
            for value in strings_in(highlights):
                self.assertNotIn("process-private", value)
                self.assertNotIn("thread-private", value)
                self.assertNotIn("turn-private", value)
                self.assertNotIn("tool-private", value)
                self.assertNotIn("secret", value)
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


if __name__ == "__main__":
    unittest.main()
