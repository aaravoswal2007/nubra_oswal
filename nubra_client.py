#!/usr/bin/env python3
"""
Phase 1: Nubra SDK client singleton.
Single shared client for order placement, market data, and order updates.
"""

import os
import threading
from pathlib import Path

# Load .env from nubra_oswal so NUBRA_ENV (and PHONE_NO, MPIN) are set before we read them
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass  # python-dotenv not installed; rely on shell env or SDK loading .env

_nubra_singleton = None
_nubra_lock = threading.Lock()


def _env_nubra() -> str:
    """UAT or PROD from env; default PROD."""
    v = (os.environ.get("NUBRA_ENV") or "").strip().upper()
    if v == "UAT":
        return "UAT"
    return "PROD"


def ensure_nubra(env_creds: bool = True, totp_login: bool = False):
    """
    Return the same Nubra SDK client every time (singleton).
    First call performs login (OTP/totp + MPIN or .env credentials).

    - env_creds: if True, read PHONE_NO and MPIN from .env (recommended).
    - totp_login: if True, use TOTP flow; requires env_creds or manual phone/totp.
    - Environment: set NUBRA_ENV=UAT for testing, NUBRA_ENV=PROD (or unset) for production.
    """
    global _nubra_singleton
    if _nubra_singleton is not None:
        return _nubra_singleton
    with _nubra_lock:
        if _nubra_singleton is not None:
            return _nubra_singleton
        from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

        env_name = _env_nubra()
        env = NubraEnv.UAT if env_name == "UAT" else NubraEnv.PROD
        _nubra_singleton = InitNubraSdk(
            env,
            env_creds=env_creds,
            totp_login=totp_login,
        )
        return _nubra_singleton


def reset_nubra():
    """For tests: clear singleton so next ensure_nubra() logs in again."""
    global _nubra_singleton
    with _nubra_lock:
        _nubra_singleton = None


if __name__ == "__main__":
    nubra = ensure_nubra()
    print(f"Nubra client ready (env={_env_nubra()})")
