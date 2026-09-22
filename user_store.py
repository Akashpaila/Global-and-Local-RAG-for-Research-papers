"""
user_store.py  —  User accounts stored in the app database.

Accounts are created and managed by an admin inside the app (sidebar → "🛠️ Users").
config.AUTH_BOOTSTRAP_ADMINS is only used to create the first admin account.
This module never handles plain passwords: it stores and returns hashes only
(hashing and verification live in auth.py).
"""

import re
import sqlite3
import threading
from datetime import datetime
from typing import List, Dict, Optional

import config as C

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
_init_lock = threading.Lock()
_initialized = {"value": False}


def _connect():
    connection = sqlite3.connect(C.SESSION_DB_PATH, timeout=30, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def initialize_users():
    with _init_lock:
        connection = _connect()
        cur = connection.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                display_name TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                memory_enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT '',
                last_login TEXT
            )
            """
        )
        # Bootstrap the first admin(s) only if they don't exist yet
        for username, password_hash in (C.AUTH_BOOTSTRAP_ADMINS or {}).items():
            cur.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, role, display_name, "
                "is_active, must_change_password, memory_enabled, created_at, created_by) "
                "VALUES (?, ?, 'admin', ?, 1, 0, 1, ?, 'bootstrap')",
                (username, password_hash, username, _now()),
            )
        connection.commit()
        connection.close()
        _initialized["value"] = True


def _ensure_ready():
    if not _initialized["value"]:
        initialize_users()


def get_user(username: str) -> Optional[Dict]:
    _ensure_ready()
    connection = _connect()
    row = connection.execute(
        "SELECT username, password_hash, role, display_name, is_active, must_change_password, "
        "memory_enabled, created_at, created_by, last_login FROM users WHERE username = ?",
        (username or "",),
    ).fetchone()
    connection.close()
    if not row:
        return None
    keys = ["username", "password_hash", "role", "display_name", "is_active",
            "must_change_password", "memory_enabled", "created_at", "created_by", "last_login"]
    user = dict(zip(keys, row))
    for flag in ("is_active", "must_change_password", "memory_enabled"):
        user[flag] = bool(user[flag])
    return user


def list_users() -> List[Dict]:
    _ensure_ready()
    connection = _connect()
    base = ("SELECT u.username, u.role, u.display_name, u.is_active, u.must_change_password, "
            "u.memory_enabled, u.created_at, u.created_by, u.last_login, ")
    order = " FROM users u ORDER BY u.role DESC, u.username ASC"
    try:
        rows = connection.execute(
            base + "(SELECT COUNT(*) FROM conversations c WHERE c.username = u.username)" + order
        ).fetchall()
    except sqlite3.OperationalError:          # chats table not created yet
        rows = connection.execute(base + "0" + order).fetchall()
    connection.close()
    keys = ["username", "role", "display_name", "is_active", "must_change_password",
            "memory_enabled", "created_at", "created_by", "last_login", "chats"]
    users = []
    for row in rows:
        user = dict(zip(keys, row))
        for flag in ("is_active", "must_change_password", "memory_enabled"):
            user[flag] = bool(user[flag])
        users.append(user)
    return users


def user_exists_active(username: str) -> bool:
    user = get_user(username)
    return bool(user and user["is_active"])


def active_admin_count() -> int:
    _ensure_ready()
    connection = _connect()
    n = connection.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1").fetchone()[0]
    connection.close()
    return int(n)


def insert_user(username: str, password_hash: str, role: str = "user",
                display_name: str = "", must_change_password: bool = True,
                created_by: str = "") -> bool:
    _ensure_ready()
    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO users (username, password_hash, role, display_name, is_active, "
            "must_change_password, memory_enabled, created_at, created_by) "
            "VALUES (?, ?, ?, ?, 1, ?, 1, ?, ?)",
            (username, password_hash, role, display_name or username,
             1 if must_change_password else 0, _now(), created_by),
        )
        connection.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        connection.close()


def update_password_hash(username: str, password_hash: str, must_change_password: bool = False):
    _ensure_ready()
    connection = _connect()
    connection.execute(
        "UPDATE users SET password_hash = ?, must_change_password = ? WHERE username = ?",
        (password_hash, 1 if must_change_password else 0, username),
    )
    connection.commit()
    connection.close()


def set_role(username: str, role: str):
    connection = _connect()
    connection.execute("UPDATE users SET role = ? WHERE username = ?",
                       (role if role in ("admin", "user") else "user", username))
    connection.commit()
    connection.close()


def set_active(username: str, active: bool):
    connection = _connect()
    connection.execute("UPDATE users SET is_active = ? WHERE username = ?",
                       (1 if active else 0, username))
    connection.commit()
    connection.close()


def set_memory_enabled(username: str, enabled: bool):
    connection = _connect()
    connection.execute("UPDATE users SET memory_enabled = ? WHERE username = ?",
                       (1 if enabled else 0, username))
    connection.commit()
    connection.close()


def set_display_name(username: str, display_name: str):
    connection = _connect()
    connection.execute("UPDATE users SET display_name = ? WHERE username = ?",
                       ((display_name or "").strip()[:60], username))
    connection.commit()
    connection.close()


def touch_login(username: str):
    connection = _connect()
    connection.execute("UPDATE users SET last_login = ? WHERE username = ?", (_now(), username))
    connection.commit()
    connection.close()


def delete_user(username: str, delete_data: bool = True):
    """Removes the account and (optionally) all of its chats, messages and memories."""
    connection = _connect()
    if delete_data:
        for statement, params in (
            ("DELETE FROM conversation WHERE session_id IN "
             "(SELECT id FROM conversations WHERE username = ?)", (username,)),
            ("DELETE FROM conversations WHERE username = ?", (username,)),
            ("DELETE FROM user_memory WHERE username = ?", (username,)),
        ):
            try:
                connection.execute(statement, params)
            except sqlite3.OperationalError:
                pass       # that table does not exist yet
    connection.execute("DELETE FROM users WHERE username = ?", (username,))
    connection.commit()
    connection.close()


def memory_enabled_for(username: str) -> bool:
    if not C.USER_MEMORY_ENABLED:
        return False
    user = get_user(username)
    return bool(user and user["memory_enabled"])
