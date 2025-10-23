import requests
import hmac
import time
import hashlib
import Dat
from store_data import update_position_metrics

BASE_URL = "https://fapi.binance.com"
POSITION_ENDPOINT = "/fapi/v2/positionRisk"
ORDER_ENDPOINT = "/fapi/v1/order"
OPEN_ORDERS_ENDPOINT = "/fapi/v1/openOrders"
ALL_OPEN_ORDERS_ENDPOINT = "/fapi/v1/allOpenOrders"     # To remove previous STOP MARKET orders
API_KEY = Dat.BinK
API_SECRET = Dat.BinS
rounding = 2    # Rounding for coins < 0.999

###----- Binance API Signature
def create_signature(params, secret):
    if isinstance(params, dict):
        query_string = "&".join([f"{key}={value}" for key, value in params.items()])
    elif isinstance(params, str):
        query_string = params
    else:
        raise TypeError("params must be dict or str")
    return hmac.new(secret.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()


###----- Get Open Positions
def get_positions():
    timestamp = int(time.time() * 1000)
    params = {"timestamp": timestamp}
    query_string = "&".join([f"{key}={value}" for key, value in params.items()])
    signature = create_signature(query_string, API_SECRET)
    params["signature"] = signature
    headers = {"X-MBX-APIKEY": API_KEY}
    response = requests.get(BASE_URL + POSITION_ENDPOINT, headers=headers, params=params)
    
    if response.status_code != 200:
        print(f"Error: {response.status_code}, Message: {response.text}")
        return []

    all_positions = response.json()
    
    filtered_positions = []
    for position in all_positions:
        if float(position['positionAmt']) != 0:
            position["positionDirection"] = determine_position_direction(position['positionAmt'])
            position["positionExchange"] = "BINANCE"
            position["positionStatus"] = 1
            filtered_positions.append(position)
    
    return filtered_positions


###------ Determine Position Direction
def determine_position_direction(positionAmt):
    if float(positionAmt) > 0:
        return "LONG"
    elif float(positionAmt) < 0:
        return "SHORT"
    else:
        return "NEUTRAL"

###------ Determine If SL already Exists
def has_existing_sl_tp(symbol):
    """Check if there is already a Stop Loss or Take Profit order for this symbol."""
    timestamp = int(time.time() * 1000)
    params = {"symbol": symbol, "timestamp": timestamp}
    params["signature"] = create_signature(params, API_SECRET)
    headers = {"X-MBX-APIKEY": API_KEY}
    response = requests.get(BASE_URL + OPEN_ORDERS_ENDPOINT, headers=headers, params=params)

    if response.status_code != 200:
        print(f"Error checking open orders for {symbol}: {response.text}")
        return False

    open_orders = response.json()

    for order in open_orders:
        order_type = order.get("type", "")
        if order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
            print(f"Existing {order_type} found for {symbol}, skipping new SL/TP.")
            return True

    return False


###----- Place Stop Loss
def place_stop_loss(symbol, side, stop_loss_price):
    headers = {"X-MBX-APIKEY": API_KEY}
    sl_params = {
        "symbol": symbol,
        "side": side,
        "type": "STOP_MARKET",
        "stopPrice": stop_loss_price,
        "closePosition": "true",
        "timestamp": int(time.time() * 1000)
    }
    sl_params["signature"] = create_signature(sl_params, API_SECRET)
    sl_response = requests.post(BASE_URL + ORDER_ENDPOINT, headers=headers, params=sl_params)
    sl_res = sl_response.json()
    update_position_metrics(symbol=sl_params['symbol'], trailing_stop=sl_params['stopPrice'])   # Update Position - DB
    print(f"---<< Stop Loss Response: {sl_params['symbol']}, {sl_params['stopPrice']}")


###----- Place Take Profit
def place_take_profit(symbol, side, take_profit_price):
    headers = {"X-MBX-APIKEY": API_KEY}
    tp_params = {
        "symbol": symbol,
        "side": side,
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": take_profit_price,
        "closePosition": "true",
        "timestamp": int(time.time() * 1000)
    }
    tp_params["signature"] = create_signature(tp_params, API_SECRET)
    tp_response = requests.post(BASE_URL + ORDER_ENDPOINT, headers=headers, params=tp_params)
    tp_res = tp_response.json()
    update_position_metrics(symbol=tp_params['symbol'], take_profit=tp_params['stopPrice'])   # Update Position - DB
    print(f">>--- Take Profit Response: {tp_params['symbol']}, {tp_params['stopPrice']}")


###----- Trailing Stop Manager (with profit threshold)
def update_trailing_stop(position, trail_perc=0.5, activation_buffer=0.5):
    """
    Dynamically adjusts trailing stops as profit increases.
    - trail_perc: how far (in %) behind the current mark price the SL should trail.
    - activation_buffer: how much profit (%) the position must have before breakeven SL is placed.
    """
    symbol = position["symbol"]
    entry = float(position["entryPrice"])
    mark = float(position["markPrice"])
    amt = float(position["positionAmt"])
    pnl = float(position["unRealizedProfit"])
    direction = position["positionDirection"]
    side = "SELL" if direction == "LONG" else "BUY"

    # Skip if not profitable yet
    if pnl <= 0:
        print(f"[{symbol}] Not in profit yet, skipping trailing stop.")
        return

    # Calculate percentage gain/loss from entry
    price_change = ((mark - entry) / entry) * 100 if direction == "LONG" else ((entry - mark) / entry) * 100
    print(f"[{symbol}] Price change: {price_change:.2f}%")

    # Skip if profit < activation threshold
    if price_change < activation_buffer:
        print(f"[{symbol}] Profit below {activation_buffer}% — waiting before breakeven SL.")
        return

    # Get all open orders
    timestamp = int(time.time() * 1000)
    params = {"symbol": symbol, "timestamp": timestamp}
    params["signature"] = create_signature(params, API_SECRET)
    headers = {"X-MBX-APIKEY": API_KEY}
    response = requests.get(BASE_URL + OPEN_ORDERS_ENDPOINT, headers=headers, params=params)
    if response.status_code != 200:
        print(f"[{symbol}] Failed to fetch open orders: {response.text}")
        return
    open_orders = response.json()

    # Identify existing Stop-Loss order if present
    existing_sl = None
    for order in open_orders:
        if order.get("type") == "STOP_MARKET":
            existing_sl = float(order.get("stopPrice", 0))
            break

    # Calculate new trailing SL (only after activation_buffer)
    if direction == "LONG":
        new_sl = entry if mark <= entry * (1 + activation_buffer / 100) else mark * (1 - trail_perc / 100)
    else:  # SHORT
        new_sl = entry if mark >= entry * (1 - activation_buffer / 100) else mark * (1 + trail_perc / 100)

    rounding = 2 if entry > 0.999 else 5
    new_sl = round(new_sl, rounding)
    print(f"[{symbol}] Current mark: {mark}, New SL target: {new_sl}")

    # If there’s no SL yet → create one
    if not existing_sl:
        print(f"[{symbol}] No existing SL found. Creating new trailing SL at {new_sl}")
        place_stop_loss(symbol, side, new_sl)
        update_position_metrics(symbol=symbol, trailing_stop=new_sl)
        return

    # If the new SL is better (closer to profit side), update it
    if (direction == "LONG" and new_sl > existing_sl) or (direction == "SHORT" and new_sl < existing_sl):
        print(f"[{symbol}] Updating SL from {existing_sl} → {new_sl}")
        cancel_stop_orders(symbol)
        place_stop_loss(symbol, side, new_sl)
        update_position_metrics(symbol=symbol, trailing_stop=new_sl)
    else:
        print(f"[{symbol}] SL already optimal ({existing_sl}), no update needed.")


###----- Cancel all existing SLs for a symbol
def cancel_stop_orders(symbol):
    timestamp = int(time.time() * 1000)
    headers = {"X-MBX-APIKEY": API_KEY}
    params = {"symbol": symbol, "timestamp": timestamp}
    params["signature"] = create_signature(params, API_SECRET)

    # Get all open orders
    open_res = requests.get(BASE_URL + OPEN_ORDERS_ENDPOINT, headers=headers, params=params)
    if open_res.status_code != 200:
        print(f"[{symbol}] Failed to fetch orders: {open_res.text}")
        return

    for order in open_res.json():
        if order["type"] == "STOP_MARKET":
            cancel_params = {"symbol": symbol, "orderId": order["orderId"], "timestamp": int(time.time() * 1000)}
            cancel_params["signature"] = create_signature(cancel_params, API_SECRET)
            cancel_res = requests.delete(BASE_URL + "/fapi/v1/order", headers=headers, params=cancel_params)
            print(f"[{symbol}] Cancel STOP_MARKET {order['orderId']} → {cancel_res.json()}")
