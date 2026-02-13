# runtime_paths_live.py — same API as Oswal for bridge/strategy compatibility.
# Paths are under nubra_oswal/_runtime_nubra so bridge state/logs stay local.
from pathlib import Path
from datetime import datetime
import os
import stat


def _ensure_dir_permissions(path: Path):
    """Ensure directory has proper permissions."""
    try:
        if os.name == 'nt':
            if path.exists():
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        else:
            if path.exists():
                os.chmod(path, 0o777)
    except (OSError, PermissionError):
        pass


ROOT = Path(__file__).resolve().parent / "_runtime_nubra"
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
CACHE_DIR = ROOT / "cache"
for d in (STATE_DIR, LOG_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)
    _ensure_dir_permissions(d)


def state_file(name: str) -> str:
    return str(STATE_DIR / name)


def cache_file(name: str) -> str:
    return str(CACHE_DIR / name)


def log_file(name: str) -> str:
    return str(LOG_DIR / name)


def daily_log(prefix: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d")
    return str(LOG_DIR / f"{prefix}-{ts}.log")
