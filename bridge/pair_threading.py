"""
Pair threading and monitoring for Bridge.
Handles PairCtx class, pair thread execution, and Leg2 monitoring.
"""
from datetime import datetime
import time
import Main_strategy as MS
from bridge.shared_state import (
    _threads, _ctx, _cmd_lock, _cmd, _resume_store_lock, _resume_store,
    _progress_lock, _progress_seen, _TL
)
from bridge.state_wrappers import (
    _get_json_states, _get_json_pair_ctx, _get_json_positions,
    _set_state, _update_pair_ctx_field, _update_position_field,
    _save_leg_details, _remove_pair
)
from bridge.utils import _log
from bridge.io import _emit
from bridge.progress_handler import _handle_progress
from bridge.pair_operations import _cleanup_pair, _drop_state
from strategy_helpers import _squareoff_deadline


class PairCtx:
    def __init__(self, pair_name, leg1_cfg, leg2_cfg, l1_state=None, l2_state=None):
        self.pair = pair_name
        self.l1 = dict(leg1_cfg)
        self.l2 = dict(leg2_cfg)
        self.l1["option_type"] = self.l1.get("option_type", "C")
        self.l2["option_type"] = self.l2.get("option_type", "C")
        self.l1_state = l1_state
        self.l2_state = l2_state
        self.exit_requested = False
        self.paused = False


def _pair_thread(ctx: PairCtx):
    pair = ctx.pair
    # Noisy lifecycle log removed (was spamming UI/stdout on every start)
    _set_state(pair, "Leg1: idle")

    # If resuming straight into Leg2, resume monitoring instead of auto-finishing
    if ctx.l2_state:
        _log(pair, "Resuming at Leg2 (state restored)")
        # Continue with Leg2 monitoring instead of finishing
        _set_state(pair, "Leg2: armed")
        _update_pair_ctx_field(pair, "leg2_status", "initialized")
        
        # Start the leg2 monitor to handle position monitoring and square off
        _leg2_monitor(pair)
        return

    l1_state = ctx.l1_state
    while True:
        # allow user to cancel while Leg1 is waiting
        with _cmd_lock:
            if _cmd.get(pair) == "squareoff":
                _log(pair, "Squareoff while Leg1 → stopping instance")
                # Mark as finished first - ensure it's persisted immediately
                _set_state(pair, "Finished", immediate=True)
                
                # Clean up RAM state only (keep JSON state)
                with _resume_store_lock:
                    _resume_store.pop(pair, None)
                with _progress_lock:
                    _progress_seen.pop(pair, None)
                _ctx.pop(pair, None)
                _threads.pop(pair, None)
                
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                # Update JSON with current state (JSON-first approach)
                # JSON updates are now handled by individual _set_* functions
                _emit({"type": "finished", "pair_name": pair})
                
                # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                # State is already set to "Finished" above and persisted
                _log(pair, "Pair squared off and marked as Finished")
                return

        # pause gate during Leg1 wait
        if ctx.paused:
            # Check if exit was requested (e.g., during deletion)
            if getattr(ctx, 'exit_requested', False):
                _log(pair, "Exit requested during Leg1 pause - stopping thread")
                return
            # CRITICAL: Re-check ctx.paused after exit check - it might have been set to False by resume
            if not ctx.paused:
                _log(pair, "Resume detected - exiting Leg1 paused loop")
                continue  # Exit paused loop if resume was called
            # Only set state to "Paused" if we're actually paused (double-check to avoid race conditions)
            if ctx.paused:
                current_state = _get_json_states().get(pair, "")
                if current_state != "Paused":
                    _set_state(pair, "Paused")
                    _log(pair, f"Leg1 paused - waiting for resume (was: {current_state})")
            # Use shorter sleep to detect resume faster (0.1s instead of 1.0s)
            time.sleep(0.1)
            continue

        # Check if leg2 square off deadline has been crossed while in leg1
        # If leg1 is still active and leg2's deadline has passed, square off the strategy
        leg2_squareoff_time = ctx.l2.get("squareoff_time")
        leg2_squareoff_day_offset = ctx.l2.get("squareoff_day_offset")
        if leg2_squareoff_time:
            try:
                # Calculate leg2 deadline
                so_offset = 1 if (leg2_squareoff_day_offset is None) else int(leg2_squareoff_day_offset)
                leg2_deadline = _squareoff_deadline(leg2_squareoff_time, so_offset)
                
                # Check if current time has crossed the deadline
                if datetime.now() >= leg2_deadline:
                    _log(pair, f"Leg2 square off deadline crossed while in Leg1 - deadline: {leg2_deadline.isoformat()}, squaring off strategy")
                    # Mark as finished first - ensure it's persisted immediately
                    _set_state(pair, "Finished", immediate=True)
                    _update_pair_ctx_field(pair, "leg1_status", "squared_off", immediate=True)
                    _update_pair_ctx_field(pair, "leg1_exit_time", datetime.now().isoformat(timespec="seconds"), immediate=True)
                    
                    # Clean up RAM state only (keep JSON state)
                    with _resume_store_lock:
                        _resume_store.pop(pair, None)
                    with _progress_lock:
                        _progress_seen.pop(pair, None)
                    _ctx.pop(pair, None)
                    _threads.pop(pair, None)
                    
                    cur_states = _get_json_states()
                    cur_pair_ctx = _get_json_pair_ctx()
                    # JSON updates are now handled by individual _set_* functions
                    _emit({"type": "finished", "pair_name": pair})
                    
                    # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                    _log(pair, "Pair squared off due to Leg2 deadline crossing and marked as Finished")
                    return
            except Exception as e:
                _log(pair, f"Error checking leg2 deadline during leg1: {e}")
                # Continue with normal leg1 processing if deadline check fails

        try:
            l1_state, ev = MS.leg1_step_start_time(
                stock=ctx.l1["stock"], strike=ctx.l1["strike"], option_type=ctx.l1["option_type"],
                trade_direction=ctx.l1["trade_direction"], start_time_str=ctx.l1["start_time_str"],
                SLval=ctx.l1["SLval"], SLtype=ctx.l1["SLtype"],
                TSLarm=ctx.l1.get("TSLarm"), TSLmove=ctx.l1.get("TSLmove"), TSLtype=ctx.l1.get("TSLtype"),
                state=l1_state, continuous_profiling=ctx.l1.get("continuous_profiling", False)
            )
        except Exception as e:
            _log(pair, f"Leg1 step error: {e}")
            time.sleep(0.5)
            continue

        ctx.l1_state = l1_state
        
        # CRITICAL: If SL was updated (TSL triggered), save immediately
        # Check if SL changed by comparing with previous state
        old_sl = getattr(ctx, '_last_saved_l1_sl', None)
        current_sl = l1_state.get("sl")
        if current_sl is not None and (old_sl is None or current_sl != old_sl):
            # SL was updated - save immediately to ensure persistence
            _save_leg_details(pair, ctx)
            # Also update JSON directly for immediate persistence
            _update_pair_ctx_field(pair, "leg1_details", {
                k: v for k, v in l1_state.items() 
                if not k.startswith('_') and not callable(v) and v is not None
            })
            ctx._last_saved_l1_sl = current_sl
        else:
            # Save leg1 details continuously (normal save)
            _save_leg_details(pair, ctx)

        for e in ev:
            if e["type"] == "leg1_armed":
                _log(pair, f"Leg1 armed: {e['symbol']} entry={e['entry_price']} spot={e['spot_entry']} SL={e['sl_level']} SLtype={e.get('sltype', 'N/A')} TSLtype={e.get('tsltype', 'N/A')}")
                _set_state(pair, "Leg1: armed")
                _update_pair_ctx_field(pair, "leg1_status", "active")
            elif e["type"] == "leg1_sl_hit":
                _log(pair, f"Leg1 SL hit: ref={e['ref_price']} SL={e['sl_level']} entry={e['entry_price']} peak={e['peak']}")
                _set_state(pair, "Leg1: SL hit")
                _update_pair_ctx_field(pair, "leg1_status", "stop_loss_hit")
                _update_pair_ctx_field(pair, "leg1_exit_time", datetime.now().isoformat(timespec="seconds"))
                break
        else:
            time.sleep(0.1)
            continue
        break  # exit Leg1 loop after decisive event

    # If paused AFTER Leg1 finishes but BEFORE Leg2 starts, wait here
    while ctx.paused:
        # Check if exit was requested (e.g., during deletion)
        if getattr(ctx, 'exit_requested', False):
            _log(pair, "Exit requested during pause between Leg1 and Leg2 - stopping thread")
            return
        # CRITICAL: Re-check ctx.paused - it might have been set to False by resume
        if not ctx.paused:
            _log(pair, "Resume detected - exiting pause between Leg1 and Leg2")
            break  # Exit paused loop if resume was called
        # Only set state to "Paused" if we're actually paused (double-check to avoid race conditions)
        if ctx.paused:
            current_state = _get_json_states().get(pair, "")
            if current_state != "Paused":
                _set_state(pair, "Paused")
                _log(pair, f"Paused between Leg1 and Leg2 - waiting for resume (was: {current_state})")
        
        # Check if leg2 square off deadline has been crossed while paused between leg1 and leg2
        leg2_squareoff_time = ctx.l2.get("squareoff_time")
        leg2_squareoff_day_offset = ctx.l2.get("squareoff_day_offset")
        if leg2_squareoff_time:
            try:
                # Calculate leg2 deadline
                so_offset = 1 if (leg2_squareoff_day_offset is None) else int(leg2_squareoff_day_offset)
                leg2_deadline = _squareoff_deadline(leg2_squareoff_time, so_offset)
                
                # Check if current time has crossed the deadline
                if datetime.now() >= leg2_deadline:
                    _log(pair, f"Leg2 square off deadline crossed while paused between Leg1 and Leg2 - deadline: {leg2_deadline.isoformat()}, squaring off strategy")
                    # Mark as finished - ensure it's persisted immediately
                    _set_state(pair, "Finished", immediate=True)
                    _update_pair_ctx_field(pair, "leg1_status", "squared_off", immediate=True)
                    _update_pair_ctx_field(pair, "leg1_exit_time", datetime.now().isoformat(timespec="seconds"), immediate=True)
                    
                    # Clean up RAM state only (keep JSON state)
                    with _resume_store_lock:
                        _resume_store.pop(pair, None)
                    with _progress_lock:
                        _progress_seen.pop(pair, None)
                    _ctx.pop(pair, None)
                    _threads.pop(pair, None)
                    
                    cur_states = _get_json_states()
                    cur_pair_ctx = _get_json_pair_ctx()
                    # JSON updates are now handled by individual _set_* functions
                    _emit({"type": "finished", "pair_name": pair})
                    
                    # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                    _log(pair, "Pair squared off due to Leg2 deadline crossing and marked as Finished")
                    return
            except Exception as e:
                _log(pair, f"Error checking leg2 deadline during pause: {e}")
                # Continue with normal pause processing if deadline check fails
        
        with _cmd_lock:
            if _cmd.get(pair) == "squareoff":
                _log(pair, "Squareoff while Leg1 → stopping instance")
                # Mark as finished - ensure it's persisted immediately
                _set_state(pair, "Finished", immediate=True)
                with _resume_store_lock:
                    _resume_store.pop(pair, None)
                with _progress_lock:
                    _progress_seen.pop(pair, None)
                _ctx.pop(pair, None)
                _threads.pop(pair, None)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                # JSON updates are now handled by individual _set_* functions
                _emit({"type":"finished","pair_name": pair})
                # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                _log(pair, "Pair squared off and marked as Finished")
                return
        # Use shorter sleep to detect resume faster (0.1s instead of 1.0s)
        time.sleep(0.1)

    # One last squareoff gate before actually starting Leg2
        with _cmd_lock:
            if _cmd.get(pair) == "squareoff":
                _log(pair, "Squareoff just before Leg2 → stopping instance")
                # Mark as finished - ensure it's persisted immediately
                _set_state(pair, "Finished", immediate=True)
                with _resume_store_lock:
                    _resume_store.pop(pair, None)
                with _progress_lock:
                    _progress_seen.pop(pair, None)
                _ctx.pop(pair, None)
                _threads.pop(pair, None)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                # JSON updates are now handled by individual _set_* functions
                _emit({"type": "finished", "pair_name": pair})
                
                # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                # State is already set to "Finished" above and persisted
                _log(pair, "Pair squared off and marked as Finished")
                return

    try:
        # Disable regex counting for entry (we have clean per-lot callbacks)
        _TL.pair = None
        _TL.phase = None

        # bridge progress adapter for MS → UI/snapshot
        def _bridge_progress(evt: dict):
            _handle_progress(pair, evt)

        # Initialize l2_state before calling leg2_start so callbacks can access it
        ctx.l2_state = {
            "fills": {
                "lots_filled": 0,
                "qty_filled": 0,
                "avg_entry": None,
                "entry_time": None
            }
        }

        l2_state = MS.leg2_start(
            stock=ctx.l2["stock"], strike=ctx.l2["strike"], option_type=ctx.l2["option_type"], trade_direction=ctx.l2["trade_direction"],
            SLval=ctx.l2["SLval"], SLtype=ctx.l2["SLtype"],
            lots=ctx.l2["lots"], modify_time=ctx.l2["modify_time"], market_order=ctx.l2["market_order"],
            TSLarm=ctx.l2.get("TSLarm"), TSLmove=ctx.l2.get("TSLmove"), TSLtype=ctx.l2.get("TSLtype"),
            squareoff_time=ctx.l2.get("squareoff_time"), squareoff_day_offset=ctx.l2.get("squareoff_day_offset"),
            continuous_profiling=ctx.l2.get("continuous_profiling", False),
            splice_lots=ctx.l2.get("splice_lots", 5),
            on_progress=_bridge_progress,
        )
        if l2_state is None:
            _log(pair, "Leg2 start failed: market data not ready")
            return
        
        # Merge the returned state with our pre-initialized fills
        if l2_state:
            # Preserve our pre-initialized fills if they exist
            if "fills" in ctx.l2_state and ctx.l2_state["fills"].get("lots_filled", 0) > 0:
                l2_state["fills"] = ctx.l2_state["fills"]
        ctx.l2_state = l2_state
    except Exception as e:
        _log(pair, f"Leg2 start error: {e}")
        return
    finally:
        _TL.pair = None
        _TL.phase = None

    _log(pair, f"Leg2 armed: {ctx.l2_state['symbol']} entry={ctx.l2_state['entry']} spot={ctx.l2_state['spot_entry']} SL={ctx.l2_state['sl']} deadline={ctx.l2_state.get('deadline')}")
    _set_state(pair, "Leg2: armed")
    _update_pair_ctx_field(pair, "leg2_status", "initialized")
    
    # Set to Running when Leg2 monitor starts actively monitoring
    _set_state(pair, "Running")
    # Pre-populate JSON fields so LTP/MTM can update before fills
    try:
        _update_position_field(pair, "symbol", ctx.l2_state.get("symbol", ""))
        _update_position_field(pair, "side", "BUY" if ctx.l2_state.get("trade_direction", "BUY").upper() == "BUY" or ctx.l2_state.get("is_long", True) else "SELL")
        if ctx.l2_state.get("lot_size"):
            _update_position_field(pair, "lot_size", int(ctx.l2_state["lot_size"]))
        entry_hint = ctx.l2_state.get("entry")
        if entry_hint:
            _update_position_field(pair, "avg_entry", float(entry_hint))
    except Exception:
        pass
    # Start the leg2 monitor to handle position monitoring and square off
    _leg2_monitor(pair)


def _leg2_monitor(pair):
    """Monitor Leg2 position and handle square off requests"""
    _log(pair, "Leg2 monitor started")
    
    ctx = _ctx.get(pair)
    if not ctx or not getattr(ctx, "l2_state", None):
        _log(pair, "No Leg2 state - exiting monitor")
        return
    
    while True:
        # Check if we should exit
        if ctx.exit_requested:
            _log(pair, "Exit requested - stopping Leg2 monitor")
            break
        
        # Check for square off command (legacy - now handled directly in _squareoff)
        with _cmd_lock:
            cmd = _cmd.pop(pair, None)
        
        if cmd == "squareoff":
            _log(pair, "Square off command received in Leg2 monitor (legacy)")
            # Square off is now handled directly in _squareoff function
            # Just mark exit requested and let the monitor detect position closure
            ctx.exit_requested = True
            continue
        
        # PAUSE CHECK - Stop all monitoring when paused
        if ctx.paused:
            # Check if exit was requested (e.g., during deletion)
            if getattr(ctx, 'exit_requested', False):
                _log(pair, "Exit requested during Leg2 monitor pause - stopping thread")
                break
            # CRITICAL: Re-check ctx.paused - it might have been set to False by resume
            if not ctx.paused:
                _log(pair, "Resume detected - exiting Leg2 monitor paused loop")
                continue  # Exit paused loop if resume was called, continue with monitoring
            # Only set state to "Paused" if we're actually paused (double-check to avoid race conditions)
            if ctx.paused:
                current_state = _get_json_states().get(pair, "")
                if current_state != "Paused":
                    _set_state(pair, "Paused")
                    _log(pair, "Leg2 monitor: paused - stopping all monitoring (TSL/SL disabled)")
            # Use shorter sleep to detect resume faster (0.1s instead of 1.0s)
            time.sleep(0.1)
            continue
        
        # Monitor the position (check for SL hits, etc.)
        try:
            from Main_strategy import leg2_step
            l2_state = ctx.l2_state
            
            # Call leg2_step to check for SL hits (pass paused state)
            l2_state, events = leg2_step(l2_state, paused=ctx.paused)
            
            # Update the state
            ctx.l2_state = l2_state
            
            # CRITICAL: If SL was updated (TSL triggered), save immediately
            # Check if SL changed by comparing with previous state
            old_sl = getattr(ctx, '_last_saved_sl', None)
            current_sl = l2_state.get("sl")
            if current_sl is not None and (old_sl is None or current_sl != old_sl):
                # SL was updated - save immediately to ensure persistence
                _save_leg_details(pair, ctx, immediate=True)
                ctx._last_saved_sl = current_sl
            else:
                # Save leg2 details continuously (normal save - batched)
                _save_leg_details(pair, ctx, immediate=False)
            
            # Handle events
            for evt in events:
                if evt.get("type") == "leg2_sl_hit":
                    _log(pair, f"Leg2 SL hit: {evt}")
                    # Update status and then invoke the standard square-off flow
                    try:
                        _update_pair_ctx_field(pair, "leg2_status", "stop_loss_hit")
                        _update_pair_ctx_field(pair, "leg2_exit_time", datetime.now().isoformat(timespec="seconds"))
                    except Exception:
                        pass
                    _log(pair, "Invoking squareoff due to SL hit")
                    # Late import to avoid circular dependency
                    from bridge.commands import _squareoff
                    _squareoff(pair)
                    return
                elif evt.get("type") == "leg2_time_squareoff":
                    _log(pair, f"Leg2 time squareoff: {evt}")
                    # Update status and then invoke the standard square-off flow
                    try:
                        _update_pair_ctx_field(pair, "leg2_status", "time_squareoff")
                        _update_pair_ctx_field(pair, "leg2_exit_time", datetime.now().isoformat(timespec="seconds"))
                    except Exception:
                        pass
                    _log(pair, "Invoking squareoff due to time deadline")
                    # Late import to avoid circular dependency
                    from bridge.commands import _squareoff
                    _squareoff(pair)
                    return
                elif evt.get("type") == "leg2_progress":
                    _handle_progress(pair, evt)
            
            # Check if position is fully closed after processing events
            position_open = l2_state.get("position_open", True)
            fills = l2_state.get("fills", {})
            lots_filled = fills.get("lots_filled", 0)
            exit_filled = fills.get("exit_filled", 0)
            if l2_state and not position_open:
                _log(pair, "Position fully closed - exiting Leg2 monitor")
                # State should already be set to "Finished" by _handle_progress
                # Just clean up and exit
                _cleanup_pair(pair)
                cur_states = _get_json_states()
                cur_pair_ctx = _get_json_pair_ctx()
                positions_copy = _get_json_positions()
                # JSON updates are now handled by individual _set_* functions
                # Late import to avoid circular dependency
                from bridge.serialization import _emit_snapshot_now
                _emit_snapshot_now()
                _emit({"type":"finished","pair_name":pair})
                # CRITICAL: Just mark as finished - don't remove pair (only deletion should remove)
                ctx.exit_requested = True
                return
            
            # Auto-save state AFTER processing events (so position data is updated)
            # Immediate JSON updates are handled above
            
        except Exception as e:
            _log(pair, f"Error in Leg2 monitor: {e}")
            break
        
        # Sleep for a bit before next check
        time.sleep(0.5)  # Increased sleep time to reduce CPU usage
    
    _log(pair, "Leg2 monitor exited")
    
    # State should already be set to Finished when position was closed
    # This is just a fallback for cases where monitor exits without position being closed

