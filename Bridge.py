#!/usr/bin/env python3
"""
Nubra Bridge: same stdin/stdout JSON protocol as Oswal Bridge.
Run from nubra_oswal with: python Bridge.py
Electron can spawn this when USE_NUBRA_BRIDGE=1 (cwd = nubra_oswal).
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

# Ensure we can import nubra_oswal modules when run as script
import os as _os
_BASE = _os.path.dirname(_os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

from bridge.io import setup_utf8_io, _emit, _emit_cmd_result
from bridge import state as bridge_state


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(pair_name: str, line: str) -> None:
    _emit({"type": "log", "line": f"[{_now()}] [{pair_name}] {line}"})


def _emit_snapshot_now() -> None:
    snap = bridge_state.build_snapshot()
    _emit(snap)


def _leg2_cfg_to_executor_params(leg2_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build key_name, side, lots, lot_size, splice_lots from leg2_cfg. Resolves ATM/ITM/OTM via symbol_resolver."""
    key_name = leg2_cfg.get("key_name") or leg2_cfg.get("symbol")
    if not key_name:
        stock = (leg2_cfg.get("stock") or "").strip().upper()
        strike = leg2_cfg.get("strike")
        strike_str = str(strike).strip() if strike is not None else ""
        opt = (leg2_cfg.get("option_type") or "C").strip().upper()
        if opt not in ("CE", "PE"):
            opt = "CE" if (opt == "C" or not opt) else "PE"
        # Resolve ATM/ITM3/OTM2 to numeric strike (same as Oswal strategy_helpers.get_symbol)
        if strike_str and (strike_str.upper() == "ATM" or strike_str.upper().startswith("ITM") or strike_str.upper().startswith("OTM")):
            from symbol_resolver import get_symbol
            key_name = get_symbol(stock, strike_str, opt)
        else:
            if strike_str:
                try:
                    strike_str = str(int(float(strike_str)))
                except (TypeError, ValueError):
                    pass
            key_name = f"{stock}_{strike_str}_{opt}" if strike_str else f"{stock}_{opt}"
    side = (leg2_cfg.get("trade_direction") or "BUY").strip().upper()
    if side not in ("BUY", "SELL"):
        side = "BUY"
    lots = int(leg2_cfg.get("lots") or 1)
    # Use lot_size from config for this stock when preset doesn't send it (so Nubra shows correct contracts)
    lot_size = leg2_cfg.get("lot_size")
    if lot_size is None:
        try:
            from configloader import get_stock_steps
            stock_steps = get_stock_steps()
            _emit({"type": "log", "line": f"[{_now()}] [Bridge] stock_steps={stock_steps}"})
            stock = (leg2_cfg.get("stock") or "").strip().upper()
            _emit({"type": "log", "line": f"[{_now()}] [Bridge] stock={stock!r}"})
            lot_size = stock_steps.get(stock, {}).get("lot_size", 600)
        except Exception:
            lot_size = 600
    lot_size = int(lot_size)
    splice_lots = int(leg2_cfg.get("splice_lots") or 5)
    if splice_lots > lots:
        splice_lots = lots
    return {
        "key_name": key_name,
        "side": side,
        "lots": lots,
        "lot_size": lot_size,
        "splice_lots": splice_lots,
    }


def _get_ltp(key_name: str) -> Optional[float]:
    """Get LTP from market_data if available."""
    try:
        from subscribe_orderbook import market_data
        entry = market_data.get(key_name) or {}
        ltp = entry.get("ltp")
        if ltp is None:
            return None
        if isinstance(ltp, (int, float)):
            return float(ltp)
        # Oswal format can be "123.45|qty"
        s = str(ltp).split("|")[0].strip()
        return float(s) if s else None
    except Exception:
        return None


class PairContext:
    """Per-pair context: order_ids for squareoff, paused, exit_requested, config."""
    __slots__ = ("pair_name", "leg1_cfg", "leg2_cfg", "order_ids", "paused", "exit_requested", "key_name")

    def __init__(self, pair_name: str, leg1_cfg: Dict, leg2_cfg: Dict):
        self.pair_name = pair_name
        self.leg1_cfg = dict(leg1_cfg) if leg1_cfg else {}
        self.leg2_cfg = dict(leg2_cfg) if leg2_cfg else {}
        self.order_ids: List[Any] = []
        self.paused = False
        self.exit_requested = False
        params = _leg2_cfg_to_executor_params(self.leg2_cfg)
        self.key_name = params["key_name"]


def _runner(ctx: PairContext) -> None:
    """Daemon thread: run executor for this pair; respect ctx.paused and ctx.exit_requested."""
    pair_name = ctx.pair_name
    try:
        params = _leg2_cfg_to_executor_params(ctx.leg2_cfg)
        key_name = params["key_name"]
        side = params["side"]
        lots = params["lots"]
        lot_size = params["lot_size"]
        splice_lots = params["splice_lots"]

        def on_order_placed(order_id: Any) -> None:
            ctx.order_ids.append(order_id)

        def should_stop() -> bool:
            return ctx.paused or ctx.exit_requested

        def on_lot_filled(info: Dict[str, Any]) -> None:
            lots_filled = int(info.get("lots_filled") or 0)
            avg_px = info.get("avg_px")
            bridge_state.update_position_field(pair_name, "lots_filled", lots_filled)
            bridge_state.update_position_field(pair_name, "net_lots", lots_filled)
            if avg_px is not None:
                bridge_state.update_position_field(pair_name, "avg_entry", float(avg_px))
            bridge_state.update_position_field(pair_name, "symbol", key_name)
            bridge_state.update_position_field(pair_name, "side", side)
            bridge_state.update_position_field(pair_name, "lot_size", lot_size)
            bridge_state.update_position_field(pair_name, "position_open", lots_filled < lots)
            ltp = _get_ltp(key_name)
            if ltp is not None:
                bridge_state.update_position_field(pair_name, "ltp", ltp)
                pos = bridge_state.get_positions().get(pair_name, {})
                avg = float(pos.get("avg_entry") or 0)
                nl = int(pos.get("net_lots") or 0)
                ls = int(pos.get("lot_size") or 1)
                if nl and avg:
                    mtm = (ltp - avg) * nl * ls * (1 if side == "BUY" else -1)
                    bridge_state.update_position_field(pair_name, "mtm", mtm)
            _emit_snapshot_now()

        import executor_nubra_bridge as exec_bridge
        exec_bridge.place_order_splices_mid_ltq(
            key_name=key_name,
            side=side,
            lots=lots,
            lot_size=lot_size,
            splice_lots=splice_lots,
            modify_interval_sec=3.0,
            poll_interval_sec=0.25,
            product_type="NRML",
            phase="entry",
            on_lot_filled=on_lot_filled,
            on_order_placed=on_order_placed,
            should_stop=should_stop,
        )
    except Exception as e:
        _log(pair_name, f"Executor error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        bridge_state.set_state(pair_name, "Finished")
        bridge_state.update_position_field(pair_name, "position_open", False)
        _emit_snapshot_now()
        _emit({"type": "finished", "pair_name": pair_name})


def _start_pair(pair_name: str, leg1_cfg: Dict, leg2_cfg: Dict, l1_state: Optional[Dict] = None, l2_state: Optional[Dict] = None) -> None:
    """Initialize state and start executor thread for this pair (Leg2-only)."""
    if bridge_state.get_ctx(pair_name) and bridge_state.get_thread(pair_name) and bridge_state.get_thread(pair_name).is_alive():
        _emit_cmd_result("start", pair_name, success=False, error="Pair already running")
        return
    params = _leg2_cfg_to_executor_params(leg2_cfg)
    key_name = params["key_name"]
    side = params["side"]
    lot_size = params["lot_size"]
    # Initialize position
    bridge_state.set_position(pair_name, {
        "symbol": key_name,
        "net_lots": 0,
        "side": side,
        "lot_size": lot_size,
        "avg_entry": 0.0,
        "mtm": 0.0,
        "ltp": 0.0,
        "entry_time": datetime.now().isoformat(timespec="seconds"),
        "exit_time": "",
        "lots_filled": 0,
        "exit_filled": 0,
        "position_open": True,
    })
    bridge_state.set_state(pair_name, "Running")
    bridge_state.set_pair_ctx(pair_name, {
        "pair_number": len(bridge_state.list_pair_names()) + 1,
        "created_time": datetime.now().isoformat(),
        "status": "active",
        "leg1_status": "not_initialized",
        "leg2_status": "initialized",
        "leg1_cfg": leg1_cfg,
        "leg2_cfg": leg2_cfg,
        "leg1_exit_price": None,
        "leg1_exit_time": None,
        "leg2_exit_price": None,
        "leg2_exit_time": None,
    })
    ctx = PairContext(pair_name, leg1_cfg, leg2_cfg)
    bridge_state.set_ctx(pair_name, ctx)
    th = threading.Thread(target=_runner, args=(ctx,), daemon=True)
    bridge_state.set_thread(pair_name, th)
    th.start()
    _emit_snapshot_now()
    _emit_cmd_result("start", pair_name, success=True)


def _squareoff(pair_name: str) -> None:
    """Cancel all orders for this pair, set state Finished, emit snapshot and finished."""
    ctx = bridge_state.get_ctx(pair_name)
    if not ctx:
        _emit_cmd_result("squareoff", pair_name, success=False, error="No context for pair")
        return
    ctx.exit_requested = True
    order_ids = list(ctx.order_ids)
    if order_ids:
        from nubra_order import cancel_order_nubra
        for oid in order_ids:
            try:
                cancel_order_nubra(oid)
            except Exception as e:
                _log(pair_name, f"Cancel order {oid} error: {e}")
    bridge_state.set_state(pair_name, "Squaring Off")
    bridge_state.update_position_field(pair_name, "position_open", False)
    _emit_snapshot_now()
    _emit({"type": "finished", "pair_name": pair_name})
    _emit_cmd_result("squareoff", pair_name, success=True)


def _pause_pair(pair_name: str) -> None:
    ctx = bridge_state.get_ctx(pair_name)
    if not ctx:
        _emit_cmd_result("pause", pair_name, success=False, error="No context for pair")
        return
    ctx.paused = True
    bridge_state.set_state(pair_name, "Paused")
    _emit_snapshot_now()
    _emit_cmd_result("pause", pair_name, success=True)


def _resume_pair(pair_name: str) -> None:
    ctx = bridge_state.get_ctx(pair_name)
    if not ctx:
        _emit_cmd_result("resume", pair_name, success=False, error="No context for pair")
        return
    ctx.paused = False
    bridge_state.set_state(pair_name, "Running")
    _emit_snapshot_now()
    _emit_cmd_result("resume", pair_name, success=True)


def _forget_pair(pair_name: str) -> None:
    state = bridge_state.get_states().get(pair_name, "")
    if state and state not in ("Finished", "Paused", "Squaring Off"):
        th = bridge_state.get_thread(pair_name)
        if th and th.is_alive():
            _emit_cmd_result("delete", pair_name, success=False, error="Strategy is running. Pause or square off first.")
            return
    bridge_state.remove_pair(pair_name)
    _emit_snapshot_now()
    _emit_cmd_result("delete", pair_name, success=True)


def _bootstrap() -> None:
    """Ensure Nubra client, order-updates socket, and market-data thread."""
    from nubra_client import ensure_nubra
    from order_updates_nubra import start_order_updates_socket

    ensure_nubra()
    start_order_updates_socket()

    def _run_subscribe() -> None:
        import subscribe_orderbook
        subscribe_orderbook.main()

    if not hasattr(_bootstrap, "_market_started"):
        threading.Thread(target=_run_subscribe, daemon=True).start()
        _bootstrap._market_started = True
    import time
    time.sleep(2)


def main() -> None:
    setup_utf8_io()
    _emit({"type": "log", "line": f"[{_now()}] [Nubra Bridge] Starting..."})
    _bootstrap()
    _emit({"type": "log", "line": f"[{_now()}] [Nubra Bridge] Ready."})
    _emit({"type": "ready"})
    _emit_snapshot_now()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            _emit({"type": "raw", "line": raw})
            continue

        cmd = msg.get("cmd")
        if cmd == "start_pair":
            try:
                _start_pair(
                    msg["pair_name"],
                    msg.get("leg1_cfg") or {},
                    msg.get("leg2_cfg") or {},
                    l1_state=msg.get("l1_state"),
                    l2_state=msg.get("l2_state"),
                )
            except Exception as e:
                _log(msg.get("pair_name", "?"), f"FATAL start_pair: {e}")
                import traceback
                traceback.print_exc()
                _emit_cmd_result("start", msg.get("pair_name", "unknown"), success=False, error=str(e))
        elif cmd == "squareoff":
            _squareoff(msg["pair_name"])
        elif cmd == "forget_pair":
            _forget_pair(msg["pair_name"])
        elif cmd == "factory_reset":
            for pn in bridge_state.list_pair_names():
                ctx = bridge_state.get_ctx(pn)
                if ctx:
                    ctx.exit_requested = True
            bridge_state.clear_all()
            _emit({"type": "log", "line": f"[{_now()}] [system] Factory reset done."})
            _emit_snapshot_now()
            _emit({"type": "cmd_ok", "cmd": "factory_reset"})
        elif cmd == "pause":
            _pause_pair(msg["pair_name"])
        elif cmd == "resume":
            _resume_pair(msg["pair_name"])
        elif cmd == "stop_all":
            bridge_state.set_cmd_all("squareoff")
            for pn in bridge_state.list_pair_names():
                ctx = bridge_state.get_ctx(pn)
                if ctx:
                    ctx.exit_requested = True
                _squareoff(pn)
            _emit({"type": "cmd_ok", "cmd": "stop_all"})
        elif cmd == "status":
            _emit_snapshot_now()
        elif cmd == "ping":
            _emit({"type": "pong", "echo": msg.get("echo")})
        else:
            _emit({"type": "stderr", "error": "unknown_cmd", "msg": msg})

    _emit({"type": "exit"})


if __name__ == "__main__":
    main()
