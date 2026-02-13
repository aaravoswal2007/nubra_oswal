"""
Serialization and snapshot generation for Bridge.
Handles serializing context states, emitting snapshots, and the main pump loop.
"""
import time
import os
from bridge.shared_state import (
    _events_lock, _events, _ctx, _resume_store_lock, _resume_store,
    _deleted_pairs_lock, _deleted_pairs, _threads
)
from bridge.state_wrappers import (
    _get_json_states, _get_json_positions, _get_json_pair_ctx,
    _get_state_mgr, _set_pair_ctx, _set_state, _set_position
)
from bridge.utils import _log
from bridge.io import _emit
from marketdata_store import market_data
from runtime_paths_live import state_file

STATE_FILE = state_file("bridge_state.json")


def _serialize_ctx_states():
    """Serialize context states - JSON-first approach"""
    # Get from JSON first, then merge with RAM
    out = _get_json_pair_ctx()
    
    # CRITICAL: Only serialize pairs that exist in JSON (source of truth)
    # If a pair was deleted from JSON, don't write it back from RAM
    json_states = _get_json_states()
    json_positions = _get_json_positions()
    
    # Check if state is valid (not empty/default) - if empty, don't remove pairs from RAM
    # Empty state might be due to temporary error or factory reset, and running pairs should stay in RAM
    # NOTE: json_states comes from bridge_state.json (metadata), json_positions comes from individual pair files
    # CRITICAL: Only check json_states and json_positions, not 'out' (which may have stale cached data)
    json_is_valid = len(json_states) > 0 or len(json_positions) > 0
    
    # Add any active contexts from RAM, but only if they exist in JSON
    # CRITICAL: Only check json_states and json_positions (not 'out') to prevent re-adding deleted pairs
    # 'out' may contain stale data from cache, so we don't trust it for existence checks
    for k, v in list(_ctx.items()):  # Use list() to avoid modification during iteration
        # Check if pair is explicitly marked as deleted
        with _deleted_pairs_lock:
            is_deleted = k in _deleted_pairs
        
        # Only serialize if pair exists in states OR positions (source of truth)
        # Do NOT check 'out' because it may contain stale cached data
        if k in json_states or k in json_positions:
            existing = out.get(k, {})
            existing["l1_internal"] = v.l1_state
            existing["l2_internal"] = v.l2_state
            out[k] = existing
            # CRITICAL: Only update JSON if pair exists in states or positions
            # Don't write back deleted pairs to the file
            _set_pair_ctx(k, existing)
        elif is_deleted:
            # Pair is explicitly marked as deleted - remove from RAM context
            _log("SYSTEM", f"Removing deleted pair '{k}' from RAM context (explicitly deleted)")
            _ctx.pop(k, None)
            # Also remove from 'out' to prevent it from being returned
            out.pop(k, None)
        elif json_is_valid:
            # JSON is valid (not empty) and pair is not in it
            # CRITICAL: Check if pair was deleted FIRST - don't recover deleted pairs
            with _deleted_pairs_lock:
                is_deleted_check = k in _deleted_pairs
            
            if is_deleted_check:
                # Pair was deleted - remove from RAM context immediately
                _log("SYSTEM", f"Removing deleted pair '{k}' from RAM context (was deleted)")
                _ctx.pop(k, None)
                out.pop(k, None)
                continue
            
            # CRITICAL: Check if pair is running before removing - don't remove running pairs
            is_running = _threads.get(k) and _threads[k].is_alive()
            if is_running:
                # Pair is running but not in JSON - this might be a race condition or recovery issue
                # BUT: Double-check if it was deleted (race condition)
                with _deleted_pairs_lock:
                    is_deleted_check = k in _deleted_pairs
                
                if is_deleted_check:
                    # Pair was deleted - stop recovery and remove from RAM
                    _log("SYSTEM", f"Pair '{k}' is running but was deleted - stopping thread and removing from RAM")
                    _ctx.pop(k, None)
                    out.pop(k, None)
                    # Stop the thread
                    th = _threads.get(k)
                    if th:
                        _threads.pop(k, None)
                    continue
                
                # Try to recover it instead of removing
                _log("SYSTEM", f"Pair '{k}' is running but not in valid JSON - attempting to recover instead of removing")
                try:
                    current_state = "Running" if not getattr(v, 'paused', False) else "Paused"
                    _set_state(k, current_state)
                    existing = out.get(k, {})
                    existing["l1_internal"] = v.l1_state
                    existing["l2_internal"] = v.l2_state
                    out[k] = existing
                    _set_pair_ctx(k, existing)
                    if hasattr(v, 'l2_state') and v.l2_state:
                        fills = v.l2_state.get('fills', {})
                        if fills and fills.get('lots_filled', 0) > 0:
                            position_data = {
                                "symbol": v.l2_state.get('symbol', ''),
                                "net_lots": fills.get('lots_filled', 0),
                                "side": v.l2_state.get('entry_side', 'BUY'),
                                "lot_size": 500,
                                "avg_entry": fills.get('avg_entry', 0),
                                "position_open": True
                            }
                            _set_position(k, position_data)
                    _log("SYSTEM", f"Recovered running pair '{k}' that was missing from JSON")
                except Exception as e:
                    _log("SYSTEM", f"Error recovering running pair '{k}': {e}")
                    # Keep in RAM even if recovery fails
            else:
                # Pair is not running and not in JSON - it was deleted
                _log("SYSTEM", f"Removing deleted pair '{k}' from RAM context (was deleted from JSON)")
                _ctx.pop(k, None)
                # Also remove from 'out' to prevent it from being returned
                out.pop(k, None)
        else:
            # JSON is empty/invalid - keep pair in RAM context (might be running)
            # CRITICAL: Check if pair was deleted FIRST - don't recover deleted pairs
            with _deleted_pairs_lock:
                is_deleted_check = k in _deleted_pairs
            
            if is_deleted_check:
                # Pair is deleted - remove from RAM context immediately
                _log("SYSTEM", f"Removing deleted pair '{k}' from RAM context (was deleted, JSON empty)")
                _ctx.pop(k, None)
                out.pop(k, None)
                continue
            
            # CRITICAL: If pair is actively running, try to write it back to JSON to recover from error
            # This prevents data loss when JSON file has parsing errors
            is_running = _threads.get(k) and _threads[k].is_alive()
            if is_running:
                # Pair is running but JSON is empty - try to recover by writing back to JSON
                # Only log once per recovery attempt to reduce spam
                if not hasattr(_serialize_ctx_states, '_recovery_logged'):
                    _serialize_ctx_states._recovery_logged = set()
                
                if k not in _serialize_ctx_states._recovery_logged:
                    _log("SYSTEM", f"JSON is empty but pair '{k}' is running - attempting to recover state")
                    _serialize_ctx_states._recovery_logged.add(k)
                
                try:
                    # Get current state from context
                    current_state = "Running" if not getattr(v, 'paused', False) else "Paused"
                    
                    # Write state back to JSON
                    _set_state(k, current_state)
                    
                    # Write context back to JSON
                    existing = out.get(k, {})
                    if hasattr(v, 'l1_state') and v.l1_state:
                        existing["l1_internal"] = v.l1_state
                    if hasattr(v, 'l2_state') and v.l2_state:
                        existing["l2_internal"] = v.l2_state
                    out[k] = existing
                    _set_pair_ctx(k, existing)
                    
                    # If pair has position data, try to recover it
                    if hasattr(v, 'l2_state') and v.l2_state and isinstance(v.l2_state, dict):
                        fills = v.l2_state.get('fills', {})
                        if fills and isinstance(fills, dict) and fills.get('lots_filled', 0) > 0:
                            position_data = {
                                "symbol": v.l2_state.get('symbol', ''),
                                "net_lots": fills.get('lots_filled', 0),
                                "side": v.l2_state.get('entry_side', 'BUY'),
                                "lot_size": 500,  # Default
                                "avg_entry": fills.get('avg_entry', 0),
                                "position_open": True
                            }
                            _set_position(k, position_data)
                            if k not in _serialize_ctx_states._recovery_logged:
                                _log("SYSTEM", f"Recovered position for '{k}' from RAM context")
                    
                    # Only log success once
                    if k in _serialize_ctx_states._recovery_logged and len(_serialize_ctx_states._recovery_logged) == 1:
                        _log("SYSTEM", f"Recovered state for running pair '{k}' from RAM context")
                except Exception as e:
                    _log("SYSTEM", f"Error recovering state for '{k}': {e}")
                    # Still keep in RAM even if recovery fails
            # Don't remove from RAM - keep it even if JSON is empty
            pass
    
    # Add any resume store data, but only if pair exists in JSON
    # CRITICAL: Only check json_states and json_positions (not 'out') to prevent re-adding deleted pairs
    with _resume_store_lock:
        for k, v in list(_resume_store.items()):  # Use list() to avoid modification during iteration
            # Check if pair is explicitly marked as deleted
            with _deleted_pairs_lock:
                is_deleted = k in _deleted_pairs
            
            # Only serialize if pair exists in states OR positions (source of truth)
            # Do NOT check 'out' because it may contain stale cached data
            if k in json_states or k in json_positions:
                if k not in out:
                    out[k] = v
                    # CRITICAL: Only update JSON if pair exists in states or positions
                    # Don't write back deleted pairs to the file
                    _set_pair_ctx(k, v)
            elif is_deleted:
                # Pair is explicitly marked as deleted - remove from resume store
                _log("SYSTEM", f"Removing deleted pair '{k}' from resume store (explicitly deleted)")
                _resume_store.pop(k, None)
                # Also remove from 'out' to prevent it from being returned
                out.pop(k, None)
            elif json_is_valid:
                # JSON is valid (not empty) and pair is not in it - remove from resume store
                _log("SYSTEM", f"Removing deleted pair '{k}' from resume store (was deleted from JSON)")
                _resume_store.pop(k, None)
                # Also remove from 'out' to prevent it from being returned
                out.pop(k, None)
            else:
                # JSON is empty/invalid - keep pair in resume store (might be running)
                # Don't remove it, just don't serialize it to JSON
                pass
    
    # CRITICAL: Always re-read JSON after recovery attempts to get updated state
    # This ensures we have the latest state after recovery from empty JSON or missing pairs
    # Recovery might have written data, so we need fresh reads
    json_states = _get_json_states()
    json_positions = _get_json_positions()
    json_is_valid = len(json_states) > 0 or len(json_positions) > 0
    
    # Final cleanup: Remove any pairs from 'out' that don't exist in states or positions
    # This prevents stale cached data from being returned
    # NOTE: Finished pairs will be preserved because they exist in json_states with state="Finished"
    # CRITICAL: Also check if pairs are marked as deleted, and don't remove running pairs
    pairs_to_remove = []
    for k in out.keys():
        # Check if pair is explicitly marked as deleted
        with _deleted_pairs_lock:
            is_deleted = k in _deleted_pairs
        
        # Check if pair is actively running in RAM
        is_running = k in _ctx and _threads.get(k) and _threads[k].is_alive()
        
        # Remove if:
        # 1. Pair is explicitly marked as deleted, OR
        # 2. Pair doesn't exist in states OR positions AND is NOT running (incomplete/invalid pair)
        # CRITICAL: Don't remove running pairs even if they're not in JSON yet (recovery in progress)
        if is_deleted or ((k not in json_states and k not in json_positions) and not is_running):
            pairs_to_remove.append(k)
    
    # CRITICAL: If we found stale pairs, remove them from output and file
    # Track which pairs we've already tried to remove to avoid repeated operations
    if not hasattr(_serialize_ctx_states, '_removal_attempted'):
        _serialize_ctx_states._removal_attempted = set()
    
    if pairs_to_remove:
        for k in pairs_to_remove:
            # CRITICAL: Double-check that pair is not running before removing
            # This prevents removing pairs that are recovering from JSON errors
            is_running_check = k in _ctx and _threads.get(k) and _threads[k].is_alive()
            if is_running_check:
                # Pair is running - don't remove it, it might be recovering
                _log("SYSTEM", f"Skipping removal of running pair '{k}' (recovery in progress or JSON error)")
                continue
            
            out.pop(k, None)  # Always remove from output
            
            # Only attempt file removal once per pair to avoid spam and redundant operations
            if k not in _serialize_ctx_states._removal_attempted:
                _serialize_ctx_states._removal_attempted.add(k)
                try:
                    state_mgr = _get_state_mgr()
                    # Check if pair still exists in file before removing
                    state_mgr.invalidate_cache()  # Force fresh read
                    current_pair_ctx = state_mgr.get_pair_ctx()
                    if k in current_pair_ctx:
                        # Final check: Make sure pair is not running before removing from file
                        is_running_final = k in _ctx and _threads.get(k) and _threads[k].is_alive()
                        if is_running_final:
                            _log("SYSTEM", f"Skipping file removal of running pair '{k}' (recovery in progress)")
                            _serialize_ctx_states._removal_attempted.discard(k)
                            continue
                        
                        # Remove from global state file
                        state_mgr.remove_pair(k)
                        state_mgr.invalidate_cache()  # Force cache refresh after removal
                        
                        # CRITICAL: Also check if per-pair file exists and delete it
                        # Per-pair files might be re-adding the pair to global state
                        try:
                            from bridge.pair_manager import remove_pair_manager
                            remove_pair_manager(k)
                            _log("SYSTEM", f"Removed per-pair file for stale pair '{k}'")
                        except Exception as per_pair_err:
                            # Per-pair file might not exist, that's okay
                            pass
                        
                        _log("SYSTEM", f"Removed stale pair '{k}' from global state file (not in states or positions)")
                    else:
                        # Pair already removed from file, remove from tracking set
                        _serialize_ctx_states._removal_attempted.discard(k)
                except Exception as e:
                    _log("SYSTEM", f"Error removing stale pair '{k}' from file: {e}")
                    import traceback
                    traceback.print_exc()
                    # Remove from tracking set so we can retry later
                    _serialize_ctx_states._removal_attempted.discard(k)
    
    # CRITICAL: Remove any pairs that are marked as deleted
    # This prevents deleted pairs from reappearing in snapshots
    with _deleted_pairs_lock:
        for deleted_pair in _deleted_pairs:
            if deleted_pair in out:
                _log("SYSTEM", f"Removing deleted pair '{deleted_pair}' from pairCtx output")
                out.pop(deleted_pair, None)
    
    return out


def _pump():
    """Main pump loop - continuously emits snapshots and handles events"""
    global _events
    last_len = 0
    
    while True:
        time.sleep(0.5)  # Increased sleep time to reduce CPU usage

        # Check resume queue for market hours
        from bridge.time_utils import _check_resume_queue
        _check_resume_queue()
        
        # Removed continuous auto-rehydration to prevent race conditions

        # Auto-pause disabled

        with _events_lock: ev = list(_events)
        if len(ev) > last_len:
            for line in ev[last_len:]:
                _emit({"type":"log","line": line})
            last_len = len(ev)
            
            # Limit log events to prevent memory issues
            if len(ev) > 5000:
                with _events_lock:
                    _events = _events[-2500:]  # Keep only last 2500 events

        st = _get_json_states()
        # Use _serialize_ctx_states() to get combined state (respects deletions)
        # This ensures deleted pairs don't reappear from RAM context
        ctx_states = _serialize_ctx_states()

        # Get persisted positions (no LTP/MTM)
        pos_copy = _get_json_positions()
        
        # CRITICAL: Remove deleted pairs and incomplete pairs from snapshot data in _pump()
        # This prevents deleted pairs and invalid instances from reappearing in continuous snapshots
        pairs_to_remove_from_snapshot = set()
        
        # 1. Remove explicitly deleted pairs
        with _deleted_pairs_lock:
            pairs_to_remove_from_snapshot.update(_deleted_pairs)
        
        # 2. Remove incomplete pairs (exist in pairCtx but not in states or positions)
        # BUT: Don't remove pairs that are actively running in RAM (might be recovering from JSON error)
        # These are invalid instances that shouldn't be shown to the user
        for pair_name in list(ctx_states.keys()):
            if pair_name not in st and pair_name not in pos_copy:
                # Check if pair is actively running in RAM - if so, don't remove it
                # This handles the case where JSON was reset due to parsing error but pairs are still running
                is_running = pair_name in _ctx and _threads.get(pair_name) and _threads[pair_name].is_alive()
                if not is_running:
                    pairs_to_remove_from_snapshot.add(pair_name)
                    _log("SYSTEM", f"Removing incomplete pair '{pair_name}' from snapshot (exists in pairCtx but not in states or positions)")
                else:
                    # Pair is running but not in JSON - this might be recovery from JSON error
                    # CRITICAL: Check if pair was deleted FIRST - don't recover deleted pairs
                    with _deleted_pairs_lock:
                        is_deleted = pair_name in _deleted_pairs
                    
                    if is_deleted:
                        # Pair was deleted - don't recover, remove from snapshot
                        pairs_to_remove_from_snapshot.add(pair_name)
                        _log("SYSTEM", f"Pair '{pair_name}' was deleted - not recovering, removing from snapshot")
                        continue
                    
                    # Try to write it back to JSON to recover state
                    # Only log once to reduce spam
                    if not hasattr(_serialize_ctx_states, '_recovery_logged'):
                        _serialize_ctx_states._recovery_logged = set()
                    if pair_name not in _serialize_ctx_states._recovery_logged:
                        _log("SYSTEM", f"Pair '{pair_name}' is running but not in JSON - attempting to recover state")
                        _serialize_ctx_states._recovery_logged.add(pair_name)
                    try:
                        # Get context from RAM
                        ctx = _ctx.get(pair_name)
                        if ctx:
                            # Try to recover position and state from context
                            # Get current position from context if available
                            if hasattr(ctx, 'l2_state') and ctx.l2_state:
                                fills = ctx.l2_state.get('fills', {})
                                if fills and fills.get('lots_filled', 0) > 0:
                                    # Has position - try to recover
                                    position_data = {
                                        "symbol": ctx.l2_state.get('symbol', ''),
                                        "net_lots": fills.get('lots_filled', 0),
                                        "side": ctx.l2_state.get('entry_side', 'BUY'),
                                        "lot_size": 500,  # Default
                                        "avg_entry": fills.get('avg_entry', 0),
                                        "position_open": True
                                    }
                                    _set_position(pair_name, position_data)
                                    _log("SYSTEM", f"Recovered position for '{pair_name}' from RAM context")
                            
                            # Recover state
                            current_state_from_ctx = "Running" if not getattr(ctx, 'paused', False) else "Paused"
                            _set_state(pair_name, current_state_from_ctx)
                            _log("SYSTEM", f"Recovered state '{current_state_from_ctx}' for '{pair_name}' from RAM context")
                            
                            # Refresh snapshot data after recovery
                            st = _get_json_states()
                            pos_copy = _get_json_positions()
                    except Exception as e:
                        _log("SYSTEM", f"Error recovering state for '{pair_name}': {e}")
        
        # Remove all identified pairs from snapshot data
        for pair_name in pairs_to_remove_from_snapshot:
            st.pop(pair_name, None)
            pos_copy.pop(pair_name, None)
            ctx_states.pop(pair_name, None)
        
        total_mtm = 0.0
        state_mgr = _get_state_mgr()
        
        # Update LTP and MTM in display cache (never persisted)
        for pair_name, pos in pos_copy.items():
            try:
                # Update LTP from market data
                symbol = pos.get("entry_symbol_key") or pos.get("symbol", "")
                if symbol:
                    md = market_data.get(symbol, {})
                    ltp = md.get("ltp")
                    if ltp is not None:
                        # Calculate MTM
                        avg_entry = pos.get("avg_entry")
                        net_lots = pos.get("net_lots", 0)
                        lot_size = pos.get("lot_size", 1)
                        is_long = pos.get("side", "BUY") == "BUY"
                        
                        mtm = 0.0
                        if avg_entry and net_lots > 0:
                            if is_long:
                                mtm = (float(ltp) - float(avg_entry)) * net_lots * lot_size
                            else:
                                mtm = (float(avg_entry) - float(ltp)) * net_lots * lot_size
                            mtm = round(mtm, 2)
                            total_mtm += mtm
                        
                        # Update display cache (never persisted)
                        state_mgr.set_display_data(pair_name, ltp=ltp, mtm=mtm)
                        
                        # Add to position copy for snapshot (display only)
                        pos["ltp"] = ltp
                        pos["mtm"] = mtm
                    else:
                        # Get from display cache if market data not available
                        display = state_mgr.get_display_data(pair_name)
                        if "ltp" in display:
                            pos["ltp"] = display["ltp"]
                        if "mtm" in display:
                            pos["mtm"] = display["mtm"]
                            total_mtm += display["mtm"]
            except Exception:
                # Don't let one position block others
                pass
        
        # Emit snapshot with combined data (persisted + display)
        _emit({"type":"snapshot","positions": pos_copy, "states": st, "pairCtx": ctx_states, "total_mtm": total_mtm})


def _emit_snapshot_now():
    """Emit current snapshot - optimized approach"""
    # Serialize RAM state to JSON first (ensures latest SL/TSL updates are saved)
    ctx_states = _serialize_ctx_states()
    
    # Get data from state manager
    st = _get_json_states()
    pos_copy = _get_json_positions()
    
    # CRITICAL: Remove deleted pairs from snapshot data
    # This prevents deleted pairs from reappearing after square off or other operations
    with _deleted_pairs_lock:
        for deleted_pair in _deleted_pairs:
            st.pop(deleted_pair, None)
            pos_copy.pop(deleted_pair, None)
            ctx_states.pop(deleted_pair, None)
    total_mtm = 0.0
    state_mgr = _get_state_mgr()
    
    # Update LTP and MTM in display cache (never persisted)
    for pair_name, pos in pos_copy.items():
        try:
            # Update LTP from market data
            symbol = pos.get("entry_symbol_key") or pos.get("symbol", "")
            if symbol:
                md = market_data.get(symbol, {})
                ltp = md.get("ltp")
                if ltp is not None:
                    # Calculate MTM
                    avg_entry = pos.get("avg_entry")
                    net_lots = pos.get("net_lots", 0)
                    lot_size = pos.get("lot_size", 1)
                    is_long = pos.get("side", "BUY") == "BUY"
                    
                    mtm = 0.0
                    if avg_entry and net_lots > 0:
                        if is_long:
                            mtm = (float(ltp) - float(avg_entry)) * net_lots * lot_size
                        else:
                            mtm = (float(avg_entry) - float(ltp)) * net_lots * lot_size
                        mtm = round(mtm, 2)
                        total_mtm += mtm
                    
                    # Update display cache (never persisted)
                    state_mgr.set_display_data(pair_name, ltp=ltp, mtm=mtm)
                    
                    # Add to position copy for snapshot (display only)
                    pos["ltp"] = ltp
                    pos["mtm"] = mtm
                else:
                    # Get from display cache if market data not available
                    display = state_mgr.get_display_data(pair_name)
                    if "ltp" in display:
                        pos["ltp"] = display["ltp"]
                    if "mtm" in display:
                        pos["mtm"] = display["mtm"]
                        total_mtm += display["mtm"]
        except Exception:
            # Don't let one position block others
            pass
    
    # Emit snapshot with combined data (persisted + display)
    _emit({"type":"snapshot","positions": pos_copy, "states": st, "pairCtx": ctx_states, "total_mtm": total_mtm})

