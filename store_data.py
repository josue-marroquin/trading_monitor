import Dat
import mysql.connector
from datetime import datetime
import time

DB_CONFIG = Dat.db_config

###---- Sincronizar posiciones (Insertar o Actualizar en lote)
def sync_positions(positions):
    """
    Actualiza o inserta posiciones activas desde el feed de Binance.
    Si una posición ya no aparece en la API, se marca como inactiva (position_status = 0).
    Ahora optimizado con executemany() para insertar/actualizar todas las posiciones de una vez.
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor_select = connection.cursor()

        # Obtener símbolos activos actuales en DB
        cursor_select.execute("SELECT symbol FROM trading_positions WHERE position_status = 1;")
        existing_symbols = {row[0] for row in cursor_select.fetchall()}

        # Preparar símbolos activos desde la API
        api_symbols = {pos['symbol'] for pos in positions}

        # --- Preparar los datos para inserción/actualización ---
        data_list = []
        for position in positions:
            last_trade_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(position['updateTime'] / 1000))
            data_list.append((
                position['symbol'], position['positionExchange'], position['positionAmt'], position['entryPrice'],
                position['marginType'], position['positionSide'], position['positionDirection'], position['leverage'],
                position['liquidationPrice'], position['markPrice'], position['unRealizedProfit'], last_trade_time, 1, position['breakEvenPrice']
            ))

        insert_query = """
            INSERT INTO trading_positions (
                symbol, position_exchange, position_amount, entry_price, margin_type, position_side, 
                position_direction, leverage, liquidation_price, mark_price, unrealized_profit, 
                last_trade_time, position_status, breakeven_price
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                position_amount = VALUES(position_amount),
                entry_price = VALUES(entry_price),
                position_direction = VALUES(position_direction),
                mark_price = VALUES(mark_price),
                unrealized_profit = VALUES(unrealized_profit),
                last_trade_time = VALUES(last_trade_time),
                position_status = 1,
                breakeven_price = VALUES(breakeven_price)
        """

        # ✅ Ejecutar todos los inserts/updates en lote
        cursor_insert_update = connection.cursor()
        if data_list:
            cursor_insert_update.executemany(insert_query, data_list)
            # print(f"\n{len(data_list)} posiciones sincronizadas en lote.")

        # --- Marcar posiciones cerradas como inactivas y restablecer valores
        closed_symbols = existing_symbols - api_symbols
        if closed_symbols:
            placeholders = ', '.join(['%s'] * len(closed_symbols))
            update_state_table = f"""
                                UPDATE position_state
                                SET status_ = 0
                                WHERE symbol IN ({placeholders});
                            """
            deactivate_query = f"""
                                UPDATE trading_positions
                                SET position_amount = 0.0,
                                    entry_price = 0.0,
                                    liquidation_price = 0.0,
                                    mark_price = 0.0,
                                    unrealized_profit = 0.0,
                                    trailing_stop = 0.0,
                                    take_profit = 0.0,
                                    position_status = 0,
                                    info = '',
                                    tp_set = 0,
                                    sl_set = 0,
                                    breakeven_price = 0.0
                                WHERE symbol IN ({placeholders});
                            """
            cursor_insert_update.execute(update_state_table, tuple(closed_symbols))
            cursor_insert_update.execute(deactivate_query, tuple(closed_symbols))
            print(f"Valores restablecidos para operaciones inactivas.")
            # print(f"Se marcaron como inactivas: {', '.join(closed_symbols)}")

        connection.commit()
        cursor_insert_update.close()

    except mysql.connector.Error as error:
        print(f"Error al sincronizar posiciones: {error}")

    finally:
        if connection.is_connected():
            connection.close()


###---- Actualizar métricas dinámicas de la posición (Trailing Stop, TP, Volumen, Cambio)
def update_position_metrics(symbol, trailing_stop=None, take_profit=None, volume=None, change_=None, info=None):
    """
    Actualiza los valores dinámicos de una posición específica.
    Solo actualiza los campos provistos (no sobrescribe los nulos).
    """
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        fields = []
        values = []

        if trailing_stop is not None:
            fields.append("trailing_stop = %s")
            values.append(trailing_stop)
        if take_profit is not None:
            fields.append("take_profit = %s")
            values.append(take_profit)
        if volume is not None:
            fields.append("volume = %s")
            values.append(volume)
        if change_ is not None:
            fields.append("change_ = %s")
            values.append(change_)
        if info is not None:
            fields.append("info = %s")
            values.append(info)

        if not fields:
            print(f"No se proporcionaron campos para actualizar en {symbol}.")
            return

        query = f"UPDATE trading_positions SET {', '.join(fields)} WHERE symbol = %s;"
        values.append(symbol)

        cursor.execute(query, tuple(values))
        connection.commit()

        print(f"Actualización de métricas completada para {symbol} → {fields}")

    except mysql.connector.Error as error:
        print(f"Error al actualizar métricas de posición: {error}")
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


###----  Keep track of TP and SL
def mark_tp_sl_as_set(symbol, tp_set=None, sl_set=None):
    """Mark TP or SL as set (1) or unset (0) for a given symbol."""
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        fields, values = [], []
        if tp_set is not None:
            fields.append("tp_set = %s")
            values.append(tp_set)
        if sl_set is not None:
            fields.append("sl_set = %s")
            values.append(sl_set)

        if not fields:
            return

        query = f"UPDATE trading_positions SET {', '.join(fields)} WHERE symbol = %s;"
        values.append(symbol)

        cursor.execute(query, tuple(values))
        connection.commit()
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


def check_tp_sl_status(symbol):
    """Return the current TP/SL flags from DB for a symbol."""
    connection = mysql.connector.connect(**DB_CONFIG)
    cursor = connection.cursor()
    cursor.execute("SELECT tp_set, sl_set FROM trading_positions WHERE symbol = %s;", (symbol,))
    row = cursor.fetchone()
    cursor.close()
    connection.close()
    if not row:
        return {"tp_set": 0, "sl_set": 0}
    return {"tp_set": row[0], "sl_set": row[1]}


def sync_info(symbol, state=None):
    """Keeping track of all changes made by the bot."""
    if not state:
        return  # No hace nada si no hay nota
    
    try:
        connection = mysql.connector.connect(**DB_CONFIG)
        cursor = connection.cursor()

        insert_state = """
                    INSERT INTO position_state (symbol, state, updated_at, status_)
                    VALUES (%s, %s, NOW(), 1)
                    ON DUPLICATE KEY UPDATE
                        state = VALUES(state),
                        updated_at = NOW(),
                        status_ = 1
                """

        data = (symbol, state)
        cursor.execute(insert_state, data)
        connection.commit()

        # print(f"[INFO] Log inserted for {symbol} at {datetime.now()}")
    
    except mysql.connector.Error as err:
        print(f"[ERROR] Database error: {err}")

    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()