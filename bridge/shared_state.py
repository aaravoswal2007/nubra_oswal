"""
Shared global state for Bridge.
Contains all global variables, locks, and shared data structures.
"""
import threading


# =============== Shared state ===============
# These are module-level globals that will be imported by Bridge.py
_events_lock = threading.Lock()
_events = []

# Removed unused locks to prevent deadlocks
_threads = {}     # pair -> Thread
_ctx = {}         # pair -> PairCtx

_cmd_lock = threading.Lock()
_cmd = {}         # pair -> "squareoff"

_resume_store_lock = threading.Lock()
_resume_store = {}  # persisted resume states

# progress de-dupe: pair -> {"entry": last_idx, "exit": last_idx}
_progress_lock = threading.Lock()
_progress_seen = {}

# JSON file lock to prevent concurrent access
_json_lock = threading.Lock()

# Track deleted pairs to prevent them from reappearing in snapshots
_deleted_pairs_lock = threading.Lock()
_deleted_pairs = set()  # Set of pair names that have been deleted

# Resume queue for market hours
_resume_queue = set()  # pairs waiting for market hours to resume

_TL = threading.local()

# State manager instance (will be set by state_wrappers)
_state_mgr = None

