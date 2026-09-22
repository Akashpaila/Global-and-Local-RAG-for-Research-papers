# import os
# import streamlit as st
# import streamlit.components.v1 as components

# st.set_page_config(page_title="Local + Global RAG", page_icon="📚", layout="wide")

# import config as C
# import auth
# import user_store
# from rag_common import pdf_url, render_pdf_page
# from session_memory import initialize_memory, get_history, clear_memory

# initialize_memory()
# user_store.initialize_users()

# # ── login ────────────────────────────────────────────────────────────────────
# username = auth.require_login()
# is_admin = auth.is_admin(username)


# @st.cache_resource(show_spinner="Loading the document index...")
# def load_backend():
#     import rag
#     import graph
#     rag.warmup()
#     return rag, graph


# rag, graph = load_backend()


# @st.cache_data(max_entries=200, show_spinner=False)
# def page_image(source: str, page: int, highlight: str):
#     return render_pdf_page(source, page, highlight)


# ss = st.session_state
# ss.setdefault("pending_question", None)
# ss.setdefault("pending_mode", None)
# ss.setdefault("autoscroll", False)
# session_id = f"user::{username}"          # one persistent conversation per user

# MODE_LABELS = {
#     "ask": "Ask me each time",
#     "local": "🔒 My documents",
#     "global": "🌐 Public research + web",
#     "both": "🔒➕🌐 Both",
# }


# # ─────────────────────────────────────────────────────────────────────────────
# # SIDEBAR
# # ─────────────────────────────────────────────────────────────────────────────
# with st.sidebar:
#     left, right = st.columns([3, 2])
#     account = user_store.get_user(username) or {}
#     left.markdown(f"👤 **{account.get('display_name') or username}**"
#                   + ("  \n`admin`" if is_admin else ""))
#     if right.button("Log out", key="logout_btn"):
#         auth.logout("You have been signed out.")

#     views = ["💬 Chat", "🔑 My account"] + (["🛠️ Users"] if is_admin else [])
#     view = st.radio("View", views, label_visibility="collapsed")

#     st.divider()
#     st.subheader("Search mode")
#     mode_keys = list(MODE_LABELS.keys())
#     default_mode = C.DEFAULT_SEARCH_MODE if C.DEFAULT_SEARCH_MODE in mode_keys else "ask"
#     search_mode = st.radio("Where to search", mode_keys,
#                            index=mode_keys.index(default_mode),
#                            format_func=lambda k: MODE_LABELS[k],
#                            label_visibility="collapsed")
#     if not C.GLOBAL_SEARCH_ENABLED and search_mode in ("global", "both"):
#         st.warning("Global search is disabled in config.py")
#         search_mode = "local"

#     with st.expander("🎛️ Answer preferences", expanded=False):
#         tone = st.selectbox("Tone", ["Formal", "Casual", "Professional", "Friendly"], index=2)
#         detail_level = st.selectbox("Detail level", ["Brief", "Moderate", "Detailed"], index=1)
#         audience = st.selectbox("Audience", ["Beginner", "Student", "Technical", "Expert"], index=1)
#         answer_format = st.selectbox(
#             "Answer format",
#             ["Paragraph", "Bullet points", "Numbered steps", "Structured explanation"], index=3)
#         language = st.selectbox("Language", ["English", "Hindi"], index=0)
#         citation_style = st.selectbox("Citation style",
#                                       ["File and page", "File only", "No citation"], index=0)

#     with st.expander("📂 Documents", expanded=False):
#         documents = rag.list_documents()
#         labels = {d["source"]: (f"{d['source']} — {d['title'][:45]}" if d["title"] else d["source"])
#                   for d in documents}
#         selected = st.multiselect("Search only in (empty = all)", options=list(labels.keys()),
#                                   format_func=lambda s: labels.get(s, s))
#         st.caption(f"{len(documents)} document(s) indexed")
#         show_previews = st.toggle("Show cited page previews", value=True)
#         if st.button("🔄 Reload index (after ingestion)"):
#             rag.reload_index()
#             st.cache_data.clear()
#             st.rerun()

#     st.divider()
#     if st.button("🗑️ Clear conversation"):
#         clear_memory(session_id=session_id)
#         ss.pending_question = None
#         st.rerun()


# # ─────────────────────────────────────────────────────────────────────────────
# # ACCOUNT / ADMIN VIEWS
# # ─────────────────────────────────────────────────────────────────────────────
# def view_my_account():
#     st.title("🔑 My account")
#     account = user_store.get_user(username) or {}
#     st.write(f"**Username:** {username} · **Role:** {account.get('role', 'user')} · "
#              f"**Last login:** {(account.get('last_login') or '—').replace('T', ' ')}")
#     display = st.text_input("Display name", value=account.get("display_name", ""))
#     if st.button("Save display name"):
#         user_store.set_display_name(username, display)
#         st.rerun()
#     st.subheader("Change password")
#     with st.form("change_password"):
#         current = st.text_input("Current password", type="password")
#         new = st.text_input("New password", type="password")
#         repeat = st.text_input("Repeat new password", type="password")
#         if st.form_submit_button("Change password"):
#             ok, message = auth.change_own_password(username, current or "", new or "", repeat or "")
#             (st.success if ok else st.error)(message)


# def view_users():
#     st.title("🛠️ Users")
#     st.caption("New users must change their password at first login.")
#     with st.expander("➕ Add user", expanded=True):
#         with st.form("add_user", clear_on_submit=True):
#             columns = st.columns([2, 2, 2, 1.4])
#             new_username = columns[0].text_input("Username")
#             new_display = columns[1].text_input("Display name (optional)")
#             new_password = columns[2].text_input("Temporary password", type="password")
#             new_role = columns[3].selectbox("Role", ["user", "admin"])
#             must_change = st.checkbox("Force password change at first login", value=True)
#             if st.form_submit_button("Create user"):
#                 ok, message = auth.create_user(new_username, new_password or "", role=new_role,
#                                                display_name=new_display,
#                                                must_change_password=must_change,
#                                                created_by=username)
#                 (st.success if ok else st.error)(message)

#     for user in user_store.list_users():
#         name = user["username"]
#         with st.container(border=True):
#             head = st.columns([3, 1.6, 1.4])
#             head[0].markdown(f"**{name}** · {user['display_name']}")
#             head[1].caption(f"created {user['created_at'][:10]} · "
#                             f"last login {(user['last_login'] or '—')[:16].replace('T', ' ')}")
#             head[2].caption("🟢 active" if user["is_active"] else "🔴 disabled")

#             columns = st.columns([1.6, 1.6])
#             role = columns[0].selectbox("Role", ["user", "admin"],
#                                         index=0 if user["role"] == "user" else 1, key=f"role_{name}")
#             if role != user["role"]:
#                 if user["role"] == "admin" and user_store.active_admin_count() <= 1:
#                     columns[0].warning("This is the last admin.")
#                 else:
#                     user_store.set_role(name, role)
#                     st.rerun()
#             active = columns[1].toggle("Active", value=user["is_active"], key=f"active_{name}",
#                                        disabled=(name == username))
#             if active != user["is_active"]:
#                 if not active and user["role"] == "admin" and user_store.active_admin_count() <= 1:
#                     columns[1].warning("This is the last admin.")
#                 else:
#                     user_store.set_active(name, active)
#                     st.rerun()

#             with st.expander(f"🔑 Reset password — {name}"):
#                 password = st.text_input("New password", type="password", key=f"pw_{name}")
#                 force = st.checkbox("Force change at next login", value=True, key=f"force_{name}")
#                 if st.button("Set password", key=f"setpw_{name}"):
#                     ok, message = auth.set_user_password(name, password or "",
#                                                          must_change_password=force)
#                     (st.success if ok else st.error)(message)

#             if name != username:
#                 with st.expander(f"🗑️ Delete {name}"):
#                     st.warning("Deletes the account and its chat history.")
#                     confirm = st.checkbox(f"Yes, delete {name}", key=f"confirm_{name}")
#                     if st.button("Delete user", key=f"del_{name}", disabled=not confirm):
#                         if user["role"] == "admin" and user_store.active_admin_count() <= 1:
#                             st.error("You cannot delete the last admin.")
#                         else:
#                             user_store.delete_user(name)
#                             clear_memory(session_id=f"user::{name}")
#                             st.rerun()


# # ─────────────────────────────────────────────────────────────────────────────
# # CHAT VIEW
# # ─────────────────────────────────────────────────────────────────────────────
# SCROLL_JS = """
# <script>
# const doc = window.parent.document;
# function scroller(){ return doc.querySelector('section.main') || doc.querySelector('[data-testid="stMain"]') || doc.documentElement; }
# function toBottom(){ const s = scroller(); s.scrollTo({top: s.scrollHeight, behavior: 'smooth'}); }
# if (!doc.getElementById('rag-scroll-btn')) {
#   const b = doc.createElement('button');
#   b.id = 'rag-scroll-btn'; b.textContent = '↓'; b.title = 'Jump to latest message';
#   Object.assign(b.style, {position:'fixed', left:'50%', transform:'translateX(-50%)', bottom:'90px',
#     zIndex:9999, width:'38px', height:'38px', borderRadius:'50%', border:'1px solid rgba(255,255,255,.2)',
#     background:'rgba(60,60,60,.92)', color:'#fff', fontSize:'18px', cursor:'pointer', display:'none'});
#   b.onclick = toBottom; doc.body.appendChild(b);
#   setInterval(() => { const s = scroller(); b.style.display = (s.scrollHeight - s.scrollTop - s.clientHeight > 150) ? 'block' : 'none'; }, 400);
# }
# AUTOSCROLL
# </script>
# """


# def show_image(data_or_path):
#     try:
#         st.image(data_or_path, width="stretch")
#     except Exception:
#         st.image(data_or_path, use_container_width=True)


# def render_sources(sources, key_prefix: str, preview_first: bool):
#     if not sources:
#         return
#     st.markdown("**📄 Sources:**")
#     for index, source in enumerate(sources):
#         # web / arXiv source
#         if source.get("url"):
#             year = f" ({source['published']})" if source.get("published") else ""
#             st.markdown(f"🌐 [{source['source']}{year}]({source['url']})")
#             continue

#         label = f" · {source['label']}" if source.get("label") else ""
#         title = f"{source['source']} — page {source['page']}{label}"
#         st.markdown(f"🔗 [{title}]({pdf_url(source['source'], source['page'], ss.get('pdf_token', ''))})")

#         image_path = source.get("image_path") or ""
#         absolute = os.path.join(C.BASE_DIR, image_path) if image_path else ""
#         if absolute and os.path.exists(absolute):
#             with st.expander(f"🖼️ {source.get('label') or 'Figure'} — cropped from the page",
#                              expanded=(preview_first and index == 0)):
#                 show_image(absolute)
#                 if source.get("caption"):
#                     st.caption(source["caption"])
#         elif show_previews:
#             with st.expander(f"Preview page {source['page']} of {source['source']}",
#                              expanded=False):
#                 if st.checkbox("Show page image", key=f"{key_prefix}_prev_{index}"):
#                     image = page_image(source["source"], int(source["page"]),
#                                        source.get("label", ""))
#                     if image:
#                         show_image(image)
#                     else:
#                         st.caption("Page image not available.")


# def view_chat():
#     st.title("📚 Local + Global RAG")
#     st.caption("🔒 your ingested documents (exact file and page)  ·  "
#                "🌐 public research articles and the web")
#     try:
#         components.html(SCROLL_JS.replace(
#             "AUTOSCROLL", "setTimeout(toBottom, 300);" if ss.autoscroll else ""), height=0)
#     except Exception:
#         pass
#     ss.autoscroll = False

#     history = get_history(session_id=session_id)
#     last_assistant = max((i for i, m in enumerate(history) if m["role"] == "assistant"), default=-1)

#     if not history:
#         st.info("Ask anything about your documents — text, tables, figures ('pull Figure 4.2'), "
#                 "flowcharts, equations or algorithms — or switch to public research in the sidebar.")

#     for index, message in enumerate(history):
#         with st.chat_message(message["role"]):
#             st.markdown(message["content"])
#             meta = message.get("meta") or {}
#             if message["role"] != "assistant":
#                 continue
#             if meta.get("mode") in ("global", "both"):
#                 st.caption("🌐 answered from public sources")
#             render_sources(meta.get("sources", []), key_prefix=f"m{index}",
#                            preview_first=(index == last_assistant))
#             if meta.get("choose_mode") and index == last_assistant:
#                 columns = st.columns(3)
#                 original = meta.get("original_question", "")
#                 for column, (mode_key, label) in zip(columns,
#                                                      [("local", "🔒 My documents"),
#                                                       ("global", "🌐 Public research"),
#                                                       ("both", "🔒➕🌐 Both")]):
#                     if column.button(label, key=f"mode_{index}_{mode_key}"):
#                         ss.pending_question = original
#                         ss.pending_mode = mode_key
#                         st.rerun()

#     typed = st.chat_input("Ask a question…")
#     question = typed or ss.pending_question
#     mode = ss.pending_mode or search_mode
#     ss.pending_question, ss.pending_mode = None, None
#     if not question:
#         return

#     ss.autoscroll = True
#     with st.chat_message("user"):
#         st.markdown(question)

#     preferences = {
#         "tone": tone, "detail_level": detail_level, "audience": audience,
#         "answer_format": answer_format, "language": language, "citation_style": citation_style,
#     }

#     with st.chat_message("assistant"):
#         status = st.status("🔍 Working on it...", expanded=True)
#         waiting = st.empty()
#         pipeline = graph.answer_stream(question, mode, preferences, session_id=session_id,
#                                        selected_sources=selected or None)

#         def tokens():
#             for item in pipeline:
#                 if not isinstance(item, dict):
#                     yield item
#                     continue
#                 if item.get("__final__") or item.get("__pdf_info__") is not None:
#                     continue
#                 label, state = item.get("label", ""), item.get("state", "running")
#                 if state == "running":
#                     status.write(label)
#                 elif state == "generating":
#                     status.write(label)
#                     waiting.caption("⏳ Model is reading the sources…")
#                 elif state == "complete":
#                     status.update(label="✅ " + label.lstrip("💬❓ "), state="complete",
#                                   expanded=False)
#                     waiting.empty()
#                 elif state == "error":
#                     status.update(label=label, state="error", expanded=False)
#                     waiting.empty()

#         try:
#             st.write_stream(tokens())
#         except Exception as e:
#             st.error(f"Error: {e}")

#     st.rerun()


# # ─────────────────────────────────────────────────────────────────────────────
# # ROUTER
# # ─────────────────────────────────────────────────────────────────────────────
# if view == "🔑 My account":
#     view_my_account()
# elif view == "🛠️ Users" and is_admin:
#     view_users()
# else:
#     view_chat()


###################################  21st Sept Update ################################### 

# """
# Streamlit UI  —  Local RAG + Global RAG, with login

#     streamlit run streamlit_first.py --server.address 0.0.0.0 --server.port 8501

#   • Login required (users are managed in the app by an admin: sidebar → 🛠️ Users)
#   • Search mode: your documents / public research + web / both — the app asks when unsure
#   • Cited figures are shown as images, cited PDF pages as links + page previews
# """

# import os
# import streamlit as st
# import streamlit.components.v1 as components

# st.set_page_config(page_title="Local + Global RAG", page_icon="📚", layout="wide")

# import config as C
# import auth
# import user_store
# from rag_common import pdf_url, render_pdf_page
# from session_memory import (
#     initialize_memory, get_history, create_conversation, list_conversations,
#     get_conversation, user_owns_conversation, rename_conversation, delete_conversation,
#     delete_user_conversations,
# )

# initialize_memory()
# user_store.initialize_users()

# # ── login ────────────────────────────────────────────────────────────────────
# username = auth.require_login()
# is_admin = auth.is_admin(username)


# @st.cache_resource(show_spinner="Loading the document index...")
# def load_backend():
#     import rag
#     import graph
#     rag.warmup()
#     return rag, graph


# rag, graph = load_backend()


# @st.cache_data(max_entries=200, show_spinner=False)
# def page_image(source: str, page: int, highlight: str):
#     return render_pdf_page(source, page, highlight)


# ss = st.session_state
# ss.setdefault("pending_question", None)
# ss.setdefault("pending_mode", None)
# ss.setdefault("autoscroll", False)
# ss.setdefault("run_counter", 0)
# # Every login starts with a NEW chat (logout clears the session state, so this is None
# # again after signing back in). The chat is created when the first question is asked.
# ss.setdefault("conversation_id", None)
# if ss.conversation_id and not user_owns_conversation(username, ss.conversation_id):
#     ss.conversation_id = None

# MODE_LABELS = {
#     "ask": "Ask me each time",
#     "local": "🔒 My documents",
#     "global": "🌐 Public research + web",
#     "both": "🔒➕🌐 Both",
# }


# # ─────────────────────────────────────────────────────────────────────────────
# # SIDEBAR
# # ─────────────────────────────────────────────────────────────────────────────
# with st.sidebar:
#     left, right = st.columns([3, 2])
#     account = user_store.get_user(username) or {}
#     left.markdown(f"👤 **{account.get('display_name') or username}**"
#                   + ("  \n`admin`" if is_admin else ""))
#     if right.button("Log out", key="logout_btn"):
#         auth.logout("You have been signed out.")

#     views = ["💬 Chat", "🔑 My account"] + (["🛠️ Users"] if is_admin else [])
#     view = st.radio("View", views, label_visibility="collapsed")

#     if view == "💬 Chat":
#         if st.button("➕ New chat", key="new_chat"):
#             ss.conversation_id = None
#             ss.pending_question, ss.pending_mode = None, None
#             st.rerun()
#         st.caption("Chats")
#         chats = list_conversations(username, limit=60)
#         if not chats:
#             st.caption("No previous chats yet.")
#         for chat_item in chats:
#             current = chat_item["id"] == ss.conversation_id
#             title = chat_item["title"]
#             title = title if len(title) <= 38 else title[:37] + "…"
#             if st.button(("▶ " if current else "") + title, key=f"chat_{chat_item['id']}",
#                          help=f"Last used {chat_item['updated_at'].replace('T', ' ')}"):
#                 ss.conversation_id = chat_item["id"]
#                 ss.pending_question, ss.pending_mode = None, None
#                 st.rerun()
#         if ss.conversation_id:
#             with st.expander("✏️ Rename / delete this chat"):
#                 conversation = get_conversation(ss.conversation_id) or {}
#                 new_title = st.text_input("Chat name", value=conversation.get("title", ""),
#                                           key=f"rename_{ss.conversation_id}")
#                 if st.button("Save name", key=f"save_{ss.conversation_id}"):
#                     rename_conversation(ss.conversation_id, new_title)
#                     st.rerun()
#                 confirm = st.checkbox("Yes, delete this chat", key=f"confirm_{ss.conversation_id}")
#                 if st.button("🗑️ Delete chat", key=f"delete_{ss.conversation_id}",
#                              disabled=not confirm):
#                     delete_conversation(ss.conversation_id)
#                     ss.conversation_id = None
#                     st.rerun()

#     st.divider()
#     st.subheader("Search mode")
#     mode_keys = list(MODE_LABELS.keys())
#     default_mode = C.DEFAULT_SEARCH_MODE if C.DEFAULT_SEARCH_MODE in mode_keys else "ask"
#     search_mode = st.radio("Where to search", mode_keys,
#                            index=mode_keys.index(default_mode),
#                            format_func=lambda k: MODE_LABELS[k],
#                            label_visibility="collapsed")
#     if not C.GLOBAL_SEARCH_ENABLED and search_mode in ("global", "both"):
#         st.warning("Global search is disabled in config.py")
#         search_mode = "local"

#     with st.expander("🎛️ Answer preferences", expanded=False):
#         tone = st.selectbox("Tone", ["Formal", "Casual", "Professional", "Friendly"], index=2)
#         detail_level = st.selectbox("Detail level", ["Brief", "Moderate", "Detailed"], index=1)
#         audience = st.selectbox("Audience", ["Beginner", "Student", "Technical", "Expert"], index=1)
#         answer_format = st.selectbox(
#             "Answer format",
#             ["Paragraph", "Bullet points", "Numbered steps", "Structured explanation"], index=3)
#         language = st.selectbox("Language", ["English", "Hindi"], index=0)
#         citation_style = st.selectbox("Citation style",
#                                       ["File and page", "File only", "No citation"], index=0)

#     with st.expander("📂 Documents", expanded=False):
#         documents = rag.list_documents()
#         labels = {d["source"]: (f"{d['source']} — {d['title'][:45]}" if d["title"] else d["source"])
#                   for d in documents}
#         selected = st.multiselect("Search only in (empty = all)", options=list(labels.keys()),
#                                   format_func=lambda s: labels.get(s, s))
#         st.caption(f"{len(documents)} document(s) indexed")
#         show_previews = st.toggle("Show cited page previews", value=True)
#         if st.button("🔄 Reload index (after ingestion)"):
#             rag.reload_index()
#             st.cache_data.clear()
#             st.rerun()



# # ─────────────────────────────────────────────────────────────────────────────
# # ACCOUNT / ADMIN VIEWS
# # ─────────────────────────────────────────────────────────────────────────────
# def view_my_account():
#     st.title("🔑 My account")
#     account = user_store.get_user(username) or {}
#     st.write(f"**Username:** {username} · **Role:** {account.get('role', 'user')} · "
#              f"**Last login:** {(account.get('last_login') or '—').replace('T', ' ')}")
#     display = st.text_input("Display name", value=account.get("display_name", ""))
#     if st.button("Save display name"):
#         user_store.set_display_name(username, display)
#         st.rerun()
#     st.subheader("Change password")
#     with st.form("change_password"):
#         current = st.text_input("Current password", type="password")
#         new = st.text_input("New password", type="password")
#         repeat = st.text_input("Repeat new password", type="password")
#         if st.form_submit_button("Change password"):
#             ok, message = auth.change_own_password(username, current or "", new or "", repeat or "")
#             (st.success if ok else st.error)(message)


# def view_users():
#     st.title("🛠️ Users")
#     st.caption("New users must change their password at first login.")
#     with st.expander("➕ Add user", expanded=True):
#         with st.form("add_user", clear_on_submit=True):
#             columns = st.columns([2, 2, 2, 1.4])
#             new_username = columns[0].text_input("Username")
#             new_display = columns[1].text_input("Display name (optional)")
#             new_password = columns[2].text_input("Temporary password", type="password")
#             new_role = columns[3].selectbox("Role", ["user", "admin"])
#             must_change = st.checkbox("Force password change at first login", value=True)
#             if st.form_submit_button("Create user"):
#                 ok, message = auth.create_user(new_username, new_password or "", role=new_role,
#                                                display_name=new_display,
#                                                must_change_password=must_change,
#                                                created_by=username)
#                 (st.success if ok else st.error)(message)

#     for user in user_store.list_users():
#         name = user["username"]
#         with st.container(border=True):
#             head = st.columns([3, 1.6, 1.4])
#             head[0].markdown(f"**{name}** · {user['display_name']}")
#             head[1].caption(f"created {user['created_at'][:10]} · "
#                             f"last login {(user['last_login'] or '—')[:16].replace('T', ' ')}")
#             head[2].caption("🟢 active" if user["is_active"] else "🔴 disabled")

#             columns = st.columns([1.6, 1.6])
#             role = columns[0].selectbox("Role", ["user", "admin"],
#                                         index=0 if user["role"] == "user" else 1, key=f"role_{name}")
#             if role != user["role"]:
#                 if user["role"] == "admin" and user_store.active_admin_count() <= 1:
#                     columns[0].warning("This is the last admin.")
#                 else:
#                     user_store.set_role(name, role)
#                     st.rerun()
#             active = columns[1].toggle("Active", value=user["is_active"], key=f"active_{name}",
#                                        disabled=(name == username))
#             if active != user["is_active"]:
#                 if not active and user["role"] == "admin" and user_store.active_admin_count() <= 1:
#                     columns[1].warning("This is the last admin.")
#                 else:
#                     user_store.set_active(name, active)
#                     st.rerun()

#             with st.expander(f"🔑 Reset password — {name}"):
#                 password = st.text_input("New password", type="password", key=f"pw_{name}")
#                 force = st.checkbox("Force change at next login", value=True, key=f"force_{name}")
#                 if st.button("Set password", key=f"setpw_{name}"):
#                     ok, message = auth.set_user_password(name, password or "",
#                                                          must_change_password=force)
#                     (st.success if ok else st.error)(message)

#             if name != username:
#                 with st.expander(f"🗑️ Delete {name}"):
#                     st.warning("Deletes the account and its chat history.")
#                     confirm = st.checkbox(f"Yes, delete {name}", key=f"confirm_{name}")
#                     if st.button("Delete user", key=f"del_{name}", disabled=not confirm):
#                         if user["role"] == "admin" and user_store.active_admin_count() <= 1:
#                             st.error("You cannot delete the last admin.")
#                         else:
#                             delete_user_conversations(name)
#                             user_store.delete_user(name)
#                             st.rerun()


# # ─────────────────────────────────────────────────────────────────────────────
# # CHAT VIEW
# # ─────────────────────────────────────────────────────────────────────────────
# SCROLL_JS = """
# <script>
# const doc = window.parent.document;
# function scroller(){ return doc.querySelector('section.main') || doc.querySelector('[data-testid="stMain"]') || doc.documentElement; }
# function toBottom(){ const s = scroller(); s.scrollTo({top: s.scrollHeight, behavior: 'smooth'}); }
# if (!doc.getElementById('rag-scroll-btn')) {
#   const b = doc.createElement('button');
#   b.id = 'rag-scroll-btn'; b.textContent = '↓'; b.title = 'Jump to latest message';
#   Object.assign(b.style, {position:'fixed', left:'50%', transform:'translateX(-50%)', bottom:'90px',
#     zIndex:9999, width:'38px', height:'38px', borderRadius:'50%', border:'1px solid rgba(255,255,255,.2)',
#     background:'rgba(60,60,60,.92)', color:'#fff', fontSize:'18px', cursor:'pointer', display:'none'});
#   b.onclick = toBottom; doc.body.appendChild(b);
#   setInterval(() => { const s = scroller(); b.style.display = (s.scrollHeight - s.scrollTop - s.clientHeight > 150) ? 'block' : 'none'; }, 400);
# }
# AUTOSCROLL
# </script>
# """


# def show_image(data_or_path):
#     try:
#         st.image(data_or_path, width="stretch")
#     except Exception:
#         st.image(data_or_path, use_container_width=True)


# def render_sources(sources, key_prefix: str, preview_first: bool):
#     if not sources:
#         return
#     st.markdown("**📄 Sources:**")
#     for index, source in enumerate(sources):
#         # web / arXiv source
#         if source.get("url"):
#             year = f" ({source['published']})" if source.get("published") else ""
#             st.markdown(f"🌐 [{source['source']}{year}]({source['url']})")
#             continue

#         label = f" · {source['label']}" if source.get("label") else ""
#         title = f"{source['source']} — page {source['page']}{label}"
#         st.markdown(f"🔗 [{title}]({pdf_url(source['source'], source['page'], ss.get('pdf_token', ''))})")

#         image_path = source.get("image_path") or ""
#         absolute = os.path.join(C.BASE_DIR, image_path) if image_path else ""
#         if absolute and os.path.exists(absolute):
#             with st.expander(f"🖼️ {source.get('label') or 'Figure'} — cropped from the page",
#                              expanded=(preview_first and index == 0)):
#                 show_image(absolute)
#                 if source.get("caption"):
#                     st.caption(source["caption"])
#         elif show_previews:
#             with st.expander(f"Preview page {source['page']} of {source['source']}",
#                              expanded=False):
#                 if st.checkbox("Show page image", key=f"{key_prefix}_prev_{index}"):
#                     image = page_image(source["source"], int(source["page"]),
#                                        source.get("label", ""))
#                     if image:
#                         show_image(image)
#                     else:
#                         st.caption("Page image not available.")


# def view_chat():
#     st.title("📚 Local + Global RAG")
#     st.caption("🔒 your ingested documents (exact file and page)  ·  "
#                "🌐 public research articles and the web")
#     try:
#         components.html(SCROLL_JS.replace(
#             "AUTOSCROLL", "setTimeout(toBottom, 300);" if ss.autoscroll else ""), height=0)
#     except Exception:
#         pass
#     ss.autoscroll = False

#     conversation_id = ss.conversation_id
#     history = get_history(session_id=conversation_id) if conversation_id else []
#     if conversation_id:
#         conversation = get_conversation(conversation_id) or {}
#         st.caption(f"💬 {conversation.get('title', '')}")
#     last_assistant = max((i for i, m in enumerate(history) if m["role"] == "assistant"), default=-1)

#     if not history:
#         st.info("Ask anything about your documents — text, tables, figures ('pull Figure 4.2'), "
#                 "flowcharts, equations or algorithms — or switch to public research in the sidebar.")

#     for index, message in enumerate(history):
#         with st.chat_message(message["role"]):
#             st.markdown(message["content"])
#             meta = message.get("meta") or {}
#             if message["role"] != "assistant":
#                 continue
#             if meta.get("mode") in ("global", "both"):
#                 st.caption("🌐 answered from public sources")
#             render_sources(meta.get("sources", []), key_prefix=f"m{index}",
#                            preview_first=(index == last_assistant))
#             if meta.get("choose_mode") and index == last_assistant:
#                 columns = st.columns(3)
#                 original = meta.get("original_question", "")
#                 for column, (mode_key, label) in zip(columns,
#                                                      [("local", "🔒 My documents"),
#                                                       ("global", "🌐 Public research"),
#                                                       ("both", "🔒➕🌐 Both")]):
#                     if column.button(label, key=f"mode_{index}_{mode_key}"):
#                         ss.pending_question = original
#                         ss.pending_mode = mode_key
#                         st.rerun()

#     typed = st.chat_input("Ask a question…")
#     question = typed or ss.pending_question
#     mode = ss.pending_mode or search_mode
#     ss.pending_question, ss.pending_mode = None, None
#     if not question:
#         return

#     ss.autoscroll = True
#     if not conversation_id:
#         conversation_id = create_conversation(username, question)
#         ss.conversation_id = conversation_id

#     with st.chat_message("user"):
#         st.markdown(question)

#     preferences = {
#         "tone": tone, "detail_level": detail_level, "audience": audience,
#         "answer_format": answer_format, "language": language, "citation_style": citation_style,
#     }

#     ss.run_counter += 1
#     with st.chat_message("assistant"):
#         # Clicking Stop makes Streamlit interrupt this run; the `finally` below then
#         # closes the pipeline, which stops LM Studio and saves the partial answer.
#         stop_slot = st.empty()
#         stop_slot.button("⏹ Stop generating", key=f"stop_{ss.run_counter}")
#         status = st.status("🔍 Working on it...", expanded=True)
#         waiting = st.empty()
#         pipeline = graph.answer_stream(question, mode, preferences, session_id=conversation_id,
#                                        selected_sources=selected or None)

#         def tokens():
#             for item in pipeline:
#                 if not isinstance(item, dict):
#                     yield item
#                     continue
#                 if item.get("__final__") or item.get("__pdf_info__") is not None:
#                     continue
#                 label, state = item.get("label", ""), item.get("state", "running")
#                 if state == "running":
#                     status.write(label)
#                 elif state == "generating":
#                     status.write(label)
#                     waiting.caption("⏳ Model is reading the sources…")
#                 elif state == "complete":
#                     status.update(label="✅ " + label.lstrip("💬❓ "), state="complete",
#                                   expanded=False)
#                     waiting.empty()
#                 elif state == "error":
#                     status.update(label=label, state="error", expanded=False)
#                     waiting.empty()

#         try:
#             st.write_stream(tokens())
#         except Exception as e:
#             st.error(f"Error: {e}")
#         finally:
#             pipeline.close()
#         stop_slot.empty()

#     st.rerun()


# # ─────────────────────────────────────────────────────────────────────────────
# # ROUTER
# # ─────────────────────────────────────────────────────────────────────────────
# if view == "🔑 My account":
#     view_my_account()
# elif view == "🛠️ Users" and is_admin:
#     view_users()
# else:
#     view_chat()




################################### 22nd Sept Update ################################### 


"""
Streamlit UI  —  Local RAG + Global RAG, with login

    streamlit run streamlit_first.py --server.address 0.0.0.0 --server.port 8501

  • Login required (users are managed in the app by an admin: sidebar → 🛠️ Users)
  • Search mode: your documents / public research + web / both — the app asks when unsure
  • Cited figures are shown as images, cited PDF pages as links + page previews
"""

import os
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Local + Global RAG", page_icon="📚", layout="wide")

import config as C
import auth
import user_store
from rag_common import pdf_url, render_pdf_page
from session_memory import (
    initialize_memory, get_history, create_conversation, list_conversations,
    get_conversation, user_owns_conversation, rename_conversation, delete_conversation,
    delete_user_conversations, get_memory_stats,
)

initialize_memory()
user_store.initialize_users()

# ── login ────────────────────────────────────────────────────────────────────
username = auth.require_login()
is_admin = auth.is_admin(username)


@st.cache_resource(show_spinner="Loading the document index...")
def load_backend():
    import rag
    import graph
    rag.warmup()
    return rag, graph


rag, graph = load_backend()


@st.cache_data(max_entries=200, show_spinner=False)
def page_image(source: str, page: int, highlight: str):
    return render_pdf_page(source, page, highlight)


ss = st.session_state
ss.setdefault("pending_question", None)
ss.setdefault("pending_mode", None)
ss.setdefault("autoscroll", False)
ss.setdefault("run_counter", 0)
# Every login starts with a NEW chat (logout clears the session state, so this is None
# again after signing back in). The chat is created when the first question is asked.
ss.setdefault("conversation_id", None)
if ss.conversation_id and not user_owns_conversation(username, ss.conversation_id):
    ss.conversation_id = None

MODE_LABELS = {
    "ask": "Ask me each time",
    "local": "🔒 My documents",
    "global": "🌐 Public research + web",
    "both": "🔒➕🌐 Both",
}


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    left, right = st.columns([3, 2])
    account = user_store.get_user(username) or {}
    left.markdown(f"👤 **{account.get('display_name') or username}**"
                  + ("  \n`admin`" if is_admin else ""))
    if right.button("Log out", key="logout_btn"):
        auth.logout("You have been signed out.")

    views = ["💬 Chat", "🔑 My account"] + (["🛠️ Users"] if is_admin else [])
    view = st.radio("View", views, label_visibility="collapsed")

    if view == "💬 Chat":
        if st.button("➕ New chat", key="new_chat"):
            ss.conversation_id = None
            ss.pending_question, ss.pending_mode = None, None
            st.rerun()
        st.caption("Chats")
        chats = list_conversations(username, limit=60)
        if not chats:
            st.caption("No previous chats yet.")
        for chat_item in chats:
            current = chat_item["id"] == ss.conversation_id
            title = chat_item["title"]
            title = title if len(title) <= 38 else title[:37] + "…"
            if st.button(("▶ " if current else "") + title, key=f"chat_{chat_item['id']}",
                         help=f"Last used {chat_item['updated_at'].replace('T', ' ')}"):
                ss.conversation_id = chat_item["id"]
                ss.pending_question, ss.pending_mode = None, None
                st.rerun()
        if ss.conversation_id:
            with st.expander("✏️ Rename / delete this chat"):
                conversation = get_conversation(ss.conversation_id) or {}
                stats = get_memory_stats(ss.conversation_id)
                memory_line = (f"🧠 Remembers this whole chat: {stats['verbatim_messages']} "
                               f"recent message(s) word for word")
                if stats["summary_tokens"]:
                    memory_line += " + a summary of everything before them"
                st.caption(memory_line)
                new_title = st.text_input("Chat name", value=conversation.get("title", ""),
                                          key=f"rename_{ss.conversation_id}")
                if st.button("Save name", key=f"save_{ss.conversation_id}"):
                    rename_conversation(ss.conversation_id, new_title)
                    st.rerun()
                confirm = st.checkbox("Yes, delete this chat", key=f"confirm_{ss.conversation_id}")
                if st.button("🗑️ Delete chat", key=f"delete_{ss.conversation_id}",
                             disabled=not confirm):
                    delete_conversation(ss.conversation_id)
                    ss.conversation_id = None
                    st.rerun()

    st.divider()
    st.subheader("Search mode")
    mode_keys = list(MODE_LABELS.keys())
    default_mode = C.DEFAULT_SEARCH_MODE if C.DEFAULT_SEARCH_MODE in mode_keys else "ask"
    search_mode = st.radio("Where to search", mode_keys,
                           index=mode_keys.index(default_mode),
                           format_func=lambda k: MODE_LABELS[k],
                           label_visibility="collapsed")
    if not C.GLOBAL_SEARCH_ENABLED and search_mode in ("global", "both"):
        st.warning("Global search is disabled in config.py")
        search_mode = "local"

    with st.expander("🎛️ Answer preferences", expanded=False):
        tone = st.selectbox("Tone", ["Formal", "Casual", "Professional", "Friendly"], index=2)
        detail_level = st.selectbox("Detail level", ["Brief", "Moderate", "Detailed"], index=1)
        audience = st.selectbox("Audience", ["Beginner", "Student", "Technical", "Expert"], index=1)
        answer_format = st.selectbox(
            "Answer format",
            ["Paragraph", "Bullet points", "Numbered steps", "Structured explanation"], index=3)
        language = st.selectbox("Language", ["English", "Hindi"], index=0)
        citation_style = st.selectbox("Citation style",
                                      ["File and page", "File only", "No citation"], index=0)

    with st.expander("📂 Documents", expanded=False):
        documents = rag.list_documents()
        labels = {d["source"]: (f"{d['source']} — {d['title'][:45]}" if d["title"] else d["source"])
                  for d in documents}
        selected = st.multiselect("Search only in (empty = all)", options=list(labels.keys()),
                                  format_func=lambda s: labels.get(s, s))
        st.caption(f"{len(documents)} document(s) indexed")
        show_previews = st.toggle("Show cited page previews", value=True)
        if st.button("🔄 Reload index (after ingestion)"):
            rag.reload_index()
            st.cache_data.clear()
            st.rerun()



# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT / ADMIN VIEWS
# ─────────────────────────────────────────────────────────────────────────────
def view_my_account():
    st.title("🔑 My account")
    account = user_store.get_user(username) or {}
    st.write(f"**Username:** {username} · **Role:** {account.get('role', 'user')} · "
             f"**Last login:** {(account.get('last_login') or '—').replace('T', ' ')}")
    display = st.text_input("Display name", value=account.get("display_name", ""))
    if st.button("Save display name"):
        user_store.set_display_name(username, display)
        st.rerun()
    st.subheader("Change password")
    with st.form("change_password"):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password")
        repeat = st.text_input("Repeat new password", type="password")
        if st.form_submit_button("Change password"):
            ok, message = auth.change_own_password(username, current or "", new or "", repeat or "")
            (st.success if ok else st.error)(message)


def view_users():
    st.title("🛠️ Users")
    st.caption("New users must change their password at first login.")
    with st.expander("➕ Add user", expanded=True):
        with st.form("add_user", clear_on_submit=True):
            columns = st.columns([2, 2, 2, 1.4])
            new_username = columns[0].text_input("Username")
            new_display = columns[1].text_input("Display name (optional)")
            new_password = columns[2].text_input("Temporary password", type="password")
            new_role = columns[3].selectbox("Role", ["user", "admin"])
            must_change = st.checkbox("Force password change at first login", value=True)
            if st.form_submit_button("Create user"):
                ok, message = auth.create_user(new_username, new_password or "", role=new_role,
                                               display_name=new_display,
                                               must_change_password=must_change,
                                               created_by=username)
                (st.success if ok else st.error)(message)

    for user in user_store.list_users():
        name = user["username"]
        with st.container(border=True):
            head = st.columns([3, 1.6, 1.4])
            head[0].markdown(f"**{name}** · {user['display_name']}")
            head[1].caption(f"created {user['created_at'][:10]} · "
                            f"last login {(user['last_login'] or '—')[:16].replace('T', ' ')}")
            head[2].caption("🟢 active" if user["is_active"] else "🔴 disabled")

            columns = st.columns([1.6, 1.6])
            role = columns[0].selectbox("Role", ["user", "admin"],
                                        index=0 if user["role"] == "user" else 1, key=f"role_{name}")
            if role != user["role"]:
                if user["role"] == "admin" and user_store.active_admin_count() <= 1:
                    columns[0].warning("This is the last admin.")
                else:
                    user_store.set_role(name, role)
                    st.rerun()
            active = columns[1].toggle("Active", value=user["is_active"], key=f"active_{name}",
                                       disabled=(name == username))
            if active != user["is_active"]:
                if not active and user["role"] == "admin" and user_store.active_admin_count() <= 1:
                    columns[1].warning("This is the last admin.")
                else:
                    user_store.set_active(name, active)
                    st.rerun()

            with st.expander(f"🔑 Reset password — {name}"):
                password = st.text_input("New password", type="password", key=f"pw_{name}")
                force = st.checkbox("Force change at next login", value=True, key=f"force_{name}")
                if st.button("Set password", key=f"setpw_{name}"):
                    ok, message = auth.set_user_password(name, password or "",
                                                         must_change_password=force)
                    (st.success if ok else st.error)(message)

            if name != username:
                with st.expander(f"🗑️ Delete {name}"):
                    st.warning("Deletes the account and its chat history.")
                    confirm = st.checkbox(f"Yes, delete {name}", key=f"confirm_{name}")
                    if st.button("Delete user", key=f"del_{name}", disabled=not confirm):
                        if user["role"] == "admin" and user_store.active_admin_count() <= 1:
                            st.error("You cannot delete the last admin.")
                        else:
                            delete_user_conversations(name)
                            user_store.delete_user(name)
                            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# CHAT VIEW
# ─────────────────────────────────────────────────────────────────────────────
SCROLL_JS = """
<script>
const doc = window.parent.document;
function scroller(){ return doc.querySelector('section.main') || doc.querySelector('[data-testid="stMain"]') || doc.documentElement; }
function toBottom(){ const s = scroller(); s.scrollTo({top: s.scrollHeight, behavior: 'smooth'}); }
if (!doc.getElementById('rag-scroll-btn')) {
  const b = doc.createElement('button');
  b.id = 'rag-scroll-btn'; b.textContent = '↓'; b.title = 'Jump to latest message';
  Object.assign(b.style, {position:'fixed', left:'50%', transform:'translateX(-50%)', bottom:'90px',
    zIndex:9999, width:'38px', height:'38px', borderRadius:'50%', border:'1px solid rgba(255,255,255,.2)',
    background:'rgba(60,60,60,.92)', color:'#fff', fontSize:'18px', cursor:'pointer', display:'none'});
  b.onclick = toBottom; doc.body.appendChild(b);
  setInterval(() => { const s = scroller(); b.style.display = (s.scrollHeight - s.scrollTop - s.clientHeight > 150) ? 'block' : 'none'; }, 400);
}
AUTOSCROLL
</script>
"""


def show_image(data_or_path):
    try:
        st.image(data_or_path, width="stretch")
    except Exception:
        st.image(data_or_path, use_container_width=True)


def render_sources(sources, key_prefix: str, preview_first: bool):
    if not sources:
        return
    st.markdown("**📄 Sources:**")
    for index, source in enumerate(sources):
        # web / arXiv source
        if source.get("url"):
            year = f" ({source['published']})" if source.get("published") else ""
            st.markdown(f"🌐 [{source['source']}{year}]({source['url']})")
            continue

        label = f" · {source['label']}" if source.get("label") else ""
        title = f"{source['source']} — page {source['page']}{label}"
        st.markdown(f"🔗 [{title}]({pdf_url(source['source'], source['page'], ss.get('pdf_token', ''))})")

        image_path = source.get("image_path") or ""
        absolute = os.path.join(C.BASE_DIR, image_path) if image_path else ""
        if absolute and os.path.exists(absolute):
            with st.expander(f"🖼️ {source.get('label') or 'Figure'} — cropped from the page",
                             expanded=(preview_first and index == 0)):
                show_image(absolute)
                if source.get("caption"):
                    st.caption(source["caption"])
        elif show_previews:
            with st.expander(f"Preview page {source['page']} of {source['source']}",
                             expanded=False):
                if st.checkbox("Show page image", key=f"{key_prefix}_prev_{index}"):
                    image = page_image(source["source"], int(source["page"]),
                                       source.get("label", ""))
                    if image:
                        show_image(image)
                    else:
                        st.caption("Page image not available.")


def view_chat():
    st.title("📚 Local + Global RAG")
    st.caption("🔒 your ingested documents (exact file and page)  ·  "
               "🌐 public research articles and the web")
    try:
        components.html(SCROLL_JS.replace(
            "AUTOSCROLL", "setTimeout(toBottom, 300);" if ss.autoscroll else ""), height=0)
    except Exception:
        pass
    ss.autoscroll = False

    conversation_id = ss.conversation_id
    history = get_history(session_id=conversation_id) if conversation_id else []
    if conversation_id:
        conversation = get_conversation(conversation_id) or {}
        st.caption(f"💬 {conversation.get('title', '')}")
    last_assistant = max((i for i, m in enumerate(history) if m["role"] == "assistant"), default=-1)

    if not history:
        st.info("Ask anything about your documents — text, tables, figures ('pull Figure 4.2'), "
                "flowcharts, equations or algorithms — or switch to public research in the sidebar.")

    for index, message in enumerate(history):
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            meta = message.get("meta") or {}
            if message["role"] != "assistant":
                continue
            if meta.get("mode") in ("global", "both"):
                st.caption("🌐 answered from public sources")
            render_sources(meta.get("sources", []), key_prefix=f"m{index}",
                           preview_first=(index == last_assistant))
            if meta.get("choose_mode") and index == last_assistant:
                columns = st.columns(3)
                original = meta.get("original_question", "")
                for column, (mode_key, label) in zip(columns,
                                                     [("local", "🔒 My documents"),
                                                      ("global", "🌐 Public research"),
                                                      ("both", "🔒➕🌐 Both")]):
                    if column.button(label, key=f"mode_{index}_{mode_key}"):
                        ss.pending_question = original
                        ss.pending_mode = mode_key
                        st.rerun()

    typed = st.chat_input("Ask a question…")
    question = typed or ss.pending_question
    mode = ss.pending_mode or search_mode
    ss.pending_question, ss.pending_mode = None, None
    if not question:
        return

    ss.autoscroll = True
    if not conversation_id:
        conversation_id = create_conversation(username, question)
        ss.conversation_id = conversation_id

    with st.chat_message("user"):
        st.markdown(question)

    preferences = {
        "tone": tone, "detail_level": detail_level, "audience": audience,
        "answer_format": answer_format, "language": language, "citation_style": citation_style,
    }

    ss.run_counter += 1
    with st.chat_message("assistant"):
        # Clicking Stop makes Streamlit interrupt this run; the `finally` below then
        # closes the pipeline, which stops LM Studio and saves the partial answer.
        stop_slot = st.empty()
        stop_slot.button("⏹ Stop generating", key=f"stop_{ss.run_counter}")
        status = st.status("🔍 Working on it...", expanded=True)
        waiting = st.empty()
        pipeline = graph.answer_stream(question, mode, preferences, session_id=conversation_id,
                                       selected_sources=selected or None)

        def tokens():
            for item in pipeline:
                if not isinstance(item, dict):
                    yield item
                    continue
                if item.get("__final__") or item.get("__pdf_info__") is not None:
                    continue
                label, state = item.get("label", ""), item.get("state", "running")
                if state == "running":
                    status.write(label)
                elif state == "generating":
                    status.write(label)
                    waiting.caption("⏳ Model is reading the sources…")
                elif state == "complete":
                    status.update(label="✅ " + label.lstrip("💬❓ "), state="complete",
                                  expanded=False)
                    waiting.empty()
                elif state == "error":
                    status.update(label=label, state="error", expanded=False)
                    waiting.empty()

        try:
            st.write_stream(tokens())
        except Exception as e:
            st.error(f"Error: {e}")
        finally:
            pipeline.close()
        stop_slot.empty()

    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────────────────────────────────────
if view == "🔑 My account":
    view_my_account()
elif view == "🛠️ Users" and is_admin:
    view_users()
else:
    view_chat()