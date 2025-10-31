import requests
import hmac
import time
import hashlib
import Dat
from endpoints import binance_api

BASE_URL = binance_api['BASE_URL']
POSITION_ENDPOINT = binance_api['POSITION_ENDPOINT']
ORDER_ENDPOINT = binance_api['ORDER_ENDPOINT']
OPEN_ORDERS_ENDPOINT = binance_api['OPEN_ORDERS_ENDPOINT']
ALL_OPEN_ORDERS_ENDPOINT = binance_api['ALL_OPEN_ORDERS_ENDPOINT']     # To remove previous STOP MARKET orders
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
            # print(f"Existing {order_type} found for {symbol}, skipping new SL/TP.")
            return True
    return False



