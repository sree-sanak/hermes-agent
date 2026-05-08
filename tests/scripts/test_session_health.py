import importlib.util
import json
import sys
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hermes-session-health.py"
_spec = importlib.util.spec_from_file_location("hermes_session_health", _SCRIPT)
session_health = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules[_spec.name] = session_health
_spec.loader.exec_module(session_health)


def test_build_report_buckets_oversized_sessions(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "sessions.json").write_text(json.dumps({
        "small": {"last_prompt_tokens": 10_000, "platform": "discord"},
        "watch": {"last_prompt_tokens": 40_000, "platform": "discord"},
        "large": {"last_prompt_tokens": 80_000, "platform": "discord"},
        "critical": {"last_prompt_tokens": 120_000, "platform": "discord"},
    }))

    report = session_health.build_report(tmp_path)

    assert report["status"] == "critical"
    assert report["summary"]["ok"] == 1
    assert report["summary"]["watch"] == 1
    assert report["summary"]["large"] == 1
    assert report["summary"]["critical"] == 1
    assert report["top_oversized_sessions"][0]["session_key"] == "critical"
    assert "fresh thread" in report["top_oversized_sessions"][0]["recommended_action"]


def test_build_report_handles_missing_file(tmp_path):
    report = session_health.build_report(tmp_path)
    assert report["status"] == "ok"
    assert report["summary"]["total_sessions"] == 0
