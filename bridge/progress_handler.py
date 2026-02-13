"""
Progress handling for Bridge.
Handles progress events, auto-saving pair state, and position updates.
"""
from datetime import datetime
from bridge.shared_state import (
    _deleted_pairs_lock, _deleted_pairs, _ctx, _progress_lock, _progress_seen
)
from bridge.state_wrappers import (
    _get_json_states, _get_json_pair_ctx, _get_json_positions,
    _set_position, _set_state, _update_position_field, _update_pair_ctx_field,
    _remove_pair
)
from bridge.utils import _log
from bridge.io import _emit
from bridge.pair_operations import _cleanup_pair
from marketdata_store import market_data


def _handle_progress(pair: str, evt: dict):
    """
    evt (normalized):
      type: "lot_fill"
      phase: "entry" | "exit"
      symbol, side
      lot_index: 1-based on the current parent flow
      lots_done: cumulative lots on this flow (alias of lot_index for convenience)
      lots_total, lot_size
      avg_px_hint: optional
    """
    if not isinstance(evt, dict) or evt.get("type") != "lot_fill":
        return

    phase = (evt.get("phase") or "entry").lower()
    lot_index = int(evt.get("lot_index") or evt.get("lots_done") or 0)
    if lot_index <= 0:
        return

    with _progress_lock:
        last = _progress_seen.setdefault(pair, {"entry": 0, "exit": 0})
        prev = last.get(phase, 0)
        if lot_index <= prev:
            return  # already accounted
        delta_lots = lot_index - prev
        last[phase] = lot_index

    lot_size = int(evt.get("lot_size") or 0)
    lots_total = int(evt.get("lots_total") or 0)

    # Update pair context with position data for state-based tracking
    ctx = _ctx.get(pair)
    if not ctx or not getattr(ctx, "l2_state", None):
        return
    
    if "fills" not in ctx.l2_state:
        ctx.l2_state["fills"] = {}
    
    fills = ctx.l2_state["fills"]

    if phase == "entry":
        current_filled = int(fills.get("lots_filled", 0) or 0)
        new_filled = max(current_filled, lot_index)
        fills["lots_filled"] = new_filled
        fills["qty_filled"] = new_filled * lot_size
        if current_filled == 0 and new_filled > 0:
            _update_pair_ctx_field(pair, "leg2_status", "has_position")
        if fills.get("entry_time") is None:
            fills["entry_time"] = datetime.now().isoformat(timespec="seconds")
        ctx.l2_state["symbol"] = evt.get("symbol", "")
        ctx.l2_state["is_long"] = (evt.get("side", "BUY").upper() == "BUY")
        
        # Update position fields (batched - will be flushed by auto-save)
        _update_position_field(pair, "lots_filled", new_filled)
        _update_position_field(pair, "qty_filled", new_filled * lot_size)
        _update_position_field(pair, "entry_time", fills.get("entry_time", ""))
        _update_position_field(pair, "symbol", ctx.l2_state["symbol"])
        _update_position_field(pair, "net_lots", new_filled)
        _update_position_field(pair, "side", "BUY" if ctx.l2_state["is_long"] else "SELL")
        _update_position_field(pair, "lot_size", lot_size)
        _update_position_field(pair, "position_open", True)
        
        # Initialize fill tracking for VWAP calculation
        if "fill_details" not in fills:
            fills["fill_details"] = []  # List of {"price": float, "qty": int, "timestamp": str}
        
        # Get executed price from the event
        executed_price = None
        if evt.get("avg_px_hint") is not None:
            executed_price = float(evt.get("avg_px_hint"))
        elif evt.get("executed_price") is not None:
            executed_price = float(evt.get("executed_price"))
        elif evt.get("price") is not None:
            executed_price = float(evt.get("price"))
        
        if executed_price is not None:
            # Add this fill to the tracking list
            fill_qty = delta_lots * lot_size  # Quantity filled in this event
            fills["fill_details"].append({
                "price": executed_price,
                "qty": fill_qty,
                "timestamp": datetime.now().isoformat(timespec="seconds")
            })
            
            # Calculate VWAP from all fills
            total_value = sum(f["price"] * f["qty"] for f in fills["fill_details"])
            total_qty = sum(f["qty"] for f in fills["fill_details"])
            
            if total_qty > 0:
                vwap = total_value / total_qty
                fills["avg_entry"] = round(vwap, 2)
                _log(pair, f"Calculated entry VWAP: {fills['avg_entry']} from {len(fills['fill_details'])} fills (latest: {executed_price})")
            else:
                fills["avg_entry"] = executed_price
                _log(pair, f"Set avg_entry to executed price: {fills['avg_entry']}")
            
            # CRITICAL: Sync VWAP back to strategy state so it uses actual fills instead of initial LTP
            if ctx and getattr(ctx, "l2_state", None):
                ctx.l2_state.setdefault("fills", {})
                ctx.l2_state["fills"]["avg_entry"] = fills["avg_entry"]
                _log(pair, f"Synced entry VWAP to strategy state: {fills['avg_entry']}")
            
            # Update avg_entry in position data immediately (will be flushed by auto-save)
            _update_position_field(pair, "avg_entry", fills["avg_entry"])
            
            # CRITICAL: Ensure the calculated VWAP is immediately available in position data
            # Flush any batched updates to ensure avg_entry is persisted
            from bridge.optimized_state import get_state_manager
            state_mgr = get_state_manager()
            state_mgr._flush_batch()
        else:
            # Fallback to entry price from state if no executed price available
            if "avg_entry" not in fills:
                fallback_price = ctx.l2_state.get("entry", 0.0)
                if fallback_price and fallback_price > 0:
                    fills["avg_entry"] = float(fallback_price)
                    _log(pair, f"Fallback: Set avg_entry from state entry: {fills['avg_entry']} (no executed price available)")
                    
                    # Sync fallback price to strategy state as well
                    if ctx and getattr(ctx, "l2_state", None):
                        ctx.l2_state.setdefault("fills", {})
                        ctx.l2_state["fills"]["avg_entry"] = fills["avg_entry"]
                        _log(pair, f"Synced fallback avg_entry to strategy state: {fills['avg_entry']}")
                else:
                    fills["avg_entry"] = 0.0
                    _log(pair, f"Fallback: Set default avg_entry: {fills['avg_entry']} (no price data available)")
                # Immediate JSON update
                _update_position_field(pair, "avg_entry", fills["avg_entry"])
        ctx.l2_state["position_open"] = True
        # Update JSON with position_open status
        _update_position_field(pair, "position_open", True)

        # Immediate JSON updates are handled above

    elif phase == "exit":
        current_exit = int(fills.get("exit_filled", 0) or 0)
        new_exit = max(current_exit, lot_index)
        fills["exit_filled"] = new_exit
        fills["exit_qty"] = new_exit * lot_size
        
        # Initialize exit fill tracking for VWAP calculation
        if "exit_fill_details" not in fills:
            fills["exit_fill_details"] = []  # List of {"price": float, "qty": int, "timestamp": str}
        
        # Get executed exit price from the event
        executed_exit_price = None
        if evt.get("avg_px_hint") is not None:
            executed_exit_price = float(evt.get("avg_px_hint"))
        elif evt.get("executed_price") is not None:
            executed_exit_price = float(evt.get("executed_price"))
        elif evt.get("price") is not None:
            executed_exit_price = float(evt.get("price"))
        
        if executed_exit_price is not None:
            # Add this exit fill to the tracking list
            exit_fill_qty = delta_lots * lot_size  # Quantity filled in this event
            fills["exit_fill_details"].append({
                "price": executed_exit_price,
                "qty": exit_fill_qty,
                "timestamp": datetime.now().isoformat(timespec="seconds")
            })
            
            # Calculate VWAP for exit fills
            total_exit_value = sum(f["price"] * f["qty"] for f in fills["exit_fill_details"])
            total_exit_qty = sum(f["qty"] for f in fills["exit_fill_details"])
            
            if total_exit_qty > 0:
                exit_vwap = total_exit_value / total_exit_qty
                fills["avg_exit"] = round(exit_vwap, 2)
                _log(pair, f"Calculated exit VWAP: {fills['avg_exit']} from {len(fills['exit_fill_details'])} fills (latest: {executed_exit_price})")
            else:
                fills["avg_exit"] = executed_exit_price
                _log(pair, f"Set avg_exit to executed price: {fills['avg_exit']}")
        
        # Calculate remaining open lots
        original_filled = int(fills.get("original_filled", fills.get("lots_filled", 0)) or 0)
        if "original_filled" not in fills:
            fills["original_filled"] = original_filled  # Store original position size
        
        remaining_lots = max(0, original_filled - new_exit)
        fills["qty_filled"] = remaining_lots * lot_size
        
        # Update exit data (batched - will be flushed by auto-save)
        _update_position_field(pair, "exit_filled", new_exit)
        _update_position_field(pair, "exit_qty", new_exit * lot_size)
        _update_position_field(pair, "lots_filled", remaining_lots)
        _update_position_field(pair, "qty_filled", remaining_lots * lot_size)
        _update_position_field(pair, "net_lots", remaining_lots)
        
        # Update position_open status
        if remaining_lots <= 0:
            # CRITICAL: Check if pair was deleted before updating fields
            with _deleted_pairs_lock:
                is_deleted = pair in _deleted_pairs
            
            # Only update fields if pair wasn't deleted
            if not is_deleted:
                ctx.l2_state["position_open"] = False
                fills["exit_time"] = datetime.now().isoformat(timespec="seconds")
                # Immediate JSON update for position closure
                _update_position_field(pair, "position_open", False)
                _update_position_field(pair, "exit_time", fills["exit_time"])
                _update_pair_ctx_field(pair, "leg2_status", "squared_off")
                _update_pair_ctx_field(pair, "leg2_exit_time", fills["exit_time"])
                _log(pair, f"Position fully closed: remaining_lots={remaining_lots}, setting position_open=False")
                
                # Immediately set state to Finished when position is fully closed
                _set_state(pair, "Finished")
                # Verify state was set correctly
                current_state_after = _get_json_states().get(pair, "Unknown")
                _log(pair, f"State set to Finished (verified: {current_state_after}) - position fully closed")
            else:
                _log(pair, f"Skipping field updates - pair was deleted")
            
            _cleanup_pair(pair)
            
            # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
            # State is already set to "Finished" above, just ensure it's persisted
            _log(pair, "Position fully closed - pair marked as Finished")
            
            cur_states = _get_json_states()
            cur_pair_ctx = _get_json_pair_ctx()
            # Update JSON with current state (JSON-first approach)
            positions_copy = _get_json_positions()
            # JSON updates are now handled by individual _set_* functions
            _log(pair, f"Emitting snapshot with states: {cur_states}")
            # Late import to avoid circular dependency
            from bridge.serialization import _emit_snapshot_now
            _emit_snapshot_now()
            _emit({"type":"finished","pair_name":pair})
            # Pair is now removed from all sections
            return

        # Immediate JSON updates are handled above

    # optional UX: log the per-lot fill
    _log(pair, f"{phase.upper()} lot filled: idx={lot_index} (+{delta_lots})")
    
    # CRITICAL: After processing fill, call auto-save to flush batched updates
    # This ensures position data is complete and written to global state
    ctx = _ctx.get(pair)
    if ctx:
        _auto_save_pair_state(pair, ctx)


def _auto_save_pair_state(pair_name: str, ctx):
    """Auto-save pair state whenever it changes - JSON-first approach"""
    try:
        # CRITICAL: Check if pair was deleted - don't auto-save deleted pairs
        with _deleted_pairs_lock:
            if pair_name in _deleted_pairs:
                print(f"[Bridge] Skipping auto_save_pair_state for '{pair_name}' - pair was deleted")
                return
        
        # Get current state from JSON
        cur_states = _get_json_states()
        cur_pair_ctx = _get_json_pair_ctx()
        
        # Extract position data from context
        if getattr(ctx, "l2_state", None) and ctx.l2_state.get("fills"):
            fills = ctx.l2_state["fills"]
            lots_filled = fills.get("lots_filled", 0)
            exit_filled = fills.get("exit_filled", 0)
            net_lots = lots_filled - exit_filled
            
            # Create position if there are any fills (even if fully exited)
            if lots_filled > 0:
                # Get average entry price (CRITICAL: use calculated VWAP from fills)
                # This ensures the entry price shown in open positions is the actual VWAP
                avg_entry = fills.get("avg_entry")
                if avg_entry is None:
                    avg_entry = ctx.l2_state.get("avg_entry", 0.0)
                
                # Log to confirm we're using the calculated VWAP
                if avg_entry and avg_entry > 0:
                    _log(pair_name, f"Using calculated entry VWAP for position: {avg_entry}")
                
                # Get current LTP from market data (with error handling)
                symbol = ctx.l2_state.get("symbol", "")
                ltp = None
                if symbol:
                    md = market_data.get(symbol, {})
                    ltp = md.get("ltp")

                # Calculate MTM if we have both avg_entry and ltp
                mtm = 0.0
                if avg_entry and ltp and net_lots > 0:
                    lot_size = int(ctx.l2_state.get("lot_size", 1))
                    is_long = ctx.l2_state.get("is_long", True)
                    if is_long:
                        mtm = (float(ltp) - float(avg_entry)) * net_lots * lot_size
                    else:
                        mtm = (float(avg_entry) - float(ltp)) * net_lots * lot_size
                    mtm = round(mtm, 2)
                
                lot_size = int(ctx.l2_state.get("lot_size", 1))
                position_data = {
                    "symbol": symbol,
                    "side": "BUY" if ctx.l2_state.get("is_long", True) else "SELL",
                    "lots_filled": lots_filled,  # Total lots that were filled (for display)
                    "lots_total": lots_filled,  # Total lots that were filled
                    "exit_filled": exit_filled,  # Total lots exited
                    "qty_filled": net_lots * lot_size,  # Net quantity filled
                    "entry_time": fills.get("entry_time", ""),
                    "exit_time": fills.get("exit_time", ""),
                    "avg_entry": avg_entry,
                    "net_lots": net_lots,  # Net open position (lots_filled - exit_filled)
                    "status": "OPEN" if net_lots > 0 else "FLAT",
                    "position_open": net_lots > 0,
                    "lot_size": lot_size,
                    "ltp": ltp,
                    "mtm": mtm,
                }
                
                # CRITICAL: Flush any pending batched updates before writing complete position
                # This ensures all field updates are included
                from bridge.optimized_state import get_state_manager
                state_mgr = get_state_manager()
                state_mgr._flush_batch()
                
                # Update position directly in JSON (immediate write to ensure it's persisted)
                _set_position(pair_name, position_data, immediate=True)
        
        # JSON-first: no RAM cache needed
        
        # Get updated state from JSON for emission
        positions_copy = _get_json_positions()
        
        # Emit snapshot to frontend
        _emit({
            "type": "snapshot",
            "positions": positions_copy,
            "states": cur_states,
            "pairCtx": cur_pair_ctx,
            "total_mtm": sum(float(p.get("mtm", 0) or 0) for p in positions_copy.values()),
        })
        
    except Exception as e:
        print(f"[Bridge] Error auto-saving state for {pair_name}: {e}")
        import traceback
        traceback.print_exc()


def _force_position_update(pair_name: str):
    """Force a position update for a specific pair (for testing)"""
    ctx = _ctx.get(pair_name)
    if ctx:
        print(f"[Bridge] Force updating position for {pair_name}")
        _auto_save_pair_state(pair_name, ctx)
    else:
        print(f"[Bridge] No context found for {pair_name}")

