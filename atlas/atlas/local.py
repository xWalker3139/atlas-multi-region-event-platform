import sqlite3
from .store import Store


def connect(path='atlas.db'):
    conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA journal_mode=WAL')
    return Store(conn)
