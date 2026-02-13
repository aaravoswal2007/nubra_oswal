"""
Time utilities and market hours handling for Bridge.
Handles time checks, auto-pause, and resume queue management.
"""
from datetime import datetime
from bridge.shared_state import _resume_queue
from bridge.utils import _log
from bridge.pair_operations import _resume_pair


def _time_reached(_t_str: str) -> bool:
    """Legacy stub - actual implementation is in bridge.utils._time_reached"""
    return False


def _auto_pause_all_running():
    """Auto-pause all running pairs - currently disabled"""
    return None


def _check_resume_queue():
    """Check if any queued resumes can now proceed (market hours)"""
    if not _resume_queue:
        return
    
    now = datetime.now().time()
    # Market hours: 9:15 AM to 3:30 PM
    market_open_time = now.hour > 9 or (now.hour == 9 and now.minute >= 15)
    market_close_time = now.hour > 15 or (now.hour == 15 and now.minute >= 30)
    within_market_hours = market_open_time and not market_close_time
    
    if within_market_hours:
        pairs_to_resume = list(_resume_queue)
        _resume_queue.clear()
        
        for pair_name in pairs_to_resume:
            _log(pair_name, "Processing queued resume - market hours now open")
            _resume_pair(pair_name)

