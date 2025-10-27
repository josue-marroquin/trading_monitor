import store_data
import api_actions
import time

TP_VAL = 1.020   # +3% TP
SL_VAL = 0.980   # -1% SL
TRAIL_PERCENT = 0.25
ACTIVATION_BUFFER = 0.5

########-----MAIN LOOP------#########
if __name__ == "__main__":

    counter = 0
    pnl_sum = 0

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
            ## Validating Token's price to establish rounding
            rounding = 2 if entry > 0.999 else 5

            print(f"\n## {symbol} - {position['positionDirection']} - Entry: {entry}, Amount: {amt}, PnL: {pnl} (%: {pnl_perc}), Volume: {volume}")

            ## Establishing SL/TP parameters
            if float(position['positionAmt']) > 0:  # LONG
                side = "SELL"
                stop_loss = round(entry * SL_VAL, rounding)
                take_profit = round(entry * TP_VAL, rounding)
            else:  # SHORT
                side = "BUY"
                stop_loss = round(entry * TP_VAL, rounding)
                take_profit = round(entry * SL_VAL, rounding)

            ## Setting TAKE PROFIT only if it doesn't Exist already
            if not api_actions.has_existing_sl_tp(symbol):
                print(f"Placing SL/TP for {symbol}: SL={stop_loss}, TP={take_profit}")
                api_actions.place_take_profit(symbol, side, take_profit)  # Setting only TP
                api_actions.place_stop_loss(symbol, side, stop_loss)  # Setting only TP
            else:
                print(f"Skipped {symbol} — existing TP already active.")

            ## Sending Signal to check if Trailing Stop needs to be set up.
            if pnl > 0:
                api_actions.update_trailing_stop(position, trail_perc=TRAIL_PERCENT, activation_buffer=ACTIVATION_BUFFER)
            else:
                print("Negative PnL, not setting Trailing Stop yet..")

            # Append position to the buffer list (we’ll insert all later)
            all_positions_buffer.append(position)


        # ✅ Perform one single DB sync for all positions collected
        if all_positions_buffer:
            store_data.sync_positions(all_positions_buffer)


        ## Check DB cache to manage TP/SL
        status = store_data.check_tp_sl_status(symbol)
        tp_exists = bool(status["tp_set"])

        if not tp_exists:
            print(f"Placing TP for {symbol}: {take_profit}")
            store_data.mark_tp_sl_as_set(symbol, tp_set=1)
        else:
            print(f"Skipped {symbol} — TP already marked in DB.")

        counter += 1
        print(f"Total unrealized PnL this cycle: {round(pnl_sum, 2)} \n")
        pnl_sum = 0
        time.sleep(10)
