#!/usr/bin/env python3
"""
Nubra Python SDK (V2) – authentication via nubra_client.
UAT vs PROD from .env NUBRA_ENV; PHONE_NO and MPIN from .env.
See README for OTP vs TOTP and .env setup.
"""

def main():
    try:
        from nubra_client import ensure_nubra, _env_nubra
    except ImportError as e:
        print("Nubra SDK not found. Install with:")
        print("  pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple nubra-sdk")
        raise SystemExit(1) from e

    # Single client; UAT/PROD from .env NUBRA_ENV, credentials from .env (PHONE_NO, MPIN)
    nubra = ensure_nubra()
    print(f"SDK initialized (env={_env_nubra()}). Use nubra for market data, orders, portfolio, etc.")
    # For TOTP login: ensure_nubra(totp_login=True)
    # nubra.logout()  # full logout; next run will ask for full auth again

if __name__ == "__main__":
    main()
