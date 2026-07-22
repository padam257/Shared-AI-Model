import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _log_path() -> Path:
    return Path(os.getenv("USAGE_LOG_PATH", "usage_events.jsonl"))


def write_usage_event(event: Dict[str, Any]) -> None:
    """Append one usage event as JSONL. Also print JSON to stdout for App Service log streaming."""
    event = dict(event)
    event.setdefault("event_type", "llm_token_usage")
    event.setdefault("timestamp_utc", datetime.now(timezone.utc).isoformat())
    line = json.dumps(event, ensure_ascii=False)
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def read_usage_events() -> List[Dict[str, Any]]:
    path = _log_path()
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def export_usage_csv(csv_path: str = "usage_summary.csv") -> str:
    events = read_usage_events()
    fields = [
        "timestamp_utc", "bu_code", "user_hash", "deployment", "prompt_tokens",
        "completion_tokens", "total_tokens", "request_id", "latency_ms"
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for e in events:
            writer.writerow({k: e.get(k, "") for k in fields})
    return csv_path
