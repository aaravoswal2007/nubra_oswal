# Main_strategy.py - Nubra: same exports as Oswal, no XTS sockets.
# Bridge and pair_threading import leg1_force_exit, leg2_force_exit, leg2_step, leg2_start, leg1_step_start_time, wait_for_spot.
import logging
import threading

if hasattr(threading, "_DummyThread"):
    threading._DummyThread.__del__ = lambda self: None

from marketdata_store import market_data

from strategy_helpers import wait_for_spot
from leg1 import leg1_step_start_time, leg1_force_exit
from leg2 import leg2_start, leg2_step, leg2_force_exit

# Nubra: no InteractiveSocketExample; use in-memory fallback for socket_state (leg2 may reference it).
socket_state = {}
_socket_lock = threading.Lock()

def get_socket_state():
    with _socket_lock:
        return dict(socket_state)

# Logger setup (same API as Oswal)
from runtime_paths_live import daily_log
logging.basicConfig(
    filename=daily_log("strategy"),
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
log = logging.getLogger("strategy")

# Nubra: do NOT start MarketdataSocketExample or InteractiveSocketExample.
# Market data is provided by subscribe_orderbook (started separately or by frontend).
# All leg1 and leg2 functions are in leg1.py and leg2.py; helpers in strategy_helpers, tsl_*, position_management.
