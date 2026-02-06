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

## Phase 1: Instrument map + Nubra client (place_order foundation)

- **instrument_dict_nubra.py**: Builds `instrument_dict` (key_name → ref_id) from `option_ref_ids_feb_filtered_by_strikes.csv`; optionally caches to `instrument_dict_nubra.json`. Same key_name format as Oswal: `{asset}_{strike//100}_{option_type}`.
- **nubra_client.py**: Singleton `ensure_nubra()` – one Nubra SDK client for orders/market data. Uses `.env` when `env_creds=True`. Set `NUBRA_ENV=UAT` or `NUBRA_ENV=PROD` (default PROD).

**Verify Phase 1:**

```bash
cd nubra_oswal
python phase1_verify.py
```

Or: `python instrument_dict_nubra.py` (prints instrument count and sample); then use `ensure_nubra()` from your scripts.

## Phase 2: Single-order placement (Nubra API only)

- **nubra_order.py**: Place one order per call via Nubra V2 API. All order API calls use a single lock.
  - `place_order_nubra(ref_id, side, qty, price_rupees, product_type="NRML", validity="DAY", exchange="NSE", price_type="LIMIT", tag=None)` → returns `{ order_id, exchange_order_id, ref_id, order_status, ... }`. Price automatically rounded to tick_size.
  - `place_order_nubra_by_key(key_name, side, qty, price_rupees, instrument_dict=None, **kwargs)` → resolves key_name → ref_id then calls `place_order_nubra`.

**Verify Phase 2:**

```bash
python phase2_verify.py           # dry run (no order)
python phase2_verify.py --live   # place one real order (use with caution)
# Or: python nubra_order.py <ref_id> BUY <qty> <price_rupees>
```

## Phase 3: Market data + mid price (reuse existing)

- **market_data_helpers.py**: Same `_touch`/`_mid` logic as Oswal executor_live.py. Works with `market_data` from `subscribe_orderbook.py` (Oswal-compatible structure).
  - `_touch(market_data, key_name, current_price=None)` → returns `(bb, ba, ltp)` - best bid, best ask, last traded price.
  - `_mid(bb, ba, ltp, current_price=None)` → calculates mid price `(bb + ba) / 2`, rounded to tick (0.05).
  - `get_mid_price(market_data, key_name, current_price=None)` → convenience: calls `_touch` then `_mid`.

**Verify Phase 3:**

```bash
python phase3_verify.py           # test with sample market_data
# Or run subscribe_orderbook.py in another terminal, then phase3_verify.py will use real data
```

## Phase 4: Realtime order/trade updates (WebSocket)

- **order_updates_nubra.py**: Nubra order/trade WebSocket → `order_state` (same shape as Oswal `socket_state`).
  - **order_state**: `order_id (int) → { filled, status, avg_px, ts }`; updated from `on_order_update` and `on_trade_update`.
  - **start_order_updates_socket()**: Starts `orderupdate.OrderUpdate` WebSocket in a daemon thread (non-blocking).
  - **update_socket_state(order_id, filled_qty, status, avg_px=None)**: Thread-safe update; use to seed just-placed orders (e.g. PENDING_LOCAL).
  - **get_socket_state()**: Returns copy of full order_state (Oswal-compatible).
  - **get_order_state(order_id)**: Returns copy of state for one order, or None.
  - **is_socket_connected()**: True if WebSocket is connected.

**Verify Phase 4:**

```bash
python phase4_verify.py   # start WebSocket, print order_state every 5s; place order in another terminal to see updates
```

## Links

- [Nubra API docs](https://nubra.io/products/api/docs/)
- [Python SDK overview](https://nubra.io/products/api/docs/python-sdk/)
- [Test PyPI – nubra-sdk](https://test.pypi.org/project/nubra-sdk/)
