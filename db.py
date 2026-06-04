import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "kalshi_data.db"


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS markets (
                ticker       TEXT PRIMARY KEY,
                title        TEXT,
                event_ticker TEXT
            );

            CREATE TABLE IF NOT EXISTS ticks (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL    NOT NULL,
                ticker  TEXT    NOT NULL,
                yes_bid REAL,
                yes_ask REAL
            );

            CREATE TABLE IF NOT EXISTS trades (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      REAL    NOT NULL,
                ticker  TEXT    NOT NULL,
                side    TEXT,
                price   REAL,
                count   INTEGER
            );

            CREATE TABLE IF NOT EXISTS leaderboard (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,
                player_name TEXT    NOT NULL,
                position    TEXT,
                total       TEXT,
                thru        TEXT
            );

            CREATE TABLE IF NOT EXISTS poly_markets (
                condition_id TEXT PRIMARY KEY,
                question     TEXT,
                player       TEXT,
                event_slug   TEXT
            );

            CREATE TABLE IF NOT EXISTS poly_ticks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           REAL    NOT NULL,
                condition_id TEXT    NOT NULL,
                yes_bid      REAL,
                yes_ask      REAL,
                yes_mid      REAL,
                volume       REAL
            );

            CREATE INDEX IF NOT EXISTS idx_ticks_ticker_ts  ON ticks(ticker, ts);
            CREATE INDEX IF NOT EXISTS idx_trades_ticker_ts ON trades(ticker, ts);
            CREATE INDEX IF NOT EXISTS idx_leaderboard_ts   ON leaderboard(ts);
            CREATE INDEX IF NOT EXISTS idx_poly_ticks_cid_ts ON poly_ticks(condition_id, ts);
        """)


def upsert_markets(markets: list[dict]):
    with _conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO markets (ticker, title, event_ticker) VALUES (?, ?, ?)",
            [(m["ticker"], m["title"], m["event_ticker"]) for m in markets],
        )


def write_tick(ticker: str, yes_bid: float, yes_ask: float):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO ticks (ts, ticker, yes_bid, yes_ask) VALUES (?, ?, ?, ?)",
            (time.time(), ticker, yes_bid, yes_ask),
        )


def write_trade(ticker: str, side: str, price: float, count: int | None):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO trades (ts, ticker, side, price, count) VALUES (?, ?, ?, ?, ?)",
            (time.time(), ticker, side, price, count),
        )


def write_leaderboard(rows: list[dict]):
    """Snapshot the full leaderboard at one point in time."""
    ts = time.time()
    with _conn() as conn:
        conn.executemany(
            "INSERT INTO leaderboard (ts, player_name, position, total, thru) "
            "VALUES (?, ?, ?, ?, ?)",
            [(ts, r["player_name"], r["position"], r["total"], r["thru"]) for r in rows],
        )


def upsert_poly_markets(markets: list[dict]):
    with _conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO poly_markets (condition_id, question, player, event_slug) "
            "VALUES (?, ?, ?, ?)",
            [(m["condition_id"], m["question"], m["player"], m.get("event_slug")) for m in markets],
        )


def write_poly_tick(condition_id: str, yes_bid, yes_ask, yes_mid, volume):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO poly_ticks (ts, condition_id, yes_bid, yes_ask, yes_mid, volume) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), condition_id, yes_bid, yes_ask, yes_mid, volume),
        )
