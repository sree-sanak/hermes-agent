import importlib.util
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hermes-dashboard-process-check.py"
_spec = importlib.util.spec_from_file_location("dashboard_process_check", _SCRIPT)
dashboard_check = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = dashboard_check
_spec.loader.exec_module(dashboard_check)


def test_extract_port_supports_space_and_equals_forms():
    assert dashboard_check._extract_port("hermes dashboard --port 9119 --no-open") == 9119
    assert dashboard_check._extract_port("python -m hermes_cli.main dashboard --port=9125") == 9125


def test_dashboard_match_is_strict_to_hermes_dashboard_commands():
    assert dashboard_check._looks_like_dashboard("/usr/local/bin/hermes dashboard --host 127.0.0.1")
    assert dashboard_check._looks_like_dashboard("python -m hermes_cli.main dashboard --tui")
    assert not dashboard_check._looks_like_dashboard("node app.js --name dashboard")
    assert not dashboard_check._looks_like_dashboard("python gateway/run.py")


def test_build_report_marks_bad_when_stale_process_exists():
    procs = [
        dashboard_check.DashboardProcess(pid=1, age_hours=1, port=9119, command="hermes dashboard", allowlisted=True),
        dashboard_check.DashboardProcess(pid=2, age_hours=48, port=9121, command="hermes dashboard", stale=True),
    ]

    report = dashboard_check.build_report(procs)

    assert report["status"] == "bad"
    assert report["summary"]["dashboard_processes"] == 2
    assert report["summary"]["stale_unallowlisted"] == 1
    assert report["summary"]["allowlisted"] == 1
