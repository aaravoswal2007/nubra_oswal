"""
In-memory state for Nubra Bridge.
Positions, states, pairCtx keyed by pair_name; threads and context objects; snapshot builder.
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

# --- Locks and shared state ---
_lock = threading.Lock()
_positions: Dict[str, Dict[str, Any]] = {}
_states: Dict[str, str] = {}
_pair_ctx: Dict[str, Dict[str, Any]] = {}
_threads: Dict[str, threading.Thread] = {}
_ctx: Dict[str, Any] = {}  # pair_name -> context object (has order_ids, paused, exit_requested, leg1_cfg, leg2_cfg)
_cmd: Dict[str, str] = {}  # pair_name -> "squareoff" (for stop_all)
_cmd_lock = threading.Lock()


def _default_position() -> Dict[str, Any]:
    return {
        "symbol": "",
        "net_lots": 0,
        "side": "BUY",
        "lot_size": 1,
        "avg_entry": 0.0,
        "mtm": 0.0,
        "ltp": 0.0,
        "entry_time": "",
        "exit_time": "",
        "lots_filled": 0,
        "exit_filled": 0,
        "position_open": False,
    }


def get_positions() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_positions)


def get_states() -> Dict[str, str]:
    with _lock:
        return dict(_states)


def get_pair_ctx() -> Dict[str, Dict[str, Any]]:
    with _lock:
        return dict(_pair_ctx)


def set_position(pair_name: str, data: Dict[str, Any]) -> None:
    with _lock:
        _positions[pair_name] = dict(data)


def set_state(pair_name: str, state: str) -> None:
    with _lock:
        _states[pair_name] = state


def set_pair_ctx(pair_name: str, data: Dict[str, Any]) -> None:
    with _lock:
        _pair_ctx[pair_name] = dict(data)


def update_position_field(pair_name: str, field: str, value: Any) -> None:
    with _lock:
        if pair_name not in _positions:
            _positions[pair_name] = _default_position()
        _positions[pair_name][field] = value


def update_pair_ctx_field(pair_name: str, field: str, value: Any) -> None:
    with _lock:
        if pair_name not in _pair_ctx:
            _pair_ctx[pair_name] = {}
        _pair_ctx[pair_name][field] = value


def remove_pair(pair_name: str) -> None:
    with _lock:
        _positions.pop(pair_name, None)
        _states.pop(pair_name, None)
        _pair_ctx.pop(pair_name, None)
        _ctx.pop(pair_name, None)
        _threads.pop(pair_name, None)
    with _cmd_lock:
        _cmd.pop(pair_name, None)


def get_ctx(pair_name: str) -> Optional[Any]:
    return _ctx.get(pair_name)


def set_ctx(pair_name: str, ctx: Any) -> None:
    _ctx[pair_name] = ctx


def get_thread(pair_name: str) -> Optional[threading.Thread]:
    return _threads.get(pair_name)


def set_thread(pair_name: str, th: threading.Thread) -> None:
    _threads[pair_name] = th


def build_snapshot() -> Dict[str, Any]:
    """Build snapshot dict: positions, states, pairCtx, total_mtm."""
    with _lock:
        positions = dict(_positions)
        states = dict(_states)
        pair_ctx = dict(_pair_ctx)
    total_mtm = sum(float(p.get("mtm") or 0) for p in positions.values())
    return {
        "type": "snapshot",
        "positions": positions,
        "states": states,
        "pairCtx": pair_ctx,
        "total_mtm": total_mtm,
    }


def clear_all() -> None:
    """Factory reset: clear all in-memory state."""
    with _lock:
        _positions.clear()
        _states.clear()
        _pair_ctx.clear()
        _ctx.clear()
    _threads.clear()
    with _cmd_lock:
        _cmd.clear()


def get_cmd(pair_name: str) -> Optional[str]:
    with _cmd_lock:
        return _cmd.get(pair_name)


def set_cmd(pair_name: str, cmd: str) -> None:
    with _cmd_lock:
        _cmd[pair_name] = cmd


def set_cmd_all(cmd: str) -> None:
    with _cmd_lock:
        for k in list(_ctx.keys()):
            _cmd[k] = cmd


def list_pair_names() -> List[str]:
    with _lock:
        return list(set(_positions.keys()) | set(_states.keys()) | set(_pair_ctx.keys()))
