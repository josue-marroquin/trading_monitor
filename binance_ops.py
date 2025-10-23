import store_data
import api_actions
import time

TP_VAL = 1.030   # +3% TP
SL_VAL = 0.990   # -1% SL
TRAIL_PERCENT = 0.3
ACTIVATION_BUFFER = 0.45

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
            amt = abs(float(position['positionAmt']))
            pnl = float(position['unRealizedProfit'])
            pnl_sum += pnl
            volume = float(amt * entry)
            pnl_perc = round(float((pnl / volume) * 100), 2)

            print(f"\n## {symbol} - {position['positionDirection']} - Entry: {entry}, Amount: {amt}, PnL: {pnl} (%: {pnl_perc}), Volume: {volume}")

            # Append position to the buffer list (we’ll insert all later)
            all_positions_buffer.append(position)

            ## Checking PnL to set Trailing Stop
            if pnl > 0:
                api_actions.update_trailing_stop(position, trail_perc=TRAIL_PERCENT, activation_buffer=ACTIVATION_BUFFER)
            else:
                print("Negative PnL, not setting SL yet..")

            ## Validating Token's price to establish rounding
            rounding = 2 if entry > 0.999 else 5

            ## Establishing SL/TP parameters
            if float(position['positionAmt']) > 0:  # LONG
                side = "SELL"
                stop_loss = round(entry * SL_VAL, rounding)
                take_profit = round(entry * TP_VAL, rounding)
            else:  # SHORT
                side = "BUY"
                stop_loss = round(entry * TP_VAL, rounding)
                take_profit = round(entry * SL_VAL, rounding)

            ## Setting SL/TP
            if not api_actions.has_existing_sl_tp(symbol):
                print(f"Placing SL/TP for {symbol}: SL={stop_loss}, TP={take_profit}")
                api_actions.place_take_profit(symbol, side, take_profit)  # Setting only TP
            else:
                print(f"Skipped {symbol} — existing SL/TP already active.")

        # ✅ Perform one single DB sync for all positions collected
        if all_positions_buffer:
            store_data.sync_positions(all_positions_buffer)

        counter += 1
        print(f"Total unrealized PnL this cycle: {round(pnl_sum, 2)}")
        pnl_sum = 0
        time.sleep(15)
