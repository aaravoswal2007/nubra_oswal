"""
Pair lifecycle operations for Bridge.
Handles initialization, starting, pausing, resuming, and cleanup of pairs.
"""
from datetime import datetime
import time
import threading
from bridge.shared_state import (
    _deleted_pairs_lock, _deleted_pairs, _threads, _ctx, _resume_store_lock,
    _resume_store, _progress_lock, _progress_seen, _resume_queue
)
from bridge.state_wrappers import (
    _get_json_positions, _get_json_states, _get_json_pair_ctx,
    _set_position, _set_state, _set_pair_ctx, _update_pair_ctx_field,
    _get_state_mgr, _update_active_pairs, _read_json, _write_json,
    _update_position_field
)
from bridge.utils import _log, _now, _deserialize_state
from bridge.io import _emit, _emit_cmd_result
from bridge.pair_manager import get_pair_manager, remove_pair_manager
from marketdata_store import market_data
import Main_strategy as MS


def _initialize_pair(pair_name: str, pair_data: dict):
    """Initialize a new pair with default format and values"""
    # Noisy lifecycle log removed (was spamming UI/stdout on every init)
    
    # Remove from deleted pairs set if it was previously deleted (allows recreation)
    with _deleted_pairs_lock:
        if pair_name in _deleted_pairs:
            _deleted_pairs.discard(pair_name)
            _log("SYSTEM", f"Removed {pair_name} from deleted pairs set (recreating)")
    
    # State manager handles directory creation automatically
    
    # Initialize position with default values
    position_data = {
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
        "position_open": False
    }
    
    # Initialize state
    state_data = "Initialized"
    
    # Initialize pair context with provided data
    ctx_data = {
        "pair_number": len(_get_json_positions()) + 1,
        "created_time": datetime.now().isoformat(),
        "status": "active",
        # per-leg human statuses
        "leg1_status": "not_initialized",
        "leg2_status": "not_initialized",
        # exit info (nullable)
        "leg1_exit_price": None,
        "leg1_exit_time": None,
        "leg2_exit_price": None,
        "leg2_exit_time": None,
        **pair_data  # Merge with provided pair data
    }
    
    # Set all three sections immediately
    # Noisy lifecycle logs removed (we still do the same work; warnings/errors remain)
    try:
        _set_position(pair_name, position_data)
    except Exception as e:
        _log(pair_name, f"ERROR setting position: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    try:
        _set_state(pair_name, state_data)
    except Exception as e:
        _log(pair_name, f"ERROR setting state: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    try:
        _set_pair_ctx(pair_name, ctx_data)
    except Exception as e:
        _log(pair_name, f"ERROR setting context: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    # Verify it was written - small delay to ensure file system sync
    time.sleep(0.1)
    verify_state = _get_json_states().get(pair_name)
    verify_pos = _get_json_positions().get(pair_name)
    verify_ctx = _get_json_pair_ctx().get(pair_name)
    # Verification log removed (too noisy). Keep warning path below.
    
    if not verify_state or not verify_pos or not verify_ctx:
        _log(pair_name, f"WARNING: Pair not fully initialized! state={verify_state}, pos={verify_pos is not None}, ctx={verify_ctx is not None}")
        # Try to read the state directly to debug
        try:
            state_mgr = _get_state_mgr()
            file_state = state_mgr.get_position(pair_name)
            _log(pair_name, f"Direct state read: {file_state}")
        except Exception as e:
            _log(pair_name, f"ERROR reading state directly: {e}")
    
    # Noisy lifecycle log removed (pair number is visible in UI/context anyway)
    
    # Update metadata with new active pair
    _update_active_pairs()
    
    return ctx_data


def _drop_state(pair):
    """Drop state for a pair - per-pair file update"""
    # Remove from per-pair file system
    try:
        manager = get_pair_manager(pair)
        manager.set_state_str("Finished")  # Mark as finished before deletion
        remove_pair_manager(pair)  # Delete the per-pair file
    except Exception as e:
        print(f"[Bridge] Warning: Failed to remove per-pair file for {pair}: {e}")


def _pause_pair(pair_name):
    """Pause a running pair. Emits cmd_ok with action: 'pause'."""
    # CRITICAL: Check if pair was deleted - don't allow pause on deleted pairs
    with _deleted_pairs_lock:
        if pair_name in _deleted_pairs:
            _log(pair_name, "Cannot pause - pair was deleted")
            # Removed duplicate _emit - _log already handles it
            _emit_cmd_result("pause", pair_name, success=False, error="Pair was deleted")
            return
    
    # CRITICAL: Check if pair exists in file - if not, it was deleted
    cur_states = _get_json_states()
    cur_positions = _get_json_positions()
    if pair_name not in cur_states and pair_name not in cur_positions:
        _log(pair_name, "Cannot pause - pair does not exist in state file (was deleted)")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("pause", pair_name, success=False, error="Pair was deleted")
        return
    
    ctx = _ctx.get(pair_name)
    if not ctx:
        _log(pair_name, "pause requested but not running")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("pause", pair_name, success=True)
        return
    
    # Check if already paused
    if ctx.paused:
        _log(pair_name, "already paused")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("pause", pair_name, success=True)
        return

    # mark paused and set public state
    ctx.paused = True
    _set_state(pair_name, "Paused")
    _log(pair_name, "Paused")
    
    # Auto-save the paused state (late import to avoid circular dependency)
    from bridge.progress_handler import _auto_save_pair_state
    _auto_save_pair_state(pair_name, ctx)
    
    # Save leg details to JSON for rehydration (create clean copies to avoid circular references)
    if getattr(ctx, "l1_state", None):
        l1_clean = {k: v for k, v in ctx.l1_state.items() 
                   if not k.startswith('_') and not callable(v) and v is not None}
        _update_pair_ctx_field(pair_name, "leg1_details", l1_clean)
    if getattr(ctx, "l2_state", None):
        l2_clean = {k: v for k, v in ctx.l2_state.items() 
                   if not k.startswith('_') and not callable(v) and v is not None}
        _update_pair_ctx_field(pair_name, "leg2_details", l2_clean)

    # Refresh MTM (best-effort) and persist full positions + states + pairCtx
    try:
        # update per-pair mtm using market_data (best-effort)
        pos_copy = _get_json_positions()
        total_mtm = 0.0

        for k, pv in pos_copy.items():
            try:
                # entry_symbol_key falls back to symbol
                sym = pv.get("entry_symbol_key") or pv.get("symbol")
                if sym:
                    md = market_data.get(sym) or {}
                    ltp = md.get("ltp")
                    if ltp is not None:
                        pv["ltp"] = ltp
                        avg = pv.get("avg_entry")
                        lot_size = int(pv.get("lot_size", 1) or 1)
                        net_lots = float(pv.get("net_lots", 0.0) or 0.0)
                        is_long = pv.get("side", "BUY") == "BUY"
                        
                        if avg is not None and net_lots > 0:
                            if is_long:
                                mtm = (float(ltp) - float(avg)) * net_lots * lot_size
                            else:
                                mtm = (float(avg) - float(ltp)) * net_lots * lot_size
                            pv["mtm"] = round(mtm, 2)
                            total_mtm += mtm
                        
                        # Update JSON with LTP and MTM changes
                        _update_position_field(k, "ltp", ltp)
                        if avg is not None and net_lots > 0:
                            _update_position_field(k, "mtm", pv["mtm"])
            except Exception:
                # don't let one pair block everything
                pass

        cur_states = _get_json_states()
        cur_pair_ctx = _get_json_pair_ctx()

        # Update JSON with current state (JSON-first approach)
        # JSON updates are now handled by individual _set_* functions

        _emit({
            "type": "snapshot",
            "positions": pos_copy,
            "states": cur_states,
            "pairCtx": cur_pair_ctx,
            "total_mtm": total_mtm,
        })

    except Exception as e:
        # never crash on persistence errors
        _log(pair_name, f"pause persist error: {e}")

    # confirm to caller
    _emit_cmd_result("pause", pair_name, success=True)


def _resume_pair(pair_name):
    """Resume a paused pair. Emits cmd_ok with action: 'resume'."""
    # Late imports to avoid circular dependencies
    from bridge.rehydration import _rehydrate_pair_from_json
    from bridge.pair_threading import _pair_thread
    from bridge.serialization import _emit_snapshot_now
    from bridge.progress_handler import _auto_save_pair_state
    
    # Always try to rehydrate first (handles restart scenarios)
    ctx = _ctx.get(pair_name)
    if not ctx:
        ctx = _rehydrate_pair_from_json(pair_name)
        if not ctx:
            _log(pair_name, "resume requested but no context found and cannot rehydrate")
            # Removed duplicate _emit - _log already handles it
            _emit_cmd_result("resume", pair_name, success=True)
            return
    
    # Check if thread is actually running - if not, restart it
    th = _threads.get(pair_name)
    if not th or not th.is_alive():
        _log(pair_name, "Thread not running - creating new thread")
        # Removed duplicate _emit - _log already handles it
        # Start new thread
        t = threading.Thread(target=_pair_thread, args=(ctx,), daemon=True)
        _threads[pair_name] = t
        t.start()
        _log(pair_name, "New thread created and started")
        # Removed duplicate _emit - _log already handles it
    else:
        _log(pair_name, "Thread already running - no new thread needed")
        # Removed duplicate _emit - _log already handles it
    
    # Check if already running (not paused)
    if not ctx.paused:
        _log(pair_name, "already running")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("resume", pair_name, success=True)
        return
    
    # Check market hours - if not within market hours, queue for later
    now = datetime.now().time()
    # Market hours: 9:15 AM to 3:30 PM
    market_open_time = now.hour > 9 or (now.hour == 9 and now.minute >= 15)
    market_close_time = now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    within_market_hours = market_open_time and not market_close_time
    
    if not within_market_hours:
        _resume_queue.add(pair_name)
        _log(pair_name, f"resume queued - outside market hours (current: {now.strftime('%H:%M')})")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("resume", pair_name, success=True)
        return

    # CRITICAL: Set paused to False FIRST, before updating state
    # This ensures the thread exits the paused loop immediately
    # Use a small delay to ensure the paused loop has a chance to detect the change
    ctx.paused = False
    time.sleep(0.05)  # Brief delay to allow paused loop to detect the change
    
    # Determine the correct state based on what leg is active
    if getattr(ctx, "l2_state", None) and ctx.l2_state.get("symbol"):
        # Leg2 is active - set to Running state
        _set_state(pair_name, "Running")
        symbol = ctx.l2_state["symbol"]
        entry = ctx.l2_state.get("entry", 0)
        spot = ctx.l2_state.get("spot_entry", 0)
        sl = ctx.l2_state.get("sl", 0)
        sltype = ctx.l2_state.get("SLtype", "POINTS")
        _log(pair_name, f"Leg2 resumed {symbol} entry={entry} spot={spot} SL={sl} ({sltype})")
    elif getattr(ctx, "l1_state", None) and ctx.l1_state.get("symbol"):
        # Leg1 is active - set to Leg1: armed state
        _set_state(pair_name, "Leg1: armed")
        symbol = ctx.l1_state["symbol"]
        entry = ctx.l1_state.get("entry", 0)
        spot = ctx.l1_state.get("spot_entry", 0)
        sl = ctx.l1_state.get("sl", 0)
        sltype = ctx.l1_state.get("SLtype", "POINTS")
        _log(pair_name, f"Leg1 resumed {symbol} entry={entry} spot={spot} SL={sl} ({sltype})")
    else:
        # Fallback - set to Running and show resumed
        _set_state(pair_name, "Running")
        _log(pair_name, "Resumed")
    
    # CRITICAL: Emit snapshot immediately after setting state to ensure frontend sees the change
    # This prevents the thread from potentially resetting the state back to "Paused"
    _emit_snapshot_now()
    
    # Auto-save the resumed state
    _auto_save_pair_state(pair_name, ctx)

    # Persist current positions+states (same logic as pause)
    try:
        # refresh mtm best-effort
        pos_copy = _get_json_positions()
        total_mtm = 0.0

        for k, pv in pos_copy.items():
            try:
                sym = pv.get("entry_symbol_key") or pv.get("symbol")
                if sym:
                    md = market_data.get(sym) or {}
                    ltp = md.get("ltp")
                    if ltp is not None:
                        pv["ltp"] = ltp
                        avg = pv.get("avg_entry")
                        lot_size = int(pv.get("lot_size", 1) or 1)
                        net_lots = float(pv.get("net_lots", 0.0) or 0.0)
                        is_long = pv.get("side", "BUY") == "BUY"
                        
                        if avg is not None and net_lots > 0:
                            if is_long:
                                mtm = (float(ltp) - float(avg)) * net_lots * lot_size
                            else:
                                mtm = (float(avg) - float(ltp)) * net_lots * lot_size
                            pv["mtm"] = round(mtm, 2)
                            total_mtm += mtm
                        
                        # Update JSON with LTP and MTM changes
                        _update_position_field(k, "ltp", ltp)
                        if avg is not None and net_lots > 0:
                            _update_position_field(k, "mtm", pv["mtm"])
            except Exception:
                pass

        cur_states = _get_json_states()
        cur_pair_ctx = _get_json_pair_ctx()

        # Update JSON with current state (JSON-first approach)
        # JSON updates are now handled by individual _set_* functions

        _emit({
            "type": "snapshot",
            "positions": pos_copy,
            "states": cur_states,
            "pairCtx": cur_pair_ctx,
            "total_mtm": total_mtm,
        })

    except Exception as e:
        _log(pair_name, f"resume persist error: {e}")

    _emit_cmd_result("resume", pair_name, success=True)


def _start_pair(pair_name, leg1_cfg, leg2_cfg, l1_state=None, l2_state=None):
    """Start (or resume) a pair. If Leg1 was armed and not completed, re-echo the banner once. Emits cmd_ok with action: 'start'."""
    # Late imports to avoid circular dependencies
    from bridge.pair_threading import PairCtx, _pair_thread
    from bridge.serialization import _emit_snapshot_now
    
    try:
        _log(pair_name, f"Start requested for '{pair_name}'")
        # _log("SYSTEM", f"[DEBUG] _start_pair ENTRY for {pair_name}")
        # Removed duplicate print - _log already handles it
        
        # State manager handles directory creation automatically - no need to check
        
        # Check current positions
        # _log("SYSTEM", f"[DEBUG] Checking positions for {pair_name}")
        # Removed duplicate print - _log already handles it
        try:
            current_positions = _get_json_positions()
            # _log("SYSTEM", f"[DEBUG] Current positions: {list(current_positions.keys())}")
            # Removed duplicate print - _log already handles it
        except Exception as e:
            error_msg = f"ERROR getting positions: {e}"
            import traceback
            traceback.print_exc()
            _log("SYSTEM", f"[ERROR] {error_msg}")
            # Removed duplicate print - _log already handles it
            _emit_cmd_result("start", pair_name, success=False, error=f"Failed to get positions: {e}")
            return
        
        # CRITICAL: Check if pair was recently deleted - prevent re-adding deleted pairs
        with _deleted_pairs_lock:
            if pair_name in _deleted_pairs:
                _log("SYSTEM", f"[WARNING] Attempted to start pair '{pair_name}' that was recently deleted. Ignoring start request.")
                # Removed duplicate print - _log already handles it
                _emit_cmd_result("start", pair_name, success=False, error=f"Pair '{pair_name}' was recently deleted. Please wait a moment before recreating it.")
                return
        
        # Initialize pair with default format and values if not already initialized
        if pair_name not in current_positions:
            # _log("SYSTEM", f"[DEBUG] Pair {pair_name} not in positions, initializing...")
            # Removed duplicate print - _log already handles it
            pair_data = {
                "leg1_cfg": leg1_cfg,
                "leg2_cfg": leg2_cfg,
                "l1_state": l1_state,
                "l2_state": l2_state
            }
            # Check for duplicate pair names and clean up if needed
            # _log("SYSTEM", f"[DEBUG] Cleaning up duplicate pairs...")
            # Removed duplicate print - _log already handles it
            _cleanup_duplicate_pairs()
            # _log("SYSTEM", f"[DEBUG] Calling _initialize_pair for {pair_name}")
            # Removed duplicate print - _log already handles it
            try:
                _initialize_pair(pair_name, pair_data)
                # _log("SYSTEM", f"[DEBUG] Pair {pair_name} initialized successfully")
                # Removed duplicate print - _log already handles it
            except Exception as e:
                error_msg = f"ERROR initializing pair {pair_name}: {e}"
                import traceback
                traceback.print_exc()
                _log("SYSTEM", f"[ERROR] {error_msg}")
                # Removed duplicate print - _log already handles it
                _emit_cmd_result("start", pair_name, success=False, error=f"Initialization failed: {e}")
                return
        else:
            _log("SYSTEM", f"[DEBUG] Pair {pair_name} already exists in positions")
            # Removed duplicate print - _log already handles it
    except Exception as e:
        error_msg = f"ERROR in _start_pair initialization check: {e}"
        import traceback
        traceback.print_exc()
        _log("SYSTEM", f"[ERROR] {error_msg}")
        # Removed duplicate print and _emit - _log already handles it
        _emit_cmd_result("start", pair_name, success=False, error=f"Start failed: {e}")
        return
    
    # CRITICAL: Verify pair is properly initialized (exists in all required sections)
    # If pair exists in positions but not in states, or vice versa, it's incomplete/invalid
    current_positions = _get_json_positions()
    current_states = _get_json_states()
    current_pair_ctx = _get_json_pair_ctx()
    
    # Check if pair exists in positions (required for start)
    if pair_name not in current_positions:
        _log(pair_name, "Cannot start pair - not properly initialized (missing from positions)")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("start", pair_name, success=False, error="Pair not properly initialized")
        return
    
    # Check if pair exists in states (required for start)
    if pair_name not in current_states:
        _log(pair_name, "Cannot start pair - not properly initialized (missing from states)")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("start", pair_name, success=False, error="Pair not properly initialized")
        return
    
    # Check if pair has already been finished - if so, cannot start again (JSON-first)
    current_state = current_states.get(pair_name, "")
    if current_state == "Finished":
        _log(pair_name, "Cannot start pair - already finished (squared off)")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("start", pair_name, success=False, error="Pair already finished")
        return
    
    # already running → treat as resume (unpause) and return
    if pair_name in _threads and _threads[pair_name].is_alive():
        ctx = _ctx.get(pair_name)
        if ctx:
            ctx.paused = False
        _log(pair_name, "already running → resume")
        _emit_cmd_result("start", pair_name, success=True)
        return

    def _resume_banner_if_leg1_armed(cfg, l1st):
        if not (l1st and l1st.get("armed") and not l1st.get("completed")):
            return l1st
        try:
            st, ev = MS.leg1_step_start_time(
                cfg["stock"], cfg["strike"], cfg["option_type"],
                trade_direction=cfg.get("trade_direction", "SELL"),
                start_time_str=cfg.get("start_time_str", "09:20:00"),
                SLval=cfg.get("SLval", 0.0),
                SLtype=cfg.get("SLtype", "POINTS"),
                TSLarm=cfg.get("TSLarm"),
                TSLmove=cfg.get("TSLmove"),
                TSLtype=cfg.get("TSLtype"),
                state=l1st,
                continuous_profiling=cfg.get("continuous_profiling", False),
                resume_emit=True,
            )
            # Logging handled by pair_threading - don't duplicate here
            # for e in ev:
            #     _log(pair_name, f"Leg1 armed: {e['symbol']} entry={e['entry_price']} spot={e['spot_entry']} SL={e['sl_level']} ({e['sltype']})")
            return st
        except Exception as ex:
            print("[bridge] leg1 resume-banner error:", ex)
            return l1st

    # 1) If caller provided l1_state, emit the single resume banner (if applicable)
    if l1_state:
        l1_state = _resume_banner_if_leg1_armed(leg1_cfg, l1_state)

    # 2) If no states provided, try resume from disk store
    if l1_state is None and l2_state is None:
        with _resume_store_lock:
            res = _resume_store.get(pair_name)
        if res:
            l1_state = res.get("l1_state")
            l2_state = res.get("l2_state")

    # 3) Deserialize before any further use (disk may hold ISO strings)
    l1_state = _deserialize_state(l1_state) if l1_state else None
    l2_state = _deserialize_state(l2_state) if l2_state else None

    # 4) If we just pulled Leg1 from disk and it's armed, echo banner once now
    if l1_state and not l2_state:
        l1_state = _resume_banner_if_leg1_armed(leg1_cfg, l1_state)

    # 5) Verify pair was initialized before proceeding
    try:
        verify_pos = _get_json_positions().get(pair_name)
        verify_state = _get_json_states().get(pair_name)
        verify_ctx = _get_json_pair_ctx().get(pair_name)
        
        if not verify_pos or not verify_state or not verify_ctx:
            print(f"[Bridge] WARNING: Pair {pair_name} not fully initialized before starting thread")
            print(f"[Bridge]   pos={verify_pos is not None}, state={verify_state}, ctx={verify_ctx is not None}")
            # Try to emit snapshot anyway to show what we have
            _emit_snapshot_now()
            _emit_cmd_result("start", pair_name, success=False, error="Pair not fully initialized")
            return
    except Exception as e:
        print(f"[Bridge] ERROR verifying pair {pair_name} before start: {e}")
        import traceback
        traceback.print_exc()
        _emit_cmd_result("start", pair_name, success=False, error=f"Verification failed: {e}")
        return
    
    # 6) Build context and launch worker
    try:
        ctx = PairCtx(pair_name, leg1_cfg, leg2_cfg, l1_state=l1_state, l2_state=l2_state)
        ctx.paused = False  # Start in running state by default
        _ctx[pair_name] = ctx
        t = threading.Thread(target=_pair_thread, args=(ctx,), daemon=True)
        _threads[pair_name] = t
        t.start()
        _emit({"type": "started", "pair_name": pair_name})
        print(f"[Bridge] Thread started for {pair_name}")
        
        # Emit snapshot immediately so frontend sees the pair in state
        _emit_snapshot_now()
        print(f"[Bridge] Snapshot emitted for {pair_name}")
        
        # Emit cmd_ok for start action
        _emit_cmd_result("start", pair_name, success=True)
        print(f"[Bridge] Start completed successfully for {pair_name}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        _log(pair_name, f"Failed to start thread: {e}")
        # Removed duplicate print and _emit - _log already handles it
        _emit_cmd_result("start", pair_name, success=False, error=f"Thread start failed: {e}")


def _cleanup_pair(pair_name):
    """Clean up a pair's resources"""
    with _resume_store_lock:
        _resume_store.pop(pair_name, None)
    with _progress_lock:
        _progress_seen.pop(pair_name, None)
    _ctx.pop(pair_name, None)
    _threads.pop(pair_name, None)


def _cleanup_duplicate_pairs():
    """Clean up duplicate pairs in JSON state"""
    try:
        state = _read_json()
        pair_ctx = state.get("pairCtx", {})
        
        # Find and remove duplicates
        seen_names = set()
        duplicates = []
        
        for pair_name in list(pair_ctx.keys()):
            if pair_name in seen_names:
                duplicates.append(pair_name)
            else:
                seen_names.add(pair_name)
        
        # Remove duplicates
        for dup in duplicates:
            _log("SYSTEM", f"Removing duplicate pair: {dup}")
            pair_ctx.pop(dup, None)
            state["positions"].pop(dup, None)
            state["states"].pop(dup, None)
        
        if duplicates:
            _write_json(state)
            _log("SYSTEM", f"Cleaned up {len(duplicates)} duplicate pairs")
            
    except Exception as e:
        _log("SYSTEM", f"Error cleaning up duplicates: {e}")

