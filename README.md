# Nubra Python SDK – nubra-test

This project runs the [Nubra Python SDK](https://test.pypi.org/project/nubra-sdk/) for programmatic access to Nubra’s trading APIs (orders, market data, portfolio, etc.).

## Prerequisites

- **Python 3.x** (3.8+ recommended). Check: `python3 --version`
- **VS Code** (optional) with the Python extension

## Setup

### 1. Create and activate a virtual environment (recommended)

```bash
cd nubra-test
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
```

### 2. Install the Nubra SDK

From this directory, with the venv activated:

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple nubra-sdk
```

Or install from `requirements.txt` (same index URLs):

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple -r requirements.txt
```

### 3. Authentication

You need a Nubra Trading account with a **registered phone number** and **MPIN** (Mobile PIN).

**Options:**

| Method | Use case |
|--------|----------|
| **OTP** | Default. Prompts for phone → OTP (SMS) → MPIN. Re-login every 7 days. |
| **TOTP** | For scripts. One-time setup: generate secret, enable TOTP, then use `totp_login=True`. |
| **.env** | No typing: SDK reads `PHONE_NO` and `MPIN` from a `.env` file when `env_creds=True`. |

**OTP (default):**
```python
nubra = InitNubraSdk(NubraEnv.UAT)
# Prompts: phone → OTP → MPIN
```

**TOTP (one-time setup, then scripted login):**
```python
# 1) Generate secret and add to Authenticator app
nubra = InitNubraSdk(env=NubraEnv.UAT)
secret = nubra.totp_generate_secret()

# 2) Enable TOTP (prompts for 6-digit TOTP + MPIN)
nubra.totp_enable()

# 3) Future logins (phone → TOTP → MPIN, no SMS)
nubra = InitNubraSdk(env=NubraEnv.UAT, totp_login=True)
```

**Login using .env (no prompts for phone/MPIN):**
```bash
cp env.example .env
# Edit .env: set PHONE_NO and MPIN
```
```python
nubra = InitNubraSdk(NubraEnv.UAT, env_creds=True)
```

**Logout (clears tokens; next run = full auth again):**
```python
nubra.logout()
```

## Run the example  
`Exception occured while fetching user info: 'NoneType' object is not subscriptable`.  
This comes from the SDK’s version-check call; UAT’s userinfo response can omit `version_info`. You can ignore it — auth succeeded and the client is usable for API calls.

## Run the example

```bash
python example_nubra.py
```

This script only checks that the SDK can be imported and shows a minimal initialization pattern. Replace with your own credentials and logic for real API calls.

## SDK features (V2)

- Market data, real-time quotes, Greeks, option chains  
- Order management (regular, CO, flexi, basket)  
- Positions, holdings, funds  
- MPIN-based authentication  
- Production-ready API access  

## Links

- [Nubra API docs](https://nubra.io/products/api/docs/)
- [Python SDK overview](https://nubra.io/products/api/docs/python-sdk/)
- [Test PyPI – nubra-sdk](https://test.pypi.org/project/nubra-sdk/)
