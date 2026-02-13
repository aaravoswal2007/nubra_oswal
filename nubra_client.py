#!/usr/bin/env python3
"""
Phase 1: Nubra SDK client singleton.
Single shared client for order placement, market data, and order updates.

Manual login: credentials are set in code below (no .env). No TOTP.
SDK will prompt for OTP (SMS) and MPIN when you run — you type them in the terminal.
For this to work, run the bridge from a terminal (e.g. python Bridge.py), not from
Electron, so stdin is your keyboard. Or run the first login from terminal and
reuse session if the SDK supports it.
"""

import os
import threading

# ---------------------------------------------------------------------------
# Credentials in code (no .env). Edit these and leave .env unused.
# ---------------------------------------------------------------------------
NUBRA_PHONE = "8180959229"
NUBRA_MPIN = "2820"
NUBRA_ENV = "UAT"  # "UAT" or "PROD"


def _env_nubra() -> str:
    """UAT or PROD; from in-code NUBRA_ENV."""
    v = (NUBRA_ENV or "PROD").strip().upper()
    return "UAT" if v == "UAT" else "PROD"


_nubra_singleton = None
_nubra_lock = threading.Lock()


def ensure_nubra(env_creds: bool = True, totp_login: bool = False):
    """
    Return the same Nubra SDK client every time (singleton).
    First call performs manual login: PHONE_NO and MPIN from in-code constants,
    no TOTP. SDK will prompt for OTP (SMS) and MPIN — user types them in terminal.
    """
    global _nubra_singleton
    if _nubra_singleton is not None:
        return _nubra_singleton
    with _nubra_lock:
        if _nubra_singleton is not None:
            return _nubra_singleton
        # Set env from in-code credentials so SDK gets them (no .env file)
        os.environ.setdefault("PHONE_NO", (NUBRA_PHONE or "").strip())
        os.environ.setdefault("MPIN", (NUBRA_MPIN or "").strip())
        os.environ.setdefault("NUBRA_ENV", (NUBRA_ENV or "PROD").strip())

        from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv

        env_name = _env_nubra()
        env = NubraEnv.UAT if env_name == "UAT" else NubraEnv.PROD

        # Manual login: SMS OTP + MPIN. No TOTP, no input/getpass patch.
        _nubra_singleton = InitNubraSdk(
            env,
            env_creds=True,
            totp_login=False,
        )
        return _nubra_singleton


def get_nubra():
    """
    Get the existing Nubra SDK client singleton without initializing.
    Raises RuntimeError if ensure_nubra() hasn't been called yet.
    Use this in child modules when the parent test file has already called ensure_nubra().
    """
    global _nubra_singleton
    if _nubra_singleton is None:
        raise RuntimeError("Nubra client not initialized. Call ensure_nubra() first (typically in parent test file).")
    return _nubra_singleton


def reset_nubra():
    """For tests: clear singleton so next ensure_nubra() logs in again."""
    global _nubra_singleton
    with _nubra_lock:
        _nubra_singleton = None


if __name__ == "__main__":
    nubra = ensure_nubra()
    print(f"Nubra client ready (env={_env_nubra()})")
