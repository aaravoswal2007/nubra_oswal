"""
I/O and communication for Nubra Bridge.
Same contract as Oswal bridge/io.py: UTF-8 stdout, one JSON object per line.
"""
import sys
import json


def setup_utf8_io() -> None:
    """Configure stdout/stderr for UTF-8 encoding."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _emit(obj: dict) -> None:
    """Emit a JSON object to stdout."""
    sys.stdout.write(json.dumps(obj, default=str) + "\n")
    sys.stdout.flush()


def _emit_cmd_result(action: str, pair_name: str, success: bool = True, error: str = None) -> None:
    """
    Emit command result: cmd_ok or cmd_error.
    action: "start", "pause", "resume", "squareoff", "delete", etc.
    """
    if success:
        _emit({"type": "cmd_ok", "action": action, "pair_name": pair_name})
    else:
        _emit({"type": "cmd_error", "action": action, "pair_name": pair_name, "error": error or "Command failed"})
