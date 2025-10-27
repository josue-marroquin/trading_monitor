import requests
import hmac
import time
import hashlib
import Dat

BASE_URL = "https://fapi.binance.com"
POSITION_ENDPOINT = "/fapi/v2/positionRisk"
POSITION_HISTORY = "/fapi/v1/positionMargin/history"
ORDER_ENDPOINT = "/fapi/v1/order"
OPEN_ORDERS_ENDPOINT = "/fapi/v1/openOrders"
ALL_OPEN_ORDERS_ENDPOINT = "/fapi/v1/allOpenOrders"     # To remove previous STOP MARKET orders
API_KEY = Dat.BinK
API_SECRET = Dat.BinS
rounding = 2    # Rounding for coins < 0.999

# ... [resto de tu código, como la función insert_position_into_db] ...

def create_signature(query_string, secret):
    # TODO: Asegúrate de que este método de firma sea compatible con Bitget
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
            print(position['breakEvenPrice'])
    
    return filtered_positions

###------ Determine Position Direction
def determine_position_direction(positionAmt):
    if float(positionAmt) > 0:
        return "LONG"
    elif float(positionAmt) < 0:
        return "SHORT"
    else:
        return "NEUTRAL"


if __name__ == "__main__":
    print(get_positions())