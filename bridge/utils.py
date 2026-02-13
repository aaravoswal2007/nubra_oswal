"""
Utility functions for Bridge.
Includes logging, time utilities, parsing, and helper functions.
"""
import logging
import json
import inspect
import os
from datetime import datetime
from runtime_paths_live import daily_log


# Custom filter to suppress "packet queue is empty, aborting" messages
class PacketQueueFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        # Suppress various forms of packet queue messages
        return not any(phrase in message.lower() for phrase in [
            "packet queue is empty, aborting",
            "packet queue is empty",
            "aborting",
            "queue is empty"
        ])


def setup_logging():
    """Setup logging configuration."""
    logging.basicConfig(
        filename=daily_log("bridge"),
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    log = logging.getLogger("bridge")
    
    # Add filter to suppress packet queue messages
    packet_filter = PacketQueueFilter()
    logging.getLogger().addFilter(packet_filter)
    
    # Also filter the root logger to catch all logging
    logging.getLogger().addFilter(packet_filter)
    
    return log


def _json_dt(o):
    """JSON serializer helper for datetime objects."""
    if isinstance(o, datetime):
        return o.isoformat()
    return o


def _now() -> str:
    """Get current time as HH:MM:SS string."""
    return datetime.now().strftime("%H:%M:%S")


def _log(pair, msg, logger=None, file_name=None):
    """
    Log a message with pair name and file name prefix.
    
    Args:
        pair: Pair name or "SYSTEM"
        msg: Message to log
        logger: Optional logger instance (uses default if None)
        file_name: Optional file name (auto-detected if None)
    """
    from bridge.shared_state import _events_lock, _events
    from bridge.io import _emit
    
    # Auto-detect file name from caller if not provided
    if file_name is None:
        try:
            # Get the caller's frame stack
            frame = inspect.currentframe()
            # Walk up the stack to find the first non-utils file
            stack = inspect.stack()
            for frame_info in stack[1:]:  # Skip this function
                caller_file = frame_info.filename
                # Only use if it's not this file or bridge/__init__.py
                if 'bridge/utils.py' not in caller_file and 'bridge/__init__.py' not in caller_file:
                    file_name = os.path.basename(caller_file)
                    break
            if file_name is None:
                # Fallback: use the immediate caller
                if len(stack) > 1:
                    file_name = os.path.basename(stack[1].filename)
                else:
                    file_name = "unknown"
        except Exception:
            file_name = "unknown"
    
    if logger is None:
        logger = logging.getLogger("bridge")
    
    # Format: [time] [pair] [file] message
    line = f"[{_now()}] [{pair}] [{file_name}] {msg}"
    print(f"[LOG] {line}", flush=True)  # Also print to stdout for debugging
    logger.info(f"[{pair}] [{file_name}] {msg}")
    # Also emit as log event so frontend sees it
    _emit({"type": "log", "line": line})
    with _events_lock:
        _events.append(line)


def _parse_dt(v):
    """Parse datetime from string or return original value."""
    try:
        return datetime.fromisoformat(v)
    except Exception:
        return v


def _deserialize_state(s):
    """Deserialize state from JSON string or dict."""
    if not isinstance(s, dict):
        return s
    out = dict(s)
    if "deadline" in out and isinstance(out["deadline"], str):
        out["deadline"] = _parse_dt(out["deadline"])
    return out


def _side_text(is_long: bool) -> str:
    """Convert boolean to side text."""
    return "BUY" if is_long else "SELL"


def _time_reached(t_str: str) -> bool:
    """Check if current time has reached the specified time string (HH:MM:SS)."""
    if not t_str:
        return False
    try:
        now = datetime.now()
        target = datetime.strptime(t_str, "%H:%M:%S").replace(
            year=now.year, month=now.month, day=now.day
        )
        return now >= target
    except:
        return False

