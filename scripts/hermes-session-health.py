#!/usr/bin/env python3
"""Report oversized Hermes gateway sessions and recommend/reset-safe hygiene actions.

Read-only by default. Intended for cron/Dojo: it summarizes sessions.json without
printing transcripts or secrets, so large context buildup is visible before users
notice slow replies.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - standalone fallback
    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME", "/root/.hermes"))


DEFAULT_HOME = get_hermes_home()
WATCH_TOKENS = 40_000
LARGE_TOKENS = 80_000
CRITICAL_TOKENS = 100_000


def _load_sessions(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _bucket(tokens: int) -> str:
    if tokens >= CRITICAL_TOKENS:
        return "critical"
    if tokens >= LARGE_TOKENS:
        return "large"
    if tokens >= WATCH_TOKENS:
        return "watch"
    return "ok"


def _session_action(entry: dict) -> str:
    tokens = int(entry.get("last_prompt_tokens") or 0)
    if tokens >= CRITICAL_TOKENS:
        return "gateway should auto-compress on next message; start a fresh thread/session if it stays critical"
    if tokens >= LARGE_TOKENS:
        return "gateway will auto-compress near 100k; prefer finishing current task before unrelated follow-ups"
    if tokens >= WATCH_TOKENS:
        return "watch; avoid unrelated follow-ups in this session"
    return "none"


def build_report(home: Path = DEFAULT_HOME, *, limit: int = 20) -> dict:
    sessions_path = home / "sessions" / "sessions.json"
    sessions = _load_sessions(sessions_path)
    rows = []
    counts = {"ok": 0, "watch": 0, "large": 0, "critical": 0}
    for key, entry in sessions.items():
        if not isinstance(entry, dict):
            continue
        tokens = int(entry.get("last_prompt_tokens") or 0)
        bucket = _bucket(tokens)
        counts[bucket] += 1
        if bucket != "ok":
            rows.append({
                "session_key": key,
                "platform": entry.get("platform"),
                "chat_type": entry.get("chat_type"),
                "display_name": entry.get("display_name"),
                "session_id": entry.get("session_id"),
                "updated_at": entry.get("updated_at"),
                "last_prompt_tokens": tokens,
                "bucket": bucket,
                "recommended_action": _session_action(entry),
            })
    rows.sort(key=lambda r: (r["last_prompt_tokens"], r.get("updated_at") or ""), reverse=True)
    status = "critical" if counts["critical"] else "degraded" if counts["large"] else "watch" if counts["watch"] else "ok"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "sessions_path": str(sessions_path),
        "summary": {
            "total_sessions": len(sessions),
            **counts,
            "thresholds": {"watch": WATCH_TOKENS, "large": LARGE_TOKENS, "critical": CRITICAL_TOKENS},
        },
        "top_oversized_sessions": rows[:limit],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_report(args.home, limit=args.limit)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        s = report["summary"]
        print(
            f"Hermes session health: {report['status']} — "
            f"{s['critical']} critical, {s['large']} large, {s['watch']} watch, "
            f"{s['total_sessions']} total sessions."
        )
        for row in report["top_oversized_sessions"]:
            print(
                f"- {row['last_prompt_tokens']:,} tokens [{row['bucket']}] "
                f"{row['session_key']} updated={row.get('updated_at')} — {row['recommended_action']}"
            )
    return 2 if report["status"] == "critical" else 1 if report["status"] in {"degraded", "watch"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
