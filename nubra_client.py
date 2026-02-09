#!/usr/bin/env python3
"""
Phase 1: Nubra SDK client singleton.
Single shared client for order placement, market data, and order updates.

When NUBRA_TOTP_SECRET is set in .env, login is fully automated:
- PHONE_NO and MPIN from .env (env_creds=True)
- TOTP from pyotp using NUBRA_TOTP_SECRET (no manual typing)
- MPIN also auto-filled from .env when SDK prompts (e.g., during totp_enable())
"""

import os
import threading
from pathlib import Path

# Load .env from nubra_oswal so NUBRA_ENV, PHONE_NO, MPIN, NUBRA_TOTP_SECRET are set
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        pass  # python-dotenv not installed; rely on shell env or SDK loading .env

_nubra_singleton = None
_nubra_lock = threading.Lock()
_totp_patch_applied = False


def _env_nubra() -> str:
    """UAT or PROD from env; default PROD."""
    v = (os.environ.get("NUBRA_ENV") or "").strip().upper()
    if v == "UAT":
        return "UAT"
    return "PROD"


def _apply_totp_patch_if_needed() -> None:
    """
    If NUBRA_TOTP_SECRET is set, patch builtins.input and getpass.getpass
    so that when the SDK prompts for TOTP or MPIN, we auto-fill from .env + pyotp.
    Applied once per process.
    """
    global _totp_patch_applied
    if _totp_patch_applied:
        return
    secret = (os.environ.get("NUBRA_TOTP_SECRET") or "").strip()
    mpin = (os.environ.get("MPIN") or "").strip()
    if not secret:
        return
    try:
        import pyotp
        import builtins
        import getpass
        totp_gen = pyotp.TOTP(secret)

        def _auto_input(prompt: str = "") -> str:
            prompt_lower = (prompt or "").lower()
            # Check if prompt is asking for MPIN/PIN
            if any(kw in prompt_lower for kw in ["mpin", "pin", "password"]):
                return mpin if mpin else totp_gen.now()  # Fallback to TOTP if MPIN not set
            # Otherwise assume TOTP (6-digit code)
            return totp_gen.now()

        def _auto_getpass(prompt: str = "") -> str:
            prompt_lower = (prompt or "").lower()
            # getpass is typically used for MPIN/PIN
            if any(kw in prompt_lower for kw in ["mpin", "pin", "password"]):
                return mpin if mpin else totp_gen.now()  # Fallback to TOTP if MPIN not set
            # Otherwise assume TOTP
            return totp_gen.now()

        builtins.input = _auto_input
        getpass.getpass = _auto_getpass
        _totp_patch_applied = True
    except Exception:
        pass  # pyotp not installed or patch failed; user will type manually


def ensure_nubra(env_creds: bool = True, totp_login: bool = False):
    """
    Return the same Nubra SDK client every time (singleton).
    First call performs login (OTP/totp + MPIN or .env credentials).

    - env_creds: if True, read PHONE_NO and MPIN from .env (recommended).
    - totp_login: if True, use TOTP flow. If NUBRA_TOTP_SECRET is set in .env,
      TOTP and MPIN are auto-filled from .env + pyotp (no manual typing).
    - Environment: NUBRA_ENV=UAT|PROD, PHONE_NO, MPIN; optional NUBRA_TOTP_SECRET for auto TOTP/MPIN.
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

        # If TOTP secret is in .env, use TOTP login and patch input/getpass so SDK gets code automatically
        use_totp = totp_login or bool((os.environ.get("NUBRA_TOTP_SECRET") or "").strip())
        if use_totp:
            _apply_totp_patch_if_needed()

        _nubra_singleton = InitNubraSdk(
            env,
            env_creds=env_creds,
            totp_login=use_totp,
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
