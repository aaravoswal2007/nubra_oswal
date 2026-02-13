# bridge/pair_manager.py - Per-pair state management
"""
Per-pair state manager with isolated JSON files and per-pair locking.
Each pair has its own JSON file and lock, enabling parallel updates.
"""

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Import STATE_DIR - handle both direct import and package import
_STATE_DIR = None
try:
    from runtime_paths_live import STATE_DIR as _STATE_DIR_IMPORTED
    _STATE_DIR = _STATE_DIR_IMPORTED
except ImportError:
    # If direct import fails, try adding parent directory to path
    try:
        parent_dir = Path(__file__).resolve().parent.parent
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        from runtime_paths_live import STATE_DIR as _STATE_DIR_IMPORTED
        _STATE_DIR = _STATE_DIR_IMPORTED
    except ImportError as e:
        # If still failing, provide a helpful error message but don't crash
        parent_dir = Path(__file__).resolve().parent.parent
        error_msg = f"[PairManager] ERROR: Could not import runtime_paths_live. Parent dir: {parent_dir}"
        print(error_msg, flush=True)
        print(f"[PairManager] sys.path (first 5): {sys.path[:5]}", flush=True)
        # Re-raise with more context
        raise ImportError(f"Could not import runtime_paths_live from {parent_dir}. Make sure the module exists. Error: {e}") from e

# Make STATE_DIR available at module level
STATE_DIR = _STATE_DIR


class PairStateManager:
    """Manages state for a single trading pair with isolated file and lock."""
    
    def __init__(self, pair_name: str, pairs_dir: Optional[Path] = None):
        """
        Initialize a pair state manager.
        
        Args:
            pair_name: Name of the trading pair
            pairs_dir: Directory for pair files (defaults to STATE_DIR / "pairs")
        """
        self.pair_name = pair_name
        self.lock = threading.Lock()  # Per-pair lock
        
        # Set up file paths
        if pairs_dir is None:
            pairs_dir = STATE_DIR / "pairs"
        self.pairs_dir = Path(pairs_dir)
        # print(f"[PairManager] {pair_name}: Initializing, pairs_dir={self.pairs_dir}, STATE_DIR={STATE_DIR}", flush=True)
        # print(f"[PairManager] {pair_name}: pairs_dir absolute = {self.pairs_dir.resolve()}", flush=True)
        # print(f"[PairManager] {pair_name}: STATE_DIR exists = {STATE_DIR.exists() if STATE_DIR else 'None'}", flush=True)
        try:
            self.pairs_dir.mkdir(parents=True, exist_ok=True)
            # print(f"[PairManager] {pair_name}: Directory created/verified: {self.pairs_dir}", flush=True)
            # print(f"[PairManager] {pair_name}: Directory exists after mkdir = {self.pairs_dir.exists()}", flush=True)
        except Exception as e:
            print(f"[PairManager] {pair_name}: ERROR creating directory {self.pairs_dir}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            raise
        self.file_path = self.pairs_dir / f"{pair_name}.json"
        # print(f"[PairManager] {pair_name}: File path will be: {self.file_path}", flush=True)
        
        # In-memory cache
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[datetime] = None
        self._cache_ttl = 1.0  # Cache for 1 second
    
    def get_state(self, use_cache: bool = True) -> Dict[str, Any]:
        """
        Get pair state, optionally using cache.
        
        Args:
            use_cache: Whether to use cached state if available and fresh
        
        Returns:
            Copy of the pair state dictionary
        """
        with self.lock:
            # Check cache validity
            if use_cache and self._cache and self._cache_time:
                age = (datetime.now() - self._cache_time).total_seconds()
                if age < self._cache_ttl:
                    return self._cache.copy()
            
            # Read from file
            state = self._read_file()
            self._cache = state
            self._cache_time = datetime.now()
            return state.copy()
    
    def update_state(self, updates: Dict[str, Any]) -> None:
        """
        Update pair state atomically.
        
        Args:
            updates: Dictionary of updates to apply to state
        """
        with self.lock:
            try:
                # print(f"[PairManager] {self.pair_name}: update_state called with updates: {list(updates.keys())}", flush=True)
                state = self._read_file()
                # print(f"[PairManager] {self.pair_name}: Current state keys: {list(state.keys())}", flush=True)
                # Deep merge updates
                self._deep_update(state, updates)
                state["metadata"]["last_updated"] = datetime.now().isoformat()
                # print(f"[PairManager] {self.pair_name}: Writing updated state to file...", flush=True)
                # print(f"[PairManager] {self.pair_name}: State to write has keys: {list(state.keys())}", flush=True)
                self._write_file(state)
                self._cache = state
                self._cache_time = datetime.now()
                # Verify file was written
                file_exists = self.file_path.exists()
                file_size = self.file_path.stat().st_size if file_exists else 0
                # print(f"[PairManager] {self.pair_name}: State updated successfully, file exists: {file_exists}, file size: {file_size} bytes, path: {self.file_path}", flush=True)
                if not file_exists:
                    print(f"[PairManager] {self.pair_name}: ERROR - File does not exist after write!", flush=True)
                elif file_size == 0:
                    print(f"[PairManager] {self.pair_name}: ERROR - File exists but is empty (0 bytes)!", flush=True)
                    # Try to read it back to see what's there
                    try:
                        with open(self.file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            print(f"[PairManager] {self.pair_name}: File content: {repr(content[:100])}", flush=True)
                    except Exception as e:
                        print(f"[PairManager] {self.pair_name}: Could not read file back: {e}", flush=True)
            except Exception as e:
                print(f"[PairManager] {self.pair_name}: ERROR in update_state: {e}", flush=True)
                import traceback
                traceback.print_exc()
                raise
    
    def update_position(self, position: Dict[str, Any]) -> None:
        """Update position data for this pair."""
        self.update_state({"position": position})
    
    def update_pair_ctx(self, pair_ctx: Dict[str, Any]) -> None:
        """Update pair context for this pair."""
        self.update_state({"pairCtx": pair_ctx})
    
    def set_state_str(self, state_str: str) -> None:
        """Set the state string (e.g., "Running", "Paused")."""
        self.update_state({"state": state_str})
    
    def get_last_updated(self) -> datetime:
        """Get last update timestamp."""
        state = self.get_state()
        return datetime.fromisoformat(state["metadata"]["last_updated"])
    
    def delete(self) -> None:
        """Delete pair state file and clear cache."""
        with self.lock:
            if self.file_path.exists():
                try:
                    self.file_path.unlink()
                except OSError as e:
                    print(f"[PairManager] Error deleting {self.file_path}: {e}")
            self._cache = None
            self._cache_time = None
    
    def _read_file(self) -> Dict[str, Any]:
        """Read pair state from file, return default if not found or corrupted."""
        if not self.file_path.exists():
            return self._default_state()
        
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return self._default_state()
                
                # Check for null bytes (corruption indicator)
                if '\x00' in content:
                    print(f"[PairManager] {self.pair_name}: File contains null bytes, using default state")
                    return self._default_state()
                
                return json.loads(content)
        except (json.JSONDecodeError, IOError, UnicodeDecodeError) as e:
            print(f"[PairManager] {self.pair_name}: Error reading file: {e}, using default state")
            return self._default_state()
    
    def _write_file(self, state: Dict[str, Any]) -> None:
        """Write pair state to file atomically using temp file + rename."""
        import uuid
        import time
        # Use unique temp file name to avoid conflicts with concurrent writes
        temp_file = self.file_path.with_suffix(f'.tmp.{uuid.uuid4().hex[:8]}')
        
        try:
            # Ensure directory exists
            self.pairs_dir.mkdir(parents=True, exist_ok=True)
            
            # Sanitize state to remove non-serializable objects (functions, etc.)
            sanitized_state = PairStateManager._sanitize_for_json(state)
            
            # Write to temp file
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(sanitized_state, f, indent=2, default=self._json_dt)
                f.flush()
                if hasattr(os, 'fsync'):
                    try:
                        os.fsync(f.fileno())
                    except (OSError, AttributeError):
                        pass
            
            # Atomic rename with retry logic for Windows permission issues
            max_attempts = 3
            rename_success = False
            
            for attempt in range(max_attempts):
                try:
                    # Try to remove old file first (if it exists) to avoid rename issues on Windows
                    if self.file_path.exists():
                        try:
                            self.file_path.unlink()
                        except (OSError, PermissionError):
                            # File might be locked - wait a bit and retry
                            if attempt < max_attempts - 1:
                                time.sleep(0.1 * (attempt + 1))
                                continue
                    
                    # Atomic rename
                    os.replace(temp_file, self.file_path)
                    rename_success = True
                    break
                except (OSError, PermissionError) as e:
                    if attempt < max_attempts - 1:
                        # Wait and retry (silently - don't log every retry)
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    # If all retries failed, try direct write as fallback
                    try:
                        # Only log if direct write also fails
                        with open(self.file_path, 'w', encoding='utf-8') as f:
                            json.dump(sanitized_state, f, indent=2, default=self._json_dt)
                            f.flush()
                            if hasattr(os, 'fsync'):
                                try:
                                    os.fsync(f.fileno())
                                except (OSError, AttributeError):
                                    pass
                        rename_success = True
                    except Exception as fallback_err:
                        # Clean up temp file
                        temp_file.unlink(missing_ok=True)
                        # Only log if both rename and direct write failed
                        print(f"[PairManager] {self.pair_name}: ERROR - Failed to write file after {max_attempts} attempts: {fallback_err}", flush=True)
                        raise fallback_err from e
            
            if not rename_success:
                temp_file.unlink(missing_ok=True)
                raise OSError(f"Failed to write pair file after {max_attempts} attempts")
            
            # Clean up temp file if rename succeeded
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass  # Ignore cleanup errors
            
            # Clear cache after write
            self._cache = None
            self._cache_time = None
                
        except Exception as e:
            print(f"[PairManager] {self.pair_name}: ERROR writing file {self.file_path}: {e}", flush=True)
            import traceback
            traceback.print_exc()
            # Clean up temp file on error
            if temp_file.exists():
                try:
                    temp_file.unlink()
                    # print(f"[PairManager] {self.pair_name}: Cleaned up temp file", flush=True)
                except Exception as cleanup_error:
                    print(f"[PairManager] {self.pair_name}: Error cleaning up temp file: {cleanup_error}", flush=True)
            raise
    
    def _default_state(self) -> Dict[str, Any]:
        """Return default state structure."""
        now = datetime.now().isoformat()
        return {
            "pair_name": self.pair_name,
            "position": {
                "symbol": "",
                "net_lots": 0,
                "side": "BUY",
                "lot_size": 1,
                "avg_entry": 0.0,
                "mtm": 0.0,
                "ltp": 0.0,
                "lots_filled": 0,
                "exit_filled": 0,
                "position_open": False
            },
            "state": "Initialized",
            "pairCtx": {},
            "metadata": {
                "created_at": now,
                "last_updated": now
            }
        }
    
    def _deep_update(self, base: Dict[str, Any], updates: Dict[str, Any]) -> None:
        """Deep update base dict with updates."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value
    
    @staticmethod
    def _json_dt(obj):
        """JSON serializer for datetime objects and other non-serializable types."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        # Handle functions, methods, and other callables
        if callable(obj):
            return None  # Remove functions from JSON
        # Try to serialize objects with __dict__
        if hasattr(obj, '__dict__'):
            try:
                return obj.__dict__
            except Exception:
                return None
        raise TypeError(f"Type {type(obj)} not serializable")
    
    @staticmethod
    def _sanitize_for_json(obj):
        """
        Recursively sanitize data structure to remove non-serializable objects.
        Removes functions, methods, and other callables.
        """
        if isinstance(obj, dict):
            return {k: PairStateManager._sanitize_for_json(v) 
                    for k, v in obj.items() 
                    if not callable(k) and not callable(v)}
        elif isinstance(obj, (list, tuple)):
            return [PairStateManager._sanitize_for_json(item) 
                    for item in obj 
                    if not callable(item)]
        elif callable(obj):
            # Skip functions, methods, lambdas
            return None
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif hasattr(obj, '__dict__') and not isinstance(obj, (str, int, float, bool, type(None))):
            # Try to serialize objects with __dict__, but skip if it's a basic type
            try:
                sanitized = {}
                for k, v in obj.__dict__.items():
                    if not callable(v) and not k.startswith('_'):
                        sanitized[k] = PairStateManager._sanitize_for_json(v)
                return sanitized if sanitized else None
            except:
                return None
        else:
            # Basic types that are JSON serializable
            return obj


# Global registry for pair managers
_pair_managers: Dict[str, PairStateManager] = {}
_pair_managers_lock = threading.Lock()


def get_pair_manager(pair_name: str) -> PairStateManager:
    """
    Get or create a PairStateManager for a pair.
    
    Args:
        pair_name: Name of the trading pair
    
    Returns:
        PairStateManager instance for the pair
    """
    with _pair_managers_lock:
        if pair_name not in _pair_managers:
            _pair_managers[pair_name] = PairStateManager(pair_name)
        return _pair_managers[pair_name]


def remove_pair_manager(pair_name: str) -> None:
    """
    Remove a PairStateManager (after pair deletion).
    
    Args:
        pair_name: Name of the trading pair to remove
    """
    with _pair_managers_lock:
        if pair_name in _pair_managers:
            manager = _pair_managers[pair_name]
            manager.delete()
            del _pair_managers[pair_name]


def get_all_pair_managers() -> Dict[str, PairStateManager]:
    """Get all active pair managers (for snapshot generation)."""
    try:
        with _pair_managers_lock:
            return _pair_managers.copy()
    except Exception as e:
        print(f"[PairManager] ERROR in get_all_pair_managers: {e}", flush=True)
        import traceback
        traceback.print_exc()
        # Return empty dict on error to prevent crashes
        return {}

