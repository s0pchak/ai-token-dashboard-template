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
OPENCODE_DB_PATH = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
DEFAULT_TIMEZONE = "America/New_York"

USAGE_KEYS = (
    "totalTokens",
    "inputTokens",
    "cachedInputTokens",
    "cacheCreationTokens",
    "freshInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "modelCalls",
)

PROVIDER_NAMES = {
    "codex": "Codex",
    "claude": "Claude Code",
    "opencode": "OpenCode",
    "mixed": "Mixed",
}

MAX_DEFENSIBLE_TASK_TURN_SECONDS = 12 * 60 * 60
TOOL_CALL_RESPONSE_TYPES = {
    "function_call",
    "custom_tool_call",
    "web_search_call",
    "tool_search_call",
}

# Token pricing lives client-side in data/pricing.js as the single source of
# truth (editable without re-running the importer). The "street value"
# highlight is computed in app.js from that table.


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
    subagent: bool = False
    project: str = ""


def get_local_tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("DASHBOARD_TIMEZONE") or DEFAULT_TIMEZONE)


def omit_project_names() -> bool:
    return bool(os.environ.get("DASHBOARD_OMIT_PROJECT_NAMES"))


def project_name(raw: str | None) -> str:
    """Best-effort project label from a working directory. Strips Claude Code
    worktree suffixes (.../<repo>/.claude/worktrees/...) back to the repo, then
    takes the final path segment. Honors DASHBOARD_OMIT_PROJECT_NAMES."""
    if not raw or omit_project_names():
        return ""
    text = str(raw)
    marker = "/.claude/"
    if marker in text:
        text = text.split(marker, 1)[0]
    text = text.rstrip("/")
    name = text.rsplit("/", 1)[-1] if "/" in text else text
    return name or ""


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


def get_opencode_db_source() -> SourceDir:
    override = os.environ.get("DASHBOARD_OPENCODE_DB")
    if override:
        return SourceDir(Path(override).expanduser(), "DASHBOARD_OPENCODE_DB")
    return SourceDir(OPENCODE_DB_PATH, "~/.local/share/opencode/opencode.db")


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


def set_model_provider(target: dict, provider_id: str) -> None:
    next_provider = provider_name(provider_id)
    existing = target.get("provider")
    if not existing:
        target["provider"] = next_provider
    elif existing != next_provider:
        target["provider"] = PROVIDER_NAMES["mixed"]


def peak_concurrent_by_day(db_path: Path, local_tz: ZoneInfo, earliest_date: str | None = None) -> dict[str, int]:
    """Peak concurrent Codex processes in any one hour, bucketed by local day,
    so the dashboard can take the max over whatever date range is selected."""
    if not db_path.exists():
        return {}
    cutoff_ts = 0
    if earliest_date:
        parsed = parse_timestamp(f"{earliest_date}T00:00:00+00:00")
        cutoff_ts = int(parsed.timestamp()) if parsed else 0
    conn = None
    cursor = None
    by_day: dict[str, int] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.execute(
            """
            SELECT (ts / 3600) * 3600 AS hour_ts, COUNT(DISTINCT process_uuid) AS process_count
            FROM logs
            WHERE ts >= ? AND process_uuid IS NOT NULL AND process_uuid != ''
            GROUP BY hour_ts
            """,
            (cutoff_ts,),
        )
        for hour_ts, process_count in cursor.fetchall():
            try:
                day_key = datetime.fromtimestamp(int(hour_ts), timezone.utc).astimezone(local_tz).date().isoformat()
            except (TypeError, ValueError, OSError):
                continue
            by_day[day_key] = max(by_day.get(day_key, 0), int(process_count))
    except sqlite3.Error:
        return {}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return by_day


def codex_session_records_by_day(source_dirs: list[SourceDir], local_tz: ZoneInfo) -> dict[str, dict]:
    """Per-local-day Codex records: longest single task turn (seconds) and the
    biggest tool-call pileup in one session. Bucketed by day so the dashboard
    can take the max over whatever date range is selected."""
    by_day: dict[str, dict] = {}

    def bump(day_key: str, field: str, value: int) -> None:
        bucket = by_day.setdefault(day_key, {"longestTaskTurnSeconds": 0, "toolCallPileup": 0})
        if value > bucket[field]:
            bucket[field] = value

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
                        bump(started_at.astimezone(local_tz).date().isoformat(), "longestTaskTurnSeconds", duration)
        if tool_calls and first_tool_call_at:
            bump(first_tool_call_at.astimezone(local_tz).date().isoformat(), "toolCallPileup", tool_calls)

    return by_day


def build_highlights(days: list[dict], session_summary: dict | None = None) -> dict:
    """All-time highlights, derived from the per-day fields already attached to
    `days` (peakConcurrentTerminals, longestTaskTurnSeconds, toolCallPileup) plus
    the session summary. The dashboard recomputes range-scoped versions from the
    same per-day fields client-side."""
    most_tokens_day = max(days, key=lambda day: int(day.get("totalTokens") or 0), default=None)
    session_summary = session_summary or {}
    longest_seconds = int(session_summary.get("longestSeconds") or 0)
    longest_date = session_summary.get("longestStartDate")
    gap_minutes = int(session_summary.get("gapMinutes") or 0)

    def best_day(field: str):
        candidate = max(days, key=lambda day: int(day.get(field) or 0), default=None)
        if candidate and int(candidate.get(field) or 0) > 0:
            return candidate
        return None

    concurrency_day = best_day("peakConcurrentTerminals")
    task_turn_day = best_day("longestTaskTurnSeconds")
    tool_pileup_day = best_day("toolCallPileup")
    return {
        "peakConcurrentTerminals": {
            "label": "Terminal swarm",
            "value": f"{int(concurrency_day['peakConcurrentTerminals']):,}" if concurrency_day else "N/A",
            "detail": "Peak Codex processes in one local hour." if concurrency_day else "No local Codex log database found.",
            "date": concurrency_day["date"] if concurrency_day else None,
            "count": int(concurrency_day["peakConcurrentTerminals"]) if concurrency_day else None,
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
            "value": format_duration(longest_seconds) if longest_seconds else "N/A",
            "detail": f"{longest_date} ran longest without a {gap_minutes}m+ break." if longest_date and longest_seconds else "No continuous session found.",
            "date": longest_date,
            "seconds": longest_seconds,
            "source": "sessions",
        },
        "longestTaskTurn": {
            "label": "Longest task turn",
            "value": format_duration(int(task_turn_day["longestTaskTurnSeconds"])) if task_turn_day else "N/A",
            "detail": f"{task_turn_day['date']} had one agent on the clock." if task_turn_day else "No paired task turn events found.",
            "date": task_turn_day["date"] if task_turn_day else None,
            "seconds": int(task_turn_day["longestTaskTurnSeconds"]) if task_turn_day else None,
            "source": "codex_task_events",
        },
        "toolCallPileup": {
            "label": "Tool pileup",
            "value": format_compact_number(int(tool_pileup_day["toolCallPileup"])) if tool_pileup_day else "N/A",
            "detail": "Most tool calls packed into one session." if tool_pileup_day else "No tool calls found.",
            "date": tool_pileup_day["date"] if tool_pileup_day else None,
            "count": int(tool_pileup_day["toolCallPileup"]) if tool_pileup_day else None,
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
    current_project_by_file: dict[str, str] = {}
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
            cwd = payload.get("cwd") or settings.get("cwd")
            if cwd:
                current_project_by_file[str(path)] = project_name(cwd)
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
            "cacheCreationTokens": 0,
            "freshInputTokens": max(input_tokens - cached_input_tokens, 0),
            "outputTokens": output_tokens,
            "reasoningOutputTokens": reasoning_output_tokens,
            "modelCalls": 1,
        }
        events.append(UsageEvent(
            "codex", event_ts, model, usage_delta,
            project=current_project_by_file.get(str(path), ""),
        ))
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
        "cacheCreationTokens": cache_creation_input_tokens,
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
        is_subagent_file = "/subagents/" in str(transcript_file)
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
                event = UsageEvent(
                    "claude", event_ts, model, usage_delta,
                    subagent=is_subagent_file,
                    project=project_name(obj.get("cwd")),
                )
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


def opencode_usage_from_tokens(raw_tokens: dict) -> dict[str, int]:
    cache = raw_tokens.get("cache") or {}
    input_tokens = int(raw_tokens.get("input") or 0)
    output_tokens = int(raw_tokens.get("output") or 0)
    reasoning_tokens = int(raw_tokens.get("reasoning") or 0)
    cache_read = int(cache.get("read") or 0)
    cache_write = int(cache.get("write") or 0)
    declared_total = raw_tokens.get("total")
    total_tokens = (
        int(declared_total)
        if declared_total is not None
        else input_tokens + output_tokens + reasoning_tokens + cache_read + cache_write
    )
    return {
        "totalTokens": total_tokens,
        "inputTokens": input_tokens + cache_read + cache_write,
        "cachedInputTokens": cache_read,
        "cacheCreationTokens": cache_write,
        "freshInputTokens": input_tokens + cache_write,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": reasoning_tokens,
        "modelCalls": 1,
    }


def import_opencode_usage(db_source: SourceDir) -> tuple[list[UsageEvent], dict]:
    stats = {
        "dbPresent": False,
        "matchedAssistantEvents": 0,
        "countedModelCalls": 0,
        "nullUsageEvents": 0,
        "zeroTokenEvents": 0,
        "parseErrors": 0,
        "unknownModelEvents": 0,
        "dbErrors": 0,
    }
    events: list[UsageEvent] = []
    if not db_source.path.exists():
        return events, stats
    stats["dbPresent"] = True

    uri = f"file:{db_source.path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.DatabaseError:
        stats["dbErrors"] += 1
        return events, stats

    try:
        cursor = connection.execute(
            "SELECT data FROM message WHERE json_extract(data,'$.role')='assistant'"
        )
        for (raw,) in cursor:
            stats["matchedAssistantEvents"] += 1
            try:
                obj = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                stats["parseErrors"] += 1
                continue
            tokens = obj.get("tokens")
            raw_model = obj.get("modelID") or obj.get("providerID")
            if not tokens or not raw_model:
                stats["nullUsageEvents"] += 1
                continue
            created_ms = ((obj.get("time") or {}).get("created"))
            if created_ms is None:
                stats["parseErrors"] += 1
                continue
            try:
                event_ts = datetime.fromtimestamp(int(created_ms) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                stats["parseErrors"] += 1
                continue

            model = normalize_model(raw_model)
            if model == "unknown":
                stats["unknownModelEvents"] += 1
            usage_delta = opencode_usage_from_tokens(tokens)
            if total_token_count(usage_delta) <= 0:
                stats["zeroTokenEvents"] += 1
                continue
            opencode_cwd = ((obj.get("path") or {}).get("cwd")) or ((obj.get("path") or {}).get("root"))
            events.append(UsageEvent(
                "opencode", event_ts, model, usage_delta,
                project=project_name(opencode_cwd),
            ))
    except sqlite3.DatabaseError:
        stats["dbErrors"] += 1
    finally:
        connection.close()

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


def get_session_gap_seconds() -> int:
    raw = os.environ.get("DASHBOARD_SESSION_GAP_MINUTES")
    try:
        minutes = float(raw) if raw else 120.0
    except (TypeError, ValueError):
        minutes = 120.0
    return int(max(minutes, 1.0) * 60)


def _top_key(tally: dict[str, int]) -> str:
    return max(tally.items(), key=lambda kv: kv[1])[0] if tally else ""


def compute_sessions(events: list[UsageEvent], gap_seconds: int) -> list[dict]:
    """Group events into continuous sessions, splitting whenever the silent
    gap between consecutive events exceeds gap_seconds. Sessions span midnight
    and ignore in-session resets like /clear (no gap = same session). Each
    session also tracks its dominant model and project (by token volume)."""
    if not events:
        return []
    ordered = sorted(events, key=lambda event: event.timestamp)

    def fresh(event: UsageEvent) -> dict:
        return {
            "start": event.timestamp,
            "end": event.timestamp,
            "tokens": 0,
            "calls": 0,
            "models": {},
            "projects": {},
        }

    def absorb(session: dict, event: UsageEvent) -> None:
        value = total_token_count(event.usage)
        session["end"] = event.timestamp
        session["tokens"] += value
        session["calls"] += 1
        if event.model:
            session["models"][event.model] = session["models"].get(event.model, 0) + value
        if event.project:
            session["projects"][event.project] = session["projects"].get(event.project, 0) + value

    sessions: list[dict] = []
    current = fresh(ordered[0])
    absorb(current, ordered[0])
    for event in ordered[1:]:
        if (event.timestamp - current["end"]).total_seconds() > gap_seconds:
            sessions.append(current)
            current = fresh(event)
        absorb(current, event)
    sessions.append(current)
    return sessions


def active_seconds_by_day(sessions: list[dict], local_tz: ZoneInfo) -> dict[str, int]:
    """Split each session's elapsed time across the local days it touches, so a
    day's 'active time' is real focus time, not first-to-last-of-day."""
    active: dict[str, float] = {}
    for session in sessions:
        cursor = session["start"].astimezone(local_tz)
        end_local = session["end"].astimezone(local_tz)
        active.setdefault(cursor.date().isoformat(), 0.0)
        while cursor < end_local:
            next_midnight = (cursor + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            segment_end = min(next_midnight, end_local)
            day_key = cursor.date().isoformat()
            active[day_key] = active.get(day_key, 0.0) + (segment_end - cursor).total_seconds()
            cursor = segment_end
    return {key: int(value) for key, value in active.items()}


def summarize_sessions(sessions: list[dict], gap_seconds: int, local_tz: ZoneInfo) -> dict:
    durations = sorted(int((s["end"] - s["start"]).total_seconds()) for s in sessions)
    longest = max(
        sessions,
        key=lambda s: (s["end"] - s["start"]).total_seconds(),
        default=None,
    )
    median_seconds = 0
    if durations:
        mid = len(durations) // 2
        median_seconds = (
            durations[mid]
            if len(durations) % 2
            else (durations[mid - 1] + durations[mid]) // 2
        )
    return {
        "count": len(sessions),
        "gapMinutes": gap_seconds // 60,
        "longestSeconds": int((longest["end"] - longest["start"]).total_seconds()) if longest else 0,
        "longestStartDate": longest["start"].astimezone(local_tz).date().isoformat() if longest else None,
        "medianSeconds": median_seconds,
        "totalActiveSeconds": sum(durations),
        # Per-session detail for the history timeline + range-scoped longest
        # session. Times are local ISO so the client can place blocks on a day.
        "list": [
            {
                "start": s["start"].astimezone(local_tz).isoformat(),
                "end": s["end"].astimezone(local_tz).isoformat(),
                "startDate": s["start"].astimezone(local_tz).date().isoformat(),
                "durationSeconds": int((s["end"] - s["start"]).total_seconds()),
                "totalTokens": int(s.get("tokens") or 0),
                "modelCalls": int(s.get("calls") or 0),
                "topModel": _top_key(s.get("models") or {}),
                "topProject": _top_key(s.get("projects") or {}),
            }
            for s in sessions
        ],
    }


def build_usage() -> dict:
    local_tz = get_local_tz()
    codex_sources = get_codex_source_dirs()
    claude_source = get_claude_projects_source()
    opencode_source = get_opencode_db_source()
    codex_events, codex_stats = import_codex_usage(codex_sources)
    claude_events, claude_stats = import_claude_usage(claude_source)
    opencode_events, opencode_stats = import_opencode_usage(opencode_source)
    all_events = [*codex_events, *claude_events, *opencode_events]

    session_gap_seconds = get_session_gap_seconds()
    sessions = compute_sessions(all_events, session_gap_seconds)
    active_by_day = active_seconds_by_day(sessions, local_tz)
    session_summary = summarize_sessions(sessions, session_gap_seconds, local_tz)

    # Per-day Codex records so the dashboard can take the max over any range.
    concurrency_by_day = peak_concurrent_by_day(get_codex_logs_db_path(), local_tz)
    codex_records_by_day = codex_session_records_by_day(codex_sources, local_tz)

    by_day: dict[str, dict[str, int]] = {}
    model_totals: dict[str, dict[str, int]] = {}
    hours_of_day = [empty_day() for _ in range(24)]

    for event in all_events:
        local_ts = event.timestamp.astimezone(local_tz)
        day_key = local_ts.date().isoformat()
        day = by_day.setdefault(
            day_key,
            {
                **empty_day(),
                "firstTokenAt": None,
                "lastTokenAt": None,
                "sessionDurationSeconds": 0,
                "models": {},
                "subagentUsage": empty_day(),
                "hours": {},
            },
        )
        add_usage(day, event.usage)
        if event.subagent:
            add_usage(day["subagentUsage"], event.usage)
        hour_bucket = day["hours"].setdefault(local_ts.hour, empty_day())
        add_usage(hour_bucket, event.usage)
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
        add_usage(hours_of_day[local_ts.hour], event.usage)

    days = []
    for date_key, values in sorted(by_day.items()):
        # Active time = sum of gap-based session time that falls on this local
        # day, not first-token-to-last-token (which inflated quiet days to ~24h).
        duration = active_by_day.get(date_key, 0)
        model_rows = [
            {"name": model, **usage_values}
            for model, usage_values in sorted(
                values["models"].items(),
                key=lambda item: item[1]["totalTokens"],
                reverse=True,
            )
        ]
        codex_record = codex_records_by_day.get(date_key) or {}
        clean_values = {key: value for key, value in values.items() if key not in ("models", "hours")}
        clean_values["sessionDurationSeconds"] = duration
        clean_values["peakConcurrentTerminals"] = int(concurrency_by_day.get(date_key, 0))
        clean_values["longestTaskTurnSeconds"] = int(codex_record.get("longestTaskTurnSeconds", 0))
        clean_values["toolCallPileup"] = int(codex_record.get("toolCallPileup", 0))
        clean_values["models"] = model_rows
        clean_values["hours"] = {str(hour): bucket for hour, bucket in sorted(values["hours"].items())}
        days.append({"date": date_key, **clean_values})

    subagent_totals = empty_day()
    for day in days:
        add_usage(subagent_totals, day.get("subagentUsage", {}))

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
        provider_row(
            "opencode",
            "OpenCode",
            [opencode_source.label],
            1 if opencode_stats["dbPresent"] else 0,
            opencode_events,
        ),
    ]
    stats = {
        **codex_stats,
        "countedModelCalls": len(all_events),
        "nullUsageEvents": (
            codex_stats["nullUsageEvents"]
            + claude_stats["nullUsageEvents"]
            + opencode_stats["nullUsageEvents"]
        ),
        "parseErrors": (
            codex_stats["parseErrors"]
            + claude_stats["parseErrors"]
            + opencode_stats["parseErrors"]
        ),
        "unknownModelEvents": (
            codex_stats["unknownModelEvents"]
            + claude_stats["unknownModelEvents"]
            + opencode_stats["unknownModelEvents"]
        ),
        "providers": {
            "codex": codex_stats,
            "claude": claude_stats,
            "opencode": opencode_stats,
        },
    }
    stats["highlights"] = build_highlights(days, session_summary)

    return {
        "schemaVersion": 2,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ownerHandle": infer_owner_handle(),
        "timezone": str(local_tz),
        "sourceDirs": (
            [source.label for source in codex_sources if source.path.exists()]
            + ([claude_source.label] if claude_source.path.exists() else [])
            + ([opencode_source.label] if opencode_source.path.exists() else [])
        ),
        "firstDate": days[0]["date"] if days else None,
        "lastDate": days[-1]["date"] if days else None,
        "methodology": {
            "usageField": "Codex last_token_usage.total_tokens; Claude message.usage token fields; OpenCode message.tokens (input/output/reasoning/cache.read/cache.write) from opencode.db.",
            "modelField": "Codex nearest prior turn_context.payload.model within each session transcript; Claude message.model; OpenCode message.modelID.",
            "dedupe": "Codex counts repeated total_token_usage.total_tokens once per session file. Claude keeps one row per transcript path and message.id, choosing the largest token total and latest timestamp on ties. OpenCode reads one row per assistant message (the table is keyed by message id).",
            "forkHandling": "Codex forked/subagent sessions skip token_count events in the first two seconds to avoid copied parent history. Claude subagents/sidechains are included. OpenCode includes all assistant messages with non-zero tokens.",
            "sessionDuration": f"Continuous sessions: events across all providers are sorted by time, and a silent gap longer than DASHBOARD_SESSION_GAP_MINUTES ({session_summary['gapMinutes']}m default 120m) starts a new session. Per-day session length is the session time that falls on that local day (cross-midnight sessions are split). In-session resets like /clear do not split a session.",
            "highlights": "Aggregate-only all-time highlights. Long task-turn highlights ignore paired task events over 12 hours to avoid stale resumed tabs. Tool pileup counts tool-call response items per Codex session transcript. API street value is computed client-side from data/pricing.js.",
            "scope": "Local Codex, Claude Code, and OpenCode transcripts only; not account billing truth.",
        },
        "stats": stats,
        "totals": totals,
        "sessions": session_summary,
        "subagentTotals": subagent_totals,
        "hoursOfDay": [
            {"hour": hour, **bucket} for hour, bucket in enumerate(hours_of_day)
        ],
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
