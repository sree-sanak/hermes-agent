#!/usr/bin/env python3
"""Post-turn continuation scanner for Hermes gateway sessions.

Reads a compact JSON payload from stdin after a Hermes response and enqueues
obvious safe follow-ups into Sree's global continuation queue. This deliberately
uses conservative heuristics: it only acts on explicit assistant phrasing like
"Next fix" / "Next step" and never auto-runs approval-risk work.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timezone

ENQUEUE = pathlib.Path('/root/.hermes/scripts/hermes-continuation-enqueue.py')
STATUS = pathlib.Path('/root/.hermes/status/hermes-post-turn-continuation-last.json')

RISK_WORDS = re.compile(
    r"\b(spend|pay|purchase|subscribe|deploy|production|prod|send (?:it|this|message|email)|"
    r"archive|delete|remove all|export private|raw data|credential|secret|token|api key|"
    r"live trading|real[- ]money|threshold)\b",
    re.I,
)
NEXT_LINE = re.compile(
    r"^\s*(?:[-*•>]\s*)?(?:next\s+(?:fix|step|thing|action)|follow[- ]?up|todo)\s*(?:,?\s*when you want it)?\s*[:：-]\s*(.+)$",
    re.I,
)

PRIMITIVE_HINTS = [
    ('github', re.compile(r"\b(issue|pr|pull request|github)\b", re.I)),
    ('agent', re.compile(r"\b(repo|code|branch|test|implement|refactor|coding agent)\b", re.I)),
    ('background', re.compile(r"\b(long[- ]running|background|overnight|batch run)\b", re.I)),
    ('webhook', re.compile(r"\b(webhook|event trigger|react to)\b", re.I)),
    ('script_queue', re.compile(r"\b(script|queue|monitor|timer|systemd|bounded|cron migration|inspect .*\.sh)\b", re.I)),
    ('cron', re.compile(r"\b(cron|daily|weekly|every \d|periodic)\b", re.I)),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def primitive_for(text: str) -> str:
    for primitive, pat in PRIMITIVE_HINTS:
        if pat.search(text):
            return primitive
    return 'tools'


def extract_candidates(final_response: str) -> list[dict]:
    candidates: list[dict] = []
    for line in final_response.splitlines():
        m = NEXT_LINE.match(line.strip())
        if not m:
            continue
        task = m.group(1).strip()
        if not task or len(task) < 12:
            continue
        risk = 'approval_required' if RISK_WORDS.search(task) else 'safe'
        candidates.append({
            'task': task,
            'primitive': primitive_for(task),
            'risk': risk,
            'reason': 'post-turn explicit next-step suggestion',
        })
    return candidates[:3]


def enqueue(candidate: dict, payload: dict) -> dict:
    source = payload.get('source') or {}
    platform = source.get('platform') or payload.get('platform') or 'gateway'
    chat = source.get('chat_name') or source.get('chat_id') or ''
    source_label = f'post-turn:{platform}' + (f':{chat}' if chat else '')
    primitive = candidate['primitive']
    # If no exact command is known, let the dispatcher route through a Hermes
    # tools run rather than pretending a script_queue/background item is ready.
    if primitive in {'script_queue', 'background'}:
        primitive = 'tools'
    cmd = [
        str(ENQUEUE),
        '--primitive', primitive,
        '--reason', candidate['reason'],
        '--source', source_label[:120],
        '--risk', candidate['risk'],
        '--deliver', 'local',
        candidate['task'],
    ]
    if candidate['risk'] != 'safe':
        return {'queued': False, 'skipped': 'approval_required', 'candidate': candidate}
    try:
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
        return {'queued': p.returncode == 0, 'returncode': p.returncode, 'output': p.stdout.strip()[-1000:], 'candidate': candidate}
    except Exception as exc:
        return {'queued': False, 'error': f'{type(exc).__name__}: {exc}', 'candidate': candidate}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        payload = {'error': f'invalid json: {exc}'}
    final_response = str(payload.get('final_response') or '')
    candidates = extract_candidates(final_response)
    results = [enqueue(c, payload) for c in candidates]
    status = {
        'ran_at': now_iso(),
        'candidate_count': len(candidates),
        'results': results,
    }
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(status, indent=2, sort_keys=True))
    print(json.dumps({'candidate_count': len(candidates), 'queued': sum(1 for r in results if r.get('queued'))}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
