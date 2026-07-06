import sqlite3
import os
import json
import threading

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA_DIR, "osrp_bot.db")

_lock = threading.Lock()
_conn = None

def get_conn():
    global _conn
    if _conn is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.row_factory = sqlite3.Row
    return _conn

def close():
    global _conn
    if _conn:
        _conn.close()
        _conn = None

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS appeals (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            discord_username TEXT,
            discord_id TEXT,
            why_banned TEXT,
            why_unban TEXT,
            ban_reason TEXT,
            time_since_ban TEXT,
            extra_info TEXT,
            submitted_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            appeal_token TEXT NOT NULL,
            source TEXT DEFAULT 'discord'
        );
        CREATE TABLE IF NOT EXISTS appeal_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            punishment TEXT,
            total_points INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id TEXT PRIMARY KEY,
            added_by TEXT,
            added_at TEXT NOT NULL,
            note TEXT,
            duration TEXT,
            barred_until TEXT,
            username TEXT,
            avatar_url TEXT
        );
        CREATE TABLE IF NOT EXISTS staff_blacklist (
            user_id TEXT PRIMARY KEY,
            added_by TEXT,
            added_at TEXT NOT NULL,
            note TEXT,
            duration TEXT,
            barred_until TEXT,
            username TEXT,
            avatar_url TEXT
        );
        CREATE TABLE IF NOT EXISTS points (
            user_id TEXT PRIMARY KEY,
            points INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS cases (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            punishment TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            guild_id TEXT,
            moderator TEXT,
            reason TEXT,
            duration TEXT
        );
        CREATE TABLE IF NOT EXISTS kicked (
            user_id TEXT PRIMARY KEY,
            roblox_username TEXT,
            roblox_id TEXT,
            roblox_url TEXT,
            roblox_created TEXT,
            kicked_at TEXT NOT NULL
        );
    """)
    conn.commit()
    _migrate_from_json(conn)

def _migrate_from_json(conn):
    """One-time migration: import data from old JSON files if DB is empty."""
    base = os.path.dirname(os.path.dirname(__file__))
    json_files = {
        "points": "points.json",
        "cases": "cases.json",
        "appeals": "appeals.json",
        "kicked": "kicked.json",
        "appeal_tokens": "appeal_tokens.json",
        "blacklist": "blacklist.json",
        "staff_blacklist": "staff_blacklist.json",
    }
    for table, filename in json_files.items():
        path = os.path.join(base, filename)
        if not os.path.exists(path):
            continue
        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if row_count > 0:
            continue
        with open(path, "r") as f:
            data = json.load(f)
        if not data:
            continue
        if table == "points":
            conn.executemany(
                "INSERT OR IGNORE INTO points (user_id, points) VALUES (?, ?)",
                [(uid, pts) for uid, pts in data.items()]
            )
        elif table == "cases":
            for cid, cdata in data.items():
                conn.execute(
                    "INSERT OR IGNORE INTO cases (id, user_id, punishment, points, guild_id, moderator, reason, duration) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, cdata.get("user_id",""), cdata.get("punishment",""), cdata.get("points",0),
                     cdata.get("guild_id"), cdata.get("moderator"), cdata.get("reason"), cdata.get("duration"))
                )
        elif table == "appeals":
            for aid, adata in data.items():
                conn.execute(
                    "INSERT OR IGNORE INTO appeals (id, user_id, discord_username, discord_id, why_banned, why_unban, "
                    "ban_reason, time_since_ban, extra_info, submitted_at, status, appeal_token, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (aid, adata.get("user_id",""), adata.get("discord_username"), adata.get("discord_id"),
                     adata.get("why_banned"), adata.get("why_unban"), adata.get("ban_reason"),
                     adata.get("time_since_ban"), adata.get("extra_info"), adata.get("submitted_at",""),
                     adata.get("status","pending"), adata.get("appeal_token",""), adata.get("source","discord"))
                )
        elif table == "kicked":
            conn.executemany(
                "INSERT OR IGNORE INTO kicked (user_id, roblox_username, roblox_id, roblox_url, roblox_created, kicked_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(uid, kdata.get("roblox_username"), kdata.get("roblox_id"),
                  kdata.get("roblox_url"), kdata.get("roblox_created"), kdata.get("kicked_at",""))
                 for uid, kdata in data.items()]
            )
        elif table == "appeal_tokens":
            conn.executemany(
                "INSERT OR IGNORE INTO appeal_tokens (token, user_id, punishment, total_points, created_at, used) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(token, tdata.get("user_id",""), tdata.get("punishment"), tdata.get("total_points",0),
                  tdata.get("created_at",""), 1 if tdata.get("used") else 0)
                 for token, tdata in data.items()]
            )
        elif table in ("blacklist", "staff_blacklist"):
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} (user_id, added_by, added_at, note, duration, barred_until, username, avatar_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(uid, bdata.get("added_by"), bdata.get("added_at",""), bdata.get("note"),
                  bdata.get("duration"), bdata.get("barred_until"), bdata.get("username"), bdata.get("avatar_url"))
                 for uid, bdata in data.items()]
            )
        print(f"[MIGRATE] Imported {len(data)} rows from {filename}")
    conn.commit()

def load_all():
    """Load all data from SQLite into the global dict-style structures."""
    conn = get_conn()

    points = {}
    for row in conn.execute("SELECT user_id, points FROM points"):
        points[row["user_id"]] = row["points"]

    cases = {}
    for row in conn.execute("SELECT * FROM cases"):
        cases[row["id"]] = dict(row)

    appeals = {}
    for row in conn.execute("SELECT * FROM appeals"):
        appeals[row["id"]] = dict(row)

    kicked = {}
    for row in conn.execute("SELECT * FROM kicked"):
        kicked[row["user_id"]] = dict(row)

    appeal_tokens = {}
    for row in conn.execute("SELECT * FROM appeal_tokens"):
        appeal_tokens[row["token"]] = dict(row)
        appeal_tokens[row["token"]]["used"] = bool(appeal_tokens[row["token"]]["used"])

    blacklist = {}
    for row in conn.execute("SELECT * FROM blacklist"):
        blacklist[row["user_id"]] = dict(row)

    staff_blacklist = {}
    for row in conn.execute("SELECT * FROM staff_blacklist"):
        staff_blacklist[row["user_id"]] = dict(row)

    return points, cases, appeals, kicked, appeal_tokens, blacklist, staff_blacklist

def save_points(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM points")
        conn.executemany(
            "INSERT INTO points (user_id, points) VALUES (?, ?)",
            [(uid, pts) for uid, pts in data.items()]
        )
        conn.commit()

def save_cases(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM cases")
        for cid, cdata in data.items():
            conn.execute(
                "INSERT INTO cases (id, user_id, punishment, points, guild_id, moderator, reason, duration) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cid,
                 cdata.get("user_id", ""),
                 cdata.get("punishment", ""),
                 cdata.get("points", 0),
                 cdata.get("guild_id"),
                 cdata.get("moderator"),
                 cdata.get("reason"),
                 cdata.get("duration"))
            )
        conn.commit()

def save_appeals(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM appeals")
        for aid, adata in data.items():
            conn.execute(
                "INSERT INTO appeals (id, user_id, discord_username, discord_id, why_banned, why_unban, "
                "ban_reason, time_since_ban, extra_info, submitted_at, status, appeal_token, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aid,
                 adata.get("user_id", ""),
                 adata.get("discord_username"),
                 adata.get("discord_id"),
                 adata.get("why_banned"),
                 adata.get("why_unban"),
                 adata.get("ban_reason"),
                 adata.get("time_since_ban"),
                 adata.get("extra_info"),
                 adata.get("submitted_at", ""),
                 adata.get("status", "pending"),
                 adata.get("appeal_token", ""),
                 adata.get("source", "discord"))
            )
        conn.commit()

def save_kicked(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM kicked")
        for uid, kdata in data.items():
            conn.execute(
                "INSERT INTO kicked (user_id, roblox_username, roblox_id, roblox_url, roblox_created, kicked_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid,
                 kdata.get("roblox_username"),
                 kdata.get("roblox_id"),
                 kdata.get("roblox_url"),
                 kdata.get("roblox_created"),
                 kdata.get("kicked_at", ""))
            )
        conn.commit()

def save_appeal_tokens(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM appeal_tokens")
        for token, tdata in data.items():
            conn.execute(
                "INSERT INTO appeal_tokens (token, user_id, punishment, total_points, created_at, used) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token,
                 tdata.get("user_id", ""),
                 tdata.get("punishment"),
                 tdata.get("total_points", 0),
                 tdata.get("created_at", ""),
                 1 if tdata.get("used") else 0)
            )
        conn.commit()

def save_blacklist(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM blacklist")
        for uid, bdata in data.items():
            conn.execute(
                "INSERT INTO blacklist (user_id, added_by, added_at, note, duration, barred_until, username, avatar_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid,
                 bdata.get("added_by"),
                 bdata.get("added_at", ""),
                 bdata.get("note"),
                 bdata.get("duration"),
                 bdata.get("barred_until"),
                 bdata.get("username"),
                 bdata.get("avatar_url"))
            )
        conn.commit()

def save_staff_blacklist(data):
    with _lock:
        conn = get_conn()
        conn.execute("DELETE FROM staff_blacklist")
        for uid, bdata in data.items():
            conn.execute(
                "INSERT INTO staff_blacklist (user_id, added_by, added_at, note, duration, barred_until, username, avatar_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid,
                 bdata.get("added_by"),
                 bdata.get("added_at", ""),
                 bdata.get("note"),
                 bdata.get("duration"),
                 bdata.get("barred_until"),
                 bdata.get("username"),
                 bdata.get("avatar_url"))
            )
        conn.commit()
