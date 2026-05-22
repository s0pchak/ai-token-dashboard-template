#!/usr/bin/env python3
"""Deterministic fixture checks for refresh_token_data.py."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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

    def test_codex_source_subagent_routes_to_subagent_usage_without_private_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_dir = root / "codex" / "sessions"
            claude_dir = root / "empty-claude"
            codex_dir.mkdir(parents=True)
            claude_dir.mkdir()
            session_file = codex_dir / "codex-subagent.jsonl"
            private_local_path = "/Users/private-codex-subagent/private/worktree"
            lines = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-01-05T16:00:00Z",
                    "payload": {
                        "model": "gpt-5.1-codex-mini",
                        "forked_from_id": "fork-private-codex-subagent",
                        "thread_source": "thread-private-codex-subagent",
                        "agent_nickname": "agent-private-codex-subagent",
                        "source": {
                            "subagent": True,
                            "process_uuid": "process-private-codex-subagent",
                            "cwd": private_local_path,
                        },
                    },
                },
                {
                    "type": "turn_context",
                    "timestamp": "2026-01-05T16:00:00Z",
                    "payload": {
                        "model": "gpt-5.1-codex-mini",
                        "thread_id": "thread-private-codex-context",
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-01-05T16:00:01Z",
                    "payload": {
                        "type": "token_count",
                        "turn_id": "turn-private-codex-bootstrap",
                        "last_agent_message": "private bootstrap message body",
                        "info": {
                            "total_token_usage": {"total_tokens": 999},
                            "last_token_usage": {
                                "total_tokens": 999,
                                "input_tokens": 700,
                                "cached_input_tokens": 200,
                                "output_tokens": 299,
                                "reasoning_output_tokens": 0,
                            },
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-01-05T16:00:03Z",
                    "payload": {
                        "type": "token_count",
                        "turn_id": "turn-private-codex-counted",
                        "last_agent_message": "private counted message body",
                        "info": {
                            "total_token_usage": {"total_tokens": 1219},
                            "last_token_usage": {
                                "total_tokens": 220,
                                "input_tokens": 150,
                                "cached_input_tokens": 25,
                                "output_tokens": 70,
                                "reasoning_output_tokens": 5,
                            },
                        },
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-01-05T16:00:04Z",
                    "payload": {
                        "type": "function_call",
                        "call_id": "tool-private-codex-subagent",
                        "name": "exec_command",
                    },
                },
            ]
            session_file.write_text(
                "\n".join(refresh_token_data.json.dumps(line, separators=(",", ":")) for line in lines) + "\n",
                encoding="utf-8",
            )

            meta = refresh_token_data.read_session_meta(session_file)
            self.assertIsNotNone(meta)
            self.assertTrue(meta.subagent)

            with patched_env(
                {
                    "DASHBOARD_CODEX_DIRS": str(codex_dir),
                    "DASHBOARD_CLAUDE_PROJECTS_DIR": str(claude_dir),
                    "DASHBOARD_OPENCODE_DB": str(root / "missing-opencode.db"),
                    "DASHBOARD_CODEX_LOGS_DB": str(root / "missing-logs.sqlite"),
                    "DASHBOARD_TIMEZONE": "UTC",
                }
            ):
                usage = refresh_token_data.build_usage()

            self.assertEqual(usage["stats"]["providers"]["codex"]["skippedForkBootstrapEvents"], 1)
            self.assertEqual(usage["totals"]["totalTokens"], 220)
            self.assertEqual(usage["subagentTotals"]["totalTokens"], 220)
            self.assertEqual(usage["subagentTotals"]["outputTokens"], 70)
            day = usage["days"][0]
            self.assertEqual(day["subagentUsage"]["totalTokens"], 220)
            self.assertEqual(day["subagentUsage"]["outputTokens"], 70)
            for value in strings_in(usage):
                self.assertNotIn("process-private-codex", value)
                self.assertNotIn("thread-private-codex", value)
                self.assertNotIn("turn-private-codex", value)
                self.assertNotIn("fork-private-codex", value)
                self.assertNotIn("tool-private-codex", value)
                self.assertNotIn("/Users/private-codex-subagent", value)
                self.assertNotIn("private bootstrap message body", value)
                self.assertNotIn("private counted message body", value)
            self.assert_no_absolute_fixture_paths(usage, root)

    def test_codex_subagent_without_model_inherits_parent_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_dir = root / "codex" / "sessions"
            claude_dir = root / "empty-claude"
            codex_dir.mkdir(parents=True)
            claude_dir.mkdir()

            parent_file = codex_dir / "parent.jsonl"
            parent_id = "parent-model-thread"
            parent_lines = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-01-06T16:00:00Z",
                    "payload": {
                        "id": parent_id,
                        "model_provider": "openai",
                    },
                },
                {
                    "type": "turn_context",
                    "timestamp": "2026-01-06T16:00:01Z",
                    "payload": {"model": "gpt-parent-inherited"},
                },
            ]
            parent_file.write_text(
                "\n".join(refresh_token_data.json.dumps(line, separators=(",", ":")) for line in parent_lines) + "\n",
                encoding="utf-8",
            )

            child_file = codex_dir / "child-subagent.jsonl"
            child_lines = [
                {
                    "type": "session_meta",
                    "timestamp": "2026-01-06T16:05:00Z",
                    "payload": {
                        "id": "child-model-thread",
                        "forked_from_id": parent_id,
                        "source": {
                            "subagent": {
                                "thread_spawn": {
                                    "parent_thread_id": parent_id,
                                    "agent_nickname": "child-private-agent",
                                },
                            },
                        },
                    },
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-01-06T16:05:03Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {"total_tokens": 321},
                            "last_token_usage": {
                                "total_tokens": 321,
                                "input_tokens": 300,
                                "cached_input_tokens": 120,
                                "output_tokens": 21,
                                "reasoning_output_tokens": 7,
                            },
                        },
                    },
                },
            ]
            child_file.write_text(
                "\n".join(refresh_token_data.json.dumps(line, separators=(",", ":")) for line in child_lines) + "\n",
                encoding="utf-8",
            )

            with patched_env(
                {
                    "DASHBOARD_CODEX_DIRS": str(codex_dir),
                    "DASHBOARD_CLAUDE_PROJECTS_DIR": str(claude_dir),
                    "DASHBOARD_OPENCODE_DB": str(root / "missing-opencode.db"),
                    "DASHBOARD_CODEX_LOGS_DB": str(root / "missing-logs.sqlite"),
                    "DASHBOARD_TIMEZONE": "UTC",
                }
            ):
                usage = refresh_token_data.build_usage()

            self.assertEqual(usage["stats"]["providers"]["codex"]["unknownModelEvents"], 0)
            self.assertEqual(usage["models"][0]["name"], "gpt-parent-inherited")
            self.assertEqual(usage["models"][0]["totalTokens"], 321)
            self.assertEqual(usage["subagentTotals"]["totalTokens"], 321)
            self.assertEqual(usage["days"][0]["models"][0]["name"], "gpt-parent-inherited")

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
            # Per-day fields the dashboard maxes over the selected range.
            self.assertIn("peakConcurrentTerminals", day)
            self.assertIn("longestTaskTurnSeconds", day)
            self.assertIn("toolCallPileup", day)
        self.assertIsInstance(usage["sessions"]["list"], list)
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
        # streetValue is computed client-side from data/pricing.js, so it is no
        # longer a server-emitted highlight.
        self.assertEqual(set(highlights.keys()), {
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
        self.assertEqual(highlights["peakConcurrentTerminals"]["value"], "N/A")
        self.assertEqual(highlights["longestTaskTurn"]["value"], "N/A")
        # Gap-based session summary is present and sane.
        self.assertEqual(usage["sessions"]["gapMinutes"], 120)
        self.assertGreaterEqual(usage["sessions"]["count"], 1)
        self.assertIn("longestSeconds", usage["sessions"])
        self.assertIn("Codex source.subagent", usage["methodology"]["forkHandling"])
        self.assertIn("Claude /subagents/", usage["methodology"]["forkHandling"])
        self.assertIn("OpenCode", usage["methodology"]["forkHandling"])
        self.assert_no_absolute_fixture_paths(usage, root)

    def test_compute_sessions_splits_on_idle_gap(self):
        base = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
        usage_one = {**refresh_token_data.empty_day(), "totalTokens": 10}

        def event(minutes_after):
            return refresh_token_data.UsageEvent(
                "claude", base + timedelta(minutes=minutes_after), "claude-opus-4-7", usage_one
            )

        # 0, 30, 60 min → one session (gaps < 120m).
        # Then 300 min (4h gap) → new session. Then 320 min (20m later) → same.
        events = [event(0), event(30), event(60), event(300), event(320)]
        sessions = refresh_token_data.compute_sessions(events, gap_seconds=120 * 60)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(int((sessions[0]["end"] - sessions[0]["start"]).total_seconds()), 60 * 60)
        self.assertEqual(int((sessions[1]["end"] - sessions[1]["start"]).total_seconds()), 20 * 60)
        # A tighter 25-minute gap threshold splits the first run at the 30→60 boundary too.
        tight = refresh_token_data.compute_sessions(events, gap_seconds=25 * 60)
        self.assertEqual(len(tight), 4)

    def test_project_name_extraction(self):
        # Repo root, Claude worktree path, Codex/OpenCode plain cwd, parent dir.
        self.assertEqual(refresh_token_data.project_name("/Users/x/Documents/Github/api"), "api")
        self.assertEqual(
            refresh_token_data.project_name("/Users/x/Documents/Github/tina-rs/.claude/worktrees/phase-1"),
            "tina-rs",
        )
        self.assertEqual(refresh_token_data.project_name(""), "")
        self.assertEqual(refresh_token_data.project_name(None), "")
        self.assertTrue(
            refresh_token_data.automated_lane_cwd(
                "/Users/x/.paperclip/instances/default/projects/agent-id/_default"
            )
        )

    def test_sessions_split_by_project_and_track_dominant_model(self):
        base = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        usage = {**refresh_token_data.empty_day(), "totalTokens": 100}

        def event(minute, model, project):
            return refresh_token_data.UsageEvent(
                "claude", base + timedelta(minutes=minute), model, usage, project=project
            )

        # No gaps, but the project switches alpha -> beta, so it's two sessions.
        events = [
            event(0, "claude-opus-4-7", "alpha"),
            event(10, "claude-opus-4-7", "alpha"),
            event(20, "claude-haiku-4-5", "beta"),
        ]
        split = refresh_token_data.compute_sessions(events, gap_seconds=120 * 60, split_by_project=True)
        self.assertEqual(len(split), 2)
        s_alpha = refresh_token_data.summarize_sessions(split, 120 * 60, refresh_token_data.get_local_tz())["list"]
        self.assertEqual(s_alpha[0]["topProject"], "alpha")
        self.assertEqual(s_alpha[0]["topModel"], "claude-opus-4-7")
        self.assertEqual(s_alpha[0]["modelCalls"], 2)
        self.assertEqual(s_alpha[1]["topProject"], "beta")
        self.assertEqual(s_alpha[1]["modelCalls"], 1)

        # Opting out keeps it a single mixed-project session (gap-only).
        merged = refresh_token_data.compute_sessions(events, gap_seconds=120 * 60, split_by_project=False)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["calls"], 3)

        # Unknown-project events do not force a split.
        with_unknown = [event(0, "claude-opus-4-7", "alpha"), event(5, "claude-opus-4-7", "")]
        self.assertEqual(len(refresh_token_data.compute_sessions(with_unknown, 120 * 60, split_by_project=True)), 1)

    def test_peak_concurrent_activity_counts_all_provider_lanes(self):
        base = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        usage = {**refresh_token_data.empty_day(), "totalTokens": 100}

        def event(provider, minute, lane, subagent=False, swarm_eligible=True):
            return refresh_token_data.UsageEvent(
                provider,
                base + timedelta(minutes=minute),
                f"{provider}-model",
                usage,
                subagent=subagent,
                lane=lane,
                swarm_eligible=swarm_eligible,
            )

        events = [
            event("codex", 0, "codex:a"),
            event("codex", 40, "codex:a"),
            event("claude", 10, "claude:b"),
            event("claude", 50, "claude:b"),
            event("opencode", 20, "opencode:c"),
            event("opencode", 21, "opencode:c"),
            event("codex", 30, "codex:subagent", subagent=True),
            event("codex", 45, "codex:subagent", subagent=True),
            event("codex", 35, "codex:forked-agent", swarm_eligible=False),
        ]

        by_day = refresh_token_data.peak_concurrent_activity_by_day(
            events,
            gap_seconds=120 * 60,
            local_tz=refresh_token_data.ZoneInfo("UTC"),
        )

        self.assertEqual(by_day["2026-03-01"], 3)

    def test_codex_process_counts_replace_transcript_lanes_for_swarm(self):
        base = datetime(2026, 3, 1, 9, 0, 0, tzinfo=timezone.utc)
        usage = {**refresh_token_data.empty_day(), "totalTokens": 100}
        hour_key = base.replace(minute=0, second=0, microsecond=0)
        events = [
            refresh_token_data.UsageEvent("codex", base, "gpt-5.4", usage, lane="codex:a"),
            refresh_token_data.UsageEvent("codex", base + timedelta(minutes=5), "gpt-5.4", usage, lane="codex:b"),
            refresh_token_data.UsageEvent("codex", base + timedelta(minutes=10), "gpt-5.4", usage, lane="codex:c"),
            refresh_token_data.UsageEvent("claude", base + timedelta(minutes=15), "claude-opus-4-7", usage, lane="claude:d"),
        ]

        by_day = refresh_token_data.peak_concurrent_activity_by_day(
            events,
            gap_seconds=120 * 60,
            local_tz=refresh_token_data.ZoneInfo("UTC"),
            codex_process_counts={("2026-03-01", hour_key): 1},
        )

        self.assertEqual(by_day["2026-03-01"], 2)

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
            peer_session_file = root / "codex/sessions/concurrent.jsonl"
            peer_session_file.write_text(
                '{"type":"session_meta","timestamp":"2026-01-02T12:09:00Z","payload":{"id":"concurrent-thread","model":"gpt-5.1-codex-mini"}}\n'
                + '{"type":"turn_context","timestamp":"2026-01-02T12:09:01Z","payload":{"model":"gpt-5.1-codex-mini"}}\n'
                + '{"type":"event_msg","timestamp":"2026-01-02T12:01:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"total_tokens":50},"last_token_usage":{"total_tokens":50,"input_tokens":30,"cached_input_tokens":0,"output_tokens":20,"reasoning_output_tokens":0}}}}\n',
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
                        "subagent": True,
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
        self.assertEqual(usage["subagentTotals"]["totalTokens"], 39194)

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
