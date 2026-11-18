# Imports
import sqlite3
import os
from dotenv import load_dotenv

# Umgebungsvariablen laden
load_dotenv()

# Datenbank-Pfad
DB_PATH = os.path.join(os.path.dirname(__file__), 'casino.db')

# Datenbank-Verbindung herstellen
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

# Datenbank lesen
def db_read(sql, params=None, single=False):
    conn = get_conn()
    try:
        sql = sql.replace('%s', '?')
        cur = conn.cursor()
        cur.execute(sql, params or ())

        if single:
            row = cur.fetchone()
            return dict(row) if row else None
        else:
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()

# Datenbank schreiben
def db_write(sql, params=None):
    conn = get_conn()
    try:
        sql = sql.replace('%s', '?')
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Datenbank-Fehler: {e}")
        return False
    finally:
        conn.close()
