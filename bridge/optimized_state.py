# bridge/optimized_state.py - Optimized State Management
"""
Optimized state management with:
- In-memory cache with dirty flag tracking
- Immediate writes for critical events
- Batched writes for non-critical updates
- Background flush thread
- Display data (LTP/MTM) separated from persisted data
- Single JSON file (no directory scanning)
"""

import json
import os
import stat
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Set
from collections import deque

# File locking support
if os.name == 'nt':  # Windows
    try:
        import msvcrt
        _HAS_FILE_LOCK = True
    except ImportError:
        _HAS_FILE_LOCK = False
else:  # Unix-like
    try:
        import fcntl
        _HAS_FILE_LOCK = True
    except ImportError:
        _HAS_FILE_LOCK = False

# Import STATE_DIR
try:
    from runtime_paths_live import STATE_DIR
except ImportError:
    parent_dir = Path(__file__).resolve().parent.parent
    import sys
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))
    from runtime_paths_live import STATE_DIR


def _ensure_permissions(path: Path, is_file: bool = False):
    """
    Ensure proper permissions on a file or directory.
    On Windows, sets read/write permissions for the current user.
    On Unix, sets 0o666 for files and 0o777 for directories.
    
    Args:
        path: Path to file or directory
        is_file: True if path is a file, False if directory
    """
    if not path.exists():
        return
    
    try:
        if os.name == 'nt':  # Windows
            # On Windows, chmod has limited effect, but we can still try
            # The main issue is usually file locks, not permissions
            # But we can make the file writable by removing read-only flag
            if is_file:
                # Remove read-only flag if set, ensure read/write
                current_mode = path.stat().st_mode
                # Remove read-only flag
                new_mode = current_mode | stat.S_IWRITE
                os.chmod(path, new_mode)
            else:
                # For directories, ensure we can read, write, and execute
                current_mode = path.stat().st_mode
                new_mode = current_mode | stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC
                os.chmod(path, new_mode)
        else:  # Unix-like
            # Set permissions: 0o666 for files, 0o777 for directories
            mode = 0o666 if is_file else 0o777
            os.chmod(path, mode)
    except (OSError, PermissionError) as e:
        # If we can't set permissions, log but don't fail
        # The file might be locked or we might not have admin rights
        print(f"[StateManager] Warning: Could not set permissions on {path}: {e}")


def fix_state_file_permissions(state_file_path: Path):
    """
    Fix permissions on an existing state file and its directory.
    This can be called to repair permissions on existing files.
    
    Args:
        state_file_path: Path to the state file
    """
    try:
        # Fix directory permissions
        if state_file_path.parent.exists():
            _ensure_permissions(state_file_path.parent, is_file=False)
            print(f"[StateManager] Fixed permissions on directory: {state_file_path.parent}")
        
        # Fix file permissions
        if state_file_path.exists():
            _ensure_permissions(state_file_path, is_file=True)
            print(f"[StateManager] Fixed permissions on file: {state_file_path}")
        else:
            print(f"[StateManager] File does not exist yet: {state_file_path}")
    except Exception as e:
        print(f"[StateManager] Error fixing permissions: {e}")


class OptimizedStateManager:
    """
    Optimized state manager with smart caching and write batching.
    
    Architecture:
    - In-memory cache for fast reads
    - Dirty flag tracking for efficient writes
    - Immediate writes for critical events
    - Batched writes for non-critical updates
    - Display data (LTP/MTM) never persisted
    """
    
    def __init__(self, state_file: Path):
        self.state_file = Path(state_file)
        self.lock = threading.RLock()  # Reentrant lock for nested calls
        
        # In-memory cache
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_dirty = False
        self._cache_last_write = 0.0
        self._cache_file_mtime = 0.0
        
        # Display cache (LTP/MTM - never persisted)
        self._display_cache: Dict[str, Dict[str, Any]] = {}
        self._display_cache_lock = threading.Lock()
        
        # Track permission errors to avoid log spam
        self._last_permission_error_log = 0.0
        self._permission_error_log_interval = 60.0  # Log at most once per minute
        
        # Write batching
        self._batch_queue: deque = deque()
        self._batch_lock = threading.Lock()
        self._flush_thread: Optional[threading.Thread] = None
        self._flush_thread_running = False
        
        # Critical events that need immediate write
        self._critical_events = {
            'order_fill', 'position_exit', 'sl_update', 'tsl_update',
            'state_change', 'pair_create', 'pair_delete'
        }
        
        # Ensure state file directory exists with proper permissions
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            # Set permissions on directory
            _ensure_permissions(self.state_file.parent, is_file=False)
        except Exception as e:
            print(f"[StateManager] Warning: Could not create/set permissions on directory {self.state_file.parent}: {e}")
        
        # Fix permissions on existing file if it exists
        if self.state_file.exists():
            try:
                fix_state_file_permissions(self.state_file)
            except Exception as e:
                print(f"[StateManager] Warning: Could not fix permissions on existing file: {e}")
        
        # Load initial state
        self._load_state()
        
        # Start background flush thread
        self._start_flush_thread()
    
    def _load_state(self) -> Dict[str, Any]:
        """Load state from file into cache."""
        with self.lock:
            if not self.state_file.exists():
                default_state = {
                    "positions": {},
                    "states": {},
                    "pairCtx": {},
                    "metadata": {
                        "version": "2.0",
                        "created_at": datetime.now().isoformat(),
                        "last_updated": datetime.now().isoformat()
                    }
                }
                self._cache = default_state
                try:
                    self._write_file_immediate(default_state)
                    # Ensure permissions on newly created file
                    if self.state_file.exists():
                        _ensure_permissions(self.state_file, is_file=True)
                except Exception as e:
                    print(f"[StateManager] Warning: Could not write default state file: {e}")
                return default_state.copy()
            
            # Check for file lock before attempting to read
            is_locked, lock_reason = self._check_file_lock()
            if is_locked:
                # File is locked - use cache if available
                current_time = time.time()
                if (current_time - self._last_permission_error_log) > self._permission_error_log_interval:
                    print(f"[StateManager] WARNING: {lock_reason}")
                    print(f"[StateManager] Attempting to fix permissions and retry...")
                    self._last_permission_error_log = current_time
                    # Try to fix permissions
                    try:
                        self._fix_file_permissions_aggressive()
                    except Exception:
                        pass
                
                # Use cached state if available
                if self._cache is not None:
                    return self._cache.copy()
                # If no cache, return default state (don't try to write it)
                default_state = {
                    "states": {},
                    "metadata": {
                        "version": "3.0",
                        "created_at": datetime.now().isoformat(),
                        "last_updated": datetime.now().isoformat()
                    }
                }
                return default_state.copy()
            
            try:
                # Check file modification time
                file_mtime = self.state_file.stat().st_mtime
                
                # If cache is valid and file hasn't changed, return cache
                if (self._cache is not None and 
                    self._cache_file_mtime == file_mtime and
                    not self._cache_dirty):
                    return self._cache.copy()
                
                # Read from file with retry logic
                max_read_retries = 3
                read_attempt = 0
                while read_attempt < max_read_retries:
                    try:
                        with open(self.state_file, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if not content:
                                raise ValueError("Empty file")
                            
                            # Check for null bytes (corruption)
                            if '\x00' in content:
                                raise ValueError("File contains null bytes")
                            
                            # CRITICAL: Check for extra data after JSON (common corruption pattern)
                            # This happens when a write is interrupted or multiple writes overlap
                            try:
                                # Try to parse JSON and check if there's extra data
                                decoder = json.JSONDecoder()
                                state, idx = decoder.raw_decode(content)
                                
                                # Check if there's extra data after the JSON
                                remaining = content[idx:].strip()
                                if remaining:
                                    # There's extra data - this is corruption
                                    # Log the issue and use only the valid JSON part
                                    print(f"[StateManager] WARNING: JSON file has extra data after valid JSON (corruption detected)")
                                    print(f"[StateManager] Extra data starts at position {idx}, length: {len(remaining)}")
                                    print(f"[StateManager] Extra data preview: {remaining[:100]}...")
                                    # Use only the valid JSON part - this recovers from partial writes
                                    state = json.loads(content[:idx])
                                    # After reading valid JSON, rewrite the file to remove corruption
                                    print(f"[StateManager] Recovering by rewriting file with valid JSON only")
                                    try:
                                        self._write_file_immediate(state)
                                    except Exception as rewrite_err:
                                        print(f"[StateManager] Warning: Could not rewrite file after corruption recovery: {rewrite_err}")
                            except (json.JSONDecodeError, ValueError):
                                # If raw_decode fails, try normal parsing (might be recoverable)
                                try:
                                    state = json.loads(content)
                                except json.JSONDecodeError as json_err:
                                    # If normal parsing also fails, it's truly corrupted
                                    raise ValueError(f"JSON decode error: {json_err}") from json_err
                            
                            # Validate structure
                            if not isinstance(state, dict):
                                raise ValueError("Invalid state structure")
                            
                            # Ensure required keys exist (only states now)
                            if "states" not in state:
                                state["states"] = {}
                            if "metadata" not in state:
                                state["metadata"] = {}
                            
                            # Migrate old format: remove positions and pairCtx if present
                            if "positions" in state:
                                del state["positions"]
                            if "pairCtx" in state:
                                del state["pairCtx"]
                            
                            # Update cache
                            self._cache = state
                            self._cache_file_mtime = file_mtime
                            self._cache_dirty = False
                            
                            return state.copy()
                    except (PermissionError, OSError) as e:
                        import errno
                        error_code = getattr(e, 'errno', None)
                        if error_code == errno.EACCES or error_code == 13:  # Permission denied
                            read_attempt += 1
                            if read_attempt < max_read_retries:
                                # Wait before retry (exponential backoff)
                                wait_time = 0.1 * (2 ** (read_attempt - 1))
                                time.sleep(wait_time)
                                # Try to fix permissions before retry
                                try:
                                    self._fix_file_permissions_aggressive()
                                except Exception:
                                    pass
                                continue
                            # All retries failed - use cache or default
                            current_time = time.time()
                            if (current_time - self._last_permission_error_log) > self._permission_error_log_interval:
                                print(f"[StateManager] Permission denied accessing {self.state_file} (after {max_read_retries} retries)")
                                self._last_permission_error_log = current_time
                            # Try to use existing cache if available
                            if self._cache is not None:
                                return self._cache.copy()
                            # Fall through to create default
                        else:
                            # Other OSError - re-raise to be handled below
                            raise
                    break  # Success - exit retry loop
                    
            except (PermissionError, OSError) as e:
                # Permission denied - likely file is locked by another process
                import errno
                error_code = getattr(e, 'errno', None)
                if error_code == errno.EACCES or error_code == 13:  # Permission denied
                    # Try to use existing cache if available
                    if self._cache is not None:
                        return self._cache.copy()
                # Fall through to create default
                print(f"[StateManager] Error loading state: {e}, creating default")
            except (json.JSONDecodeError, IOError, ValueError) as e:
                # File corrupted or doesn't exist - create default
                print(f"[StateManager] Error loading state: {e}, creating default")
                default_state = {
                    "states": {},
                    "metadata": {
                        "version": "3.0",
                        "created_at": datetime.now().isoformat(),
                        "last_updated": datetime.now().isoformat()
                    }
                }
                self._cache = default_state
                try:
                    self._write_file_immediate(default_state)
                    # Ensure permissions on newly created file
                    if self.state_file.exists():
                        _ensure_permissions(self.state_file, is_file=True)
                except Exception as e:
                    print(f"[StateManager] Warning: Could not write default state file: {e}")
                return default_state.copy()
    
    def get_state(self) -> Dict[str, Any]:
        """Get full state (persisted data only, no LTP/MTM)."""
        with self.lock:
            return self._load_state()
    
    def get_positions(self) -> Dict[str, Dict[str, Any]]:
        """Get all positions - DEPRECATED: positions are now in individual pair files."""
        # Positions are no longer stored in bridge_state.json
        return {}
    
    def get_states(self) -> Dict[str, str]:
        """Get all pair states."""
        with self.lock:
            state = self._load_state()
            if state is None:
                return {}
            return state.get("states", {}).copy()
    
    def get_pair_ctx(self) -> Dict[str, Dict[str, Any]]:
        """Get all pair contexts - DEPRECATED: pairCtx is now in individual pair files."""
        # PairCtx is no longer stored in bridge_state.json
        return {}
    
    def get_position(self, pair_name: str) -> Optional[Dict[str, Any]]:
        """Get position for a specific pair - DEPRECATED: positions are now in individual pair files."""
        return {}
    
    def get_pair_state(self, pair_name: str) -> Optional[str]:
        """Get state for a specific pair."""
        with self.lock:
            state = self._load_state()
            if state is None:
                return None
            return state.get("states", {}).get(pair_name)
    
    def get_pair_ctx_single(self, pair_name: str) -> Optional[Dict[str, Any]]:
        """Get pair context for a specific pair - DEPRECATED: pairCtx is now in individual pair files."""
        return {}
    
    def set_position(self, pair_name: str, position: Dict[str, Any], immediate: bool = False):
        """
        Set position for a pair - DEPRECATED: positions are now written to individual pair files.
        This method is a no-op to maintain backward compatibility.
        """
        # Positions are no longer stored in bridge_state.json
        # They are written directly to individual pair files via state_wrappers
        pass
    
    def set_pair_state(self, pair_name: str, state_str: str, immediate: bool = False):
        """
        Set state for a pair in bridge_state.json (kept in sync with individual pair files).
        After refactoring, individual pair files are the source of truth, but we keep
        bridge_state.json updated for quick metadata access.
        """
        with self.lock:
            state = self._load_state()
            if "states" not in state:
                state["states"] = {}
            
            # Always allow state updates - individual pair files are source of truth
            # bridge_state.json is just kept in sync for metadata/backup
            state["states"][pair_name] = state_str
            state["metadata"]["last_updated"] = datetime.now().isoformat()
            self._cache = state
            self._cache_dirty = True
            
            if immediate:
                try:
                    self._write_file_immediate(state)
                except (PermissionError, OSError) as write_err:
                    # Write failed but cache is updated - log but don't block
                    current_time = time.time()
                    if (current_time - self._last_permission_error_log) > self._permission_error_log_interval:
                        print(f"[StateManager] WARNING: Failed to write state immediately (using cache): {write_err}")
                        self._last_permission_error_log = current_time
                    # Cache is still updated, so reads will work
                    # Write will be retried on next update or via batch queue
            else:
                self._schedule_write(state)
    
    def set_pair_ctx(self, pair_name: str, ctx: Dict[str, Any], immediate: bool = False):
        """
        Set pair context for a pair - DEPRECATED: pairCtx is now written to individual pair files.
        This method is a no-op to maintain backward compatibility.
        """
        # PairCtx is no longer stored in bridge_state.json
        # It is written directly to individual pair files via state_wrappers
        pass
    
    def update_position_field(self, pair_name: str, field: str, value: Any, immediate: bool = False):
        """
        Update a specific field in a position - DEPRECATED: positions are now in individual pair files.
        This method is a no-op to maintain backward compatibility.
        """
        # Positions are no longer stored in bridge_state.json
        pass
    
    def update_pair_ctx_field(self, pair_name: str, field: str, value: Any, immediate: bool = False):
        """
        Update a specific field in pair context - DEPRECATED: pairCtx is now in individual pair files.
        This method is a no-op to maintain backward compatibility.
        """
        # PairCtx is no longer stored in bridge_state.json
        pass
    
    def remove_pair(self, pair_name: str):
        """Remove a pair from states (positions and pairCtx are in individual files)."""
        with self.lock:
            state = self._load_state()
            removed = False
            
            if "states" in state:
                if pair_name in state["states"]:
                    state["states"].pop(pair_name, None)
                    removed = True
            
            # CRITICAL: Always write if we removed something
            if removed:
                state["metadata"]["last_updated"] = datetime.now().isoformat()
                self._cache = state
                self._cache_dirty = True
                
                # CRITICAL: Immediate write for deletion - must succeed
                try:
                    self._write_file_immediate(state)
                    print(f"[StateManager] Removed {pair_name} from states and wrote to file")
                except Exception as e:
                    print(f"[StateManager] ERROR: Failed to write deletion of {pair_name} to file: {e}")
                    import traceback
                    traceback.print_exc()
                    # Still invalidate cache even if write failed
                
                # CRITICAL: Invalidate cache to force re-read from file
                # This ensures subsequent reads get the updated state
                self._cache_file_mtime = 0.0
                self._cache = None  # Clear cache completely
                print(f"[StateManager] Removed {pair_name} from all sections and invalidated cache")
            else:
                # Pair not found in cache - but might still exist in file
                # Force a fresh read and check again
                print(f"[StateManager] Pair {pair_name} not found in cache, forcing fresh read from file")
                self._cache_file_mtime = 0.0
                self._cache = None
                
                # Re-read from file to check if pair exists there
                fresh_state = self._load_state()
                fresh_removed = False
                fresh_removed_from = []
                
                if "positions" in fresh_state and pair_name in fresh_state["positions"]:
                    fresh_state["positions"].pop(pair_name, None)
                    fresh_removed = True
                    fresh_removed_from.append("positions")
                
                if "states" in fresh_state and pair_name in fresh_state["states"]:
                    fresh_state["states"].pop(pair_name, None)
                    fresh_removed = True
                    fresh_removed_from.append("states")
                
                if "pairCtx" in fresh_state and pair_name in fresh_state["pairCtx"]:
                    fresh_state["pairCtx"].pop(pair_name, None)
                    fresh_removed = True
                    fresh_removed_from.append("pairCtx")
                
                if fresh_removed:
                    fresh_state["metadata"]["last_updated"] = datetime.now().isoformat()
                    self._cache = fresh_state
                    self._cache_dirty = True
                    
                    try:
                        self._write_file_immediate(fresh_state)
                        print(f"[StateManager] Removed {pair_name} from file (fresh read): {', '.join(fresh_removed_from)}")
                    except Exception as e:
                        print(f"[StateManager] ERROR: Failed to write deletion of {pair_name} to file (fresh read): {e}")
                        import traceback
                        traceback.print_exc()
                    
                    self._cache_file_mtime = 0.0
                    self._cache = None
                else:
                    print(f"[StateManager] Pair {pair_name} not found in file either (already removed)")
            
            # Also remove from display cache
            with self._display_cache_lock:
                self._display_cache.pop(pair_name, None)
    
    def invalidate_cache(self):
        """Invalidate cache to force fresh read from file on next access."""
        with self.lock:
            self._cache_file_mtime = 0.0
            self._cache_dirty = False
            print(f"[StateManager] Cache invalidated - next read will be from file")
    
    def clear_all_state(self):
        """Clear all state and cache - used for factory reset."""
        with self.lock:
            # Clear cache
            self._cache = None
            self._cache_dirty = False
            self._cache_file_mtime = 0.0
            
            # Clear display cache
            with self._display_cache_lock:
                self._display_cache.clear()
            
            # Clear batch queue
            with self._batch_lock:
                self._batch_queue.clear()
            
            # Create empty state
            empty_state = {
                "positions": {},
                "states": {},
                "pairCtx": {},
                "metadata": {
                    "version": "2.0",
                    "created_at": datetime.now().isoformat(),
                    "last_updated": datetime.now().isoformat()
                }
            }
            
            # Write empty state immediately
            try:
                self._write_file_immediate(empty_state)
                # Update cache to empty state
                self._cache = empty_state
                self._cache_dirty = False
                print(f"[StateManager] Cleared all state and wrote empty state file")
            except Exception as e:
                print(f"[StateManager] Error writing empty state after clear: {e}")
                # Still update cache even if write fails
                self._cache = empty_state
    
    def set_display_data(self, pair_name: str, ltp: Optional[float] = None, mtm: Optional[float] = None):
        """Set display data (LTP/MTM) - never persisted."""
        with self._display_cache_lock:
            if pair_name not in self._display_cache:
                self._display_cache[pair_name] = {}
            if ltp is not None:
                self._display_cache[pair_name]["ltp"] = ltp
            if mtm is not None:
                self._display_cache[pair_name]["mtm"] = mtm
    
    def get_display_data(self, pair_name: str) -> Dict[str, Any]:
        """Get display data (LTP/MTM) for a pair."""
        with self._display_cache_lock:
            return self._display_cache.get(pair_name, {}).copy()
    
    def get_all_display_data(self) -> Dict[str, Dict[str, Any]]:
        """Get all display data."""
        with self._display_cache_lock:
            return {k: v.copy() for k, v in self._display_cache.items()}
    
    def _check_file_lock(self):
        """
        Check if the file is locked by another process.
        Uses a non-intrusive method that doesn't require exclusive access.
        
        Returns:
            (is_locked, reason) tuple
        """
        if not self.state_file.exists():
            return (False, "File does not exist")
        
        try:
            # Try to open the file in read mode first (non-exclusive)
            # This is less intrusive than exclusive access
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    # Try to read a small amount to ensure file is accessible
                    f.read(1)
                    f.seek(0)  # Reset to beginning
                # File is readable - not locked
                return (False, "File is not locked")
            except (OSError, PermissionError) as e:
                import errno
                error_code = getattr(e, 'errno', None)
                if error_code == errno.EACCES or error_code == 13:
                    # Permission denied - likely locked
                    return (True, f"File is locked (Permission denied)")
                # Other error - might be locked
                return (True, f"File may be locked ({e})")
        except Exception as e:
            # If we can't even check, assume it might be locked
            return (True, f"Could not check lock status: {e}")
    
    def _fix_file_permissions_aggressive(self):
        """Aggressively fix file permissions, including removing read-only flags."""
        if not self.state_file.exists():
            return
        
        try:
            # On Windows, remove read-only attribute
            if os.name == 'nt':
                import subprocess
                # Use attrib command to remove read-only flag
                try:
                    subprocess.run(
                        ['attrib', '-R', str(self.state_file)],
                        check=True,
                        capture_output=True,
                        timeout=2
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
                    pass  # Fall back to os.chmod
            
            # Use chmod to set permissions
            _ensure_permissions(self.state_file, is_file=True)
            
            # Verify we can write
            try:
                with open(self.state_file, 'r+') as f:
                    f.read(1)  # Try to read
                    f.seek(0, 2)  # Seek to end
            except (OSError, PermissionError) as e:
                raise PermissionError(f"File still not writable after permission fix: {e}")
        except Exception as e:
            raise PermissionError(f"Failed to fix file permissions: {e}")
    
    def _write_file_immediate(self, state: Dict[str, Any], retries: int = 5):
        """
        Write state to file immediately (for critical events).
        Aggressively fixes permissions and handles file locks.
        
        Args:
            state: State dictionary to write
            retries: Number of retry attempts (default: 5)
        
        Raises:
            PermissionError: If file cannot be written after all retries
            OSError: For other file system errors
        """
        import errno
        
        # Check if file is locked before attempting write
        is_locked, lock_reason = self._check_file_lock()
        if is_locked:
            print(f"[StateManager] WARNING: File appears to be locked: {lock_reason}")
            print(f"[StateManager] Attempting to fix permissions and retry...")
            try:
                self._fix_file_permissions_aggressive()
            except PermissionError as e:
                print(f"[StateManager] Could not fix permissions: {e}")
        
        last_error = None
        for attempt in range(retries):
            try:
                # Ensure directory and permissions before write
                self.state_file.parent.mkdir(parents=True, exist_ok=True)
                _ensure_permissions(self.state_file.parent, is_file=False)
                
                # Sanitize state to remove non-serializable objects (functions, etc.)
                sanitized_state = self._sanitize_for_json(state)
                
                # Direct write (simpler and more reliable, especially on Windows)
                # We don't use temp file approach as it causes issues on Windows
                # With retries and error handling, direct write is sufficient
                
                # Fix permissions on target file first
                if self.state_file.exists():
                    try:
                        self._fix_file_permissions_aggressive()
                    except PermissionError:
                        pass  # Continue anyway
                
                # CRITICAL: Use atomic write to prevent corruption
                # Write to temp file first, then rename atomically
                # This ensures the file is never in a partially-written state
                # Use unique temp file name per attempt to avoid conflicts
                import uuid
                temp_file = self.state_file.with_suffix(f'.tmp.{uuid.uuid4().hex[:8]}')
                
                try:
                    # CRITICAL: Ensure directory exists right before creating temp file
                    # This handles cases where directory might be deleted between retries
                    self.state_file.parent.mkdir(parents=True, exist_ok=True)
                    _ensure_permissions(self.state_file.parent, is_file=False)
                    
                    # Clean up any existing temp files from previous failed writes
                    # Remove all .tmp.* files in the directory
                    try:
                        for tmp_file in self.state_file.parent.glob(f'{self.state_file.stem}.tmp.*'):
                            try:
                                if tmp_file.exists():
                                    tmp_file.unlink()
                            except Exception:
                                pass  # Ignore cleanup errors for individual files
                    except Exception:
                        pass  # Ignore cleanup errors
                    
                    # Write to temp file first
                    with open(temp_file, 'w', encoding='utf-8') as f:
                        json.dump(sanitized_state, f, indent=2, default=self._json_dt)
                        f.flush()  # Ensure data is written to buffer
                        if hasattr(os, 'fsync'):
                            try:
                                os.fsync(f.fileno())  # Force write to disk
                            except (OSError, AttributeError):
                                pass  # fsync may not be available on all systems
                    
                    # CRITICAL: Verify temp file was created and has content
                    if not temp_file.exists():
                        raise OSError(f"Temp file was not created: {temp_file}")
                    
                    if temp_file.stat().st_size == 0:
                        temp_file.unlink(missing_ok=True)
                        raise ValueError("Temp file is empty after write")
                    
                    # CRITICAL: File is now closed, validate it
                    # This prevents writing corrupted JSON
                    try:
                        with open(temp_file, 'r', encoding='utf-8') as f:
                            json.load(f)  # Validate JSON is parseable
                    except FileNotFoundError:
                        # Temp file was deleted (possibly by antivirus or another process)
                        raise OSError(f"Temp file was deleted before validation: {temp_file}")
                    except json.JSONDecodeError as json_err:
                        # Temp file has invalid JSON - don't use it
                        temp_file.unlink(missing_ok=True)
                        raise ValueError(f"Generated invalid JSON in temp file: {json_err}") from json_err
                    
                    # Atomic rename - this is the critical step that prevents corruption
                    # On Windows, we need to handle the case where target file might be locked
                    try:
                        # Try to remove old file first (if it exists) to avoid rename issues on Windows
                        if self.state_file.exists():
                            try:
                                self.state_file.unlink()
                            except (OSError, PermissionError):
                                # File might be locked, try to fix permissions
                                try:
                                    self._fix_file_permissions_aggressive()
                                    self.state_file.unlink()
                                except Exception:
                                    pass  # Continue anyway, rename might still work
                        
                        # Atomic rename - verify temp file still exists
                        if not temp_file.exists():
                            raise OSError(f"Temp file was deleted before rename: {temp_file}")
                        
                        # Atomic rename
                        temp_file.replace(self.state_file)
                    except (OSError, PermissionError) as rename_err:
                        # If rename fails, try direct copy as fallback
                        import shutil
                        try:
                            # Verify temp file exists before copying
                            if not temp_file.exists():
                                raise OSError(f"Temp file was deleted before copy: {temp_file}")
                            shutil.copy2(temp_file, self.state_file)
                            temp_file.unlink(missing_ok=True)
                        except Exception as copy_err:
                            # Clean up temp file on failure
                            temp_file.unlink(missing_ok=True)
                            # If both fail, raise the rename error
                            raise rename_err from copy_err
                    
                    # Success - break out of retry loop
                    last_error = None
                    break
                except Exception as write_err:
                    # Clean up temp file on any error
                    try:
                        temp_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                    # Re-raise to be handled by retry logic
                    raise write_err
                except (OSError, PermissionError) as e:
                    error_code = getattr(e, 'errno', None)
                    last_error = e
                    
                    if error_code == errno.EACCES or error_code == 13:  # Permission denied
                        if attempt < retries - 1:
                            # Try to fix permissions
                            try:
                                self._fix_file_permissions_aggressive()
                            except PermissionError as perm_err:
                                print(f"[StateManager] Permission fix attempt failed: {perm_err}")
                            
                            # Wait before retry (exponential backoff)
                            wait_time = 0.2 * (2 ** attempt)
                            # Only log on first attempt to avoid spam
                            if attempt == 0:
                                current_time = time.time()
                                if (current_time - self._last_permission_error_log) > self._permission_error_log_interval:
                                    print(f"[StateManager] Permission denied writing to {self.state_file}, fixing permissions and retrying...")
                                    self._last_permission_error_log = current_time
                            time.sleep(wait_time)
                            continue
                    else:
                        # Other error - will be re-raised if all retries fail
                        if attempt < retries - 1:
                            wait_time = 0.2 * (2 ** attempt)
                            print(f"[StateManager] Error writing file (attempt {attempt + 1}/{retries}): {e}, retrying in {wait_time:.2f}s...")
                            time.sleep(wait_time)
                            continue
                
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    wait_time = 0.2 * (2 ** attempt)
                    print(f"[StateManager] Error writing state file (attempt {attempt + 1}/{retries}): {e}, retrying in {wait_time:.2f}s...")
                    time.sleep(wait_time)
                    continue
        
        # If we still have an error after all retries, raise it
        if last_error is not None:
            error_msg = f"[StateManager] CRITICAL: Failed to write state file after {retries} attempts: {last_error}"
            print(error_msg)
            print(f"[StateManager] File path: {self.state_file}")
            print(f"[StateManager] Please check:")
            print(f"[StateManager]   1. No other instance of Bridge.py is running")
            print(f"[StateManager]   2. File is not open in another program")
            print(f"[StateManager]   3. You have write permissions to the directory")
            print(f"[StateManager]   4. Antivirus is not blocking access")
            raise PermissionError(error_msg) from last_error
        
        # Update cache metadata and ensure permissions (only if write succeeded)
        try:
            self._cache_dirty = False
            self._cache_last_write = time.time()
            if self.state_file.exists():
                self._cache_file_mtime = self.state_file.stat().st_mtime
                # Ensure file has proper permissions after write
                _ensure_permissions(self.state_file, is_file=True)
        except Exception as e:
            print(f"[StateManager] Warning: Could not update cache metadata: {e}")
    
    def _schedule_write(self, state: Dict[str, Any]):
        """Schedule a write (for batching)."""
        with self._batch_lock:
            # Keep only the latest state in queue
            if len(self._batch_queue) > 0:
                self._batch_queue.clear()
            self._batch_queue.append(state)
    
    def _flush_batch(self):
        """Flush batched writes to disk."""
        with self._batch_lock:
            if len(self._batch_queue) == 0:
                return
            
            # Get latest state from queue
            state = self._batch_queue[-1]
            self._batch_queue.clear()
        
        # Write to file
        try:
            self._write_file_immediate(state)
        except Exception as e:
            print(f"[StateManager] Error flushing batch: {e}")
    
    def _flush_thread_worker(self):
        """Background thread worker for flushing batched writes."""
        while self._flush_thread_running:
            time.sleep(5.0)  # Flush every 5 seconds
            if self._flush_thread_running:
                try:
                    self._flush_batch()
                except Exception as e:
                    print(f"[StateManager] Error in flush thread: {e}")
    
    def _start_flush_thread(self):
        """Start background flush thread."""
        if self._flush_thread is None or not self._flush_thread.is_alive():
            self._flush_thread_running = True
            self._flush_thread = threading.Thread(
                target=self._flush_thread_worker,
                daemon=True,
                name="StateFlushThread"
            )
            self._flush_thread.start()
    
    def shutdown(self):
        """Shutdown state manager (flush pending writes)."""
        self._flush_thread_running = False
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=2.0)
        
        # Final flush
        with self.lock:
            if self._cache_dirty:
                self._flush_batch()
    
    @staticmethod
    def _sanitize_for_json(obj):
        """
        Recursively sanitize data structure to remove non-serializable objects.
        Removes functions, methods, and other callables.
        """
        if isinstance(obj, dict):
            return {k: OptimizedStateManager._sanitize_for_json(v) 
                    for k, v in obj.items() 
                    if not callable(k) and not callable(v)}
        elif isinstance(obj, (list, tuple)):
            return [OptimizedStateManager._sanitize_for_json(item) 
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
                        sanitized[k] = OptimizedStateManager._sanitize_for_json(v)
                return sanitized if sanitized else None
            except:
                return None
        else:
            # Basic types that are JSON serializable
            return obj
    
    @staticmethod
    def _json_dt(obj):
        """
        JSON serializer for non-serializable objects.
        Handles datetime, functions, methods, and other types.
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif callable(obj):
            # Functions, methods, lambdas - skip them
            return None
        elif hasattr(obj, '__dict__'):
            # Objects with __dict__ - try to serialize as dict
            try:
                return {k: v for k, v in obj.__dict__.items() 
                        if not callable(v) and not k.startswith('_')}
            except:
                return None
        else:
            # For other types, return None to skip
            return None


# Global state manager instance
_state_manager: Optional[OptimizedStateManager] = None
_state_manager_lock = threading.Lock()


def get_state_manager(state_file: Optional[Path] = None) -> OptimizedStateManager:
    """Get or create global state manager instance."""
    global _state_manager
    
    with _state_manager_lock:
        if _state_manager is None:
            if state_file is None:
                from runtime_paths_live import state_file as get_state_file
                state_file = get_state_file("bridge_state.json")
            _state_manager = OptimizedStateManager(state_file)
        return _state_manager

