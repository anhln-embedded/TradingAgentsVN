"""Git management helper for TradingAgentsVN Web Dashboard.

Provides safe operations to:
- Check upstream updates (git fetch upstream)
- Inspect commit diffs between local branch and upstream/main
- Prepare upstream updates in an isolated candidate checkout
"""

import subprocess
import logging
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

def run_git_cmd(args: list[str]) -> tuple[int, str, str]:
    """Execute a git command in the repository root."""
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as e:
        logger.exception("Git execution error: %s", e)
        return 1, "", str(e)

def check_upstream_status() -> dict:
    """Fetch and check if upstream has new commits available."""
    # 1. Fetch upstream
    ret, out, err = run_git_cmd(["fetch", "upstream"])
    if ret:
        return {"error": err or out, "behind_count": None, "is_up_to_date": False}

    # 2. Get current branch
    _, current_branch, _ = run_git_cmd(["rev-parse", "--abbrev-ref", "HEAD"])

    # 3. Check commits behind upstream/main
    ret_log, commits_log, _ = run_git_cmd(["log", "HEAD..upstream/main", "--oneline", "-n", "20"])

    # 4. Count commits behind
    _, count_behind, _ = run_git_cmd(["rev-list", "--count", "HEAD..upstream/main"])
    behind_num = int(count_behind) if count_behind.isdigit() else 0

    # 5. Check if local working tree is clean
    _, status_out, _ = run_git_cmd(["status", "--porcelain"])
    # Filter out untracked files like results/, web/, run_web.py
    modified_tracked = [
        line for line in status_out.splitlines()
        if not line.startswith("??") and line.strip()
    ]

    return {
        "current_branch": current_branch or "feat/vietnam-market",
        "behind_count": behind_num,
        "is_up_to_date": behind_num == 0,
        "new_commits": [c for c in commits_log.splitlines() if c.strip()],
        "has_uncommitted_tracked_changes": len(modified_tracked) > 0,
        "modified_files": modified_tracked,
        "untracked_files": [line for line in status_out.splitlines() if line.startswith("??")],
    }

def pull_upstream_code() -> dict:
    """Prepare a candidate checkout; never merge the branch serving the web UI."""
    # First check status
    status = check_upstream_status()
    if status.get("error"):
        return {"success": False, "message": "Không fetch được upstream", "error": status["error"]}
    if status["has_uncommitted_tracked_changes"] or status["untracked_files"]:
        return {
            "success": False,
            "message": "Cần commit code hiện tại trước khi chuẩn bị cập nhật: " + ", ".join(status["modified_files"] + status["untracked_files"]),
            "details": status,
        }

    if status["behind_count"] == 0:
        return {
            "success": True,
            "message": "Mã nguồn đã ở phiên bản mới nhất từ Upstream (TauricResearch/TradingAgents). Không cần cập nhật thêm.",
            "details": status,
        }

    name = "upstream-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "prepare_upstream.py"), "--name", name],
            cwd=REPO_ROOT, text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=300,
        )
        return {
            "success": result.returncode == 0,
            "message": "Đã chuẩn bị bản cập nhật riêng; cần kiểm thử và build image trước khi triển khai." if result.returncode == 0 else "Bản cập nhật cần xử lý; checkout đang chạy không đổi.",
            "output": result.stdout,
            "error": result.stderr if result.returncode else None,
            "candidate": str(REPO_ROOT / ".updates" / name),
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Chuẩn bị cập nhật quá thời gian chờ; kiểm tra candidate trước khi thử lại.", "candidate": str(REPO_ROOT / ".updates" / name)}
