"""
auth.py  —  Login, account management and signed tokens for PDF links

Accounts live in the database (see user_store.py) and are managed by an admin
inside the app. config.AUTH_BOOTSTRAP_ADMINS only creates the first admin.

    python auth.py            → print a password hash (for manual bootstrap)
    python auth.py --add      → create a user from the command line
"""

import os
import hmac
import time
import base64
import hashlib
import secrets
import threading
from typing import Optional, Tuple, Dict

import config as C
import user_store

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000


# ─────────────────────────────────────────────────────────────────────────────
# PASSWORD HASHING
# ─────────────────────────────────────────────────────────────────────────────
def hash_password(password: str, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return f"{_ALGO}${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != _ALGO:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def check_credentials(username: str, password: str) -> Optional[Dict]:
    """Returns the user record on success, else None (same timing for unknown users)."""
    user = user_store.get_user(username)
    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    if not user["is_active"]:
        return None
    return user


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT MANAGEMENT (used by the admin page)
# ─────────────────────────────────────────────────────────────────────────────
def validate_password(password: str) -> Tuple[bool, str]:
    if not password or len(password) < C.AUTH_MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {C.AUTH_MIN_PASSWORD_LENGTH} characters."
    if password.lower() in ("password", "12345678", "qwertyui"):
        return False, "Please choose a less predictable password."
    return True, ""


def create_user(username: str, password: str, role: str = "user", display_name: str = "",
                must_change_password: Optional[bool] = None,
                created_by: str = "") -> Tuple[bool, str]:
    username = (username or "").strip()
    if not user_store.USERNAME_RE.match(username):
        return False, "Username must be 3-32 characters: letters, digits, dot, dash or underscore."
    if user_store.get_user(username):
        return False, f"User '{username}' already exists."
    ok, message = validate_password(password)
    if not ok:
        return False, message
    if must_change_password is None:
        must_change_password = C.AUTH_NEW_USERS_MUST_CHANGE_PASSWORD
    created = user_store.insert_user(
        username, hash_password(password), role=role if role in ("admin", "user") else "user",
        display_name=display_name, must_change_password=must_change_password, created_by=created_by)
    if not created:
        return False, f"User '{username}' already exists."
    return True, f"User '{username}' created."


def set_user_password(username: str, password: str,
                      must_change_password: bool = False) -> Tuple[bool, str]:
    if not user_store.get_user(username):
        return False, "No such user."
    ok, message = validate_password(password)
    if not ok:
        return False, message
    user_store.update_password_hash(username, hash_password(password), must_change_password)
    return True, "Password updated."


def change_own_password(username: str, current_password: str, new_password: str,
                        repeat_password: str) -> Tuple[bool, str]:
    if not check_credentials(username, current_password):
        return False, "Your current password is not correct."
    if new_password != repeat_password:
        return False, "The new passwords do not match."
    if new_password == current_password:
        return False, "The new password must be different."
    return set_user_password(username, new_password, must_change_password=False)


def is_admin(username: str) -> bool:
    user = user_store.get_user(username)
    return bool(user and user["role"] == "admin" and user["is_active"])


# ─────────────────────────────────────────────────────────────────────────────
# SIGNED TOKENS FOR PDF LINKS
# ─────────────────────────────────────────────────────────────────────────────
_secret_lock = threading.Lock()
_secret_cache = {"value": None}


def _secret() -> bytes:
    if C.AUTH_SECRET_KEY:
        return C.AUTH_SECRET_KEY.encode("utf-8")
    with _secret_lock:
        if _secret_cache["value"] is None:
            path = os.path.join(C.BASE_DIR, ".auth_secret")
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(secrets.token_hex(32))
            with open(path, "r", encoding="utf-8") as f:
                _secret_cache["value"] = f.read().strip().encode("utf-8")
        return _secret_cache["value"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_pdf_token(username: str, hours: Optional[float] = None) -> str:
    expiry = int(time.time() + 3600 * (hours if hours is not None else C.PDF_LINK_TOKEN_HOURS))
    payload = f"{username}:{expiry}".encode("utf-8")
    sig = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()[:40]
    return f"{_b64(payload)}.{sig}"


def verify_pdf_token(token: str) -> Optional[str]:
    """Returns the username if the token is valid, unexpired and the account is active."""
    try:
        payload_b64, sig = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(_secret(), payload, hashlib.sha256).hexdigest()[:40]
        if not hmac.compare_digest(sig, expected):
            return None
        username, expiry = payload.decode("utf-8").rsplit(":", 1)
        if int(expiry) < time.time():
            return None
        if username != "cli" and not user_store.user_exists_active(username):
            return None
        return username
    except Exception:
        return None


def token_expiry(token: str) -> float:
    try:
        return float(_unb64(token.split(".", 1)[0]).decode("utf-8").rsplit(":", 1)[1])
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# FAILED-LOGIN TRACKER (shared by all browser sessions of this server)
# ─────────────────────────────────────────────────────────────────────────────
_fail_lock = threading.Lock()
_failures = {}


def _lock_remaining(username: str) -> int:
    with _fail_lock:
        rec = _failures.get(username)
        return int(rec[2] - time.time()) if rec and rec[2] > time.time() else 0


def _register_failure(username: str):
    with _fail_lock:
        now = time.time()
        rec = _failures.get(username)
        if not rec or now - rec[1] > C.AUTH_LOCKOUT_MINUTES * 60:
            rec = [0, now, 0]
        rec[0] += 1
        if rec[0] >= C.AUTH_MAX_FAILED_ATTEMPTS:
            rec = [0, now, now + C.AUTH_LOCKOUT_MINUTES * 60]
        _failures[username] = rec


def _clear_failures(username: str):
    with _fail_lock:
        _failures.pop(username, None)


# ─────────────────────────────────────────────────────────────────────────────
# STREAMLIT LOGIN GATE
# ─────────────────────────────────────────────────────────────────────────────
def logout(message: Optional[str] = None):
    import streamlit as st
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    if message:
        st.session_state["login_message"] = message
    st.rerun()


def require_login() -> str:
    """Call right after st.set_page_config(). Stops the script until login is complete."""
    import streamlit as st
    ss = st.session_state
    user_store.initialize_users()

    if not C.AUTH_ENABLED:
        ss.setdefault("authenticated", True)
        ss.setdefault("username", "local")
        ss.setdefault("role", "admin")
        ss.setdefault("pdf_token", "")
        return ss["username"]

    if ss.get("authenticated"):
        user = user_store.get_user(ss.get("username", ""))
        if not user or not user["is_active"]:
            logout("Your account is no longer active. Please contact the administrator.")
        if time.time() - ss.get("last_activity", 0) > C.AUTH_SESSION_TIMEOUT_MINUTES * 60:
            logout("Your session expired. Please sign in again.")
        ss["last_activity"] = time.time()
        ss["role"] = user["role"]
        if user["must_change_password"]:
            _render_password_change(user["username"])
            st.stop()
        if token_expiry(ss.get("pdf_token", "")) - time.time() < 3600:
            ss["pdf_token"] = make_pdf_token(user["username"])
        return user["username"]

    _render_login()
    st.stop()


def _hide_sidebar():
    import streamlit as st
    st.markdown("<style>[data-testid='stSidebar'],[data-testid='collapsedControl']{display:none}</style>",
                unsafe_allow_html=True)


def _render_login():
    import streamlit as st
    _hide_sidebar()
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.markdown("## 🔒 Local Document RAG")
        st.caption("Sign in to continue")
        message = st.session_state.pop("login_message", None)
        if message:
            st.info(message)
        with st.form("login_form"):
            username = st.text_input("Username", autocomplete="username")
            password = st.text_input("Password", type="password", autocomplete="current-password")
            submitted = st.form_submit_button("Sign in")

        if submitted:
            username = (username or "").strip()
            wait = _lock_remaining(username)
            if wait:
                st.error(f"Too many failed attempts. Try again in {wait // 60 + 1} minute(s).")
                return
            user = check_credentials(username, password or "")
            if user:
                _clear_failures(username)
                user_store.touch_login(username)
                st.session_state["authenticated"] = True
                st.session_state["username"] = user["username"]
                st.session_state["role"] = user["role"]
                st.session_state["last_activity"] = time.time()
                st.session_state["pdf_token"] = make_pdf_token(user["username"])
                st.rerun()
            else:
                _register_failure(username)
                time.sleep(1.0)
                st.error("Invalid username or password, or the account is disabled.")


def _render_password_change(username: str):
    import streamlit as st
    _hide_sidebar()
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.markdown("## 🔑 Choose a new password")
        st.caption(f"Signed in as **{username}**. You must set your own password before continuing.")
        with st.form("force_password_form"):
            current = st.text_input("Current password", type="password")
            new = st.text_input("New password", type="password")
            repeat = st.text_input("Repeat new password", type="password")
            submitted = st.form_submit_button("Save password")
        if submitted:
            ok, message = change_own_password(username, current or "", new or "", repeat or "")
            if ok:
                st.session_state["pdf_token"] = make_pdf_token(username)
                st.success("Password updated.")
                st.rerun()
            else:
                st.error(message)
        if st.button("Log out"):
            logout()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import getpass
    user_store.initialize_users()

    if "--add" in sys.argv:
        name = input("New username: ").strip()
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
        else:
            role = (input("Role [user/admin] (default user): ").strip() or "user")
            ok, message = create_user(name, pw, role=role, must_change_password=False,
                                      created_by="cli")
            print(message)
    else:
        name = input("Username: ").strip()
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
        else:
            ok, message = validate_password(pw)
            print(message if not ok else
                  "\nHash (for AUTH_BOOTSTRAP_ADMINS in config.py):\n\n"
                  f'    "{name}": "{hash_password(pw)}",')
