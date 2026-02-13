"""
I/O and communication functions for Bridge.
Handles all output to stdout and command result emissions.
"""
import sys
import json


def setup_utf8_io():
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


def _emit_cmd_result(action: str, pair_name: str, success: bool = True, error: str = None):
    """
    Standardized helper to emit command result events.
    
    Args:
        action: The action name (e.g., "start", "pause", "resume", "squareoff", "delete")
        pair_name: The pair name
        success: True for cmd_ok, False for cmd_error
        error: Error message if success is False
    """
    if success:
        _emit({"type": "cmd_ok", "action": action, "pair_name": pair_name})
    else:
        _emit({"type": "cmd_error", "action": action, "pair_name": pair_name, "error": error or "Command failed"})

