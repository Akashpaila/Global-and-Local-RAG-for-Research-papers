# """
# graph.py  —  LangGraph orchestration:  LOCAL RAG  ⇄  GLOBAL RAG

#     ┌──────────┐   mode = "ask"      → the app asks the user first
#     │  route   │   mode = "local"    → your ingested documents
#     └────┬─────┘   mode = "global"   → arXiv / OpenAlex / Crossref / web search
#          │         mode = "both"     → local first, then the public sources
#          ▼
#   local_retrieve ──▶ plan_queries ──▶ search ──▶ fetch ──▶ reflect ──▶ prepare ──▶ answer
#                           ▲                                    │
#                           └──────── one refinement round ──────┘

# The graph does routing, retrieval, tool calls and evidence checking.
# The final answer is streamed token by token by `answer_stream()` so the UI stays live.
# """

# import json
# import re
# from typing import Dict, List, Optional, TypedDict

# from langgraph.graph import StateGraph, START, END

# import config as C
# import rag
# import websearch
# from rag_common import chat_text, stream_tokens, count_tokens, pdf_path
# from session_memory import add_message


# # ─────────────────────────────────────────────────────────────────────────────
# # STATE
# # ─────────────────────────────────────────────────────────────────────────────
# class RAGState(TypedDict, total=False):
#     question: str
#     mode: str                     # ask | local | global | both
#     session_id: str
#     preferences: Dict
#     selected_sources: Optional[List[str]]
#     standalone: str
#     need_mode: bool
#     local_context: str
#     local_sources: Dict[int, Dict]
#     notes: List[str]
#     queries: List[str]
#     results: List[Dict]
#     evidence: List[Dict]
#     rounds: int
#     enough: bool
#     prompt: str
#     source_map: Dict[int, Dict]
#     status: List[str]


# def _add_status(state: RAGState, message: str) -> List[str]:
#     return list(state.get("status") or []) + [message]


# # ─────────────────────────────────────────────────────────────────────────────
# # NODES
# # ─────────────────────────────────────────────────────────────────────────────
# def route_node(state: RAGState) -> RAGState:
#     mode = (state.get("mode") or C.DEFAULT_SEARCH_MODE or "ask").lower()
#     if mode not in ("local", "global", "both", "ask"):
#         mode = "ask"
#     if mode == "ask":
#         return {"mode": mode, "need_mode": True,
#                 "status": _add_status(state, "❓ Waiting for the user to choose local or global search")}
#     label = {"local": "🔒 Searching your own documents",
#              "global": "🌐 Searching public research articles and the web",
#              "both": "🔒➕🌐 Searching your documents and public sources"}[mode]
#     return {"mode": mode, "need_mode": False, "status": _add_status(state, label)}


# def local_retrieve_node(state: RAGState) -> RAGState:
#     analysis = rag.analyze_question(state["question"], state.get("session_id", "default"),
#                                     state.get("selected_sources"))
#     documents, notes = rag.retrieve_documents(analysis)
#     context, source_map = rag.build_context(documents)
#     types = {}
#     for document in documents:
#         key = document["metadata"].get("content_type", "text")
#         types[key] = types.get(key, 0) + 1
#     summary = " · ".join(f"{value} {key}" for key, value in types.items()) or "nothing"
#     return {
#         "standalone": analysis["standalone"],
#         "local_context": context,
#         "local_sources": source_map,
#         "notes": notes,
#         "status": _add_status(state, f"📄 Local hits: {summary}"),
#     }


# PLAN_PROMPT = """You are planning a literature search for this question:

# "{question}"

# {extra}

# Write {n} short web/arXiv search queries that would find public research articles or reliable
# technical pages answering it. Use the terminology of the field, no full sentences, no quotes.

# Return ONLY JSON: {{"queries": ["...", "..."]}}"""


# def plan_queries_node(state: RAGState) -> RAGState:
#     question = state.get("standalone") or state["question"]
#     extra = ""
#     if state.get("mode") == "both" and state.get("local_context"):
#         extra = ("Context already found in the user's own documents (use its vocabulary, "
#                  "but search for public sources):\n" + state["local_context"][:1200])
#     previous = state.get("queries") or []
#     if previous:
#         extra += ("\n\nThese queries were already tried and did not return enough: "
#                   + "; ".join(previous) + ". Write DIFFERENT, broader or more specific ones.")
#     try:
#         raw = chat_text([{"role": "user", "content": PLAN_PROMPT.format(
#             question=question, extra=extra, n=3)}], max_tokens=400, temperature=0.2)
#         raw = raw[raw.find("{"): raw.rfind("}") + 1]
#         queries = [q.strip() for q in json.loads(raw).get("queries", []) if q and q.strip()][:4]
#     except Exception as e:
#         print(f"  [Query planning failed: {e}] — using the question itself")
#         queries = []
#     if not queries:
#         queries = [re.sub(r"[?\"']", " ", question).strip()[:180]]
#     return {"queries": previous + queries,
#             "status": _add_status(state, "🔎 Search queries: " + " | ".join(queries))}


# def search_node(state: RAGState) -> RAGState:
#     queries = (state.get("queries") or [])[-4:]
#     results = websearch.gather_sources(queries, want_papers=True,
#                                        want_web=C.WEB_SEARCH_BACKEND != "off")
#     known = {(r.get("title", "").lower()[:80]) for r in (state.get("results") or [])}
#     merged = list(state.get("results") or [])
#     for item in results:
#         if item.get("title", "").lower()[:80] not in known:
#             merged.append(item)
#     kinds = {}
#     for item in merged:
#         kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
#     summary = " · ".join(f"{value} {key}" for key, value in kinds.items()) or "no results"
#     return {"results": merged,
#             "status": _add_status(state, f"🌐 Found: {summary}")}


# def fetch_node(state: RAGState) -> RAGState:
#     results = state.get("results") or []
#     evidence = list(state.get("evidence") or [])
#     fetched_urls = {item["url"] for item in evidence}
#     downloads = 0

#     for item in results:
#         if downloads >= C.WEB_FETCH_PAGES:
#             break
#         url = item.get("pdf_url") or item.get("url") or ""
#         if not url or url in fetched_urls:
#             continue
#         # papers with a usable abstract do not always need a full download
#         body = ""
#         if item["kind"] == "web" or not item.get("abstract"):
#             body = websearch.fetch_page(url)
#             downloads += 1
#         evidence.append({
#             "title": item.get("title", ""),
#             "url": item.get("url") or url,
#             "kind": item["kind"],
#             "published": item.get("published", ""),
#             "authors": ", ".join(item.get("authors", [])[:4]),
#             "venue": item.get("venue", ""),
#             "text": (item.get("abstract") or "")[:3000] + ("\n\n" + body if body else ""),
#         })
#         fetched_urls.add(url)

#     # sources that were only listed (no download) still go in as title + abstract
#     for item in results:
#         url = item.get("url") or item.get("pdf_url") or ""
#         if url and url not in fetched_urls and item.get("abstract"):
#             evidence.append({
#                 "title": item.get("title", ""), "url": url, "kind": item["kind"],
#                 "published": item.get("published", ""),
#                 "authors": ", ".join(item.get("authors", [])[:4]),
#                 "venue": item.get("venue", ""), "text": item["abstract"][:2500],
#             })
#             fetched_urls.add(url)

#     return {"evidence": evidence,
#             "status": _add_status(state, f"📥 Collected {len(evidence)} public source(s)")}


# REFLECT_PROMPT = """Question: "{question}"

# Here are the titles and short extracts of the sources found so far:
# {summaries}

# Can this question be answered from these sources, with specific facts and not just guesses?
# Return ONLY JSON: {{"enough": true or false, "missing": "what is still missing, one sentence"}}"""


# def reflect_node(state: RAGState) -> RAGState:
#     evidence = state.get("evidence") or []
#     rounds = int(state.get("rounds") or 0) + 1
#     if not evidence:
#         return {"rounds": rounds, "enough": False,
#                 "status": _add_status(state, "⚠️ No public sources reachable yet")}
#     if rounds >= C.GLOBAL_SEARCH_ROUNDS:
#         return {"rounds": rounds, "enough": True,
#                 "status": _add_status(state, "✅ Evidence collected")}

#     summaries = "\n".join(f"- {item['title']}: {item['text'][:300]}" for item in evidence[:8])
#     try:
#         raw = chat_text([{"role": "user", "content": REFLECT_PROMPT.format(
#             question=state.get("standalone") or state["question"], summaries=summaries)}],
#             max_tokens=300, temperature=0.0)
#         raw = raw[raw.find("{"): raw.rfind("}") + 1]
#         verdict = json.loads(raw)
#         enough = bool(verdict.get("enough"))
#         missing = verdict.get("missing", "")
#     except Exception:
#         enough, missing = True, ""
#     message = "✅ Evidence looks sufficient" if enough else f"🔁 Searching again — missing: {missing[:120]}"
#     return {"rounds": rounds, "enough": enough, "status": _add_status(state, message)}


# GLOBAL_RULES = """
# =========================================================
# PUBLIC SOURCE RULES
# =========================================================
# The sources below come from arXiv, OpenAlex, Crossref and the open web — NOT from the user's documents.
# 1. Use only what the sources say; never fill gaps from memory.
# 2. Cite every factual sentence with the tags [S1], [S2] ... The app turns them into the source titles and links.
# 3. Say clearly when something is a preprint, when sources disagree, or when the evidence is thin.
# 4. Mention publication years when they matter for how current the information is.
# 5. If the sources do not answer the question, say so and suggest what to search for instead.
# """


# def prepare_node(state: RAGState) -> RAGState:
#     preferences = state.get("preferences") or {}
#     source_map: Dict[int, Dict] = {}
#     blocks: List[str] = []
#     used = 0
#     number = 0

#     for entry in (state.get("local_sources") or {}).values():
#         number += 1
#         source_map[number] = entry

#     if state.get("local_context"):
#         # re-number the local context so [S#] stays consistent
#         local_block = state["local_context"]
#         blocks.append("YOUR OWN DOCUMENTS\n" + local_block)
#         used += count_tokens(local_block)

#     for item in state.get("evidence") or []:
#         if used > C.MAX_CONTEXT_TOKENS:
#             break
#         number += 1
#         text = item["text"][:C.WEB_FETCH_MAX_CHARS]
#         meta = " | ".join(x for x in [item.get("kind", ""), item.get("authors", ""),
#                                       item.get("venue", ""), item.get("published", "")] if x)
#         block = (f"[S{number}] web source: {item['title']} | {meta}\n"
#                  f"URL: {item['url']}\n{text}")
#         used += count_tokens(block)
#         blocks.append(block)
#         source_map[number] = {
#             "source": item["title"][:120] or item["url"],
#             "page": 0,
#             "url": item["url"],
#             "kind": item.get("kind", "web"),
#             "label": "",
#             "content_type": "web",
#             "image_path": "",
#             "published": item.get("published", ""),
#         }

#     context = "\n\n".join(blocks) if blocks else "(no sources found)"
#     mode = state.get("mode", "local")
#     if mode == "local":
#         prompt = rag._RAG_PROMPT.format(
#             tone=preferences.get("tone", "Professional"),
#             detail_level=preferences.get("detail_level", "Moderate"),
#             audience=preferences.get("audience", "Student"),
#             answer_format=preferences.get("answer_format", "Structured explanation"),
#             language=preferences.get("language", "English"),
#             conversation_history=preferences.get("history", "No previous conversation."),
#             context=context,
#             notes="\n".join(state.get("notes") or []) or "none",
#             question=state["question"],
#         )
#     else:
#         prompt = rag._RAG_PROMPT.format(
#             tone=preferences.get("tone", "Professional"),
#             detail_level=preferences.get("detail_level", "Moderate"),
#             audience=preferences.get("audience", "Student"),
#             answer_format=preferences.get("answer_format", "Structured explanation"),
#             language=preferences.get("language", "English"),
#             conversation_history=preferences.get("history", "No previous conversation."),
#             context=context,
#             notes=("\n".join(state.get("notes") or []) or "none") + "\n" + GLOBAL_RULES,
#             question=state["question"],
#         )
#     return {"prompt": prompt, "source_map": source_map,
#             "status": _add_status(state, "⏳ Writing the answer...")}


# # ─────────────────────────────────────────────────────────────────────────────
# # GRAPH WIRING
# # ─────────────────────────────────────────────────────────────────────────────
# def _after_route(state: RAGState) -> str:
#     if state.get("need_mode"):
#         return END
#     return "local_retrieve" if state["mode"] in ("local", "both") else "plan_queries"


# def _after_local(state: RAGState) -> str:
#     return "plan_queries" if state["mode"] == "both" else "prepare"


# def _after_reflect(state: RAGState) -> str:
#     return "prepare" if state.get("enough") else "plan_queries"


# def build_graph():
#     graph = StateGraph(RAGState)
#     graph.add_node("route", route_node)
#     graph.add_node("local_retrieve", local_retrieve_node)
#     graph.add_node("plan_queries", plan_queries_node)
#     graph.add_node("search", search_node)
#     graph.add_node("fetch", fetch_node)
#     graph.add_node("reflect", reflect_node)
#     graph.add_node("prepare", prepare_node)

#     graph.add_edge(START, "route")
#     graph.add_conditional_edges("route", _after_route,
#                                 {"local_retrieve": "local_retrieve",
#                                  "plan_queries": "plan_queries", END: END})
#     graph.add_conditional_edges("local_retrieve", _after_local,
#                                 {"plan_queries": "plan_queries", "prepare": "prepare"})
#     graph.add_edge("plan_queries", "search")
#     graph.add_edge("search", "fetch")
#     graph.add_edge("fetch", "reflect")
#     graph.add_conditional_edges("reflect", _after_reflect,
#                                 {"plan_queries": "plan_queries", "prepare": "prepare"})
#     graph.add_edge("prepare", END)
#     return graph.compile()


# _compiled = {"graph": None}


# def get_graph():
#     if _compiled["graph"] is None:
#         _compiled["graph"] = build_graph()
#     return _compiled["graph"]


# # ─────────────────────────────────────────────────────────────────────────────
# # PUBLIC ENTRY POINT (streaming)
# # ─────────────────────────────────────────────────────────────────────────────
# MODE_QUESTION = ("Where should I look for this?\n\n"
#                  "- **🔒 My documents** — only the PDFs you ingested (with page numbers)\n"
#                  "- **🌐 Public research** — arXiv, OpenAlex, Crossref and web search\n"
#                  "- **🔒➕🌐 Both** — your documents first, then public sources")


# def answer_stream(question: str, mode: str, preferences: Dict, session_id: str = "default",
#                   selected_sources: Optional[List[str]] = None):
#     """Yields status dicts, then answer tokens, then {'__final__': {...}}."""
#     from session_memory import format_history

#     preferences = dict(preferences or {})
#     preferences.setdefault("history", format_history(C.MAX_MEMORY_MESSAGES, session_id))
#     citation_style = preferences.get("citation_style", "File and page")

#     state: RAGState = {
#         "question": question,
#         "mode": (mode or C.DEFAULT_SEARCH_MODE),
#         "session_id": session_id,
#         "preferences": preferences,
#         "selected_sources": selected_sources,
#         "rounds": 0,
#         "status": [],
#     }

#     final_state: RAGState = dict(state)
#     seen_status = 0
#     try:
#         for update in get_graph().stream(state, stream_mode="updates"):
#             for node_name, node_state in update.items():
#                 if not isinstance(node_state, dict):
#                     continue
#                 final_state.update(node_state)
#                 messages = node_state.get("status") or []
#                 for message in messages[seen_status:]:
#                     yield rag.status(message)
#                 seen_status = max(seen_status, len(messages))
#     except Exception as e:
#         message = f"⚠️ Search failed: {e}"
#         yield rag.status(message, "error")
#         yield message
#         yield {"__status__": True, "__final__": {"answer": message, "sources": [], "mode": mode}}
#         return

#     # The user still has to choose local or global
#     if final_state.get("need_mode"):
#         yield rag.status("❓ Choose where to search", "complete")
#         yield MODE_QUESTION
#         add_message("user", question, session_id=session_id)
#         add_message("assistant", MODE_QUESTION, session_id=session_id,
#                     meta={"choose_mode": True, "original_question": question})
#         yield {"__status__": True,
#                "__final__": {"answer": MODE_QUESTION, "sources": [], "choose_mode": True,
#                              "original_question": question, "mode": "ask"}}
#         return

#     source_map = final_state.get("source_map") or {}
#     citations = rag.CitationFormatter(source_map, citation_style)
#     parts, first = [], True
#     try:
#         for token in stream_tokens([{"role": "user", "content": final_state["prompt"]}]):
#             visible = citations.feed(token)
#             if not visible:
#                 continue
#             if first:
#                 yield rag.status("💬 Streaming answer...", "complete")
#                 first = False
#             parts.append(visible)
#             yield visible
#         tail = citations.flush()
#         if tail:
#             parts.append(tail)
#             yield tail
#     except Exception as e:
#         error = f"\n\n⚠️ The language model failed: {e}"
#         parts.append(error)
#         yield error

#     answer = "".join(parts).strip()
#     sources = citations.cited_sources()
#     add_message("user", question, session_id=session_id)
#     add_message("assistant", answer, session_id=session_id,
#                 meta={"sources": sources, "mode": final_state.get("mode", mode)})

#     if C.AUTO_OPEN_PDF_ON_HOST:
#         for source in sources:
#             if source.get("page"):
#                 rag.open_pdf_at_page(pdf_path(source["source"]), source["page"])
#                 break

#     yield {"__status__": True, "__pdf_info__": sources}
#     yield {"__status__": True, "__final__": {"answer": answer, "sources": sources,
#                                              "mode": final_state.get("mode", mode)}}




##############################  21st Sept Update ################################# 

# """
# graph.py  —  LangGraph orchestration:  LOCAL RAG  ⇄  GLOBAL RAG

#     ┌──────────┐   mode = "ask"      → the app asks the user first
#     │  route   │   mode = "local"    → your ingested documents
#     └────┬─────┘   mode = "global"   → arXiv / OpenAlex / Crossref / web search
#          │         mode = "both"     → local first, then the public sources
#          ▼
#   local_retrieve ──▶ plan_queries ──▶ search ──▶ fetch ──▶ reflect ──▶ prepare ──▶ answer
#                           ▲                                    │
#                           └──────── one refinement round ──────┘

# The graph does routing, retrieval, tool calls and evidence checking.
# The final answer is streamed token by token by `answer_stream()` so the UI stays live.
# """

# import json
# import re
# from typing import Dict, List, Optional, TypedDict

# from langgraph.graph import StateGraph, START, END

# import config as C
# import rag
# import websearch
# from rag_common import chat_text, stream_tokens, count_tokens, pdf_path
# from session_memory import add_message


# # ─────────────────────────────────────────────────────────────────────────────
# # STATE
# # ─────────────────────────────────────────────────────────────────────────────
# class RAGState(TypedDict, total=False):
#     question: str
#     mode: str                     # ask | local | global | both
#     session_id: str
#     preferences: Dict
#     selected_sources: Optional[List[str]]
#     standalone: str
#     need_mode: bool
#     local_context: str
#     local_sources: Dict[int, Dict]
#     notes: List[str]
#     queries: List[str]
#     results: List[Dict]
#     evidence: List[Dict]
#     rounds: int
#     enough: bool
#     prompt: str
#     source_map: Dict[int, Dict]
#     status: List[str]


# def _add_status(state: RAGState, message: str) -> List[str]:
#     return list(state.get("status") or []) + [message]


# # ─────────────────────────────────────────────────────────────────────────────
# # NODES
# # ─────────────────────────────────────────────────────────────────────────────
# def route_node(state: RAGState) -> RAGState:
#     mode = (state.get("mode") or C.DEFAULT_SEARCH_MODE or "ask").lower()
#     if mode not in ("local", "global", "both", "ask"):
#         mode = "ask"
#     if mode == "ask":
#         return {"mode": mode, "need_mode": True,
#                 "status": _add_status(state, "❓ Waiting for the user to choose local or global search")}
#     label = {"local": "🔒 Searching your own documents",
#              "global": "🌐 Searching public research articles and the web",
#              "both": "🔒➕🌐 Searching your documents and public sources"}[mode]
#     return {"mode": mode, "need_mode": False, "status": _add_status(state, label)}


# def local_retrieve_node(state: RAGState) -> RAGState:
#     analysis = rag.analyze_question(state["question"], state.get("session_id", "default"),
#                                     state.get("selected_sources"))
#     documents, notes = rag.retrieve_documents(analysis)
#     context, source_map = rag.build_context(documents)
#     types = {}
#     for document in documents:
#         key = document["metadata"].get("content_type", "text")
#         types[key] = types.get(key, 0) + 1
#     summary = " · ".join(f"{value} {key}" for key, value in types.items()) or "nothing"
#     return {
#         "standalone": analysis["standalone"],
#         "local_context": context,
#         "local_sources": source_map,
#         "notes": notes,
#         "status": _add_status(state, f"📄 Local hits: {summary}"),
#     }


# PLAN_PROMPT = """You are planning a literature search for this question:

# "{question}"

# {extra}

# Write {n} short web/arXiv search queries that would find public research articles or reliable
# technical pages answering it. Use the terminology of the field, no full sentences, no quotes.

# Return ONLY JSON: {{"queries": ["...", "..."]}}"""


# def plan_queries_node(state: RAGState) -> RAGState:
#     question = state.get("standalone") or state["question"]
#     extra = ""
#     if state.get("mode") == "both" and state.get("local_context"):
#         extra = ("Context already found in the user's own documents (use its vocabulary, "
#                  "but search for public sources):\n" + state["local_context"][:1200])
#     previous = state.get("queries") or []
#     if previous:
#         extra += ("\n\nThese queries were already tried and did not return enough: "
#                   + "; ".join(previous) + ". Write DIFFERENT, broader or more specific ones.")
#     try:
#         raw = chat_text([{"role": "user", "content": PLAN_PROMPT.format(
#             question=question, extra=extra, n=3)}], max_tokens=400, temperature=0.2)
#         raw = raw[raw.find("{"): raw.rfind("}") + 1]
#         queries = [q.strip() for q in json.loads(raw).get("queries", []) if q and q.strip()][:4]
#     except Exception as e:
#         print(f"  [Query planning failed: {e}] — using the question itself")
#         queries = []
#     if not queries:
#         queries = [re.sub(r"[?\"']", " ", question).strip()[:180]]
#     return {"queries": previous + queries,
#             "status": _add_status(state, "🔎 Search queries: " + " | ".join(queries))}


# def search_node(state: RAGState) -> RAGState:
#     queries = (state.get("queries") or [])[-4:]
#     results = websearch.gather_sources(queries, want_papers=True,
#                                        want_web=C.WEB_SEARCH_BACKEND != "off")
#     known = {(r.get("title", "").lower()[:80]) for r in (state.get("results") or [])}
#     merged = list(state.get("results") or [])
#     for item in results:
#         if item.get("title", "").lower()[:80] not in known:
#             merged.append(item)
#     kinds = {}
#     for item in merged:
#         kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
#     summary = " · ".join(f"{value} {key}" for key, value in kinds.items()) or "no results"
#     return {"results": merged,
#             "status": _add_status(state, f"🌐 Found: {summary}")}


# def fetch_node(state: RAGState) -> RAGState:
#     results = state.get("results") or []
#     evidence = list(state.get("evidence") or [])
#     fetched_urls = {item["url"] for item in evidence}
#     downloads = 0

#     for item in results:
#         if downloads >= C.WEB_FETCH_PAGES:
#             break
#         url = item.get("pdf_url") or item.get("url") or ""
#         if not url or url in fetched_urls:
#             continue
#         # papers with a usable abstract do not always need a full download
#         body = ""
#         if item["kind"] == "web" or not item.get("abstract"):
#             body = websearch.fetch_page(url)
#             downloads += 1
#         evidence.append({
#             "title": item.get("title", ""),
#             "url": item.get("url") or url,
#             "kind": item["kind"],
#             "published": item.get("published", ""),
#             "authors": ", ".join(item.get("authors", [])[:4]),
#             "venue": item.get("venue", ""),
#             "text": (item.get("abstract") or "")[:3000] + ("\n\n" + body if body else ""),
#         })
#         fetched_urls.add(url)

#     # sources that were only listed (no download) still go in as title + abstract
#     for item in results:
#         url = item.get("url") or item.get("pdf_url") or ""
#         if url and url not in fetched_urls and item.get("abstract"):
#             evidence.append({
#                 "title": item.get("title", ""), "url": url, "kind": item["kind"],
#                 "published": item.get("published", ""),
#                 "authors": ", ".join(item.get("authors", [])[:4]),
#                 "venue": item.get("venue", ""), "text": item["abstract"][:2500],
#             })
#             fetched_urls.add(url)

#     return {"evidence": evidence,
#             "status": _add_status(state, f"📥 Collected {len(evidence)} public source(s)")}


# REFLECT_PROMPT = """Question: "{question}"

# Here are the titles and short extracts of the sources found so far:
# {summaries}

# Can this question be answered from these sources, with specific facts and not just guesses?
# Return ONLY JSON: {{"enough": true or false, "missing": "what is still missing, one sentence"}}"""


# def reflect_node(state: RAGState) -> RAGState:
#     evidence = state.get("evidence") or []
#     rounds = int(state.get("rounds") or 0) + 1
#     if not evidence:
#         return {"rounds": rounds, "enough": False,
#                 "status": _add_status(state, "⚠️ No public sources reachable yet")}
#     if rounds >= C.GLOBAL_SEARCH_ROUNDS:
#         return {"rounds": rounds, "enough": True,
#                 "status": _add_status(state, "✅ Evidence collected")}

#     summaries = "\n".join(f"- {item['title']}: {item['text'][:300]}" for item in evidence[:8])
#     try:
#         raw = chat_text([{"role": "user", "content": REFLECT_PROMPT.format(
#             question=state.get("standalone") or state["question"], summaries=summaries)}],
#             max_tokens=300, temperature=0.0)
#         raw = raw[raw.find("{"): raw.rfind("}") + 1]
#         verdict = json.loads(raw)
#         enough = bool(verdict.get("enough"))
#         missing = verdict.get("missing", "")
#     except Exception:
#         enough, missing = True, ""
#     message = "✅ Evidence looks sufficient" if enough else f"🔁 Searching again — missing: {missing[:120]}"
#     return {"rounds": rounds, "enough": enough, "status": _add_status(state, message)}


# GLOBAL_RULES = """
# =========================================================
# PUBLIC SOURCE RULES
# =========================================================
# The sources below come from arXiv, OpenAlex, Crossref and the open web — NOT from the user's documents.
# 1. Use only what the sources say; never fill gaps from memory.
# 2. Cite every factual sentence with the tags [S1], [S2] ... The app turns them into the source titles and links.
# 3. Say clearly when something is a preprint, when sources disagree, or when the evidence is thin.
# 4. Mention publication years when they matter for how current the information is.
# 5. If the sources do not answer the question, say so and suggest what to search for instead.
# """


# def prepare_node(state: RAGState) -> RAGState:
#     preferences = state.get("preferences") or {}
#     source_map: Dict[int, Dict] = {}
#     blocks: List[str] = []
#     used = 0
#     number = 0

#     for entry in (state.get("local_sources") or {}).values():
#         number += 1
#         source_map[number] = entry

#     if state.get("local_context"):
#         # re-number the local context so [S#] stays consistent
#         local_block = state["local_context"]
#         blocks.append("YOUR OWN DOCUMENTS\n" + local_block)
#         used += count_tokens(local_block)

#     for item in state.get("evidence") or []:
#         if used > C.MAX_CONTEXT_TOKENS:
#             break
#         number += 1
#         text = item["text"][:C.WEB_FETCH_MAX_CHARS]
#         meta = " | ".join(x for x in [item.get("kind", ""), item.get("authors", ""),
#                                       item.get("venue", ""), item.get("published", "")] if x)
#         block = (f"[S{number}] web source: {item['title']} | {meta}\n"
#                  f"URL: {item['url']}\n{text}")
#         used += count_tokens(block)
#         blocks.append(block)
#         source_map[number] = {
#             "source": item["title"][:120] or item["url"],
#             "page": 0,
#             "url": item["url"],
#             "kind": item.get("kind", "web"),
#             "label": "",
#             "content_type": "web",
#             "image_path": "",
#             "published": item.get("published", ""),
#         }

#     context = "\n\n".join(blocks) if blocks else "(no sources found)"
#     mode = state.get("mode", "local")
#     if mode == "local":
#         prompt = rag._RAG_PROMPT.format(
#             tone=preferences.get("tone", "Professional"),
#             detail_level=preferences.get("detail_level", "Moderate"),
#             audience=preferences.get("audience", "Student"),
#             answer_format=preferences.get("answer_format", "Structured explanation"),
#             language=preferences.get("language", "English"),
#             conversation_history=preferences.get("history", "No previous conversation."),
#             context=context,
#             notes="\n".join(state.get("notes") or []) or "none",
#             question=state["question"],
#         )
#     else:
#         prompt = rag._RAG_PROMPT.format(
#             tone=preferences.get("tone", "Professional"),
#             detail_level=preferences.get("detail_level", "Moderate"),
#             audience=preferences.get("audience", "Student"),
#             answer_format=preferences.get("answer_format", "Structured explanation"),
#             language=preferences.get("language", "English"),
#             conversation_history=preferences.get("history", "No previous conversation."),
#             context=context,
#             notes=("\n".join(state.get("notes") or []) or "none") + "\n" + GLOBAL_RULES,
#             question=state["question"],
#         )
#     return {"prompt": prompt, "source_map": source_map,
#             "status": _add_status(state, "⏳ Writing the answer...")}


# # ─────────────────────────────────────────────────────────────────────────────
# # GRAPH WIRING
# # ─────────────────────────────────────────────────────────────────────────────
# def _after_route(state: RAGState) -> str:
#     if state.get("need_mode"):
#         return END
#     return "local_retrieve" if state["mode"] in ("local", "both") else "plan_queries"


# def _after_local(state: RAGState) -> str:
#     return "plan_queries" if state["mode"] == "both" else "prepare"


# def _after_reflect(state: RAGState) -> str:
#     return "prepare" if state.get("enough") else "plan_queries"


# def build_graph():
#     graph = StateGraph(RAGState)
#     graph.add_node("route", route_node)
#     graph.add_node("local_retrieve", local_retrieve_node)
#     graph.add_node("plan_queries", plan_queries_node)
#     graph.add_node("search", search_node)
#     graph.add_node("fetch", fetch_node)
#     graph.add_node("reflect", reflect_node)
#     graph.add_node("prepare", prepare_node)

#     graph.add_edge(START, "route")
#     graph.add_conditional_edges("route", _after_route,
#                                 {"local_retrieve": "local_retrieve",
#                                  "plan_queries": "plan_queries", END: END})
#     graph.add_conditional_edges("local_retrieve", _after_local,
#                                 {"plan_queries": "plan_queries", "prepare": "prepare"})
#     graph.add_edge("plan_queries", "search")
#     graph.add_edge("search", "fetch")
#     graph.add_edge("fetch", "reflect")
#     graph.add_conditional_edges("reflect", _after_reflect,
#                                 {"plan_queries": "plan_queries", "prepare": "prepare"})
#     graph.add_edge("prepare", END)
#     return graph.compile()


# _compiled = {"graph": None}


# def get_graph():
#     if _compiled["graph"] is None:
#         _compiled["graph"] = build_graph()
#     return _compiled["graph"]


# # ─────────────────────────────────────────────────────────────────────────────
# # PUBLIC ENTRY POINT (streaming)
# # ─────────────────────────────────────────────────────────────────────────────
# MODE_QUESTION = ("Where should I look for this?\n\n"
#                  "- **🔒 My documents** — only the PDFs you ingested (with page numbers)\n"
#                  "- **🌐 Public research** — arXiv, OpenAlex, Crossref and web search\n"
#                  "- **🔒➕🌐 Both** — your documents first, then public sources")


# def answer_stream(question: str, mode: str, preferences: Dict, session_id: str = "default",
#                   selected_sources: Optional[List[str]] = None):
#     """Yields status dicts, then answer tokens, then {'__final__': {...}}.

#     If the caller stops consuming (the Stop button), the generator is closed: the LM Studio
#     stream is closed too, and whatever was written so far is saved as a stopped answer.
#     """
#     from session_memory import format_history, maybe_generate_title

#     preferences = dict(preferences or {})
#     preferences.setdefault("history", format_history(C.MAX_MEMORY_MESSAGES, session_id))
#     citation_style = preferences.get("citation_style", "File and page")

#     state: RAGState = {
#         "question": question,
#         "mode": (mode or C.DEFAULT_SEARCH_MODE),
#         "session_id": session_id,
#         "preferences": preferences,
#         "selected_sources": selected_sources,
#         "rounds": 0,
#         "status": [],
#     }

#     final_state: RAGState = dict(state)
#     parts: List[str] = []
#     citations = None
#     saved = False

#     def save(answer_text: str, meta: Dict):
#         nonlocal saved
#         if saved:
#             return
#         saved = True
#         add_message("user", question, session_id=session_id)
#         add_message("assistant", answer_text, session_id=session_id, meta=meta)

#     try:
#         # ── 1. graph: routing, retrieval, web tools ─────────────────────────
#         seen_status = 0
#         try:
#             for update in get_graph().stream(state, stream_mode="updates"):
#                 for _node, node_state in update.items():
#                     if not isinstance(node_state, dict):
#                         continue
#                     final_state.update(node_state)
#                     messages = node_state.get("status") or []
#                     for message in messages[seen_status:]:
#                         yield rag.status(message)
#                     seen_status = max(seen_status, len(messages))
#         except GeneratorExit:
#             raise
#         except Exception as e:
#             message = f"⚠️ Search failed: {e}"
#             yield rag.status(message, "error")
#             yield message
#             save(message, {"mode": mode, "error": True})
#             yield {"__status__": True, "__final__": {"answer": message, "sources": [], "mode": mode}}
#             return

#         # ── 2. the user still has to choose local or global ─────────────────
#         if final_state.get("need_mode"):
#             yield rag.status("❓ Choose where to search", "complete")
#             yield MODE_QUESTION
#             save(MODE_QUESTION, {"choose_mode": True, "original_question": question})
#             yield {"__status__": True,
#                    "__final__": {"answer": MODE_QUESTION, "sources": [], "choose_mode": True,
#                                  "original_question": question, "mode": "ask"}}
#             return

#         # ── 3. stream the answer ────────────────────────────────────────────
#         source_map = final_state.get("source_map") or {}
#         citations = rag.CitationFormatter(source_map, citation_style)
#         first = True
#         token_stream = stream_tokens([{"role": "user", "content": final_state["prompt"]}])
#         try:
#             for token in token_stream:
#                 visible = citations.feed(token)
#                 if not visible:
#                     continue
#                 if first:
#                     yield rag.status("💬 Streaming answer...", "complete")
#                     first = False
#                 parts.append(visible)
#                 yield visible
#             tail = citations.flush()
#             if tail:
#                 parts.append(tail)
#                 yield tail
#         except GeneratorExit:
#             raise
#         except Exception as e:
#             error = f"\n\n⚠️ The language model failed: {e}"
#             parts.append(error)
#             yield error
#         finally:
#             token_stream.close()          # stops LM Studio if we were interrupted

#         answer = "".join(parts).strip()
#         sources = citations.cited_sources()
#         save(answer, {"sources": sources, "mode": final_state.get("mode", mode)})
#         maybe_generate_title(session_id)

#         if C.AUTO_OPEN_PDF_ON_HOST:
#             for source in sources:
#                 if source.get("page"):
#                     rag.open_pdf_at_page(pdf_path(source["source"]), source["page"])
#                     break

#         yield {"__status__": True, "__pdf_info__": sources}
#         yield {"__status__": True, "__final__": {"answer": answer, "sources": sources,
#                                                  "mode": final_state.get("mode", mode)}}

#     except GeneratorExit:
#         # ── Stop button: keep what was written so far ───────────────────────
#         if not saved:
#             partial = "".join(parts).strip()
#             if citations is not None:
#                 partial += citations.flush()
#             sources = citations.cited_sources() if (citations is not None and citations.cited) else []
#             text = (partial + "\n\n*⏹ Generation stopped.*") if partial \
#                 else "*⏹ Stopped before an answer was generated.*"
#             save(text, {"sources": sources, "mode": final_state.get("mode", mode),
#                         "stopped_early": True})
#         raise




#####################################  22nd Sept Update ############################################ 

"""
graph.py  —  LangGraph orchestration:  LOCAL RAG  ⇄  GLOBAL RAG

    ┌──────────┐   mode = "ask"      → the app asks the user first
    │  route   │   mode = "local"    → your ingested documents
    └────┬─────┘   mode = "global"   → arXiv / OpenAlex / Crossref / web search
         │         mode = "both"     → local first, then the public sources
         ▼
  local_retrieve ──▶ plan_queries ──▶ search ──▶ fetch ──▶ reflect ──▶ prepare ──▶ answer
                          ▲                                    │
                          └──────── one refinement round ──────┘

The graph does routing, retrieval, tool calls and evidence checking.
The final answer is streamed token by token by `answer_stream()` so the UI stays live.
"""

import json
import re
from typing import Dict, List, Optional, TypedDict

from langgraph.graph import StateGraph, START, END

import config as C
import rag
import websearch
from rag_common import chat_text, stream_tokens, count_tokens, pdf_path
from session_memory import add_message


# ─────────────────────────────────────────────────────────────────────────────
# STATE
# ─────────────────────────────────────────────────────────────────────────────
class RAGState(TypedDict, total=False):
    question: str
    mode: str                     # ask | local | global | both
    session_id: str
    preferences: Dict
    selected_sources: Optional[List[str]]
    standalone: str
    need_mode: bool
    local_context: str
    local_sources: Dict[int, Dict]
    notes: List[str]
    queries: List[str]
    results: List[Dict]
    evidence: List[Dict]
    rounds: int
    enough: bool
    prompt: str
    source_map: Dict[int, Dict]
    status: List[str]


def _add_status(state: RAGState, message: str) -> List[str]:
    return list(state.get("status") or []) + [message]


# ─────────────────────────────────────────────────────────────────────────────
# NODES
# ─────────────────────────────────────────────────────────────────────────────
def route_node(state: RAGState) -> RAGState:
    mode = (state.get("mode") or C.DEFAULT_SEARCH_MODE or "ask").lower()
    if mode not in ("local", "global", "both", "ask"):
        mode = "ask"
    if mode == "ask":
        return {"mode": mode, "need_mode": True,
                "status": _add_status(state, "❓ Waiting for the user to choose local or global search")}
    label = {"local": "🔒 Searching your own documents",
             "global": "🌐 Searching public research articles and the web",
             "both": "🔒➕🌐 Searching your documents and public sources"}[mode]
    return {"mode": mode, "need_mode": False, "status": _add_status(state, label)}


def local_retrieve_node(state: RAGState) -> RAGState:
    analysis = rag.analyze_question(state["question"], state.get("session_id", "default"),
                                    state.get("selected_sources"))
    documents, notes = rag.retrieve_documents(analysis)
    context, source_map = rag.build_context(documents)
    types = {}
    for document in documents:
        key = document["metadata"].get("content_type", "text")
        types[key] = types.get(key, 0) + 1
    summary = " · ".join(f"{value} {key}" for key, value in types.items()) or "nothing"
    return {
        "standalone": analysis["standalone"],
        "local_context": context,
        "local_sources": source_map,
        "notes": notes,
        "status": _add_status(state, f"📄 Local hits: {summary}"),
    }


PLAN_PROMPT = """You are planning a literature search for this question:

"{question}"

{extra}

Write {n} short web/arXiv search queries that would find public research articles or reliable
technical pages answering it. Use the terminology of the field, no full sentences, no quotes.

Return ONLY JSON: {{"queries": ["...", "..."]}}"""


def plan_queries_node(state: RAGState) -> RAGState:
    question = state.get("standalone") or state["question"]
    extra = ""
    if state.get("mode") == "both" and state.get("local_context"):
        extra = ("Context already found in the user's own documents (use its vocabulary, "
                 "but search for public sources):\n" + state["local_context"][:1200])
    previous = state.get("queries") or []
    if previous:
        extra += ("\n\nThese queries were already tried and did not return enough: "
                  + "; ".join(previous) + ". Write DIFFERENT, broader or more specific ones.")
    try:
        raw = chat_text([{"role": "user", "content": PLAN_PROMPT.format(
            question=question, extra=extra, n=3)}], max_tokens=400, temperature=0.2)
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        queries = [q.strip() for q in json.loads(raw).get("queries", []) if q and q.strip()][:4]
    except Exception as e:
        print(f"  [Query planning failed: {e}] — using the question itself")
        queries = []
    if not queries:
        queries = [re.sub(r"[?\"']", " ", question).strip()[:180]]
    return {"queries": previous + queries,
            "status": _add_status(state, "🔎 Search queries: " + " | ".join(queries))}


def search_node(state: RAGState) -> RAGState:
    queries = (state.get("queries") or [])[-4:]
    results = websearch.gather_sources(queries, want_papers=True,
                                       want_web=C.WEB_SEARCH_BACKEND != "off")
    known = {(r.get("title", "").lower()[:80]) for r in (state.get("results") or [])}
    merged = list(state.get("results") or [])
    for item in results:
        if item.get("title", "").lower()[:80] not in known:
            merged.append(item)
    kinds = {}
    for item in merged:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    summary = " · ".join(f"{value} {key}" for key, value in kinds.items()) or "no results"
    return {"results": merged,
            "status": _add_status(state, f"🌐 Found: {summary}")}


def fetch_node(state: RAGState) -> RAGState:
    results = state.get("results") or []
    evidence = list(state.get("evidence") or [])
    fetched_urls = {item["url"] for item in evidence}
    downloads = 0

    for item in results:
        if downloads >= C.WEB_FETCH_PAGES:
            break
        url = item.get("pdf_url") or item.get("url") or ""
        if not url or url in fetched_urls:
            continue
        # papers with a usable abstract do not always need a full download
        body = ""
        if item["kind"] == "web" or not item.get("abstract"):
            body = websearch.fetch_page(url)
            downloads += 1
        evidence.append({
            "title": item.get("title", ""),
            "url": item.get("url") or url,
            "kind": item["kind"],
            "published": item.get("published", ""),
            "authors": ", ".join(item.get("authors", [])[:4]),
            "venue": item.get("venue", ""),
            "text": (item.get("abstract") or "")[:3000] + ("\n\n" + body if body else ""),
        })
        fetched_urls.add(url)

    # sources that were only listed (no download) still go in as title + abstract
    for item in results:
        url = item.get("url") or item.get("pdf_url") or ""
        if url and url not in fetched_urls and item.get("abstract"):
            evidence.append({
                "title": item.get("title", ""), "url": url, "kind": item["kind"],
                "published": item.get("published", ""),
                "authors": ", ".join(item.get("authors", [])[:4]),
                "venue": item.get("venue", ""), "text": item["abstract"][:2500],
            })
            fetched_urls.add(url)

    return {"evidence": evidence,
            "status": _add_status(state, f"📥 Collected {len(evidence)} public source(s)")}


REFLECT_PROMPT = """Question: "{question}"

Here are the titles and short extracts of the sources found so far:
{summaries}

Can this question be answered from these sources, with specific facts and not just guesses?
Return ONLY JSON: {{"enough": true or false, "missing": "what is still missing, one sentence"}}"""


def reflect_node(state: RAGState) -> RAGState:
    evidence = state.get("evidence") or []
    rounds = int(state.get("rounds") or 0) + 1
    if not evidence:
        return {"rounds": rounds, "enough": False,
                "status": _add_status(state, "⚠️ No public sources reachable yet")}
    if rounds >= C.GLOBAL_SEARCH_ROUNDS:
        return {"rounds": rounds, "enough": True,
                "status": _add_status(state, "✅ Evidence collected")}

    summaries = "\n".join(f"- {item['title']}: {item['text'][:300]}" for item in evidence[:8])
    try:
        raw = chat_text([{"role": "user", "content": REFLECT_PROMPT.format(
            question=state.get("standalone") or state["question"], summaries=summaries)}],
            max_tokens=300, temperature=0.0)
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        verdict = json.loads(raw)
        enough = bool(verdict.get("enough"))
        missing = verdict.get("missing", "")
    except Exception:
        enough, missing = True, ""
    message = "✅ Evidence looks sufficient" if enough else f"🔁 Searching again — missing: {missing[:120]}"
    return {"rounds": rounds, "enough": enough, "status": _add_status(state, message)}


GLOBAL_RULES = """
=========================================================
PUBLIC SOURCE RULES
=========================================================
The sources below come from arXiv, OpenAlex, Crossref and the open web — NOT from the user's documents.
1. Use only what the sources say; never fill gaps from memory.
2. Cite every factual sentence with the tags [S1], [S2] ... The app turns them into the source titles and links.
3. Say clearly when something is a preprint, when sources disagree, or when the evidence is thin.
4. Mention publication years when they matter for how current the information is.
5. If the sources do not answer the question, say so and suggest what to search for instead.
"""


def prepare_node(state: RAGState) -> RAGState:
    preferences = state.get("preferences") or {}
    source_map: Dict[int, Dict] = {}
    blocks: List[str] = []
    used = 0
    number = 0

    for entry in (state.get("local_sources") or {}).values():
        number += 1
        source_map[number] = entry

    if state.get("local_context"):
        # re-number the local context so [S#] stays consistent
        local_block = state["local_context"]
        blocks.append("YOUR OWN DOCUMENTS\n" + local_block)
        used += count_tokens(local_block)

    for item in state.get("evidence") or []:
        if used > C.MAX_CONTEXT_TOKENS:
            break
        number += 1
        text = item["text"][:C.WEB_FETCH_MAX_CHARS]
        meta = " | ".join(x for x in [item.get("kind", ""), item.get("authors", ""),
                                      item.get("venue", ""), item.get("published", "")] if x)
        block = (f"[S{number}] web source: {item['title']} | {meta}\n"
                 f"URL: {item['url']}\n{text}")
        used += count_tokens(block)
        blocks.append(block)
        source_map[number] = {
            "source": item["title"][:120] or item["url"],
            "page": 0,
            "url": item["url"],
            "kind": item.get("kind", "web"),
            "label": "",
            "content_type": "web",
            "image_path": "",
            "published": item.get("published", ""),
        }

    context = "\n\n".join(blocks) if blocks else "(no sources found)"
    mode = state.get("mode", "local")
    if mode == "local":
        prompt = rag._RAG_PROMPT.format(
            tone=preferences.get("tone", "Professional"),
            detail_level=preferences.get("detail_level", "Moderate"),
            audience=preferences.get("audience", "Student"),
            answer_format=preferences.get("answer_format", "Structured explanation"),
            language=preferences.get("language", "English"),
            conversation_history=preferences.get("history", "No previous conversation."),
            context=context,
            notes="\n".join(state.get("notes") or []) or "none",
            question=state["question"],
        )
    else:
        prompt = rag._RAG_PROMPT.format(
            tone=preferences.get("tone", "Professional"),
            detail_level=preferences.get("detail_level", "Moderate"),
            audience=preferences.get("audience", "Student"),
            answer_format=preferences.get("answer_format", "Structured explanation"),
            language=preferences.get("language", "English"),
            conversation_history=preferences.get("history", "No previous conversation."),
            context=context,
            notes=("\n".join(state.get("notes") or []) or "none") + "\n" + GLOBAL_RULES,
            question=state["question"],
        )
    return {"prompt": prompt, "source_map": source_map,
            "status": _add_status(state, "⏳ Writing the answer...")}


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH WIRING
# ─────────────────────────────────────────────────────────────────────────────
def _after_route(state: RAGState) -> str:
    if state.get("need_mode"):
        return END
    return "local_retrieve" if state["mode"] in ("local", "both") else "plan_queries"


def _after_local(state: RAGState) -> str:
    return "plan_queries" if state["mode"] == "both" else "prepare"


def _after_reflect(state: RAGState) -> str:
    return "prepare" if state.get("enough") else "plan_queries"


def build_graph():
    graph = StateGraph(RAGState)
    graph.add_node("route", route_node)
    graph.add_node("local_retrieve", local_retrieve_node)
    graph.add_node("plan_queries", plan_queries_node)
    graph.add_node("search", search_node)
    graph.add_node("fetch", fetch_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("prepare", prepare_node)

    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", _after_route,
                                {"local_retrieve": "local_retrieve",
                                 "plan_queries": "plan_queries", END: END})
    graph.add_conditional_edges("local_retrieve", _after_local,
                                {"plan_queries": "plan_queries", "prepare": "prepare"})
    graph.add_edge("plan_queries", "search")
    graph.add_edge("search", "fetch")
    graph.add_edge("fetch", "reflect")
    graph.add_conditional_edges("reflect", _after_reflect,
                                {"plan_queries": "plan_queries", "prepare": "prepare"})
    graph.add_edge("prepare", END)
    return graph.compile()


_compiled = {"graph": None}


def get_graph():
    if _compiled["graph"] is None:
        _compiled["graph"] = build_graph()
    return _compiled["graph"]


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT (streaming)
# ─────────────────────────────────────────────────────────────────────────────
MODE_QUESTION = ("Where should I look for this?\n\n"
                 "- **🔒 My documents** — only the PDFs you ingested (with page numbers)\n"
                 "- **🌐 Public research** — arXiv, OpenAlex, Crossref and web search\n"
                 "- **🔒➕🌐 Both** — your documents first, then public sources")


def answer_stream(question: str, mode: str, preferences: Dict, session_id: str = "default",
                  selected_sources: Optional[List[str]] = None):
    """Yields status dicts, then answer tokens, then {'__final__': {...}}.

    If the caller stops consuming (the Stop button), the generator is closed: the LM Studio
    stream is closed too, and whatever was written so far is saved as a stopped answer.
    """
    from session_memory import (format_history, maybe_generate_title,
                                chat_memory_needs_compaction, compact_chat_memory,
                                compact_in_background)

    # The whole conversation of THIS chat (summary of older part + recent messages).
    # If the chat outgrew its budget and the background summary has not caught up,
    # summarise now so nothing from the chat is lost.
    if chat_memory_needs_compaction(session_id):
        yield rag.status("🧠 Summarising the earlier part of this chat...")
        compact_chat_memory(session_id)

    preferences = dict(preferences or {})
    preferences["history"] = format_history(session_id=session_id)
    citation_style = preferences.get("citation_style", "File and page")

    state: RAGState = {
        "question": question,
        "mode": (mode or C.DEFAULT_SEARCH_MODE),
        "session_id": session_id,
        "preferences": preferences,
        "selected_sources": selected_sources,
        "rounds": 0,
        "status": [],
    }

    final_state: RAGState = dict(state)
    parts: List[str] = []
    citations = None
    saved = False

    def save(answer_text: str, meta: Dict):
        nonlocal saved
        if saved:
            return
        saved = True
        add_message("user", question, session_id=session_id)
        add_message("assistant", answer_text, session_id=session_id, meta=meta)

    try:
        # ── 1. graph: routing, retrieval, web tools ─────────────────────────
        seen_status = 0
        try:
            for update in get_graph().stream(state, stream_mode="updates"):
                for _node, node_state in update.items():
                    if not isinstance(node_state, dict):
                        continue
                    final_state.update(node_state)
                    messages = node_state.get("status") or []
                    for message in messages[seen_status:]:
                        yield rag.status(message)
                    seen_status = max(seen_status, len(messages))
        except GeneratorExit:
            raise
        except Exception as e:
            message = f"⚠️ Search failed: {e}"
            yield rag.status(message, "error")
            yield message
            save(message, {"mode": mode, "error": True})
            yield {"__status__": True, "__final__": {"answer": message, "sources": [], "mode": mode}}
            return

        # ── 2. the user still has to choose local or global ─────────────────
        if final_state.get("need_mode"):
            yield rag.status("❓ Choose where to search", "complete")
            yield MODE_QUESTION
            save(MODE_QUESTION, {"choose_mode": True, "original_question": question})
            yield {"__status__": True,
                   "__final__": {"answer": MODE_QUESTION, "sources": [], "choose_mode": True,
                                 "original_question": question, "mode": "ask"}}
            return

        # ── 3. stream the answer ────────────────────────────────────────────
        source_map = final_state.get("source_map") or {}
        citations = rag.CitationFormatter(source_map, citation_style)
        first = True
        token_stream = stream_tokens([{"role": "user", "content": final_state["prompt"]}])
        try:
            for token in token_stream:
                visible = citations.feed(token)
                if not visible:
                    continue
                if first:
                    yield rag.status("💬 Streaming answer...", "complete")
                    first = False
                parts.append(visible)
                yield visible
            tail = citations.flush()
            if tail:
                parts.append(tail)
                yield tail
        except GeneratorExit:
            raise
        except Exception as e:
            error = f"\n\n⚠️ The language model failed: {e}"
            parts.append(error)
            yield error
        finally:
            token_stream.close()          # stops LM Studio if we were interrupted

        answer = "".join(parts).strip()
        sources = citations.cited_sources()
        save(answer, {"sources": sources, "mode": final_state.get("mode", mode)})
        maybe_generate_title(session_id)
        compact_in_background(session_id)      # keeps the next question fast

        if C.AUTO_OPEN_PDF_ON_HOST:
            for source in sources:
                if source.get("page"):
                    rag.open_pdf_at_page(pdf_path(source["source"]), source["page"])
                    break

        yield {"__status__": True, "__pdf_info__": sources}
        yield {"__status__": True, "__final__": {"answer": answer, "sources": sources,
                                                 "mode": final_state.get("mode", mode)}}

    except GeneratorExit:
        # ── Stop button: keep what was written so far ───────────────────────
        if not saved:
            partial = "".join(parts).strip()
            if citations is not None:
                partial += citations.flush()
            sources = citations.cited_sources() if (citations is not None and citations.cited) else []
            text = (partial + "\n\n*⏹ Generation stopped.*") if partial \
                else "*⏹ Stopped before an answer was generated.*"
            save(text, {"sources": sources, "mode": final_state.get("mode", mode),
                        "stopped_early": True})
        raise