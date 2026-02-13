"""
Command handlers for Bridge.
Handles squareoff, delete (forget_pair), factory_reset, and status commands.
"""
import os
import threading
import time
from datetime import datetime
from bridge.shared_state import (
    _deleted_pairs_lock, _deleted_pairs, _threads, _ctx, _cmd_lock, _cmd,
    _resume_store_lock, _resume_store, _progress_lock, _progress_seen,
    _events_lock, _events
)
from bridge.state_wrappers import (
    _get_json_states, _get_json_positions, _get_json_pair_ctx,
    _set_state, _update_pair_ctx_field, _remove_pair, _get_state_mgr,
    _update_active_pairs, STATE_FILE
)
from bridge.utils import _log, _now
from bridge.io import _emit, _emit_cmd_result
from bridge.progress_handler import _handle_progress
from bridge.pair_operations import _cleanup_pair, _drop_state
from bridge.serialization import _emit_snapshot_now
from bridge.pair_manager import remove_pair_manager
from bridge.state_store import get_pairs_dir, list_all_pairs


def _squareoff(pair_name):
    """Square off a pair based on current state. Emits cmd_ok with action: 'squareoff'."""
    _log(pair_name, "Square off requested")
    
    # CRITICAL: Check if pair was deleted - don't allow square off on deleted pairs
    with _deleted_pairs_lock:
        if pair_name in _deleted_pairs:
            _log(pair_name, "Cannot square off - pair was deleted")
            # Removed duplicate _emit - _log already handles it
            _emit_cmd_result("squareoff", pair_name, success=False, error="Pair was deleted")
            return
    
    # CRITICAL: Check if pair exists in file - if not, it was deleted
    cur_states = _get_json_states()
    cur_positions = _get_json_positions()
    if pair_name not in cur_states and pair_name not in cur_positions:
        _log(pair_name, "Cannot square off - pair does not exist in state file (was deleted)")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("squareoff", pair_name, success=False, error="Pair was deleted")
        return
    
    # Get current state and context
    current_state = cur_states.get(pair_name, "")
    ctx = _ctx.get(pair_name)
    th = _threads.get(pair_name)
    
    # Check if strategy is not started or already finished
    if current_state == "" or current_state == "Finished":
        _log(pair_name, f"Strategy is not running (state: {current_state}) - cannot square off")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("squareoff", pair_name, success=False, error=f"Strategy is not running (state: {current_state})")
        return
    
    # Check if we have an active thread
    if not th or not th.is_alive():
        _log(pair_name, "No active thread - cannot square off")
        # Removed duplicate _emit - _log already handles it
        _emit_cmd_result("squareoff", pair_name, success=False, error="No active thread")
        return
    
    # Check if we're in Leg1 (no position)
    if "Leg1" in current_state and ("Leg1" in current_state and "Leg2" not in current_state):
        _log(pair_name, "Pair in Leg1 (no position) - stopping strategy")
        
        # Use the main strategy's square off function for Leg1
        try:
            from Main_strategy import leg1_force_exit
            _log(pair_name, "Using main strategy Leg1 square off")
            
            # Set the on_progress callback for the square off
            if getattr(ctx, "l1_state", None):
                ctx.l1_state["_on_progress"] = lambda evt: _handle_progress(pair_name, evt)
                ctx.l1_state = leg1_force_exit(ctx.l1_state)
            
            # Set exit requested to stop the thread
            ctx.exit_requested = True
            _set_state(pair_name, "Finished", immediate=True)
            _update_pair_ctx_field(pair_name, "leg1_status", "squared_off", immediate=True)
            _update_pair_ctx_field(pair_name, "leg1_exit_time", datetime.now().isoformat(timespec="seconds"), immediate=True)
        except Exception as e:
            _log(pair_name, f"Error in Leg1 square off: {e}")
            ctx.exit_requested = True
            _set_state(pair_name, "Finished", immediate=True)
            _cleanup_pair(pair_name)

        # Check if pair was deleted before emitting snapshot
        with _deleted_pairs_lock:
            is_deleted = pair_name in _deleted_pairs
        
        if not is_deleted:
            cur_states = _get_json_states()
            cur_pair_ctx = _get_json_pair_ctx()
            positions_copy = _get_json_positions()
            # JSON updates are now handled by individual _set_* functions
            _emit_snapshot_now()
            _emit({"type":"finished","pair_name":pair_name})
        else:
            _log(pair_name, "Skipping snapshot emission after Leg1 square off - pair was deleted")
        
        # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
        # State is already set to "Finished" above, just ensure it's persisted
        _log(pair_name, "Pair in Leg1 stopped and marked as Finished")
        _emit_cmd_result("squareoff", pair_name, success=True)
        return
    
    # If we're in Running or Paused state, determine which leg is active
    # Square off should work even when paused
    if current_state == "Running" or current_state == "Paused":
        if ctx and getattr(ctx, "l2_state", None) and ctx.l2_state.get("symbol"):
            # Leg2 is active - treat as Leg2 square off
            _log(pair_name, f"Pair in {current_state} state with Leg2 active - squaring off positions")
            
            # Check if we have open positions in state
            if not ctx.l2_state.get("fills"):
                _log(pair_name, "No position data in state - finishing pair")
                ctx.exit_requested = True
                _set_state(pair_name, "Finished", immediate=True)
                _cleanup_pair(pair_name)
                
                # Check if pair was deleted before emitting snapshot
                with _deleted_pairs_lock:
                    is_deleted = pair_name in _deleted_pairs
                
                if not is_deleted:
                    # Only emit snapshot if pair wasn't deleted
                    cur_states = _get_json_states()
                    cur_pair_ctx = _get_json_pair_ctx()
                    positions_copy = _get_json_positions()
                    # JSON updates are now handled by individual _set_* functions
                    _emit_snapshot_now()
                    _emit({"type":"finished","pair_name":pair_name})
                else:
                    _log(pair_name, "Skipping snapshot emission - pair was deleted")
                
                # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                # State is already set to "Finished" above, just ensure it's persisted
                _emit_cmd_result("squareoff", pair_name, success=True)
                return

            # Use the main strategy's square off function for Leg2
            try:
                from Main_strategy import leg2_force_exit
                _log(pair_name, "Using main strategy Leg2 square off")
                
                # Set the on_progress callback for the square off
                if getattr(ctx, "l2_state", None):
                    ctx.l2_state["_on_progress"] = lambda evt: _handle_progress(pair_name, evt)
                    
                    # CRITICAL: Pass net_lots from bridge state to strategy state for square off
                    positions = _get_json_positions()
                    if pair_name in positions and "net_lots" in positions[pair_name]:
                        ctx.l2_state["net_lots"] = positions[pair_name]["net_lots"]
                        _log(pair_name, f"Passing net_lots to strategy: {ctx.l2_state['net_lots']}")
                    
                    ctx.l2_state = leg2_force_exit(ctx.l2_state)
                
                # Mark that square off has been initiated - monitor will detect when position is fully closed
                ctx.exit_requested = True
                # CRITICAL: Set state to indicate square off is in progress
                # This allows deletion to work even if position hasn't closed yet
                _set_state(pair_name, "Squaring Off")
                # Verify state was set correctly
                current_state_after = _get_json_states().get(pair_name, "Unknown")
                _log(pair_name, f"State set to 'Squaring Off' (verified: {current_state_after})")
                # Verify state was set correctly
                current_state_after = _get_json_states().get(pair_name, "Unknown")
                _log(pair_name, f"State set to 'Squaring Off' (verified: {current_state_after}) - monitor will detect when position is fully closed")
                _emit_cmd_result("squareoff", pair_name, success=True)
                return
                
            except Exception as e:
                _log(pair_name, f"Error in Leg2 square off: {e}")
                # Fallback to simple cleanup
                ctx.exit_requested = True
                _set_state(pair_name, "Finished", immediate=True)
                _cleanup_pair(pair_name)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                positions_copy = _get_json_positions()
                # JSON updates are now handled by individual _set_* functions
                _emit_snapshot_now()
                _emit({"type":"finished","pair_name":pair_name})
                _emit_cmd_result("squareoff", pair_name, success=True)
                return

        elif ctx and getattr(ctx, "l1_state", None) and ctx.l1_state.get("symbol"):
            # Leg1 is active - treat as Leg1 square off
            _log(pair_name, f"Pair in {current_state} state with Leg1 active - stopping strategy")
            
            # Use the main strategy's square off function for Leg1
            try:
                from Main_strategy import leg1_force_exit
                _log(pair_name, "Using main strategy Leg1 square off")
                
                # Set the on_progress callback for the square off
                if ctx.l1_state:
                    ctx.l1_state["_on_progress"] = lambda evt: _handle_progress(pair_name, evt)
                    ctx.l1_state = leg1_force_exit(ctx.l1_state)
                
                # Set exit requested to stop the thread
                ctx.exit_requested = True
                # CRITICAL: Set state to indicate square off is in progress
                # This allows deletion to work even if position hasn't closed yet
                _set_state(pair_name, "Squaring Off")
                # Verify state was set correctly
                current_state_after = _get_json_states().get(pair_name, "Unknown")
                _log(pair_name, f"State set to 'Squaring Off' (verified: {current_state_after})")
                _cleanup_pair(pair_name)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                positions_copy = _get_json_positions()
                # JSON updates are now handled by individual _set_* functions
                _emit_snapshot_now()
                _emit({"type":"finished","pair_name":pair_name})
                _log(pair_name, "Pair in Leg1 stopped and marked as Finished")
                return
                
            except Exception as e:
                _log(pair_name, f"Error in Leg1 square off: {e}")
                # Fallback to simple cleanup
                ctx.exit_requested = True
                _set_state(pair_name, "Finished", immediate=True)
                _cleanup_pair(pair_name)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                positions_copy = _get_json_positions()
                # JSON updates are now handled by individual _set_* functions
                _emit_snapshot_now()
                _emit({"type":"finished","pair_name":pair_name})
                _log(pair_name, "Pair in Leg1 stopped and marked as Finished")
                return
        else:
            # No active leg - just finish
            _log(pair_name, "Pair in Running state but no active leg - finishing")
            if ctx:
                ctx.exit_requested = True
                _set_state(pair_name, "Finished", immediate=True)
                _cleanup_pair(pair_name)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                positions_copy = _get_json_positions()
                # JSON updates are now handled by individual _set_* functions
                _emit_snapshot_now()
                _emit({"type":"finished","pair_name":pair_name})
                _emit_cmd_result("squareoff", pair_name, success=True)
                return
    
    # Check if we're in Leg2 (with position)
    if "Leg2" in current_state:
        _log(pair_name, "Pair in Leg2 (with position) - squaring off positions")
        
        # Check if we have open positions in state
        if not ctx or not getattr(ctx, "l2_state", None) or not ctx.l2_state.get("fills"):
            _log(pair_name, "No position data in state - finishing pair")
            if ctx:
                ctx.exit_requested = True
            _set_state(pair_name, "Finished", immediate=True)
            _cleanup_pair(pair_name)
            
            # Check if pair was deleted before emitting snapshot
            with _deleted_pairs_lock:
                is_deleted = pair_name in _deleted_pairs
            
            if not is_deleted:
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                positions_copy = _get_json_positions()
                # JSON updates are now handled by individual _set_* functions
                _emit_snapshot_now()
                _emit({"type":"finished","pair_name":pair_name})
            else:
                _log(pair_name, "Skipping snapshot emission after Leg2 square off - pair was deleted")
            
            # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
            _emit_cmd_result("squareoff", pair_name, success=True)
            return
        
        # Use the main strategy's square off function for Leg2
        try:
            from Main_strategy import leg2_force_exit
            _log(pair_name, "Using main strategy Leg2 square off")
            
            # Set the on_progress callback for the square off
            if ctx.l2_state:
                ctx.l2_state["_on_progress"] = lambda evt: _handle_progress(pair_name, evt)
                
                # CRITICAL: Pass net_lots from bridge state to strategy state for square off
                positions = _get_json_positions()
                if pair_name in positions and "net_lots" in positions[pair_name]:
                    ctx.l2_state["net_lots"] = positions[pair_name]["net_lots"]
                    _log(pair_name, f"Passing net_lots to strategy: {ctx.l2_state['net_lots']}")
                
                ctx.l2_state = leg2_force_exit(ctx.l2_state)
            
            # Mark that square off has been initiated - monitor will detect when position is fully closed
            ctx.exit_requested = True
            # CRITICAL: Set state to indicate square off is in progress
            # This allows deletion to work even if position hasn't closed yet
            _set_state(pair_name, "Squaring Off")
            _log(pair_name, f"Square off initiated through main strategy - monitor will detect when position is fully closed")
            _emit_cmd_result("squareoff", pair_name, success=True)
            return
            
        except Exception as e:
            _log(pair_name, f"Error in Leg2 square off: {e}")
            # Fallback to simple cleanup
            ctx.exit_requested = True
            _set_state(pair_name, "Finished", immediate=True)
            _cleanup_pair(pair_name)
            cur_states = _get_json_states()
            cur_pair_ctx = _get_json_pair_ctx()
            positions_copy = _get_json_positions()
            # JSON updates are now handled by individual _set_* functions
            _emit_snapshot_now()
            _emit({"type":"finished","pair_name":pair_name})
            # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
            _emit_cmd_result("squareoff", pair_name, success=True)
            return
    
    # If we get here, state is unknown
    _log(pair_name, f"Unknown state '{current_state}' - cannot square off")
    # Removed duplicate _emit - _log already handles it
    _emit_cmd_result("squareoff", pair_name, success=False, error=f"Unknown state: {current_state}")


def _forget_pair(pair_name):
    """Forget this pair after safety checks, then delete all JSON entries for it. Emits cmd_ok with action: 'delete'."""
    _log(pair_name, "Delete requested")

    # CRITICAL: Check if already being deleted (idempotent deletion)
    with _deleted_pairs_lock:
        if pair_name in _deleted_pairs:
            _log(pair_name, "Delete already in progress - skipping duplicate request")
            _emit_cmd_result("delete", pair_name, success=True)  # Already deleted
            return
        # Mark pair as deleted FIRST to prevent recovery logic from re-adding it
        _deleted_pairs.add(pair_name)
    
    # Small delay to ensure all concurrent operations see the deletion flag
    time.sleep(0.05)
    
    # 0) Safety checks
    try:
        current_state = _get_json_states().get(pair_name, "")
        ctx = _ctx.get(pair_name)
        th = _threads.get(pair_name)
        
        # If pair is finished, wait for thread to fully exit (similar to paused state)
        if current_state == "Finished":
            if th and th.is_alive():
                # Wait for thread to actually exit
                max_wait = 1.0  # Maximum wait time (1 second)
                wait_interval = 0.1  # Check every 0.1 seconds
                waited = 0.0
                while th.is_alive() and waited < max_wait:
                    time.sleep(wait_interval)
                    waited += wait_interval
                    # Re-check thread status
                    th = _threads.get(pair_name)
                    if not th:
                        break
                
                if th and th.is_alive():
                    _log(pair_name, f"Warning: Finished thread still alive after {waited}s wait, proceeding with deletion anyway")
                else:
                    _log(pair_name, f"Finished thread exited cleanly after {waited}s")
            # Re-check after wait
            ctx = _ctx.get(pair_name)
            th = _threads.get(pair_name)

        # CRITICAL: Check if pair is being squared off (exit_requested is set)
        # If exit_requested is set, the pair is in cleanup mode and should be deletable
        is_squaring_off = ctx and getattr(ctx, 'exit_requested', False)
        
        # CRITICAL: Check if position is already closed (net_lots = 0)
        # If position is closed, pair should be deletable even if state shows "Running"
        position_closed = False
        try:
            positions = _get_json_positions()
            if pair_name in positions:
                net_lots = positions[pair_name].get("net_lots", 0)
                position_open = positions[pair_name].get("position_open", True)
                if net_lots <= 0 or not position_open:
                    position_closed = True
                    _log(pair_name, f"Position is closed (net_lots={net_lots}, position_open={position_open}) - allowing deletion")
        except Exception as e:
            _log(pair_name, f"Error checking position status: {e}")
        
        # Allow deletion if:
        # 1. State is Finished, Paused, or Squaring Off
        # 2. Pair is being squared off (exit_requested is set)
        # 3. Position is already closed (net_lots = 0)
        # Only block if pair is actively running with open position
        if current_state and current_state not in ["Finished", "Paused", "Squaring Off"] and "paused" not in current_state.lower() and "squaring" not in current_state.lower():
            if is_squaring_off:
                _log(pair_name, f"Pair is being squared off (exit_requested=True) - allowing deletion even though state is '{current_state}'")
            elif position_closed:
                _log(pair_name, f"Position is closed - allowing deletion even though state is '{current_state}'")
            else:
                _log(pair_name, f"Cannot forget pair - strategy is running (state: {current_state}). Please pause or square-off first.")
                _emit_cmd_result("delete", pair_name, success=False, error=f"Strategy is running (state: {current_state}). Please pause or square-off first.")
                return

        # Active thread check - only prevent if thread is actually running (not paused, not in cleanup)
        if th and th.is_alive():
            # If context exists and exit is not requested, check if thread is paused
            if ctx and not getattr(ctx, 'exit_requested', False):
                # If thread is paused, it's safe to delete - set exit_requested and allow deletion
                if getattr(ctx, 'paused', False):
                    _log(pair_name, "Thread is paused - allowing deletion, setting exit_requested")
                    ctx.exit_requested = True
                    # CRITICAL: Wait for thread to actually exit (similar to finished state)
                    # Check multiple times to ensure thread has fully exited
                    max_wait = 1.0  # Maximum wait time (1 second)
                    wait_interval = 0.1  # Check every 0.1 seconds
                    waited = 0.0
                    while th.is_alive() and waited < max_wait:
                        time.sleep(wait_interval)
                        waited += wait_interval
                        # Re-check thread status
                        th = _threads.get(pair_name)
                        if not th:
                            break
                    
                    if th and th.is_alive():
                        _log(pair_name, f"Warning: Thread still alive after {waited}s wait, proceeding with deletion anyway")
                    else:
                        _log(pair_name, f"Thread exited cleanly after {waited}s")
                elif not is_squaring_off and not position_closed:
                    # Thread is actively running (not paused, not squaring off, position open) - block deletion
                    _log(pair_name, "Cannot forget pair - active thread running (not paused, not squaring off)")
                    _emit_cmd_result("delete", pair_name, success=False, error="Active thread running. Please pause or square off first.")
                    return
            # If context doesn't exist or exit is requested, thread is in cleanup - allow forget
            if is_squaring_off:
                _log(pair_name, "Thread is squaring off (exit_requested=True) - allowing deletion")
            else:
                _log(pair_name, "Thread in cleanup mode - allowing forget operation")
    except Exception as e:
        _emit_cmd_result("delete", pair_name, success=False, error=f"precheck failed: {e}")
        return

    # 1) Clear RAM state FIRST to prevent recovery
    try:
        with _progress_lock:
            _progress_seen.pop(pair_name, None)
        with _resume_store_lock:
            _resume_store.pop(pair_name, None)
        _threads.pop(pair_name, None)
        _ctx.pop(pair_name, None)
        _log(pair_name, "Cleared in-memory traces for pair")
    except Exception as e:
        _log(pair_name, f"Error clearing in-memory traces: {e}")

    # 2) Remove from individual pair file
    try:
        from bridge.pair_manager import remove_pair_manager
        from pathlib import Path
        from runtime_paths_live import STATE_DIR
        
        # Remove the pair manager (deletes the file)
        remove_pair_manager(pair_name)
        
        # Verify the file is actually deleted with retries
        pair_file = Path(STATE_DIR) / "pairs" / f"{pair_name}.json"
        max_retries = 3
        for retry in range(max_retries):
            if not pair_file.exists():
                _log(pair_name, "Removed per-pair file")
                break
            # File still exists - try to delete it directly
            try:
                pair_file.unlink()
                # Wait a bit and verify deletion
                time.sleep(0.1)
                if not pair_file.exists():
                    _log(pair_name, f"Force-deleted per-pair file (retry {retry + 1})")
                    break
                elif retry == max_retries - 1:
                    _log(pair_name, f"Warning: Per-pair file still exists after {max_retries} attempts")
            except Exception as e2:
                if retry == max_retries - 1:
                    _log(pair_name, f"Warning: Could not delete per-pair file after {max_retries} attempts: {e2}")
                else:
                    time.sleep(0.1)  # Wait before retry
    except Exception as e:
        _log(pair_name, f"Error removing per-pair file: {e}")

    # 3) Remove from bridge_state.json
    try:
        _remove_pair(pair_name)
        state_mgr = _get_state_mgr()
        state_mgr.invalidate_cache()
        _log(pair_name, "Removed from bridge_state.json")
    except Exception as e:
        _log(pair_name, f"Error removing from bridge_state.json: {e}")

    # 4) Wait for file system sync and ensure all operations complete
    time.sleep(0.3)  # Increased wait time for file system sync
    
    # 5) Get fresh state and remove pair from snapshot data
    # Invalidate cache multiple times to ensure fresh reads
    state_mgr.invalidate_cache()
    time.sleep(0.1)  # Small delay after cache invalidation
    state_mgr.invalidate_cache()
    
    remaining_states = _get_json_states()
    remaining_positions = _get_json_positions()
    remaining_pair_ctx = _get_json_pair_ctx()
    
    # Force remove from snapshot data (defensive - should already be gone)
    remaining_states.pop(pair_name, None)
    remaining_positions.pop(pair_name, None)
    remaining_pair_ctx.pop(pair_name, None)
    
    # Double-check: Verify pair is actually gone from all sources
    if pair_name in remaining_states or pair_name in remaining_positions or pair_name in remaining_pair_ctx:
        _log(pair_name, f"Warning: Pair still found in state after deletion - states: {pair_name in remaining_states}, positions: {pair_name in remaining_positions}, pairCtx: {pair_name in remaining_pair_ctx}")
        # Force remove again
        remaining_states.pop(pair_name, None)
        remaining_positions.pop(pair_name, None)
        remaining_pair_ctx.pop(pair_name, None)

    # 6) Schedule cleanup of deleted_pairs set after 30 seconds
    def cleanup_deleted_pair():
        time.sleep(30.0)
        with _deleted_pairs_lock:
            _deleted_pairs.discard(pair_name)
    
    cleanup_thread = threading.Thread(target=cleanup_deleted_pair, daemon=True)
    cleanup_thread.start()

    # 7) Emit snapshot
    try:
        _emit({
            "type": "snapshot",
            "positions": remaining_positions,
            "states": remaining_states,
            "pairCtx": remaining_pair_ctx,
            "total_mtm": sum(float(p.get("mtm", 0) or 0) for p in remaining_positions.values()),
        })
    except Exception as e:
        _log(pair_name, f"Error emitting snapshot: {e}")

    _log(pair_name, "Forgotten (deleted from state) - deletion complete")
    _emit_cmd_result("delete", pair_name, success=True)


def _factory_reset():
    """Complete factory reset - clear all pairs, states, and data (per-pair architecture)"""
    global _events
    print(f"[Bridge] Factory reset initiated")
    
    # 1) Signal all runners to stop
    with _cmd_lock:
        for k in list(_ctx.keys()):
            _cmd[k] = "squareoff"
    
    # 2) Set exit requested for all contexts
    for ctx in _ctx.values():
        if ctx:
            ctx.exit_requested = True
    
    # 3) Clear all memory state
    with _resume_store_lock: 
        _resume_store.clear()
    with _progress_lock: 
        _progress_seen.clear()
    
    # 4) Clear all threads, contexts, and commands
    _ctx.clear()
    _threads.clear()
    _cmd.clear()

    # 4b) Clear all pending logs/events so the UI does not show old ones
    with _events_lock:
        _events.clear()
    
    # 5) Delete all per-pair files
    try:
        pairs_dir = get_pairs_dir()
        if pairs_dir.exists():
            # Get all pair names first
            all_pairs = list_all_pairs()
            print(f"[Bridge] Deleting {len(all_pairs)} per-pair files...")
            
            # Remove all pair managers from registry and delete files
            for pair_name in all_pairs:
                try:
                    remove_pair_manager(pair_name)
                    print(f"[Bridge] Deleted per-pair file for {pair_name}")
                except Exception as e:
                    print(f"[Bridge] Error deleting per-pair file for {pair_name}: {e}")
            
            # Also delete any remaining JSON files in pairs directory (cleanup)
            for json_file in pairs_dir.glob("*.json"):
                try:
                    if json_file.exists():
                        json_file.unlink()
                        print(f"[Bridge] Deleted remaining file: {json_file.name}")
                except Exception as e:
                    print(f"[Bridge] Error deleting {json_file.name}: {e}")
            
            print(f"[Bridge] All per-pair files deleted")
    except Exception as e:
        print(f"[Bridge] Error deleting per-pair files: {e}")
    
    # 6) Clear state manager cache and write empty state
    try:
        state_mgr = _get_state_mgr()
        state_mgr.clear_all_state()
        print(f"[Bridge] Cleared state manager cache and wrote empty state")
    except Exception as e:
        print(f"[Bridge] Warning: Failed to clear state manager: {e}")
        import traceback
        traceback.print_exc()
    
    # 6b) Also delete old bridge_state.json if it exists (for cleanup)
    # Note: clear_all_state() already writes an empty state, but we delete it here
    # to ensure a fresh start. The state manager will recreate it if needed.
    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print(f"[Bridge] Deleted old bridge_state.json: {STATE_FILE}")
            # Write empty state again after deletion
            state_mgr = _get_state_mgr()
            state_mgr.clear_all_state()
    except Exception as e:
        print(f"[Bridge] Warning: Failed to delete old bridge_state.json: {e}")
    
    # 7) Clear deleted pairs set (since we're doing a full reset)
    with _deleted_pairs_lock:
        _deleted_pairs.clear()
        print(f"[Bridge] Cleared deleted pairs tracking set")
    
    # 8) Emit clean state to frontend
    _emit({"type":"log","line": f"[{_now()}] [system] Factory reset done - all per-pair files deleted"})
    _emit({"type":"snapshot","positions": {}, "states": {}, "pairCtx": {}, "total_mtm": 0})
    _emit({"type":"cmd_ok","cmd":"factory_reset"})
    
    print(f"[Bridge] Factory reset completed - all pairs and states cleared")


def _status():
    """Emit current status snapshot"""
    st = _get_json_states()
    ctx_states = _get_json_pair_ctx()
    _emit({"type":"snapshot","positions": {}, "states": st, "pairCtx": ctx_states, "total_mtm": 0.0})

