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


def get_opencode_db_source() -> SourceDir:
    override = os.environ.get("DASHBOARD_OPENCODE_DB")
    if override:
        return SourceDir(Path(override).expanduser(), "DASHBOARD_OPENCODE_DB")
    return SourceDir(OPENCODE_DB_PATH, "~/.local/share/opencode/opencode.db")


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


def set_model_provider(target: dict, provider_id: str) -> None:
    next_provider = provider_name(provider_id)
    existing = target.get("provider")
    if not existing:
        target["provider"] = next_provider
    elif existing != next_provider:
        target["provider"] = PROVIDER_NAMES["mixed"]


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
            "cacheCreationTokens": 0,
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
            events.append(UsageEvent("opencode", event_ts, model, usage_delta))
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


def build_usage() -> dict:
    local_tz = get_local_tz()
    codex_sources = get_codex_source_dirs()
    claude_source = get_claude_projects_source()
    opencode_source = get_opencode_db_source()
    codex_events, codex_stats = import_codex_usage(codex_sources)
    claude_events, claude_stats = import_claude_usage(claude_source)
    opencode_events, opencode_stats = import_opencode_usage(opencode_source)
    all_events = [*codex_events, *claude_events, *opencode_events]

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
            "sessionDuration": "Per local day, first counted token event timestamp through last counted token event timestamp.",
            "scope": "Local Codex, Claude Code, and OpenCode transcripts only; not account billing truth.",
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
