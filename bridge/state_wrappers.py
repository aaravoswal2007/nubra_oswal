"""
State management wrapper functions for Bridge.
Provides convenient wrappers around the OptimizedStateManager.
"""
from bridge.shared_state import _deleted_pairs_lock, _deleted_pairs
from bridge.optimized_state import get_state_manager
from runtime_paths_live import state_file


# State manager instance
_state_mgr = None
STATE_FILE = state_file("bridge_state.json")


def _get_state_mgr():
    """Get or create state manager instance."""
    global _state_mgr
    if _state_mgr is None:
        _state_mgr = get_state_manager(STATE_FILE)
    return _state_mgr


def _read_metadata():
    """Legacy function - returns full state."""
    return _get_state_mgr().get_state()


def _read_json():
    """Legacy function - returns full state."""
    return _get_state_mgr().get_state()


def _write_json(data):
    """Legacy function - not used in optimized version."""
    pass


def _update_metadata(updates: dict):
    """Update metadata - not used in optimized version."""
    pass


def _update_active_pairs():
    """Update active pairs - not used in optimized version."""
    pass


def _get_json_positions():
    """Get all positions from individual pair files (persisted data only, no LTP/MTM)."""
    from bridge.pair_manager import get_all_pair_managers, get_pair_manager
    from pathlib import Path
    from runtime_paths_live import STATE_DIR
    positions = {}
    
    # Get all active managers from registry
    try:
        managers = get_all_pair_managers()
        for pair_name, manager in managers.items():
            try:
                state = manager.get_state(use_cache=True)
                if "position" in state and state["position"]:
                    positions[pair_name] = state["position"].copy()
            except Exception:
                continue
    except Exception:
        pass
    
    # Also scan filesystem for pair files that might not be in registry yet
    try:
        pairs_dir = Path(STATE_DIR) / "pairs"
        if pairs_dir.exists():
            for pair_file in pairs_dir.glob("*.json"):
                pair_name = pair_file.stem
                if pair_name not in positions:
                    try:
                        manager = get_pair_manager(pair_name)
                        state = manager.get_state(use_cache=True)
                        if "position" in state and state["position"]:
                            positions[pair_name] = state["position"].copy()
                    except Exception:
                        continue
    except Exception:
        pass
    
    return positions


def _get_json_states():
    """Get all pair states from individual pair files (source of truth)."""
    from bridge.pair_manager import get_all_pair_managers, get_pair_manager
    from pathlib import Path
    from runtime_paths_live import STATE_DIR
    states = {}
    
    # Get all active managers from registry
    try:
        managers = get_all_pair_managers()
        for pair_name, manager in managers.items():
            try:
                state_data = manager.get_state(use_cache=True)
                if "state" in state_data and state_data["state"]:
                    states[pair_name] = state_data["state"]
            except Exception:
                continue
    except Exception:
        pass
    
    # Also scan filesystem for pair files that might not be in registry yet
    try:
        pairs_dir = Path(STATE_DIR) / "pairs"
        if pairs_dir.exists():
            for pair_file in pairs_dir.glob("*.json"):
                pair_name = pair_file.stem
                if pair_name not in states:
                    try:
                        manager = get_pair_manager(pair_name)
                        state_data = manager.get_state(use_cache=True)
                        if "state" in state_data and state_data["state"]:
                            states[pair_name] = state_data["state"]
                    except Exception:
                        continue
    except Exception:
        pass
    
    return states


def _get_json_pair_ctx():
    """Get all pair contexts from individual pair files."""
    from bridge.pair_manager import get_all_pair_managers, get_pair_manager
    from pathlib import Path
    from runtime_paths_live import STATE_DIR
    pair_ctx = {}
    
    # Get all active managers from registry
    try:
        managers = get_all_pair_managers()
        for pair_name, manager in managers.items():
            try:
                state = manager.get_state(use_cache=True)
                if "pairCtx" in state and state["pairCtx"]:
                    pair_ctx[pair_name] = state["pairCtx"].copy()
            except Exception:
                continue
    except Exception:
        pass
    
    # Also scan filesystem for pair files that might not be in registry yet
    try:
        pairs_dir = Path(STATE_DIR) / "pairs"
        if pairs_dir.exists():
            for pair_file in pairs_dir.glob("*.json"):
                pair_name = pair_file.stem
                if pair_name not in pair_ctx:
                    try:
                        manager = get_pair_manager(pair_name)
                        state = manager.get_state(use_cache=True)
                        if "pairCtx" in state and state["pairCtx"]:
                            pair_ctx[pair_name] = state["pairCtx"].copy()
                    except Exception:
                        continue
    except Exception:
        pass
    
    return pair_ctx


def _set_position(pair_name: str, data: dict, immediate: bool = True):
    """Set position for a pair - write only to individual pair file."""
    # Write only to per-pair file (not bridge_state.json)
    try:
        from bridge.pair_manager import get_pair_manager
        manager = get_pair_manager(pair_name)
        manager.update_position(data)
    except Exception as e:
        # Log error but don't fail - per-pair file is source of truth
        print(f"[StateWrappers] Error writing position to pair file for '{pair_name}': {e}")


def _set_state(pair_name: str, state_str: str, immediate: bool = True):
    """Set state for a pair - write to individual pair file first (source of truth), then sync to bridge_state.json."""
    # CRITICAL: Write to individual pair file first (source of truth)
    try:
        from bridge.pair_manager import get_pair_manager
        manager = get_pair_manager(pair_name)
        manager.set_state_str(state_str)
    except Exception as e:
        # Log error but continue - we'll still try to update bridge_state.json
        print(f"[StateWrappers] Error writing state to pair file for '{pair_name}': {e}")
    
    # Also update bridge_state.json (metadata/backup) - but individual file is source of truth
    try:
        _get_state_mgr().set_pair_state(pair_name, state_str, immediate=immediate)
    except Exception as e:
        # Log but don't fail - individual file is source of truth
        print(f"[StateWrappers] Error writing state to bridge_state.json for '{pair_name}': {e}")


def _set_pair_ctx(pair_name: str, ctx_data: dict, immediate: bool = True):
    """Set pair context for a pair - write only to individual pair file."""
    # Write only to per-pair file (not bridge_state.json)
    try:
        from bridge.pair_manager import get_pair_manager
        manager = get_pair_manager(pair_name)
        manager.update_pair_ctx(ctx_data)
    except Exception as e:
        # Log error but don't fail - per-pair file is source of truth
        print(f"[StateWrappers] Error writing pairCtx to pair file for '{pair_name}': {e}")


def _update_pair_ctx_field(pair_name: str, field: str, value, immediate: bool = False):
    """Update a specific field in pairCtx - write only to individual pair file."""
    # CRITICAL: Check if pair was deleted before attempting update
    with _deleted_pairs_lock:
        if pair_name in _deleted_pairs:
            return
    
    # Check if pair exists in states (metadata in bridge_state.json)
    json_states = _get_json_states()
    if pair_name not in json_states:
        # Pair doesn't exist - skip update silently
        return
    
    # Write only to per-pair file (not bridge_state.json)
    try:
        from bridge.pair_manager import get_pair_manager
        manager = get_pair_manager(pair_name)
        current_ctx = manager.get_state().get("pairCtx", {})
        current_ctx[field] = value
        manager.update_pair_ctx(current_ctx)
    except Exception as e:
        # Log error but don't fail
        print(f"[StateWrappers] Error updating pairCtx field '{field}' for '{pair_name}': {e}")


def _save_leg_details(pair_name: str, ctx, immediate: bool = False):
    """Save current leg details (batched for normal saves, immediate for SL updates)."""
    from bridge.utils import _log
    try:
        # Check if ctx is None
        if ctx is None:
            return
        
        l1_state = getattr(ctx, "l1_state", None)
        if l1_state and isinstance(l1_state, dict):
            try:
                l1_clean = {k: v for k, v in l1_state.items() 
                           if k is not None and isinstance(k, str) and not k.startswith('_') 
                           and not callable(v) and v is not None}
                if l1_clean:
                    _update_pair_ctx_field(pair_name, "leg1_details", l1_clean, immediate=immediate)
            except Exception as e:
                _log(pair_name, f"Error processing l1_state: {e}")
        
        l2_state = getattr(ctx, "l2_state", None)
        if l2_state and isinstance(l2_state, dict):
            try:
                l2_clean = {k: v for k, v in l2_state.items() 
                           if k is not None and isinstance(k, str) and not k.startswith('_') 
                           and not callable(v) and v is not None}
                if l2_clean:
                    _update_pair_ctx_field(pair_name, "leg2_details", l2_clean, immediate=immediate)
            except Exception as e:
                _log(pair_name, f"Error processing l2_state: {e}")
    except Exception as e:
        _log(pair_name, f"Error saving leg details: {e}")


def _remove_pair(pair_name: str):
    """Remove pair from all sections."""
    _get_state_mgr().remove_pair(pair_name)


def _update_position_field(pair_name: str, field: str, value, immediate: bool = False):
    """Update a specific field in a position - write only to individual pair file."""
    # LTP/MTM should never be persisted - they go to display cache
    if field in ('ltp', 'mtm'):
        return
    # Write only to per-pair file (not bridge_state.json)
    try:
        from bridge.pair_manager import get_pair_manager
        manager = get_pair_manager(pair_name)
        current_position = manager.get_state().get("position", {})
        current_position[field] = value
        manager.update_position(current_position)
    except Exception as e:
        # Log error but don't fail
        print(f"[StateWrappers] Error updating position field '{field}' for '{pair_name}': {e}")

