"""
Rehydration logic for Bridge.
Handles rebuilding pairs from JSON state, auto-rehydration on startup, and state migration.
"""
import os
import threading
from bridge.shared_state import _ctx, _threads, _deleted_pairs_lock, _deleted_pairs
from bridge.state_wrappers import (
    _get_json_pair_ctx, _get_json_states, _get_json_positions,
    _set_state, _read_json, _update_active_pairs, STATE_FILE
)
from bridge.utils import _log
from bridge.pair_threading import PairCtx, _pair_thread
from bridge.serialization import _emit_snapshot_now
from bridge.state_store import list_all_pairs
from bridge.pair_manager import get_pair_manager


def _rehydrate_pair_from_json(pair_name: str):
    """Rebuild PairCtx and thread from JSON if possible; returns ctx or None."""
    try:
        # CRITICAL: Check if pair was deleted - don't rehydrate deleted pairs
        with _deleted_pairs_lock:
            if pair_name in _deleted_pairs:
                _log(pair_name, "Cannot rehydrate - pair was deleted")
                return None
        
        # CRITICAL: Check if pair file exists - if it was deleted, don't rehydrate
        from pathlib import Path
        from runtime_paths_live import STATE_DIR
        pair_file = Path(STATE_DIR) / "pairs" / f"{pair_name}.json"
        if not pair_file.exists():
            _log(pair_name, "Cannot rehydrate - pair file does not exist (was deleted)")
            return None
        
        ctxmap = _get_json_pair_ctx()
        if pair_name not in ctxmap:
            _log(pair_name, "No JSON context found for rehydration")
            return None
        
        pc = ctxmap[pair_name]
        leg1_cfg = (pc.get("leg1_cfg") or {})
        leg2_cfg = (pc.get("leg2_cfg") or {})
        l1_state = pc.get("leg1_details")
        l2_state = pc.get("leg2_details")
        
        # If leg configurations are missing, try to reconstruct from state
        if not leg1_cfg and l1_state:
            try:
                symbol = l1_state.get("symbol", "")
                _log("SYSTEM", f"Reconstructing leg1_cfg for {pair_name} with symbol: {symbol}")
                if symbol and "_" in symbol:
                    parts = symbol.split("_")
                    leg1_cfg = {
                        "stock": parts[0],
                        "strike": float(parts[1]),
                        "option_type": l1_state.get("option_type", "C"),
                        "trade_direction": l1_state.get("entry_side", "SELL"),
                        "start_time_str": "09:20:00",
                        "SLval": l1_state.get("sl", 0),
                        "SLtype": "POINTS",
                        "TSLarm": l1_state.get("TSLarm"),
                        "TSLmove": l1_state.get("TSLmove"),
                        "TSLtype": l1_state.get("TSLtype")
                    }
                    _log("SYSTEM", f"Successfully reconstructed leg1_cfg for {pair_name}: {leg1_cfg}")
                else:
                    _log(pair_name, f"Cannot reconstruct leg1_cfg - invalid symbol: {symbol}")
                    return None
            except Exception as e:
                _log(pair_name, f"Error reconstructing leg1_cfg: {e}")
                return None
        
        if not leg2_cfg and l2_state:
            try:
                symbol = l2_state.get("symbol", "")
                if symbol and "_" in symbol:
                    parts = symbol.split("_")
                    leg2_cfg = {
                        "stock": parts[0],
                        "strike": float(parts[1]),
                        "option_type": l2_state.get("option_type", "C"),
                        "trade_direction": l2_state.get("entry_side", "SELL"),
                        "start_time_str": "09:20:00",
                        "SLval": l2_state.get("sl", 0),
                        "SLtype": "POINTS",
                        "TSLarm": l2_state.get("TSLarm"),
                        "TSLmove": l2_state.get("TSLmove"),
                        "TSLtype": l2_state.get("TSLtype")
                    }
                else:
                    _log(pair_name, f"Cannot reconstruct leg2_cfg - invalid symbol: {symbol}")
                    # Don't return None here, leg2 might not be needed yet
            except Exception as e:
                _log(pair_name, f"Error reconstructing leg2_cfg: {e}")
                # Don't return None here, leg2 might not be needed yet

        # Ensure we have at least leg1_cfg
        if not leg1_cfg:
            _log(pair_name, "Cannot rehydrate - missing leg1_cfg and cannot reconstruct")
            return None

        # Check if pair was finished - don't rehydrate finished pairs
        current_state = _get_json_states().get(pair_name, "")
        if current_state == "Finished":
            _log(pair_name, "Cannot rehydrate - pair is finished")
            return None

        ctx = PairCtx(pair_name, leg1_cfg, leg2_cfg, l1_state=l1_state, l2_state=l2_state)
        # Rehydrate as paused (state should already be "Paused" from _auto_rehydrate_pairs check)
        # Don't change the state - preserve it as "Paused"
        ctx.paused = True
        _ctx[pair_name] = ctx
        
        # Don't change state - it should already be "Paused"
        # Only set if it's not already set correctly (defensive check)
        current_state = _get_json_states().get(pair_name, "")
        if current_state != "Paused":
            _log(pair_name, f"Warning: Rehydrating pair with state '{current_state}', setting to Paused")
            _set_state(pair_name, "Paused")
        
        # Start thread
        t = threading.Thread(target=_pair_thread, args=(ctx,), daemon=True)
        _threads[pair_name] = t
        t.start()
        
        # Show detailed rehydration snapshot
        _log(pair_name, f"Context rehydrated from JSON (paused={ctx.paused})")
        
        # Show current state snapshot
        if l2_state and l2_state.get("symbol"):
            symbol = l2_state["symbol"]
            entry = l2_state.get("entry", 0)
            spot = l2_state.get("spot_entry", 0)
            sl = l2_state.get("sl", 0)
            sltype = l2_state.get("SLtype", "POINTS")
            fills = l2_state.get("fills", {})
            lots_filled = fills.get("lots_filled", 0)
            avg_entry = fills.get("avg_entry", 0)
            _log(pair_name, f"Leg2 state restored: {symbol} entry={entry} spot={spot} SL={sl} ({sltype}) lots_filled={lots_filled} avg_entry={avg_entry}")
        elif l1_state and l1_state.get("symbol"):
            symbol = l1_state["symbol"]
            entry = l1_state.get("entry", 0)
            spot = l1_state.get("spot_entry", 0)
            sl = l1_state.get("sl", 0)
            sltype = l1_state.get("SLtype", "POINTS")
            _log(pair_name, f"Leg1 state restored: {symbol} entry={entry} spot={spot} SL={sl} ({sltype})")
        else:
            _log(pair_name, "State restored (no active leg details)")
        
        # Emit current snapshot to UI
        _emit_snapshot_now()
        
        return ctx
    except Exception as e:
        _log(pair_name, f"rehydrate error: {e}")
        import traceback
        traceback.print_exc()
        return None


def _migrate_old_state_to_per_pair():
    """Migrate pairs from old bridge_state.json to per-pair files (only if old file exists and has data)"""
    try:
        # Check if old bridge_state.json exists
        if not os.path.exists(STATE_FILE):
            print(f"[Bridge] Old bridge_state.json does not exist - skipping migration (using per-pair files)")
            return
        
        # Check if old bridge_state.json has data (only check states, not positions/pairCtx)
        # After refactoring, positions and pairCtx are in individual files
        old_state = _read_json()
        old_states = old_state.get("states", {})
        
        # Check if per-pair files already exist
        existing_pairs = list_all_pairs()
        if existing_pairs:
            print(f"[Bridge] Per-pair files already exist ({len(existing_pairs)} pairs), skipping migration")
            return
        
        # Only migrate states if old file has states and no per-pair files exist
        if not old_states:
            print(f"[Bridge] Old bridge_state.json has no states - skipping migration (using per-pair files)")
            return
        
        print(f"[Bridge] Migrating {len(old_states)} pair states from old bridge_state.json to per-pair files...")
        
        migrated_count = 0
        for pair_name, state_str in old_states.items():
            try:
                manager = get_pair_manager(pair_name)
                
                # Migrate state only (positions and pairCtx should already be in individual files)
                manager.set_state_str(state_str)
                
                migrated_count += 1
                print(f"[Bridge] Migrated state for pair: {pair_name}")
            except Exception as e:
                print(f"[Bridge] Error migrating pair {pair_name}: {e}")
                continue
        
        print(f"[Bridge] Migration completed: {migrated_count} pair states migrated to per-pair files")
    except Exception as e:
        print(f"[Bridge] Migration error: {e}")
        import traceback
        traceback.print_exc()


def _auto_rehydrate_pairs():
    """Auto-rehydrate pairs from per-pair files on startup"""
    try:
        # First, migrate old state if needed (only if old file exists)
        _migrate_old_state_to_per_pair()
        
        # Update metadata with active pairs from per-pair files
        _update_active_pairs()
        
        # Get state from global bridge_state.json
        pair_ctx = _get_json_pair_ctx()
        states = _get_json_states()
        positions = _get_json_positions()
        
        # CRITICAL: Skip if global state is completely empty (factory reset was done)
        # This prevents rehydrating pairs that were deleted
        if (not pair_ctx or len(pair_ctx) == 0) and \
           (not states or len(states) == 0) and \
           (not positions or len(positions) == 0):
            # Silent skip - no need to log empty state
            return
        
        # Skip if no pairs to rehydrate
        if not pair_ctx or len(pair_ctx) == 0:
            # Silent skip - no need to log
            return
        
        # Only log if there are pairs to process
        print(f"[Bridge] Starting rehydration: {len(pair_ctx)} pairs found in global state")
        
        # CRITICAL: Rehydrate pairs that are in "Paused" state
        # Also rehydrate pairs that were "Running" or in other active states (Leg1, Leg2, etc.) as paused
        # Finished pairs should stay finished (not rehydrated)
        paused_pairs = []
        running_pairs_to_pause = []
        finished_pairs = []
        
        for pair_name in pair_ctx.keys():
            current_state = states.get(pair_name, "")
            if current_state == "Paused":
                paused_pairs.append(pair_name)
            elif current_state == "Finished":
                finished_pairs.append(pair_name)
            else:
                # Any other state (Running, Leg1, Leg2, Squaring Off, etc.) should be rehydrated as paused
                running_pairs_to_pause.append((pair_name, current_state))
        
        print(f"[Bridge] State breakdown: {len(paused_pairs)} paused, {len(finished_pairs)} finished, {len(running_pairs_to_pause)} running/active (will be paused)")
        
        # Log finished pairs (they stay finished, don't rehydrate)
        if finished_pairs:
            print(f"[Bridge] Finished pairs (not rehydrating): {finished_pairs}")
            _log("SYSTEM", f"Finished pairs will remain finished: {finished_pairs}")
        
        # Log running pairs that will be converted to paused
        if running_pairs_to_pause:
            pair_names = [p[0] for p in running_pairs_to_pause]
            state_list = [f"{p[0]}:{p[1]}" for p in running_pairs_to_pause]
            print(f"[Bridge] Running/active pairs (will be rehydrated as paused): {pair_names}")
            _log("SYSTEM", f"Pairs in active state will be rehydrated as paused: {state_list}")
            # Set their state to "Paused" before rehydration
            for pair_name, old_state in running_pairs_to_pause:
                _set_state(pair_name, "Paused")
                _log("SYSTEM", f"Converted {pair_name} from '{old_state}' to 'Paused' for rehydration")
        
        # Rehydrate both paused pairs and pairs that were running (now set to paused)
        all_pairs_to_rehydrate = paused_pairs + [p[0] for p in running_pairs_to_pause]
        
        for pair_name in all_pairs_to_rehydrate:
            try:
                # CRITICAL: Check if pair was deleted - don't rehydrate deleted pairs
                with _deleted_pairs_lock:
                    if pair_name in _deleted_pairs:
                        print(f"[Bridge] Skipping {pair_name} - was deleted")
                        _log("SYSTEM", f"Skipping rehydration for deleted pair: {pair_name}")
                        continue
                
                # Skip if already active
                if pair_name in _ctx:
                    print(f"[Bridge] Skipping {pair_name} - already active")
                    continue
                
                # Try to rehydrate
                print(f"[Bridge] Rehydrating paused pair: {pair_name}")
                _log("SYSTEM", f"Rehydrating paused pair: {pair_name}")
                ctx = _rehydrate_pair_from_json(pair_name)
                if ctx:
                    print(f"[Bridge] Auto-rehydrated successfully: {pair_name}")
                    _log("SYSTEM", f"Auto-rehydrated successfully: {pair_name}")
                else:
                    print(f"[Bridge] Auto-rehydration failed for: {pair_name}")
                    _log("SYSTEM", f"Auto-rehydration failed for: {pair_name}")
            except Exception as e:
                print(f"[Bridge] Error rehydrating {pair_name}: {e}")
                _log("SYSTEM", f"Error rehydrating {pair_name}: {e}")
                continue
        
        # Only log if pairs were actually rehydrated
        if all_pairs_to_rehydrate:
            print(f"[Bridge] Rehydration completed: {len(all_pairs_to_rehydrate)} pairs rehydrated, {len(_ctx)} active pairs")
        else:
            # Silent completion if nothing to rehydrate
            pass
    except Exception as e:
        print(f"[Bridge] Auto-rehydration error: {e}")
        _log("SYSTEM", f"Auto-rehydration error: {e}")

