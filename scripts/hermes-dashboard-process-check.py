#!/usr/bin/env python3
"""Report or clean up stale ad-hoc Hermes dashboard processes.

Designed for a daily cron/systemd check.  It is intentionally conservative:
only commands that clearly invoke `hermes dashboard` or
`python -m hermes_cli.main dashboard` are candidates, and allowlisted ports/PIDs
are never killed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_DASHBOARD_PATTERNS = (
    "hermes dashboard",
    "hermes_cli.main dashboard",
    "hermes_cli/main.py dashboard",
)
_PORT_RE = re.compile(r"--port(?:=|\s+)(\d+)")


@dataclass
class DashboardProcess:
    pid: int
    age_hours: float
    port: int | None
    command: str
    allowlisted: bool = False
    stale: bool = False
    action: str = "keep"
    error: str | None = None


def _read_cmdline(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()


def _system_uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return time.monotonic()


def _clock_ticks() -> int:
    return int(os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK")))


def _process_age_seconds(pid: int, *, uptime_seconds: float | None = None) -> float | None:
    uptime = _system_uptime_seconds() if uptime_seconds is None else uptime_seconds
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
        start_ticks = int(fields[21])
    except (FileNotFoundError, PermissionError, OSError, ValueError, IndexError):
        return None
    return max(0.0, uptime - (start_ticks / _clock_ticks()))


def _extract_port(command: str) -> int | None:
    match = _PORT_RE.search(command)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for idx, part in enumerate(parts):
        if part == "--port" and idx + 1 < len(parts):
            try:
                return int(parts[idx + 1])
            except ValueError:
                return None
    return None


def _looks_like_dashboard(command: str) -> bool:
    normalized = " ".join(command.split())
    return any(pattern in normalized for pattern in _DASHBOARD_PATTERNS)


def _parse_csv_ints(value: str | None) -> set[int]:
    if not value:
        return set()
    out: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            pass
    return out


def iter_dashboard_processes(
    *,
    allow_ports: Iterable[int] = (),
    allow_pids: Iterable[int] = (),
    max_age_hours: float = 12.0,
) -> list[DashboardProcess]:
    allow_ports_set = set(allow_ports)
    allow_pids_set = set(allow_pids)
    processes: list[DashboardProcess] = []
    uptime = _system_uptime_seconds()
    current_pid = os.getpid()

    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        pid = int(proc_dir.name)
        if pid == current_pid:
            continue
        command = _read_cmdline(pid)
        if not command or not _looks_like_dashboard(command):
            continue
        age_seconds = _process_age_seconds(pid, uptime_seconds=uptime)
        if age_seconds is None:
            continue
        port = _extract_port(command)
        allowlisted = pid in allow_pids_set or (port is not None and port in allow_ports_set)
        age_hours = age_seconds / 3600.0
        stale = age_hours >= max_age_hours and not allowlisted
        processes.append(
            DashboardProcess(
                pid=pid,
                age_hours=round(age_hours, 2),
                port=port,
                command=command,
                allowlisted=allowlisted,
                stale=stale,
                action="would_kill" if stale else "keep",
            )
        )

    return sorted(processes, key=lambda p: (p.port is None, p.port or 0, p.pid))


def terminate_stale(processes: list[DashboardProcess], *, force: bool = False) -> None:
    sig = signal.SIGKILL if force else signal.SIGTERM
    for proc in processes:
        if not proc.stale:
            continue
        try:
            os.kill(proc.pid, sig)
            proc.action = "killed" if force else "terminated"
        except ProcessLookupError:
            proc.action = "already_exited"
        except Exception as exc:
            proc.action = "error"
            proc.error = str(exc)


def build_report(processes: list[DashboardProcess]) -> dict:
    stale = [p for p in processes if p.stale]
    allowlisted = [p for p in processes if p.allowlisted]
    return {
        "status": "bad" if stale else "ok",
        "summary": {
            "dashboard_processes": len(processes),
            "stale_unallowlisted": len(stale),
            "allowlisted": len(allowlisted),
        },
        "processes": [asdict(p) for p in processes],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-hours", type=float, default=float(os.getenv("HERMES_DASHBOARD_MAX_AGE_HOURS", "12")))
    parser.add_argument("--allow-ports", default=os.getenv("HERMES_DASHBOARD_ALLOW_PORTS", "9119"))
    parser.add_argument("--allow-pids", default=os.getenv("HERMES_DASHBOARD_ALLOW_PIDS", ""))
    parser.add_argument("--kill", action="store_true", help="Terminate stale unallowlisted dashboard processes")
    parser.add_argument("--force", action="store_true", help="Use SIGKILL instead of SIGTERM with --kill")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args(argv)

    processes = iter_dashboard_processes(
        allow_ports=_parse_csv_ints(args.allow_ports),
        allow_pids=_parse_csv_ints(args.allow_pids),
        max_age_hours=args.max_age_hours,
    )
    if args.kill:
        terminate_stale(processes, force=args.force)

    report = build_report(processes)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        icon = "✅" if report["status"] == "ok" else "⚠️"
        summary = report["summary"]
        print(
            f"{icon} Hermes dashboard process check: "
            f"{summary['dashboard_processes']} found, "
            f"{summary['stale_unallowlisted']} stale/unallowlisted, "
            f"{summary['allowlisted']} allowlisted."
        )
        for proc in processes:
            port = proc.port if proc.port is not None else "?"
            print(
                f"- pid={proc.pid} port={port} age={proc.age_hours:.2f}h "
                f"allowlisted={proc.allowlisted} stale={proc.stale} action={proc.action}"
            )
            if proc.error:
                print(f"  error: {proc.error}")
    return 1 if any(p.stale and p.action in {"would_kill", "error"} for p in processes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
