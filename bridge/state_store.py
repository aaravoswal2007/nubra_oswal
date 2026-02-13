# bridge/state_store.py - State store utilities
"""
Utilities for managing global state and pair directories.
"""

import sys
from pathlib import Path

# Import STATE_DIR - handle both direct import and package import
try:
    from runtime_paths_live import STATE_DIR
except ImportError:
    # If direct import fails, try adding parent directory to path
    try:
        parent_dir = Path(__file__).resolve().parent.parent
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        from runtime_paths_live import STATE_DIR
    except ImportError as e:
        # If still failing, provide a helpful error message
        print(f"[StateStore] ERROR: Could not import runtime_paths_live. Parent dir: {parent_dir}, sys.path: {sys.path[:3]}", flush=True)
        raise ImportError(f"Could not import runtime_paths_live from {parent_dir}. Make sure the module exists.") from e


def get_pairs_dir() -> Path:
    """Get the directory for per-pair JSON files."""
    # print(f"[StateStore] get_pairs_dir: STATE_DIR = {STATE_DIR}", flush=True)
    # print(f"[StateStore] get_pairs_dir: STATE_DIR type = {type(STATE_DIR)}", flush=True)
    
    # Ensure STATE_DIR is a Path object
    if not isinstance(STATE_DIR, Path):
        state_dir_path = Path(STATE_DIR)
        # print(f"[StateStore] get_pairs_dir: Converted STATE_DIR to Path: {state_dir_path}", flush=True)
    else:
        state_dir_path = STATE_DIR
    
    # print(f"[StateStore] get_pairs_dir: STATE_DIR exists = {state_dir_path.exists()}", flush=True)
    # print(f"[StateStore] get_pairs_dir: STATE_DIR absolute = {state_dir_path.resolve()}", flush=True)
    
    # Ensure STATE_DIR exists first
    if not state_dir_path.exists():
        # print(f"[StateStore] get_pairs_dir: STATE_DIR does not exist, creating it...", flush=True)
        try:
            state_dir_path.mkdir(parents=True, exist_ok=True)
            # print(f"[StateStore] get_pairs_dir: STATE_DIR created successfully", flush=True)
        except Exception as e:
            print(f"[StateStore] get_pairs_dir: ERROR creating STATE_DIR: {e}", flush=True)
            import traceback
            traceback.print_exc()
            raise
    
    pairs_dir = state_dir_path / "pairs"
    # print(f"[StateStore] get_pairs_dir: pairs_dir = {pairs_dir}", flush=True)
    # print(f"[StateStore] get_pairs_dir: pairs_dir absolute = {pairs_dir.resolve()}", flush=True)
    try:
        pairs_dir.mkdir(parents=True, exist_ok=True)
        # print(f"[StateStore] get_pairs_dir: Directory created/verified: {pairs_dir}", flush=True)
        # print(f"[StateStore] get_pairs_dir: Directory exists after mkdir = {pairs_dir.exists()}", flush=True)
    except Exception as e:
        # print(f"[StateStore] get_pairs_dir: ERROR creating directory: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise
    return pairs_dir


def get_global_state_file() -> Path:
    """Get the path to the global bridge_state.json file."""
    return STATE_DIR / "bridge_state.json"


def list_all_pairs() -> list[str]:
    """List all pair names that have state files."""
    pairs_dir = get_pairs_dir()
    if not pairs_dir.exists():
        return []
    
    pairs = []
    for file_path in pairs_dir.glob("*.json"):
        # Skip temp files
        if file_path.suffix == ".json" and not file_path.name.endswith(".tmp"):
            pair_name = file_path.stem
            pairs.append(pair_name)
    
    return sorted(pairs)

