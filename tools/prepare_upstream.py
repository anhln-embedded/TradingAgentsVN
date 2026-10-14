"""Prepare an upstream merge in a separate clone; never mutate the running checkout."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "https://github.com/TauricResearch/TradingAgents.git"


def git(*args, cwd=ROOT, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="main", help="Upstream branch/tag to integrate")
    parser.add_argument("--name", default="upstream-candidate")
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("--name must use letters, digits, hyphens or underscores")
    changes = git("status", "--porcelain").stdout.strip()
    if changes:
        raise RuntimeError("Commit project changes first (including web/, vnapp/, deploy/, tools/ and tests). Never add .env secrets.")
    destination = ROOT / ".updates" / args.name
    if destination.exists():
        raise RuntimeError(f"Candidate already exists: {destination}; choose another --name")
    destination.parent.mkdir(exist_ok=True)
    git("clone", "--no-hardlinks", "--no-local", str(ROOT), str(destination))
    git("switch", "-c", "codex/update-upstream", cwd=destination)
    git("remote", "add", "upstream", UPSTREAM, cwd=destination)
    git("fetch", "upstream", args.ref, cwd=destination)
    target = git("rev-parse", "FETCH_HEAD", cwd=destination).stdout.strip()
    for key in ("user.name", "user.email"):
        value = git("config", "--get", key, check=False).stdout.strip()
        if value:
            git("config", key, value, cwd=destination)
    merge = git("merge", "--no-edit", target, cwd=destination, check=False)
    if merge.returncode:
        print(f"Candidate needs conflict resolution: {destination}\n{merge.stdout}\n{merge.stderr}")
        print("Original checkout and server are unchanged. Resolve conflicts in the candidate.")
        return 1
    manifest = destination / "vnapp" / "core-version.json"
    manifest.write_text(json.dumps({"repository": UPSTREAM, "upstream_commit": target, "strategy": "tested-vietnam-fork"}, indent=2) + "\n", encoding="utf-8")
    print(f"Candidate prepared: {destination}\nUpstream commit: {target}")
    print("Install candidate dependencies in its own environment, run web/core contract tests and the upstream test suite, then build a versioned image. This command does not deploy.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
