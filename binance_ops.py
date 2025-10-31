import store_data
import api_actions
import place_orders
import time

TP_VAL = 1.030   # +2% TP
SL_VAL = 0.980   # -2% SL
TRAIL_PERCENT = 0.4
ACTIVATION_BUFFER = 0.55

########-----MAIN LOOP------#########
if __name__ == "__main__":

    counter = 0
    pnl_sum = 0

    # ✅ Cache for TP/SL status to avoid repeated DB/API checks
    tp_status_cache = {}

    while True:
        positions_info = api_actions.get_positions()
        all_positions_buffer = []  # temporary list to store positions before DB commit

        for position in positions_info:
            symbol = position['symbol']
            entry = float(position['entryPrice'])
            mark = float(position["markPrice"])
            amt = abs(float(position['positionAmt']))
            pnl = float(position['unRealizedProfit'])
            pnl_sum += pnl
            volume = float(amt * entry)
            pnl_perc = round(float((pnl / volume) * 100), 2)
            position["positionDirection"] = api_actions.determine_position_direction(position['positionAmt'])
            rounding = 2 if entry > 0.999 else 5

            print(f"\n## {symbol} - {position['positionDirection']} - Entry: {entry}, Amount: {amt}, PnL: {pnl} (%: {pnl_perc}), Volume: {volume}")

            # --- Establish SL/TP parameters ---
            if float(position['positionAmt']) > 0:  # LONG
                side = "SELL"
                stop_loss = round(entry * SL_VAL, rounding)
                take_profit = round(entry * TP_VAL, rounding)
            else:  # SHORT
                side = "BUY"
                stop_loss = round(entry * TP_VAL, rounding)
                take_profit = round(entry * SL_VAL, rounding)

            # --- TP/SL cache check ---
            tp_exists = tp_status_cache.get(symbol, None)

            if tp_exists is None:
                # Not in cache → check DB once and store
                status = store_data.check_tp_sl_status(symbol)
                tp_exists = bool(status["tp_set"])
                tp_status_cache[symbol] = tp_exists
                print(f"[CACHE MISS] Checked DB for {symbol}: TP set = {tp_exists}")
                print(tp_status_cache)
            else:
                print(f"[CACHE HIT] {symbol}: TP already cached = {tp_exists}")

            # --- Place TP/SL only if not set ---
            if not tp_exists:
                print(f"Placing SL/TP for {symbol}: SL={stop_loss}, TP={take_profit}")
                place_orders.place_take_profit(symbol, side, take_profit)
                place_orders.place_stop_loss(symbol, side, stop_loss)
                store_data.mark_tp_sl_as_set(symbol, tp_set=1)
                tp_status_cache[symbol] = True  # ✅ Update cache immediately
            else:
                print(f"Skipped {symbol} — TP already active or cached.")

            # --- Trailing Stop Management ---
            if pnl > 0:
                place_orders.update_trailing_stop(position, trail_perc=TRAIL_PERCENT, activation_buffer=ACTIVATION_BUFFER)
            else:
                print("Negative PnL, not setting Trailing Stop yet..")

            # Append position for bulk DB sync
            all_positions_buffer.append(position)

        # ✅ Perform one single DB sync for all positions collected
        if all_positions_buffer:
            store_data.sync_positions(all_positions_buffer)

        # ✅ Cache auto-refresh every 30 min
        if counter % 30 == 0:  # 15s × 120 = 30 min
            print("\n[Cache Refresh] Clearing TP/SL status cache.")
            tp_status_cache.clear()

        counter += 1
        print(f"Total unrealized PnL this cycle: {round(pnl_sum, 2)} \n")
        pnl_sum = 0
        time.sleep(5)
