"""Application-owned paths; independent of cwd and upstream defaults."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    return Path(os.environ.get("TA_DATA_DIR", str(ROOT))).expanduser().resolve()


def reports_dir() -> Path:
    return data_dir() / "results"


def runtime_env_path() -> Path:
    configured = os.environ.get("TA_RUNTIME_ENV_FILE")
    return Path(configured).expanduser().resolve() if configured else ROOT / ".env"


def load_runtime_env() -> None:
    from dotenv import load_dotenv
    # The deployment environment remains the base; UI settings persist separately.
    load_dotenv(ROOT / ".env", override=False)
    path = runtime_env_path()
    if path != ROOT / ".env":
        load_dotenv(path, override=True)


def git_updates_enabled() -> bool:
    return os.environ.get("TA_ALLOW_GIT_UPDATES", "true").lower() == "true"
