import requests
import time
import api_actions
from endpoints import binance_api
import Dat
from store_data import update_position_metrics, sync_info

###---- Using this to trigger breakEven SL
LOWER_TRIGGER = 0.35
API_KEY = Dat.BinK
API_SECRET = Dat.BinS

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
    sl_params["signature"] = api_actions.create_signature(sl_params, API_SECRET)
    sl_response = requests.post(binance_api['BASE_URL'] + binance_api['ORDER_ENDPOINT'], headers=headers, params=sl_params)
    sl_res = sl_response.json()
    print(f"---<< Stop Loss placed for {sl_params['symbol']} at {sl_params['stopPrice']}")


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
    tp_params["signature"] = api_actions.create_signature(tp_params, API_SECRET)
    tp_response = requests.post(binance_api['BASE_URL'] + binance_api['ORDER_ENDPOINT'],
                                headers=headers, params=tp_params)
    tp_res = tp_response.json()
    update_position_metrics(symbol=tp_params['symbol'], take_profit=tp_params['stopPrice'], info='TP set')   # Update DB
    print(f">>--- Take Profit placed for {tp_params['symbol']} at {tp_params['stopPrice']}")


def update_trailing_stop(position, trail_perc, activation_buffer):
    """
    Dynamically adjusts trailing stops as profit increases.
    - First SL is placed at breakeven when price_change ∈ (0.25%, activation_buffer)
    - After activation_buffer, SL trails mark price by trail_perc.
    """
    symbol = position["symbol"]
    entry = float(position["entryPrice"])
    mark = float(position["markPrice"])
    amt = float(position["positionAmt"])
    pnl = float(position["unRealizedProfit"])
    direction = position["positionDirection"]
    break_even = float(position["breakEvenPrice"])
    side = "SELL" if direction == "LONG" else "BUY"
    rounding = 2 if entry > 0.999 else 5

    # --- Fetch all open orders first (so existing_sl is defined early)
    timestamp = int(time.time() * 1000)
    params = {"symbol": symbol, "timestamp": timestamp}
    params["signature"] = api_actions.create_signature(params, API_SECRET)
    headers = {"X-MBX-APIKEY": API_KEY}
    response = requests.get(binance_api['BASE_URL'] + binance_api['OPEN_ORDERS_ENDPOINT'], headers=headers, params=params)
    if response.status_code != 200:
        print(f"[{symbol}] Failed to fetch open orders: {response.text}")
        return
    open_orders = response.json()

    # Identify existing Stop Loss (if any)
    existing_sl = next((float(o["stopPrice"]) for o in open_orders if o["type"] == "STOP_MARKET"), None)
    print(f"Existing SL: {existing_sl}")

    # --- Calculate price change (%)
    price_change = ((mark - entry) / entry) * 100 if direction == "LONG" else ((entry - mark) / entry) * 100
    print(f"[{symbol}] ΔPrice: {price_change:.2f}% | Mark: {mark} | BreakEven: {break_even}")

    # --- Step 1: BREAKEVEN window trigger (LOWER_TRIGGER % < Price % < activation_buffer %)
    # Coloca un SL en break-even SOLO si:
    #  - No existe aún un SL, o
    #  - El SL existente está “peor” que el break-even, considerando la dirección,
    #  - Y el SL actual NO está ya en breakeven (dentro de margen de tolerancia)

    TOLERANCE = 0.0001  # 0.01% de margen para evitar duplicados por redondeo

    if direction == "LONG":
        condition_sl_weaker = (existing_sl is None or existing_sl < break_even * (1 - TOLERANCE))
        condition_already_be = existing_sl and abs(existing_sl - break_even) / break_even < TOLERANCE
    else:  # SHORT
        condition_sl_weaker = (existing_sl is None or existing_sl > break_even * (1 + TOLERANCE))
        condition_already_be = existing_sl and abs(existing_sl - break_even) / break_even < TOLERANCE

    if condition_already_be:
        print(f"[{symbol}] Existing SL is already at breakeven ({existing_sl}), skipping re-placement.")
        sync_info(symbol=symbol, state=14)  # opcional: nuevo código para "BE ya existe"
        return

    if condition_sl_weaker and LOWER_TRIGGER < price_change < activation_buffer:
        new_sl = round(break_even, rounding)
        print(f"[{symbol}] Dir={direction} | SL actual={existing_sl} | BE={break_even}")
        print(f"[{symbol}] ΔPrice {price_change:.2f}% dentro de ventana ({LOWER_TRIGGER:.2f}–{activation_buffer:.2f}) → "
            f"estableciendo SL inicial en breakeven {new_sl}")

        place_stop_loss(symbol, side, new_sl)
        update_position_metrics(symbol=symbol, trailing_stop=new_sl, info='Break even SL set')
        sync_info(symbol=symbol, state=12)
        return


    # --- Step 2: Skip if below activation buffer
    ## Only Updates Logs - By this point break_even sl should already have been placed
    if price_change < activation_buffer:  # Example:  price_change = 0.3% < activation_buffer = 0.5%
        print(f"[{symbol}] Profit < {activation_buffer}%, waiting before trailing activation.")
        sync_info(symbol=symbol,state=13)
        return

    ## -- TRAILING STOP
    # --- Step 3: Calculate new trailing SL once activation is reached
    if direction == "LONG":
        new_sl = mark * (1 - trail_perc / 100)   # Ejemplo ETH, $4,000.00 * (1 - 0.35/100) = $4,014.00 -> new_sl
    else: # SHORT
        new_sl = mark * (1 + trail_perc / 100)
    new_sl = round(new_sl, rounding)
    # print(f"[{symbol}] Active trailing phase → new SL: {new_sl}")
    sync_info(symbol=symbol,state=8)

    # --- Step 4: Update only if improvement
    if existing_sl:
        if (direction == "LONG" and new_sl > existing_sl) or (direction == "SHORT" and new_sl < existing_sl):
            # print(f"[{symbol}] Updating SL from {existing_sl} → {new_sl}")
            cancel_stop_orders(symbol)
        else:
            # print(f"[{symbol}] SL already optimal ({existing_sl}), no update needed.")
            sync_info(symbol=symbol,state=10)
            return
    # Trailing Stop Enhancement if pirce rises above 1%    
    trail_perc = 0.75 if price_change >= 1.0 else trail_perc

    # --- Step 5: Place or update SL With the new better SL
    ## By this point all other options have been used and Trailing Stop is being updated.
    place_stop_loss(symbol, side, new_sl)
    update_position_metrics(symbol=symbol, trailing_stop=new_sl, info='SL set')
    sync_info(symbol=symbol,state=9)


###----- Cancel all existing SLs for a symbol
def cancel_stop_orders(symbol):
    timestamp = int(time.time() * 1000)
    headers = {"X-MBX-APIKEY": API_KEY}
    params = {"symbol": symbol, "timestamp": timestamp}
    params["signature"] = api_actions.create_signature(params, API_SECRET)

    open_res = requests.get(binance_api['BASE_URL'] + binance_api['OPEN_ORDERS_ENDPOINT'], headers=headers, params=params)
    if open_res.status_code != 200:
        print(f"[{symbol}] Failed to fetch orders: {open_res.text}")
        return

    for order in open_res.json():
        if order["type"] == "STOP_MARKET":
            cancel_params = {
                "symbol": symbol,
                "orderId": order["orderId"],
                "timestamp": int(time.time() * 1000)
            }
            cancel_params["signature"] = api_actions.create_signature(cancel_params, API_SECRET)
            cancel_res = requests.delete(binance_api['BASE_URL'] + binance_api['ORDER_ENDPOINT'], headers=headers, params=cancel_params)
            print(f"[{symbol}] Cancelled STOP_MARKET {order['orderId']} → {cancel_res.json()}")
            sync_info(symbol=symbol,state=11)
