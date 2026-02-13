# bridge package - Per-pair state management for Bridge
"""
Bridge package for managing per-pair state with isolated JSON files.
"""

from bridge.pair_manager import PairStateManager, get_pair_manager, remove_pair_manager
from bridge.state_store import get_pairs_dir, get_global_state_file

__all__ = [
    "PairStateManager",
    "get_pair_manager",
    "remove_pair_manager",
    "get_pairs_dir",
    "get_global_state_file",
]

