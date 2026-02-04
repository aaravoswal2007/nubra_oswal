#!/usr/bin/env python3
"""
Nubra Python SDK (V2) – authentication examples.
See README for OTP vs TOTP and .env setup.
"""

def main():
    try:
        from nubra_python_sdk.start_sdk import InitNubraSdk, NubraEnv
    except ImportError as e:
        print("Nubra SDK not found. Install with:")
        print("  pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple nubra-sdk")
        raise SystemExit(1) from e

    # --- Choose one ---
    # 1) OTP: prompts for phone → OTP → MPIN (re-login every 7 days)


    # nubra = InitNubraSdk(NubraEnv.PROD)

    # 2) TOTP: after enabling TOTP once, use this for scripted login (phone → TOTP → MPIN)
    # nubra = InitNubraSdk(env=NubraEnv.PROD, totp_login=True)

    # 3) Credentials from .env (copy .env.example to .env, set PHONE_NO and MPIN)
    nubra = InitNubraSdk(NubraEnv.PROD, env_creds=True)

    print("SDK initialized. Use nubra for market data, orders, portfolio, etc.")
    # nubra.logout()  # full logout; next run will ask for full auth again

if __name__ == "__main__":
    main()
