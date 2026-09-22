# """
# session_memory.py  —  per-user conversation memory (SQLite)

# Same idea as before (one session_id per user/conversation), with two additions:
#   • WAL mode + busy timeout, so several people can chat at the same time
#   • a `meta` column that stores the cited sources of each answer, so the source
#     links and figure images survive a page reload
# """

# import os
# import json
# import sqlite3
# from datetime import datetime
# from typing import List, Dict, Optional

# import config as C

# DATABASE_NAME = C.SESSION_DB_PATH
# DEFAULT_SESSION_ID = "default"


# def get_connection():
#     connection = sqlite3.connect(DATABASE_NAME, timeout=30, check_same_thread=False)
#     connection.execute("PRAGMA journal_mode=WAL")
#     connection.execute("PRAGMA busy_timeout=30000")
#     return connection


# def initialize_memory():
#     connection = get_connection()
#     cursor = connection.cursor()
#     cursor.execute(
#         """
#         CREATE TABLE IF NOT EXISTS conversation (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             session_id TEXT NOT NULL DEFAULT 'default',
#             role TEXT NOT NULL,
#             content TEXT NOT NULL,
#             timestamp TEXT NOT NULL,
#             meta TEXT
#         )
#         """
#     )
#     columns = [row[1] for row in cursor.execute("PRAGMA table_info(conversation)").fetchall()]
#     if "session_id" not in columns:
#         cursor.execute("ALTER TABLE conversation ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
#     if "meta" not in columns:
#         cursor.execute("ALTER TABLE conversation ADD COLUMN meta TEXT")
#     cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_session "
#                    "ON conversation(session_id, id)")
#     connection.commit()
#     connection.close()


# def add_message(role: str, content: str, session_id: str = DEFAULT_SESSION_ID,
#                 meta: Optional[Dict] = None):
#     connection = get_connection()
#     connection.execute(
#         "INSERT INTO conversation (session_id, role, content, timestamp, meta) "
#         "VALUES (?, ?, ?, ?, ?)",
#         (session_id, role, content, datetime.now().isoformat(timespec="seconds"),
#          json.dumps(meta, ensure_ascii=False) if meta else None),
#     )
#     connection.commit()
#     connection.close()


# def _rows_to_messages(rows) -> List[Dict]:
#     messages = []
#     for role, content, meta in rows:
#         try:
#             parsed = json.loads(meta) if meta else {}
#         except Exception:
#             parsed = {}
#         messages.append({"role": role, "content": content, "meta": parsed})
#     return messages


# def get_history(session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
#     connection = get_connection()
#     rows = connection.execute(
#         "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id ASC",
#         (session_id,)).fetchall()
#     connection.close()
#     return _rows_to_messages(rows)


# def get_recent_history(max_messages: int = 10,
#                        session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
#     connection = get_connection()
#     rows = connection.execute(
#         "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id DESC LIMIT ?",
#         (session_id, max_messages)).fetchall()
#     connection.close()
#     rows.reverse()
#     return _rows_to_messages(rows)


# def format_history(max_messages: int = 10, session_id: str = DEFAULT_SESSION_ID) -> str:
#     history = get_recent_history(max_messages, session_id)
#     if not history:
#         return "No previous conversation."
#     parts = []
#     for message in history:
#         content = message["content"]
#         if len(content) > 4000:
#             content = content[:4000] + " …[truncated]"
#         parts.append(f"{'User' if message['role'] == 'user' else 'Assistant'}: {content}")
#     return "\n".join(parts)


# def get_last_sources(session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
#     for message in reversed(get_recent_history(6, session_id)):
#         if message["role"] == "assistant" and message["meta"].get("sources"):
#             return message["meta"]["sources"]
#     return []


# def clear_memory(session_id: str = DEFAULT_SESSION_ID):
#     connection = get_connection()
#     connection.execute("DELETE FROM conversation WHERE session_id = ?", (session_id,))
#     connection.commit()
#     connection.close()


# def delete_session_database():
#     """Deletes the whole database (every user). Only use when nobody is connected."""
#     for suffix in ("", "-wal", "-shm"):
#         path = DATABASE_NAME + suffix
#         try:
#             if os.path.exists(path):
#                 os.remove(path)
#         except Exception as e:
#             print(f"Could not delete {path}: {e}")


# def show_memory(session_id: str = DEFAULT_SESSION_ID):
#     history = get_history(session_id)
#     print("\n" + "=" * 70 + "\nSESSION MEMORY\n" + "=" * 70)
#     if not history:
#         print("No conversation history.")
#     for message in history:
#         print(f"\n{'User' if message['role'] == 'user' else 'Assistant'}:\n{message['content']}")
#     print("=" * 70)


# if __name__ == "__main__":
#     initialize_memory()
#     print("Session memory database initialized.")


###################################  21st Sept Update ################################# 

# """
# session_memory.py  —  ChatGPT-style chats per user (SQLite)

#   • Every user has any number of chats ("conversations"); each login starts a new one.
#   • Old chats are listed in the sidebar with a short title that the model writes
#     from the first exchange (generated in the background, no waiting).
#   • Messages keep a `meta` column with the cited sources, so links and figure
#     images survive reloads.

# `session_id` in the message functions is the chat (conversation) id.
# """

# import os
# import json
# import uuid
# import sqlite3
# import threading
# from datetime import datetime
# from typing import List, Dict, Optional

# import config as C

# DATABASE_NAME = C.SESSION_DB_PATH
# DEFAULT_SESSION_ID = "default"
# NEW_CHAT_TITLE = "New chat"


# def get_connection():
#     connection = sqlite3.connect(DATABASE_NAME, timeout=30, check_same_thread=False)
#     connection.execute("PRAGMA journal_mode=WAL")
#     connection.execute("PRAGMA busy_timeout=30000")
#     return connection


# def _now() -> str:
#     return datetime.now().isoformat(timespec="seconds")


# def initialize_memory():
#     connection = get_connection()
#     cursor = connection.cursor()
#     cursor.execute(
#         """
#         CREATE TABLE IF NOT EXISTS conversation (
#             id INTEGER PRIMARY KEY AUTOINCREMENT,
#             session_id TEXT NOT NULL DEFAULT 'default',
#             role TEXT NOT NULL,
#             content TEXT NOT NULL,
#             timestamp TEXT NOT NULL,
#             meta TEXT
#         )
#         """
#     )
#     columns = [row[1] for row in cursor.execute("PRAGMA table_info(conversation)").fetchall()]
#     if "session_id" not in columns:
#         cursor.execute("ALTER TABLE conversation ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
#     if "meta" not in columns:
#         cursor.execute("ALTER TABLE conversation ADD COLUMN meta TEXT")
#     cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation(session_id, id)")

#     cursor.execute(
#         """
#         CREATE TABLE IF NOT EXISTS conversations (
#             id TEXT PRIMARY KEY,
#             username TEXT NOT NULL,
#             title TEXT NOT NULL,
#             title_generated INTEGER NOT NULL DEFAULT 0,
#             created_at TEXT NOT NULL,
#             updated_at TEXT NOT NULL
#         )
#         """
#     )
#     conv_columns = [row[1] for row in cursor.execute("PRAGMA table_info(conversations)").fetchall()]
#     if "title_generated" not in conv_columns:
#         cursor.execute("ALTER TABLE conversations ADD COLUMN title_generated INTEGER NOT NULL DEFAULT 0")
#     cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(username, updated_at)")

#     # Migrate the previous version's single chat per user ("user::<name>") into a listed chat
#     old_sessions = cursor.execute(
#         "SELECT DISTINCT session_id FROM conversation WHERE session_id LIKE 'user::%' "
#         "AND session_id NOT IN (SELECT id FROM conversations)").fetchall()
#     for (session_id,) in old_sessions:
#         cursor.execute(
#             "INSERT OR IGNORE INTO conversations (id, username, title, title_generated, "
#             "created_at, updated_at) VALUES (?, ?, 'Earlier conversation', 0, ?, ?)",
#             (session_id, session_id.split("::", 1)[1], _now(), _now()))
#     connection.commit()
#     connection.close()


# # ─────────────────────────────────────────────────────────────────────────────
# # CHATS
# # ─────────────────────────────────────────────────────────────────────────────
# def create_conversation(username: str, first_question: str = "") -> str:
#     """Creates a chat. The provisional title is the start of the first question;
#     a proper title is generated after the first answer."""
#     conversation_id = str(uuid.uuid4())
#     title = " ".join((first_question or NEW_CHAT_TITLE).split())
#     title = (title[:50] + "…") if len(title) > 50 else (title or NEW_CHAT_TITLE)
#     connection = get_connection()
#     connection.execute(
#         "INSERT INTO conversations (id, username, title, title_generated, created_at, updated_at) "
#         "VALUES (?, ?, ?, 0, ?, ?)",
#         (conversation_id, username, title, _now(), _now()))
#     connection.commit()
#     connection.close()
#     return conversation_id


# def list_conversations(username: str, limit: int = 100) -> List[Dict]:
#     connection = get_connection()
#     rows = connection.execute(
#         """
#         SELECT c.id, c.title, c.updated_at,
#                (SELECT COUNT(*) FROM conversation m WHERE m.session_id = c.id)
#         FROM conversations c WHERE c.username = ?
#         ORDER BY c.updated_at DESC LIMIT ?
#         """, (username, limit)).fetchall()
#     connection.close()
#     return [{"id": r[0], "title": r[1], "updated_at": r[2], "messages": r[3]}
#             for r in rows if r[3] > 0]          # empty chats are not listed


# def get_conversation(conversation_id: str) -> Optional[Dict]:
#     connection = get_connection()
#     row = connection.execute(
#         "SELECT id, username, title, title_generated, created_at, updated_at "
#         "FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
#     connection.close()
#     if not row:
#         return None
#     return dict(zip(["id", "username", "title", "title_generated", "created_at", "updated_at"], row))


# def user_owns_conversation(username: str, conversation_id: str) -> bool:
#     conversation = get_conversation(conversation_id)
#     return bool(conversation and conversation["username"] == username)


# def rename_conversation(conversation_id: str, title: str):
#     title = " ".join((title or "").split())[:80]
#     if not title:
#         return
#     connection = get_connection()
#     connection.execute("UPDATE conversations SET title = ?, title_generated = 1 WHERE id = ?",
#                        (title, conversation_id))
#     connection.commit()
#     connection.close()


# def delete_conversation(conversation_id: str):
#     connection = get_connection()
#     connection.execute("DELETE FROM conversation WHERE session_id = ?", (conversation_id,))
#     connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
#     connection.commit()
#     connection.close()


# def delete_user_conversations(username: str):
#     connection = get_connection()
#     connection.execute("DELETE FROM conversation WHERE session_id IN "
#                        "(SELECT id FROM conversations WHERE username = ?)", (username,))
#     connection.execute("DELETE FROM conversations WHERE username = ?", (username,))
#     connection.commit()
#     connection.close()


# # ─────────────────────────────────────────────────────────────────────────────
# # AUTOMATIC TITLES
# # ─────────────────────────────────────────────────────────────────────────────
# TITLE_PROMPT = """Write a short title (3 to 6 words) for this conversation, like a chat-history title.
# Name the specific subject (paper, figure, table, method or topic). No quotes, no final period.

# User: {question}
# Assistant: {answer}

# Title:"""

# _title_lock = threading.Lock()
# _titling = set()


# def _generate_title(conversation_id: str):
#     conversation = get_conversation(conversation_id)
#     if not conversation or conversation["title_generated"]:
#         return
#     connection = get_connection()
#     rows = connection.execute(
#         "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id ASC LIMIT 12",
#         (conversation_id,)).fetchall()
#     connection.close()

#     question, answer = "", ""
#     for role, content, meta in rows:
#         parsed = json.loads(meta) if meta else {}
#         if role == "user":
#             question = content
#         elif role == "assistant" and not parsed.get("choose_mode") and not parsed.get("stopped_early"):
#             answer = content
#             break
#     if not answer:
#         return        # wait for a real answer (not the "where should I search?" question)

#     try:
#         from rag_common import chat_text
#         title = chat_text([{"role": "user", "content": TITLE_PROMPT.format(
#             question=question[:800], answer=answer[:1500])}], max_tokens=60, temperature=0.2)
#         title = title.strip().split("\n")[0].strip(" \"'.#*`")
#         if title.lower().startswith("title:"):
#             title = title[6:].strip()
#     except Exception as e:
#         print(f"[Chat title] generation failed: {e}")
#         return
#     if 2 <= len(title) <= 80:
#         connection = get_connection()
#         connection.execute("UPDATE conversations SET title = ?, title_generated = 1 WHERE id = ?",
#                            (title, conversation_id))
#         connection.commit()
#         connection.close()


# def maybe_generate_title(conversation_id: str, background: bool = True):
#     with _title_lock:
#         if conversation_id in _titling:
#             return
#         _titling.add(conversation_id)

#     def run():
#         try:
#             _generate_title(conversation_id)
#         finally:
#             with _title_lock:
#                 _titling.discard(conversation_id)

#     if background:
#         threading.Thread(target=run, daemon=True).start()
#     else:
#         run()


# # ─────────────────────────────────────────────────────────────────────────────
# # MESSAGES
# # ─────────────────────────────────────────────────────────────────────────────
# def add_message(role: str, content: str, session_id: str = DEFAULT_SESSION_ID,
#                 meta: Optional[Dict] = None):
#     connection = get_connection()
#     connection.execute(
#         "INSERT INTO conversation (session_id, role, content, timestamp, meta) VALUES (?, ?, ?, ?, ?)",
#         (session_id, role, content, _now(), json.dumps(meta, ensure_ascii=False) if meta else None))
#     connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), session_id))
#     connection.commit()
#     connection.close()


# def _rows_to_messages(rows) -> List[Dict]:
#     messages = []
#     for role, content, meta in rows:
#         try:
#             parsed = json.loads(meta) if meta else {}
#         except Exception:
#             parsed = {}
#         messages.append({"role": role, "content": content, "meta": parsed})
#     return messages


# def get_history(session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
#     connection = get_connection()
#     rows = connection.execute(
#         "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id ASC",
#         (session_id,)).fetchall()
#     connection.close()
#     return _rows_to_messages(rows)


# def get_recent_history(max_messages: int = 10, session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
#     connection = get_connection()
#     rows = connection.execute(
#         "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id DESC LIMIT ?",
#         (session_id, max_messages)).fetchall()
#     connection.close()
#     rows.reverse()
#     return _rows_to_messages(rows)


# def format_history(max_messages: int = 10, session_id: str = DEFAULT_SESSION_ID) -> str:
#     history = get_recent_history(max_messages, session_id)
#     if not history:
#         return "No previous conversation."
#     parts = []
#     for message in history:
#         content = message["content"]
#         if len(content) > 4000:
#             content = content[:4000] + " …[truncated]"
#         parts.append(f"{'User' if message['role'] == 'user' else 'Assistant'}: {content}")
#     return "\n".join(parts)


# def get_last_sources(session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
#     for message in reversed(get_recent_history(6, session_id)):
#         if message["role"] == "assistant" and message["meta"].get("sources"):
#             return message["meta"]["sources"]
#     return []


# def clear_memory(session_id: str = DEFAULT_SESSION_ID):
#     connection = get_connection()
#     connection.execute("DELETE FROM conversation WHERE session_id = ?", (session_id,))
#     connection.commit()
#     connection.close()


# def delete_session_database():
#     """Deletes the whole database (every user). Only use when nobody is connected."""
#     for suffix in ("", "-wal", "-shm"):
#         path = DATABASE_NAME + suffix
#         try:
#             if os.path.exists(path):
#                 os.remove(path)
#         except Exception as e:
#             print(f"Could not delete {path}: {e}")


# def show_memory(session_id: str = DEFAULT_SESSION_ID):
#     history = get_history(session_id)
#     print("\n" + "=" * 70 + "\nSESSION MEMORY\n" + "=" * 70)
#     if not history:
#         print("No conversation history.")
#     for message in history:
#         print(f"\n{'User' if message['role'] == 'user' else 'Assistant'}:\n{message['content']}")
#     print("=" * 70)


# if __name__ == "__main__":
#     initialize_memory()
#     print("Session memory database initialized.")





#################################### 22nd Sept Update ####################################

"""
session_memory.py  —  ChatGPT-style chats per user (SQLite)

  • Every user has any number of chats ("conversations"); each login starts a new one.
  • Each chat remembers its ENTIRE conversation and nothing from other chats.
    Recent messages are sent word for word; when a chat grows past
    config.CHAT_HISTORY_TOKEN_BUDGET its oldest messages are folded into a running
    summary of that chat (first in, first out), so no part of the chat is forgotten.
  • Old chats are listed in the sidebar with a short title that the model writes
    from the first exchange (generated in the background, no waiting).
  • Messages keep a `meta` column with the cited sources, so links and figure
    images survive reloads.

`session_id` in the message functions is the chat (conversation) id.
"""

import os
import json
import uuid
import sqlite3
import threading
from datetime import datetime
from typing import List, Dict, Optional

import config as C

DATABASE_NAME = C.SESSION_DB_PATH
DEFAULT_SESSION_ID = "default"
NEW_CHAT_TITLE = "New chat"


def get_connection():
    connection = sqlite3.connect(DATABASE_NAME, timeout=30, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def initialize_memory():
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL DEFAULT 'default',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            meta TEXT
        )
        """
    )
    columns = [row[1] for row in cursor.execute("PRAGMA table_info(conversation)").fetchall()]
    if "session_id" not in columns:
        cursor.execute("ALTER TABLE conversation ADD COLUMN session_id TEXT NOT NULL DEFAULT 'default'")
    if "meta" not in columns:
        cursor.execute("ALTER TABLE conversation ADD COLUMN meta TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_session ON conversation(session_id, id)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            title TEXT NOT NULL,
            title_generated INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conv_columns = [row[1] for row in cursor.execute("PRAGMA table_info(conversations)").fetchall()]
    if "title_generated" not in conv_columns:
        cursor.execute("ALTER TABLE conversations ADD COLUMN title_generated INTEGER NOT NULL DEFAULT 0")
    if "summary" not in conv_columns:
        cursor.execute("ALTER TABLE conversations ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    if "summarized_until" not in conv_columns:
        # id of the last message already folded into the summary
        cursor.execute("ALTER TABLE conversations ADD COLUMN summarized_until INTEGER NOT NULL DEFAULT 0")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(username, updated_at)")

    # Migrate the previous version's single chat per user ("user::<name>") into a listed chat
    old_sessions = cursor.execute(
        "SELECT DISTINCT session_id FROM conversation WHERE session_id LIKE 'user::%' "
        "AND session_id NOT IN (SELECT id FROM conversations)").fetchall()
    for (session_id,) in old_sessions:
        cursor.execute(
            "INSERT OR IGNORE INTO conversations (id, username, title, title_generated, "
            "created_at, updated_at) VALUES (?, ?, 'Earlier conversation', 0, ?, ?)",
            (session_id, session_id.split("::", 1)[1], _now(), _now()))
    connection.commit()
    connection.close()


# ─────────────────────────────────────────────────────────────────────────────
# CHATS
# ─────────────────────────────────────────────────────────────────────────────
def create_conversation(username: str, first_question: str = "") -> str:
    """Creates a chat. The provisional title is the start of the first question;
    a proper title is generated after the first answer."""
    conversation_id = str(uuid.uuid4())
    title = " ".join((first_question or NEW_CHAT_TITLE).split())
    title = (title[:50] + "…") if len(title) > 50 else (title or NEW_CHAT_TITLE)
    connection = get_connection()
    connection.execute(
        "INSERT INTO conversations (id, username, title, title_generated, created_at, updated_at) "
        "VALUES (?, ?, ?, 0, ?, ?)",
        (conversation_id, username, title, _now(), _now()))
    connection.commit()
    connection.close()
    return conversation_id


def list_conversations(username: str, limit: int = 100) -> List[Dict]:
    connection = get_connection()
    rows = connection.execute(
        """
        SELECT c.id, c.title, c.updated_at,
               (SELECT COUNT(*) FROM conversation m WHERE m.session_id = c.id)
        FROM conversations c WHERE c.username = ?
        ORDER BY c.updated_at DESC LIMIT ?
        """, (username, limit)).fetchall()
    connection.close()
    return [{"id": r[0], "title": r[1], "updated_at": r[2], "messages": r[3]}
            for r in rows if r[3] > 0]          # empty chats are not listed


def get_conversation(conversation_id: str) -> Optional[Dict]:
    connection = get_connection()
    row = connection.execute(
        "SELECT id, username, title, title_generated, created_at, updated_at, summary, "
        "summarized_until FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    connection.close()
    if not row:
        return None
    return dict(zip(["id", "username", "title", "title_generated", "created_at", "updated_at",
                     "summary", "summarized_until"], row))


def user_owns_conversation(username: str, conversation_id: str) -> bool:
    conversation = get_conversation(conversation_id)
    return bool(conversation and conversation["username"] == username)


def rename_conversation(conversation_id: str, title: str):
    title = " ".join((title or "").split())[:80]
    if not title:
        return
    connection = get_connection()
    connection.execute("UPDATE conversations SET title = ?, title_generated = 1 WHERE id = ?",
                       (title, conversation_id))
    connection.commit()
    connection.close()


def delete_conversation(conversation_id: str):
    connection = get_connection()
    connection.execute("DELETE FROM conversation WHERE session_id = ?", (conversation_id,))
    connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    connection.commit()
    connection.close()


def delete_user_conversations(username: str):
    connection = get_connection()
    connection.execute("DELETE FROM conversation WHERE session_id IN "
                       "(SELECT id FROM conversations WHERE username = ?)", (username,))
    connection.execute("DELETE FROM conversations WHERE username = ?", (username,))
    connection.commit()
    connection.close()


# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATIC TITLES
# ─────────────────────────────────────────────────────────────────────────────
TITLE_PROMPT = """Write a short title (3 to 6 words) for this conversation, like a chat-history title.
Name the specific subject (paper, figure, table, method or topic). No quotes, no final period.

User: {question}
Assistant: {answer}

Title:"""

_title_lock = threading.Lock()
_titling = set()


def _generate_title(conversation_id: str):
    conversation = get_conversation(conversation_id)
    if not conversation or conversation["title_generated"]:
        return
    connection = get_connection()
    rows = connection.execute(
        "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id ASC LIMIT 12",
        (conversation_id,)).fetchall()
    connection.close()

    question, answer = "", ""
    for role, content, meta in rows:
        parsed = json.loads(meta) if meta else {}
        if role == "user":
            question = content
        elif role == "assistant" and not parsed.get("choose_mode") and not parsed.get("stopped_early"):
            answer = content
            break
    if not answer:
        return        # wait for a real answer (not the "where should I search?" question)

    try:
        from rag_common import chat_text
        title = chat_text([{"role": "user", "content": TITLE_PROMPT.format(
            question=question[:800], answer=answer[:1500])}], max_tokens=60, temperature=0.2)
        title = title.strip().split("\n")[0].strip(" \"'.#*`")
        if title.lower().startswith("title:"):
            title = title[6:].strip()
    except Exception as e:
        print(f"[Chat title] generation failed: {e}")
        return
    if 2 <= len(title) <= 80:
        connection = get_connection()
        connection.execute("UPDATE conversations SET title = ?, title_generated = 1 WHERE id = ?",
                           (title, conversation_id))
        connection.commit()
        connection.close()


def maybe_generate_title(conversation_id: str, background: bool = True):
    with _title_lock:
        if conversation_id in _titling:
            return
        _titling.add(conversation_id)

    def run():
        try:
            _generate_title(conversation_id)
        finally:
            with _title_lock:
                _titling.discard(conversation_id)

    if background:
        threading.Thread(target=run, daemon=True).start()
    else:
        run()


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGES
# ─────────────────────────────────────────────────────────────────────────────
def add_message(role: str, content: str, session_id: str = DEFAULT_SESSION_ID,
                meta: Optional[Dict] = None):
    connection = get_connection()
    connection.execute(
        "INSERT INTO conversation (session_id, role, content, timestamp, meta) VALUES (?, ?, ?, ?, ?)",
        (session_id, role, content, _now(), json.dumps(meta, ensure_ascii=False) if meta else None))
    connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), session_id))
    connection.commit()
    connection.close()


def _rows_to_messages(rows) -> List[Dict]:
    messages = []
    for role, content, meta in rows:
        try:
            parsed = json.loads(meta) if meta else {}
        except Exception:
            parsed = {}
        messages.append({"role": role, "content": content, "meta": parsed})
    return messages


def get_history(session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
    connection = get_connection()
    rows = connection.execute(
        "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id ASC",
        (session_id,)).fetchall()
    connection.close()
    return _rows_to_messages(rows)


def get_recent_history(max_messages: int = 10, session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
    connection = get_connection()
    rows = connection.execute(
        "SELECT role, content, meta FROM conversation WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, max_messages)).fetchall()
    connection.close()
    rows.reverse()
    return _rows_to_messages(rows)


# ─────────────────────────────────────────────────────────────────────────────
# FULL-CHAT MEMORY
# ─────────────────────────────────────────────────────────────────────────────
def _tokens(text: str) -> int:
    from rag_common import count_tokens
    return count_tokens(text)


def _memory_rows(session_id: str, after_id: int = 0) -> List[tuple]:
    """(id, role, content) of every message that counts as conversation memory.
    The "where should I search?" prompt and the duplicate question before it are skipped."""
    connection = get_connection()
    rows = connection.execute(
        "SELECT id, role, content, meta FROM conversation WHERE session_id = ? AND id > ? "
        "ORDER BY id ASC", (session_id, after_id)).fetchall()
    connection.close()

    kept = []
    for message_id, role, content, meta in rows:
        try:
            parsed = json.loads(meta) if meta else {}
        except Exception:
            parsed = {}
        if role == "assistant" and parsed.get("choose_mode"):
            if kept and kept[-1][1] == "user":
                kept.pop()           # the same question is asked again after the choice
            continue
        kept.append((message_id, role, content))
    return kept


def _line(role: str, content: str) -> str:
    return f"{'User' if role == 'user' else 'Assistant'}: {content}"


def format_history(max_messages: Optional[int] = None, session_id: str = DEFAULT_SESSION_ID,
                   token_budget: Optional[int] = None, include_summary: bool = True) -> str:
    """The memory of ONE chat: its running summary (older part) + every newer message.

    Nothing from other chats is ever included. `max_messages` is accepted for backward
    compatibility and no longer limits the memory unless explicitly given.
    """
    conversation = get_conversation(session_id) or {}
    summary = (conversation.get("summary") or "").strip()
    rows = _memory_rows(session_id, int(conversation.get("summarized_until") or 0))
    if max_messages:
        rows = rows[-max_messages:]

    budget = token_budget if token_budget is not None else C.CHAT_HISTORY_TOKEN_BUDGET
    lines, used = [], 0
    for _message_id, role, content in reversed(rows):
        line = _line(role, content)
        cost = _tokens(line)
        if lines and used + cost > budget:
            break
        if not lines and cost > budget:          # one enormous message: keep its end
            line = _line(role, "…" + content[-int(budget * 3):])
            cost = _tokens(line)
        lines.append(line)
        used += cost
    lines.reverse()
    omitted = len(rows) - len(lines)

    parts = []
    if summary and include_summary:
        parts.append("SUMMARY OF THE EARLIER PART OF THIS CHAT:\n" + summary)
    if omitted > 0:
        parts.append(f"({omitted} older message(s) of this chat are not shown word for word.)")
    if lines:
        parts.append(("RECENT MESSAGES OF THIS CHAT:\n" if parts else "") + "\n".join(lines))
    return "\n\n".join(parts) if parts else "No previous conversation."


def get_memory_stats(session_id: str) -> Dict:
    conversation = get_conversation(session_id) or {}
    rows = _memory_rows(session_id, int(conversation.get("summarized_until") or 0))
    connection = get_connection()
    total = connection.execute("SELECT COUNT(*) FROM conversation WHERE session_id = ?",
                               (session_id,)).fetchone()[0]
    connection.close()
    summary = conversation.get("summary") or ""
    return {
        "messages": total,
        "verbatim_messages": len(rows),
        "verbatim_tokens": sum(_tokens(_line(r, c)) for _, r, c in rows),
        "summary_tokens": _tokens(summary) if summary else 0,
        "budget": C.CHAT_HISTORY_TOKEN_BUDGET,
    }


SUMMARY_PROMPT = """You keep the memory of ONE chat between a user and a document assistant.
Merge the EXISTING SUMMARY with the OLDER MESSAGES below into one updated summary of the chat.

Keep, concisely and in bullet points:
- every question the user asked and the key answer to it
- documents, sections, figures, tables, equations and algorithms discussed, with file names,
  labels and page numbers exactly as written
- numbers, results, definitions and conclusions that were given
- the user's goals, preferences, corrections and decisions
- anything left open or promised for later

Never invent anything. Keep names, labels, numbers and page numbers exactly.
Keep the summary under {words} words. Return ONLY the summary.

EXISTING SUMMARY:
{summary}

OLDER MESSAGES (oldest first):
{messages}

UPDATED SUMMARY:"""

_chat_locks: Dict[str, threading.Lock] = {}
_chat_locks_guard = threading.Lock()


def _chat_lock(session_id: str) -> threading.Lock:
    with _chat_locks_guard:
        if session_id not in _chat_locks:
            _chat_locks[session_id] = threading.Lock()
        return _chat_locks[session_id]


def _summarize(existing: str, rows: List[tuple]) -> Optional[str]:
    from rag_common import chat_text
    messages = "\n\n".join(_line(role, content[:12000]) for _, role, content in rows)
    try:
        text = chat_text([{"role": "user", "content": SUMMARY_PROMPT.format(
            words=int(C.CHAT_SUMMARY_MAX_TOKENS * 0.7), summary=existing or "(none yet)",
            messages=messages)}], max_tokens=int(C.CHAT_SUMMARY_MAX_TOKENS * 1.5) + 200,
            temperature=0.0)
    except Exception as e:
        print(f"[Chat memory] summarising failed, will retry later: {e}")
        return None
    return text.strip() or None


def compact_chat_memory(session_id: str, force: bool = False) -> bool:
    """Folds the oldest messages of a chat into its summary when the chat is over budget."""
    with _chat_lock(session_id):
        conversation = get_conversation(session_id)
        if not conversation:
            return False
        rows = _memory_rows(session_id, int(conversation.get("summarized_until") or 0))
        costs = [_tokens(_line(role, content)) for _, role, content in rows]
        if not rows or (not force and sum(costs) <= C.CHAT_HISTORY_TOKEN_BUDGET):
            return False

        # keep the newest messages word for word, summarise everything before them
        keep_from, kept = len(rows), 0
        for index in range(len(rows) - 1, -1, -1):
            if len(rows) - index <= 2 or kept + costs[index] <= C.CHAT_KEEP_RECENT_TOKENS:
                kept += costs[index]
                keep_from = index
            else:
                break
        while keep_from > 0 and rows[keep_from - 1][1] == "user":
            keep_from -= 1                       # never separate a question from its answer
        old = rows[:keep_from]
        if not old:
            return False

        summary = conversation.get("summary") or ""
        batch, batch_tokens = [], 0
        for row, cost in zip(old, costs[:keep_from]):
            if batch and batch_tokens + cost > C.CHAT_SUMMARY_BATCH_TOKENS:
                summary = _summarize(summary, batch)
                if summary is None:
                    return False
                batch, batch_tokens = [], 0
            batch.append(row)
            batch_tokens += cost
        if batch:
            summary = _summarize(summary, batch)
            if summary is None:
                return False

        connection = get_connection()
        connection.execute("UPDATE conversations SET summary = ?, summarized_until = ? WHERE id = ?",
                           (summary, old[-1][0], session_id))
        connection.commit()
        connection.close()
        print(f"[Chat memory] {session_id[:8]}: {len(old)} older message(s) folded into the summary")
        return True


def chat_memory_needs_compaction(session_id: str) -> bool:
    conversation = get_conversation(session_id) or {}
    rows = _memory_rows(session_id, int(conversation.get("summarized_until") or 0))
    return sum(_tokens(_line(r, c)) for _, r, c in rows) > C.CHAT_HISTORY_TOKEN_BUDGET


def ensure_chat_memory_fits(session_id: str) -> bool:
    """Called before answering: if the chat is over budget (and the background summary
    has not caught up yet), summarise now so the whole chat is represented."""
    if chat_memory_needs_compaction(session_id):
        return compact_chat_memory(session_id)
    return False


def compact_in_background(session_id: str):
    def run():
        try:
            compact_chat_memory(session_id)
        except Exception as e:
            print(f"[Chat memory] background summarising error: {e}")
    threading.Thread(target=run, daemon=True).start()


def get_last_sources(session_id: str = DEFAULT_SESSION_ID) -> List[Dict]:
    for message in reversed(get_recent_history(6, session_id)):
        if message["role"] == "assistant" and message["meta"].get("sources"):
            return message["meta"]["sources"]
    return []


def clear_memory(session_id: str = DEFAULT_SESSION_ID):
    connection = get_connection()
    connection.execute("DELETE FROM conversation WHERE session_id = ?", (session_id,))
    connection.execute("UPDATE conversations SET summary = '', summarized_until = 0 WHERE id = ?",
                       (session_id,))
    connection.commit()
    connection.close()


def delete_session_database():
    """Deletes the whole database (every user). Only use when nobody is connected."""
    for suffix in ("", "-wal", "-shm"):
        path = DATABASE_NAME + suffix
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            print(f"Could not delete {path}: {e}")


def show_memory(session_id: str = DEFAULT_SESSION_ID):
    history = get_history(session_id)
    print("\n" + "=" * 70 + "\nSESSION MEMORY\n" + "=" * 70)
    if not history:
        print("No conversation history.")
    for message in history:
        print(f"\n{'User' if message['role'] == 'user' else 'Assistant'}:\n{message['content']}")
    print("=" * 70)


if __name__ == "__main__":
    initialize_memory()
    print("Session memory database initialized.")