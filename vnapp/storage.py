"""Atomic, native JSON snapshots used by the dashboard history API."""
import json
import os
import re
import tempfile
from pathlib import Path

from vnapp.settings import reports_dir


def save_snapshot(ticker, state):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", ticker) or ticker in (".", ".."):
        raise ValueError("Invalid ticker path")
    folder = reports_dir() / f"{ticker.lower()}_analysis"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "final_state.json"
    fd, temporary = tempfile.mkstemp(dir=folder, prefix=".snapshot-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def report_folder(report_id):
    root = reports_dir().resolve()
    candidate = (root / report_id).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Invalid report path")
    if not candidate.exists() and report_id.startswith("report_"):
        candidate = (root / "reports" / report_id[len("report_"):]).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Invalid report path")
    return candidate
