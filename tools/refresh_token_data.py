#!/usr/bin/env python3
"""Build local AI token-usage data for the dashboard."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = DASHBOARD_ROOT / "data"
CODEX_ROOT = Path.home() / ".codex"
CLAUDE_PROJECTS_ROOT = Path.home() / ".claude" / "projects"
DEFAULT_TIMEZONE = "America/New_York"

USAGE_KEYS = (
    "totalTokens",
    "inputTokens",
    "cachedInputTokens",
    "freshInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "modelCalls",
)

PROVIDER_NAMES = {
    "codex": "Codex",
    "claude": "Claude Code",
    "mixed": "Mixed",
}

MAX_DEFENSIBLE_TASK_TURN_SECONDS = 12 * 60 * 60
TOOL_CALL_RESPONSE_TYPES = {
    "function_call",
    "custom_tool_call",
    "web_search_call",
    "tool_search_call",
}

GENERIC_MODEL_RATE = {
    "freshInputTokens": 1.75,
    "cachedInputTokens": 0.175,
    "outputTokens": 14.0,
}

MODEL_PRICE_MAP = (
    (re.compile(r"gpt-5\.5", re.I), {
        "freshInputTokens": 5.0,
        "cachedInputTokens": 0.50,
        "outputTokens": 30.0,
    }),
    (re.compile(r"gpt-5\.4-mini", re.I), {
        "freshInputTokens": 0.75,
        "cachedInputTokens": 0.075,
        "outputTokens": 4.5,
    }),
    (re.compile(r"gpt-5\.4", re.I), {
        "freshInputTokens": 2.5,
        "cachedInputTokens": 0.25,
        "outputTokens": 15.0,
    }),
    (re.compile(r"gpt-5\.[0-9]+-codex-mini|gpt-5.*mini|codex-mini", re.I), {
        "freshInputTokens": 1.5,
        "cachedInputTokens": 0.375,
        "outputTokens": 6.0,
    }),
    (re.compile(r"gpt-5\.[0-9]+-codex|gpt-5-codex|gpt-5\.[0-9]+|gpt-5\b", re.I), GENERIC_MODEL_RATE),
    (re.compile(r"claude.*haiku", re.I), {
        "freshInputTokens": 1.0,
        "cachedInputTokens": 0.10,
        "outputTokens": 5.0,
    }),
    (re.compile(r"claude.*sonnet", re.I), {
        "freshInputTokens": 3.0,
        "cachedInputTokens": 0.30,
        "outputTokens": 15.0,
    }),
    (re.compile(r"claude.*opus", re.I), {
        "freshInputTokens": 5.0,
        "cachedInputTokens": 0.50,
        "outputTokens": 25.0,
    }),
)


@dataclass(frozen=True)
class SourceDir:
    path: Path
    label: str


@dataclass(frozen=True)
class SessionMeta:
    skip_until: datetime | None
    thread_source: str | None
    agent_nickname: str | None
    model: str | None


@dataclass(frozen=True)
class UsageEvent:
    provider: str
    timestamp: datetime
    model: str
    usage: dict[str, int]


def get_local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("DASHBOARD_TIMEZONE") or DEFAULT_TIMEZONE)


def normalize_owner_handle(value: str | None) -> str | None:
    if not value:
        return None
    handle = value.strip().lstrip("@")
    if not handle:
        return None
    return handle.split("/", 1)[0].strip() or None


def owner_from_github_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"github\.com[:/]([^/\s:]+)/[^/\s]+?(?:\.git)?/?$", value.strip())
    return normalize_owner_handle(match.group(1)) if match else None


def infer_owner_handle() -> str | None:
    explicit = normalize_owner_handle(os.environ.get("DASHBOARD_OWNER_HANDLE"))
    if explicit:
        return explicit

    github_repository = normalize_owner_handle(os.environ.get("GITHUB_REPOSITORY"))
    if github_repository:
        return github_repository

    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=DASHBOARD_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    return owner_from_github_url(result.stdout)


def parse_source_dirs(value: str | None, defaults: list[SourceDir], env_label: str) -> list[SourceDir]:
    if value is None:
        return defaults
    dirs = [item.strip() for item in value.split(",") if item.strip()]
    return [SourceDir(Path(item).expanduser(), f"{env_label}[{index}]") for index, item in enumerate(dirs)]


def get_codex_source_dirs() -> list[SourceDir]:
    return parse_source_dirs(
        os.environ.get("DASHBOARD_CODEX_DIRS"),
        [
            SourceDir(CODEX_ROOT / "sessions", "~/.codex/sessions"),
            SourceDir(CODEX_ROOT / "archived_sessions", "~/.codex/archived_sessions"),
        ],
        "DASHBOARD_CODEX_DIRS",
    )


def get_claude_projects_source() -> SourceDir:
    override = os.environ.get("DASHBOARD_CLAUDE_PROJECTS_DIR")
    if override:
        return SourceDir(Path(override).expanduser(), "DASHBOARD_CLAUDE_PROJECTS_DIR")
    return SourceDir(CLAUDE_PROJECTS_ROOT, "~/.claude/projects")


def get_codex_logs_db_path() -> Path:
    return Path(os.environ.get("DASHBOARD_CODEX_LOGS_DB") or (CODEX_ROOT / "logs_2.sqlite")).expanduser()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_session_meta(path: Path) -> SessionMeta | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
        obj = json.loads(first_line)
    except (OSError, json.JSONDecodeError):
        return None

    if obj.get("type") != "session_meta":
        return None

    payload = obj.get("payload") or {}
    start = parse_timestamp(obj.get("timestamp") or payload.get("timestamp"))
    source = payload.get("source")
    source_is_subagent = isinstance(source, dict) and bool(source.get("subagent"))
    forked = bool(payload.get("forked_from_id") or source_is_subagent)

    # Forked/subagent transcripts can begin with a copied parent history block that
    # has rewritten timestamps. Skipping the first two seconds removes that copied
    # bootstrap without hiding the agent's own later model calls.
    skip_until = start + timedelta(seconds=2) if forked and start else None
    return SessionMeta(
        skip_until=skip_until,
        thread_source=payload.get("thread_source"),
        agent_nickname=payload.get("agent_nickname"),
        model=payload.get("model"),
    )


def iter_session_files(source_dirs: Iterable[SourceDir]) -> list[Path]:
    files: list[Path] = []
    for source_dir in source_dirs:
        if source_dir.path.exists():
            files.extend(source_dir.path.rglob("*.jsonl"))
    return sorted(files)


def iter_relevant_lines(session_dirs: Iterable[SourceDir]) -> Iterable[tuple[Path, str]]:
    dirs = [str(source.path) for source in session_dirs if source.path.exists()]
    rg = shutil.which("rg")
    if rg and dirs:
        with subprocess.Popen(
            [rg, "--json", '"type":"token_count"|"type":"turn_context"', *dirs],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as proc:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "match":
                    continue
                data = rec.get("data") or {}
                path_obj = data.get("path")
                lines_obj = data.get("lines")
                path_text = path_obj.get("text") if isinstance(path_obj, dict) else path_obj
                line_text = lines_obj.get("text") if isinstance(lines_obj, dict) else lines_obj
                if path_text and line_text:
                    yield Path(path_text), line_text
            proc.wait()
        return

    for session_file in iter_session_files(session_dirs):
        try:
            with session_file.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if '"type":"token_count"' in line or '"type":"turn_context"' in line:
                        yield session_file, line
        except OSError:
            continue


def normalize_model(model: str | None) -> str:
    if not model:
        return "unknown"
    return str(model).strip() or "unknown"


def empty_day() -> dict[str, int]:
    return {key: 0 for key in USAGE_KEYS}


def add_usage(target: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        target[key] = int(target.get(key, 0)) + int(value)


def total_token_count(usage: dict[str, int]) -> int:
    return int(usage.get("totalTokens") or 0)


def provider_name(provider_id: str) -> str:
    return PROVIDER_NAMES.get(provider_id, provider_id)


def format_duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {remaining_seconds}s"
    return f"{remaining_seconds}s"


def format_compact_number(value: int | float) -> str:
    value = float(value or 0)
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}".rstrip("0").rstrip(".") + "B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"{int(round(value)):,}"


def format_compact_money(value: float) -> str:
    value = float(value or 0)
    if value >= 1_000_000:
        return "$" + f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if value >= 1_000:
        return "$" + f"{value / 1_000:.1f}".rstrip("0").rstrip(".") + "K"
    return f"${int(round(value)):,}"


def set_model_provider(target: dict, provider_id: str) -> None:
    next_provider = provider_name(provider_id)
    existing = target.get("provider")
    if not existing:
        target["provider"] = next_provider
    elif existing != next_provider:
        target["provider"] = PROVIDER_NAMES["mixed"]


def rate_for_model(model: str) -> tuple[dict[str, float], bool]:
    for pattern, rate in MODEL_PRICE_MAP:
        if pattern.search(model):
            return rate, False
    return GENERIC_MODEL_RATE, True


def build_api_street_value(model_rows: list[dict]) -> dict:
    total_usd = 0.0
    estimated = False
    for model in model_rows:
        rate, model_estimated = rate_for_model(str(model.get("name") or "unknown"))
        usd = sum((int(model.get(key) or 0) / 1_000_000) * rate[key] for key in rate)
        total_usd += usd
        estimated = estimated or model_estimated
    return {
        "usd": round(total_usd, 4),
        "estimated": estimated,
    }


def peak_concurrent_codex_terminals(db_path: Path, local_tz: ZoneInfo, earliest_date: str | None = None) -> dict | None:
    if not db_path.exists():
        return None
    cutoff_ts = 0
    if earliest_date:
        parsed = parse_timestamp(f"{earliest_date}T00:00:00+00:00")
        cutoff_ts = int(parsed.timestamp()) if parsed else 0
    conn = None
    cursor = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute(
            """
            SELECT (ts / 3600) * 3600 AS hour_ts, COUNT(DISTINCT process_uuid) AS process_count
            FROM logs
            WHERE ts >= ? AND process_uuid IS NOT NULL AND process_uuid != ''
            GROUP BY hour_ts
            ORDER BY process_count DESC, hour_ts DESC
            LIMIT 1
            """,
            (cutoff_ts,),
        )
        row = cursor.fetchone()
    except sqlite3.Error:
        return None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    if not row:
        return None

    hour_ts, process_count = row
    try:
        hour_label = datetime.fromtimestamp(int(hour_ts), timezone.utc).astimezone(local_tz).isoformat()
    except (TypeError, ValueError, OSError):
        return None
    return {
        "count": int(process_count),
        "hour": hour_label,
        "date": hour_label[:10],
    }


def codex_session_records(source_dirs: list[SourceDir], local_tz: ZoneInfo) -> dict[str, dict | None]:
    best_task_turn: tuple[int, datetime] | None = None
    best_tool_pileup: tuple[int, datetime | None] | None = None
    for session_file in iter_session_files(source_dirs):
        started_by_turn: dict[str, datetime] = {}
        tool_calls = 0
        first_tool_call_at: datetime | None = None
        try:
            handle = session_file.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                is_task_event = '"task_started"' in line or '"task_complete"' in line
                is_tool_call = (
                    '"type":"response_item"' in line
                    and (
                        '"type":"function_call"' in line
                        or '"type":"custom_tool_call"' in line
                        or '"type":"web_search_call"' in line
                        or '"type":"tool_search_call"' in line
                    )
                )
                if not is_task_event and not is_tool_call:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_ts = parse_timestamp(obj.get("timestamp"))
                payload = obj.get("payload") or {}
                if obj.get("type") == "response_item" and payload.get("type") in TOOL_CALL_RESPONSE_TYPES:
                    tool_calls += 1
                    if event_ts and first_tool_call_at is None:
                        first_tool_call_at = event_ts
                    continue
                if obj.get("type") == "event_msg":
                    turn_id = payload.get("turn_id")
                    if not turn_id or not event_ts:
                        continue
                    if payload.get("type") == "task_started":
                        started_by_turn[str(turn_id)] = event_ts
                    elif payload.get("type") == "task_complete":
                        started_at = started_by_turn.pop(str(turn_id), None)
                        if not started_at:
                            continue
                        duration = max(int((event_ts - started_at).total_seconds()), 0)
                        if duration > MAX_DEFENSIBLE_TASK_TURN_SECONDS:
                            continue
                        if not best_task_turn or duration > best_task_turn[0]:
                            best_task_turn = (duration, started_at)
        if tool_calls and (not best_tool_pileup or tool_calls > best_tool_pileup[0]):
            best_tool_pileup = (tool_calls, first_tool_call_at)

    task_turn = None
    if best_task_turn:
        local_start = best_task_turn[1].astimezone(local_tz)
        task_turn = {
            "durationSeconds": best_task_turn[0],
            "date": local_start.date().isoformat(),
        }

    tool_pileup = None
    if best_tool_pileup:
        local_tool_start = best_tool_pileup[1].astimezone(local_tz) if best_tool_pileup[1] else None
        tool_pileup = {
            "count": best_tool_pileup[0],
            "date": local_tool_start.date().isoformat() if local_tool_start else None,
        }

    return {
        "longestTaskTurn": task_turn,
        "toolCallPileup": tool_pileup,
    }


def build_highlights(days: list[dict], model_rows: list[dict], codex_sources: list[SourceDir], local_tz: ZoneInfo) -> dict:
    most_tokens_day = max(days, key=lambda day: int(day.get("totalTokens") or 0), default=None)
    longest_session_day = max(days, key=lambda day: int(day.get("sessionDurationSeconds") or 0), default=None)
    street_value = build_api_street_value(model_rows)
    peak_concurrency = peak_concurrent_codex_terminals(
        get_codex_logs_db_path(),
        local_tz,
        days[0]["date"] if days else None,
    )
    session_records = codex_session_records(codex_sources, local_tz)
    task_turn = session_records["longestTaskTurn"]
    tool_pileup = session_records["toolCallPileup"]
    return {
        "streetValue": {
            "label": "Street value",
            "value": format_compact_money(street_value["usd"]),
            "detail": "API-grade token contraband at public rack rates.",
            "usd": street_value["usd"],
            "estimated": bool(street_value["estimated"]),
            "source": "model_totals",
        },
        "peakConcurrentTerminals": {
            "label": "Terminal swarm",
            "value": f"{peak_concurrency['count']:,}" if peak_concurrency else "N/A",
            "detail": "Peak Codex processes in one local hour." if peak_concurrency else "No local Codex log database found.",
            "date": peak_concurrency["date"] if peak_concurrency else None,
            "count": peak_concurrency["count"] if peak_concurrency else None,
            "source": "codex_logs",
        },
        "peakDay": {
            "label": "Most tokens",
            "value": format_compact_number(int(most_tokens_day["totalTokens"])) if most_tokens_day else "N/A",
            "detail": f"{most_tokens_day['date']} hit the thickest bag." if most_tokens_day else "No usage days found.",
            "date": most_tokens_day["date"] if most_tokens_day else None,
            "tokens": int(most_tokens_day["totalTokens"]) if most_tokens_day else None,
            "source": "daily_usage",
        },
        "longestSession": {
            "label": "Longest session",
            "value": format_duration(int(longest_session_day["sessionDurationSeconds"])) if longest_session_day else "N/A",
            "detail": f"{longest_session_day['date']} kept the lights on." if longest_session_day else "No session span found.",
            "date": longest_session_day["date"] if longest_session_day else None,
            "seconds": int(longest_session_day["sessionDurationSeconds"]) if longest_session_day else None,
            "source": "daily_usage",
        },
        "longestTaskTurn": {
            "label": "Longest task turn",
            "value": format_duration(task_turn["durationSeconds"]) if task_turn else "N/A",
            "detail": f"{task_turn['date']} had one agent on the clock." if task_turn else "No paired task turn events found.",
            "date": task_turn["date"] if task_turn else None,
            "seconds": task_turn["durationSeconds"] if task_turn else None,
            "source": "codex_task_events",
        },
        "toolCallPileup": {
            "label": "Tool pileup",
            "value": format_compact_number(tool_pileup["count"]) if tool_pileup else "N/A",
            "detail": "Most tool calls packed into one session." if tool_pileup else "No tool calls found.",
            "date": tool_pileup["date"] if tool_pileup else None,
            "count": tool_pileup["count"] if tool_pileup else None,
            "source": "codex_response_items",
        },
    }


def import_codex_usage(source_dirs: list[SourceDir]) -> tuple[list[UsageEvent], dict]:
    session_files = iter_session_files(source_dirs)
    metas: dict[str, SessionMeta] = {}
    for session_file in session_files:
        meta = read_session_meta(session_file)
        if meta:
            metas[str(session_file)] = meta

    seen_totals_by_file: dict[str, set[int]] = {}
    current_model_by_file: dict[str, str] = {
        path: normalize_model(meta.model) for path, meta in metas.items() if meta.model
    }
    stats = {
        "sessionFiles": len(session_files),
        "matchedRelevantEvents": 0,
        "matchedTokenEvents": 0,
        "countedModelCalls": 0,
        "skippedForkBootstrapEvents": 0,
        "duplicateCumulativeEvents": 0,
        "nullUsageEvents": 0,
        "parseErrors": 0,
        "unknownModelEvents": 0,
    }
    events: list[UsageEvent] = []

    for path, line in iter_relevant_lines(source_dirs):
        stats["matchedRelevantEvents"] += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            stats["parseErrors"] += 1
            continue

        if obj.get("type") == "turn_context":
            payload = obj.get("payload") or {}
            settings = ((payload.get("collaboration_mode") or {}).get("settings") or {})
            current_model_by_file[str(path)] = normalize_model(payload.get("model") or settings.get("model"))
            continue

        payload = obj.get("payload") or {}
        if obj.get("type") != "event_msg" or payload.get("type") != "token_count":
            continue
        stats["matchedTokenEvents"] += 1

        info = payload.get("info")
        if not info:
            stats["nullUsageEvents"] += 1
            continue

        event_ts = parse_timestamp(obj.get("timestamp"))
        if not event_ts:
            stats["parseErrors"] += 1
            continue

        meta = metas.get(str(path))
        if meta and meta.skip_until and event_ts <= meta.skip_until:
            stats["skippedForkBootstrapEvents"] += 1
            continue

        total_usage = (info.get("total_token_usage") or {}).get("total_tokens")
        last_usage = info.get("last_token_usage") or {}
        last_total = last_usage.get("total_tokens")
        if total_usage is None or last_total is None:
            stats["nullUsageEvents"] += 1
            continue

        seen_totals = seen_totals_by_file.setdefault(str(path), set())
        if int(total_usage) in seen_totals:
            stats["duplicateCumulativeEvents"] += 1
            continue
        seen_totals.add(int(total_usage))

        model = normalize_model(current_model_by_file.get(str(path)) or (meta.model if meta else None))
        if model == "unknown":
            stats["unknownModelEvents"] += 1
        input_tokens = int(last_usage.get("input_tokens") or 0)
        cached_input_tokens = int(last_usage.get("cached_input_tokens") or 0)
        output_tokens = int(last_usage.get("output_tokens") or 0)
        reasoning_output_tokens = int(last_usage.get("reasoning_output_tokens") or 0)
        total_tokens = int(last_total)
        usage_delta = {
            "totalTokens": total_tokens,
            "inputTokens": input_tokens,
            "cachedInputTokens": cached_input_tokens,
            "freshInputTokens": max(input_tokens - cached_input_tokens, 0),
            "outputTokens": output_tokens,
            "reasoningOutputTokens": reasoning_output_tokens,
            "modelCalls": 1,
        }
        events.append(UsageEvent("codex", event_ts, model, usage_delta))
        stats["countedModelCalls"] += 1

    return events, stats


def iter_claude_files(projects_source: SourceDir) -> list[Path]:
    if not projects_source.path.exists():
        return []
    return sorted(projects_source.path.rglob("*.jsonl"))


def claude_usage_from_message_usage(raw_usage: dict) -> dict[str, int]:
    input_tokens = int(raw_usage.get("input_tokens") or 0)
    cache_creation_input_tokens = int(raw_usage.get("cache_creation_input_tokens") or 0)
    cache_read_input_tokens = int(raw_usage.get("cache_read_input_tokens") or 0)
    output_tokens = int(raw_usage.get("output_tokens") or 0)
    return {
        "totalTokens": input_tokens + cache_creation_input_tokens + cache_read_input_tokens + output_tokens,
        "inputTokens": input_tokens + cache_creation_input_tokens + cache_read_input_tokens,
        "cachedInputTokens": cache_read_input_tokens,
        "freshInputTokens": input_tokens + cache_creation_input_tokens,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": 0,
        "modelCalls": 1,
    }


def import_claude_usage(projects_source: SourceDir) -> tuple[list[UsageEvent], dict]:
    transcript_files = iter_claude_files(projects_source)
    stats = {
        "transcriptFiles": len(transcript_files),
        "matchedAssistantEvents": 0,
        "candidateUsageEvents": 0,
        "countedModelCalls": 0,
        "duplicateMessageEvents": 0,
        "nullUsageEvents": 0,
        "zeroTokenEvents": 0,
        "missingMessageIdEvents": 0,
        "parseErrors": 0,
        "unknownModelEvents": 0,
    }
    selected: dict[tuple[str, str], tuple[int, datetime, UsageEvent]] = {}

    for transcript_file in transcript_files:
        try:
            handle = transcript_file.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    stats["parseErrors"] += 1
                    continue

                if obj.get("type") != "assistant":
                    continue
                stats["matchedAssistantEvents"] += 1
                message = obj.get("message") or {}
                raw_usage = message.get("usage")
                raw_model = message.get("model")
                if not raw_usage or not raw_model:
                    stats["nullUsageEvents"] += 1
                    continue
                message_id = message.get("id")
                if not message_id:
                    stats["missingMessageIdEvents"] += 1
                    continue
                event_ts = parse_timestamp(obj.get("timestamp"))
                if not event_ts:
                    stats["parseErrors"] += 1
                    continue

                model = normalize_model(raw_model)
                if model == "unknown":
                    stats["unknownModelEvents"] += 1
                usage_delta = claude_usage_from_message_usage(raw_usage)
                total_tokens = total_token_count(usage_delta)
                if total_tokens <= 0:
                    stats["zeroTokenEvents"] += 1
                    continue
                event = UsageEvent("claude", event_ts, model, usage_delta)
                dedupe_key = (str(transcript_file), str(message_id))
                current = selected.get(dedupe_key)
                stats["candidateUsageEvents"] += 1
                if current is None:
                    selected[dedupe_key] = (total_tokens, event_ts, event)
                    continue
                stats["duplicateMessageEvents"] += 1
                current_total, current_ts, _ = current
                if total_tokens > current_total or (total_tokens == current_total and event_ts >= current_ts):
                    selected[dedupe_key] = (total_tokens, event_ts, event)

    events = [item[2] for item in selected.values()]
    stats["countedModelCalls"] = len(events)
    return events, stats


def provider_row(
    provider_id: str,
    name: str,
    source_labels: list[str],
    file_count: int,
    events: list[UsageEvent],
) -> dict:
    totals = empty_day()
    first_at: datetime | None = None
    last_at: datetime | None = None
    for event in events:
        add_usage(totals, event.usage)
        first_at = min(first_at, event.timestamp) if first_at else event.timestamp
        last_at = max(last_at, event.timestamp) if last_at else event.timestamp
    return {
        "id": provider_id,
        "name": name,
        "sourceLabels": source_labels,
        "transcriptFiles": file_count,
        "firstTokenAt": first_at.isoformat() if first_at else None,
        "lastTokenAt": last_at.isoformat() if last_at else None,
        **totals,
    }


def build_usage() -> dict:
    local_tz = get_local_tz()
    codex_sources = get_codex_source_dirs()
    claude_source = get_claude_projects_source()
    codex_events, codex_stats = import_codex_usage(codex_sources)
    claude_events, claude_stats = import_claude_usage(claude_source)
    all_events = [*codex_events, *claude_events]

    by_day: dict[str, dict[str, int]] = {}
    model_totals: dict[str, dict[str, int]] = {}

    for event in all_events:
        day_key = event.timestamp.astimezone(local_tz).date().isoformat()
        day = by_day.setdefault(
            day_key,
            {
                **empty_day(),
                "firstTokenAt": None,
                "lastTokenAt": None,
                "sessionDurationSeconds": 0,
                "models": {},
            },
        )
        add_usage(day, event.usage)
        day["firstTokenAt"] = (
            min(day["firstTokenAt"], event.timestamp.isoformat()) if day["firstTokenAt"] else event.timestamp.isoformat()
        )
        day["lastTokenAt"] = (
            max(day["lastTokenAt"], event.timestamp.isoformat()) if day["lastTokenAt"] else event.timestamp.isoformat()
        )
        day_model = day["models"].setdefault(event.model, empty_day())
        set_model_provider(day_model, event.provider)
        add_usage(day_model, event.usage)
        model_total = model_totals.setdefault(event.model, empty_day())
        set_model_provider(model_total, event.provider)
        add_usage(model_total, event.usage)

    days = []
    for date_key, values in sorted(by_day.items()):
        first_at = parse_timestamp(values["firstTokenAt"])
        last_at = parse_timestamp(values["lastTokenAt"])
        duration = int((last_at - first_at).total_seconds()) if first_at and last_at else 0
        model_rows = [
            {"name": model, **usage_values}
            for model, usage_values in sorted(
                values["models"].items(),
                key=lambda item: item[1]["totalTokens"],
                reverse=True,
            )
        ]
        clean_values = {key: value for key, value in values.items() if key != "models"}
        clean_values["sessionDurationSeconds"] = duration
        clean_values["models"] = model_rows
        days.append({"date": date_key, **clean_values})

    totals = empty_day()
    for day in days:
        for key in totals:
            totals[key] += int(day[key])
    model_rows = [
        {"name": model, **usage_values}
        for model, usage_values in sorted(
            model_totals.items(),
            key=lambda item: item[1]["totalTokens"],
            reverse=True,
        )
    ]

    providers = [
        provider_row(
            "codex",
            "Codex",
            [source.label for source in codex_sources],
            codex_stats["sessionFiles"],
            codex_events,
        ),
        provider_row(
            "claude",
            "Claude Code",
            [claude_source.label],
            claude_stats["transcriptFiles"],
            claude_events,
        ),
    ]
    stats = {
        **codex_stats,
        "countedModelCalls": len(all_events),
        "nullUsageEvents": codex_stats["nullUsageEvents"] + claude_stats["nullUsageEvents"],
        "parseErrors": codex_stats["parseErrors"] + claude_stats["parseErrors"],
        "unknownModelEvents": codex_stats["unknownModelEvents"] + claude_stats["unknownModelEvents"],
        "providers": {
            "codex": codex_stats,
            "claude": claude_stats,
        },
    }
    stats["highlights"] = build_highlights(days, model_rows, codex_sources, local_tz)

    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ownerHandle": infer_owner_handle(),
        "timezone": str(local_tz),
        "sourceDirs": [source.label for source in codex_sources if source.path.exists()] + (
            [claude_source.label] if claude_source.path.exists() else []
        ),
        "firstDate": days[0]["date"] if days else None,
        "lastDate": days[-1]["date"] if days else None,
        "methodology": {
            "usageField": "Codex last_token_usage.total_tokens; Claude message.usage token fields.",
            "modelField": "Codex nearest prior turn_context.payload.model within each session transcript; Claude message.model.",
            "dedupe": "Codex counts repeated total_token_usage.total_tokens once per session file. Claude keeps one row per transcript path and message.id, choosing the largest token total and latest timestamp on ties.",
            "forkHandling": "Codex forked/subagent sessions skip token_count events in the first two seconds to avoid copied parent history. Claude subagents/sidechains are included.",
            "sessionDuration": "Per local day, first counted token event timestamp through last counted token event timestamp.",
            "highlights": "Aggregate-only all-time highlights. API street value uses fresh input, cached input, and output token classes; Codex reasoning tokens are not double counted because they are already included in output tokens. Long task-turn highlights ignore paired task events over 12 hours to avoid stale resumed tabs. Tool pileup counts tool-call response items per Codex session transcript.",
            "scope": "Local Codex and Claude Code transcripts only; not account billing truth.",
        },
        "stats": stats,
        "totals": totals,
        "models": model_rows,
        "providers": providers,
        "days": days,
    }


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    usage = build_usage()
    json_path = DATA_DIR / "usage.json"
    js_path = DATA_DIR / "usage.js"
    json_path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    js_path.write_text(
        "window.AI_TOKEN_USAGE = "
        + json.dumps(usage, separators=(",", ":"))
        + ";\nwindow.CODEX_TOKEN_USAGE = window.AI_TOKEN_USAGE;\n",
        encoding="utf-8",
    )
    print(f"Wrote {json_path}")
    print(f"Wrote {js_path}")
    print(f"Days: {usage['firstDate']} to {usage['lastDate']}")
    print(f"Total tokens: {usage['totals']['totalTokens']:,}")


if __name__ == "__main__":
    main()
