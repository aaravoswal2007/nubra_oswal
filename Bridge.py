# Bridge.py - Nubra: same protocol as Oswal, backend is executor_nubra_bridge / Main_strategy (nubra).
import sys, json, threading, time, re, builtins, os
from datetime import datetime, timedelta
import math

# ---------- I/O: UTF-8 ----------
from bridge.io import setup_utf8_io, _emit, _emit_cmd_result
setup_utf8_io()

# ---------- Nubra: ensure nubra_oswal is on path; strategy/executor resolved by Main_strategy, leg2, position_management ----------
_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

# Strategy and market data: bridge modules import Main_strategy, marketdata_store (nubra adapters)
# Executor: leg2 and position_management use executor_nubra_bridge.place_order_splices_mid_ltq
print("[BRIDGE] Using Nubra backend (Main_strategy, executor_nubra_bridge)")

# =============== Persistence ===============
from runtime_paths_live import state_file, daily_log
from bridge.state_wrappers import STATE_FILE
print(f"[Bridge] STATE_FILE path: {STATE_FILE}")

# =============== Optimized State Management ===============
from bridge.optimized_state import get_state_manager
from bridge.state_store import list_all_pairs, get_pairs_dir
from bridge.pair_manager import get_pair_manager, remove_pair_manager

# =============== Setup Logging ===============
from bridge.utils import setup_logging
log = setup_logging()

# =============== Import State Management Functions ===============
from bridge.state_wrappers import (
    _get_state_mgr, _read_metadata, _read_json, _write_json,
    _update_metadata, _update_active_pairs,
    _get_json_positions, _get_json_states, _get_json_pair_ctx,
    _set_position, _set_state, _set_pair_ctx, _update_pair_ctx_field,
    _save_leg_details, _remove_pair, _update_position_field
)

# =============== Import Utility Functions ===============
from bridge.utils import (
    _json_dt, _now, _log, _parse_dt, _deserialize_state, _side_text, _time_reached
)

# =============== Import Shared State ===============
from bridge.shared_state import (
    _events_lock, _events, _threads, _ctx, _cmd_lock, _cmd,
    _resume_store_lock, _resume_store, _progress_lock, _progress_seen,
    _json_lock, _deleted_pairs_lock, _deleted_pairs, _TL
)

# =============== Import Pair Operations ===============
from bridge.pair_operations import (
    _initialize_pair, _drop_state, _pause_pair, _resume_pair,
    _start_pair, _cleanup_pair, _cleanup_duplicate_pairs
)

# =============== Import Progress Handler ===============
from bridge.progress_handler import (
    _handle_progress, _auto_save_pair_state, _force_position_update
)

# =============== Import Pair Threading ===============
from bridge.pair_threading import PairCtx, _pair_thread, _leg2_monitor

# =============== Import Serialization ===============
from bridge.serialization import (
    _serialize_ctx_states, _pump, _emit_snapshot_now
)

# =============== Import Rehydration ===============
from bridge.rehydration import (
    _rehydrate_pair_from_json, _auto_rehydrate_pairs, _migrate_old_state_to_per_pair
)

# =============== Import Commands ===============
from bridge.commands import (
    _squareoff, _forget_pair, _factory_reset, _status
)

# =============== Import Command Processor ===============
from bridge.command_processor import (
    _process_exec_line, _print_hook, setup_print_hook
)

# =============== Import Time Utils ===============
from bridge.time_utils import (
    _check_resume_queue, _auto_pause_all_running
)

# Setup print hook to intercept all prints
setup_print_hook()

# =============== Main pump / command loop ===============
def main():
    # 2) Auto-rehydrate pairs BEFORE starting pump
    _auto_rehydrate_pairs()

    # 3) Start the pump and announce readiness
    threading.Thread(target=_pump, daemon=True).start()
    _emit({"type":"ready"})

    # 4) Immediately push a snapshot with restored positions/states
    _emit_snapshot_now()

    # 5) Command loop
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            _emit({"type":"raw","line": raw})
            continue

        cmd = msg.get("cmd")
        if cmd == "start_pair":
            try:
                _start_pair(
                    msg["pair_name"], msg["leg1_cfg"], msg["leg2_cfg"],
                    l1_state=msg.get("l1_state"), l2_state=msg.get("l2_state")
                )
            except Exception as e:
                print(f"[Bridge] FATAL ERROR in start_pair command: {e}")
                import traceback
                traceback.print_exc()
                _emit({"type":"log","line": f"[{_now()}] [SYSTEM] FATAL ERROR in start_pair: {e}"})
                _emit_cmd_result("start", msg.get("pair_name", "unknown"), success=False, error=f"Fatal error: {e}")
        elif cmd == "squareoff":
            _squareoff(msg["pair_name"])
        elif cmd == "forget_pair":
            _forget_pair(msg["pair_name"])
        elif cmd == "factory_reset":
            _factory_reset()
        elif cmd == "pause":
            _pause_pair(msg["pair_name"])
        elif cmd == "resume":
            _resume_pair(msg["pair_name"])
        elif cmd == "stop_all":
            with _cmd_lock:
                for k in list(_ctx.keys()):
                    _cmd[k] = "squareoff"
            _emit({"type":"cmd_ok","cmd":"stop_all"})
        elif cmd == "status":
            _status()
        elif cmd == "ping":
            _emit({"type":"pong","echo": msg.get("echo")})
        else:
            _emit({"type":"stderr","error":"unknown_cmd","msg": msg})

    _emit({"type":"exit"})

if __name__ == "__main__":
    main()
