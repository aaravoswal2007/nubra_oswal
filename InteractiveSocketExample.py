# Thin adapter for bridge/command_processor: same import name as Oswal.
# Nubra uses Main_strategy.get_socket_state; this re-exports for bridge compatibility.
from Main_strategy import socket_state, get_socket_state

def connect_socket_with_retry(max_retries=1, delay=1):
    """No-op for Nubra; bridge never needs to connect to XTS interactive socket."""
    return True
