# """
# rag.py  —  LOCAL RAG over your ingested documents

# Retrieval = exact label pins ("pull Figure 4.2")  +  vector search  +  BM25 keyword
# search (pure python, nothing is downloaded)  →  rank fusion  →  neighbour expansion
# →  token-budgeted context with [S1], [S2] ... source ids.

# The model cites [S3]; the code turns that into the exact file and page from the
# chunk's metadata, so page numbers are never invented. Figures carry the path of
# their cropped image, so the UI can show the actual figure.
# """

# import os
# import re
# import math
# import subprocess
# import threading
# from collections import defaultdict
# from typing import Iterator, List, Dict, Optional, Tuple

# import numpy as np
# from langchain_core.prompts import PromptTemplate

# import config as C
# from rag_common import (
#     get_collection, get_embedding, stream_tokens, chat_text, count_tokens,
#     find_labels, caption_label, pretty_label, pdf_url, pdf_path, render_pdf_page,
# )
# from session_memory import (
#     initialize_memory, add_message, format_history, clear_memory, show_memory,
# )

# initialize_memory()


# # ─────────────────────────────────────────────────────────────────────────────
# # TOKENIZER + BM25  (in-memory, no external packages)
# # ─────────────────────────────────────────────────────────────────────────────
# STOPWORDS = set("""a an the of and or in on to for with by is are was were be been this that these
# those it its as at from which what how does do did explain describe show give me tell about please
# can could would should you your i we our their there than then into over under between also any all
# each other such not no yes if so may might will shall has have had get got using used""".split())


# def tokenize(text: str) -> List[str]:
#     tokens = []
#     for token in re.findall(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*|\d+(?:\.\d+)*", (text or "").lower()):
#         if token in STOPWORDS or (len(token) == 1 and not token.isdigit()):
#             continue
#         tokens.append(token)
#         if "-" in token:
#             tokens.extend(part for part in token.split("-") if part and part not in STOPWORDS)
#     tokens.extend(label.replace(" ", "_") for label in find_labels(text))
#     return tokens


# class BM25:
#     def __init__(self, documents_tokens: List[List[str]], k1: float = 1.4, b: float = 0.75):
#         self.n = len(documents_tokens)
#         self.k1, self.b = k1, b
#         self.doc_len = np.array([len(t) for t in documents_tokens], dtype=np.float32)
#         self.avgdl = float(self.doc_len.mean()) if self.n else 1.0
#         postings = defaultdict(lambda: defaultdict(int))
#         for index, tokens in enumerate(documents_tokens):
#             for token in tokens:
#                 postings[token][index] += 1
#         self.postings, self.idf = {}, {}
#         for term, entries in postings.items():
#             ids = np.fromiter(entries.keys(), dtype=np.int32, count=len(entries))
#             tfs = np.fromiter(entries.values(), dtype=np.float32, count=len(entries))
#             self.postings[term] = (ids, tfs)
#             self.idf[term] = math.log(1 + (self.n - len(entries) + 0.5) / (len(entries) + 0.5))

#     def search(self, query_tokens: List[str], k: int,
#                mask: Optional[np.ndarray] = None) -> List[int]:
#         if not self.n:
#             return []
#         scores = np.zeros(self.n, dtype=np.float32)
#         for term in set(query_tokens):
#             if term not in self.postings:
#                 continue
#             ids, tfs = self.postings[term]
#             denominator = tfs + self.k1 * (1 - self.b + self.b * self.doc_len[ids] / self.avgdl)
#             scores[ids] += self.idf[term] * tfs * (self.k1 + 1) / denominator
#         if mask is not None:
#             scores[~mask] = 0
#         k = min(k, self.n)
#         top = np.argpartition(-scores, k - 1)[:k]
#         top = top[np.argsort(-scores[top])]
#         return [int(i) for i in top if scores[i] > 0]


# # ─────────────────────────────────────────────────────────────────────────────
# # DOCUMENT INDEX
# # ─────────────────────────────────────────────────────────────────────────────
# class DocumentIndex:
#     def __init__(self):
#         collection = get_collection()
#         ids, documents, metadatas = [], [], []
#         offset, page_size = 0, 5000
#         while True:
#             batch = collection.get(include=["documents", "metadatas"],
#                                    limit=page_size, offset=offset)
#             batch_ids = batch.get("ids") or []
#             if not batch_ids:
#                 break
#             ids += batch_ids
#             documents += batch["documents"]
#             metadatas += batch["metadatas"]
#             offset += len(batch_ids)
#             if len(batch_ids) < page_size:
#                 break

#         self.ids = ids
#         self.docs = documents
#         self.metas = [m or {} for m in metadatas]
#         self.position = {chunk_id: i for i, chunk_id in enumerate(ids)}
#         self.bm25 = BM25([tokenize(text) for text in documents])

#         self.sources = sorted({m.get("source", "") for m in self.metas if m.get("source")})
#         self.source_code = {source: index for index, source in enumerate(self.sources)}
#         self.source_array = np.array([self.source_code.get(m.get("source", ""), -1)
#                                       for m in self.metas], dtype=np.int32)

#         self.label_map = defaultdict(list)     # "figure 4.2" → element chunks
#         self.caption_map = defaultdict(list)   # "figure 4.2" → text chunks with that caption
#         self.chunk_map = {}                    # (source, page, chunk) → position
#         self.titles = {}
#         self.elements = defaultdict(list)

#         for index, meta in enumerate(self.metas):
#             source = meta.get("source", "")
#             content_type = meta.get("content_type", "text")
#             label = meta.get("element_label", "")
#             if label:
#                 self.label_map[label].append(index)
#                 self.elements[source].append(index)
#             if content_type == "document_card":
#                 self.titles[source] = meta.get("title", "")
#             if content_type == "text":
#                 self.chunk_map[(source, meta.get("page"), meta.get("chunk"))] = index
#                 for line in documents[index].split("\n"):
#                     found = caption_label(line)
#                     if found:
#                         self.caption_map[found].append(index)
#         print(f"[Index] {len(ids)} chunks from {len(self.sources)} document(s) loaded.")

#     def mask_for(self, scope: Optional[List[str]]) -> Optional[np.ndarray]:
#         if not scope:
#             return None
#         codes = [self.source_code[s] for s in scope if s in self.source_code]
#         return np.isin(self.source_array, codes)


# _index_lock = threading.Lock()
# _index = {"value": None}


# def get_index() -> DocumentIndex:
#     with _index_lock:
#         if _index["value"] is None:
#             _index["value"] = DocumentIndex()
#         return _index["value"]


# def reload_index():
#     with _index_lock:
#         _index["value"] = None
#     return get_index()


# def list_documents() -> List[Dict]:
#     index = get_index()
#     return [{"source": source, "title": index.titles.get(source, "")} for source in index.sources]


# def warmup():
#     get_index()


# # ─────────────────────────────────────────────────────────────────────────────
# # QUESTION ANALYSIS
# # ─────────────────────────────────────────────────────────────────────────────
# DETAILED_KEYWORDS = [
#     "in detail", "detailed", "step by step", "step-by-step", "in depth", "thoroughly",
#     "elaborate", "how does", "how do", "algorithm", "methodology", "proposed method",
#     "proposed algorithm", "architecture", "derivation", "compare", "comparison",
#     "which paper", "all papers", "list all",
# ]
# TYPE_WORDS = {
#     "figure_description": re.compile(r"\b(fig\w*|diagram|flow ?chart|chart|graph|plot|image|picture|"
#                                      r"illustration|visuali[sz]ation|architecture|block diagram|"
#                                      r"photo\w*|show me|pull|display|draw)\b", re.I),
#     "table": re.compile(r"\b(tab(le)?s?|tabular|rows?|columns?|cell)\b", re.I),
#     "equation": re.compile(r"\b(equations?|formula\w*|math\w*|derive|derivation|loss function|"
#                            r"objective function|symbols?|notation|latex|eq)\b", re.I),
#     "algorithm": re.compile(r"\b(algorithm\w*|pseudo-?code|procedure|steps of)\b", re.I),
# }

# _REWRITE_PROMPT = PromptTemplate(
#     input_variables=["history", "question"],
#     template="""You rewrite the user's latest message into ONE standalone question for document search.

# Resolve pronouns (it, this, that figure, the previous table) using the conversation.
# Keep labels exactly as written (Figure 4.2, Table II, Eq. (5), Algorithm 1) and keep any
# document/file name the user is referring to. Do NOT answer. If it is already standalone,
# return it unchanged.

# CONVERSATION HISTORY:
# {history}

# CURRENT QUESTION:
# {question}

# Return ONLY the standalone question.""",
# )


# def create_standalone_question(question: str, session_id: str = "default") -> str:
#     history = format_history(max_messages=C.MAX_MEMORY_MESSAGES, session_id=session_id)
#     if history == "No previous conversation.":
#         return question
#     try:
#         rewritten = chat_text(
#             [{"role": "user", "content": _REWRITE_PROMPT.format(history=history, question=question)}],
#             max_tokens=300, temperature=0.0).strip().strip('"')
#         return rewritten if 3 <= len(rewritten) <= 600 else question
#     except Exception as e:
#         print(f"  [Rewrite failed]: {e}")
#         return question


# def detect_sources(question: str) -> List[str]:
#     """Documents named in the question ('from the radar paper', 'in report2.pdf')."""
#     index = get_index()
#     lowered = question.lower()
#     words = set(re.findall(r"[a-z0-9]+", lowered))
#     found = []
#     for source in index.sources:
#         stem = os.path.splitext(source)[0].lower()
#         if source.lower() in lowered or (len(stem) >= 4 and stem in lowered):
#             found.append(source)
#             continue
#         name_words = {w for w in re.findall(r"[a-z0-9]+", stem)
#                       if len(w) > 3 and w not in STOPWORDS}
#         if len(name_words) >= 2 and len(name_words & words) >= max(2, math.ceil(0.6 * len(name_words))):
#             found.append(source)
#             continue
#         title_words = {w for w in re.findall(r"[a-z0-9]+", index.titles.get(source, "").lower())
#                        if len(w) > 3 and w not in STOPWORDS}
#         if len(title_words) >= 3 and len(title_words & words) >= max(3, math.ceil(0.6 * len(title_words))):
#             found.append(source)
#     return found


# def analyze_question(question: str, session_id: str,
#                      selected_sources: Optional[List[str]] = None) -> Dict:
#     standalone = create_standalone_question(question, session_id)
#     lowered = standalone.lower()
#     labels = find_labels(standalone)
#     types = {ctype for ctype, regex in TYPE_WORDS.items() if regex.search(standalone)}
#     for label in labels:
#         kind = label.split()[0]
#         types.add({"figure": "figure_description", "table": "table",
#                    "equation": "equation", "algorithm": "algorithm"}.get(kind, "text"))
#     mentioned = detect_sources(standalone)
#     if selected_sources:
#         scope = [s for s in mentioned if s in selected_sources] or list(selected_sources)
#     else:
#         scope = mentioned
#     return {
#         "question": question,
#         "standalone": standalone,
#         "history": format_history(C.MAX_MEMORY_MESSAGES, session_id),
#         "labels": labels,
#         "types": types,
#         "scope": scope,
#         "detailed": any(keyword in lowered for keyword in DETAILED_KEYWORDS),
#     }


# # ─────────────────────────────────────────────────────────────────────────────
# # RETRIEVAL
# # ─────────────────────────────────────────────────────────────────────────────
# def _where(scope: Optional[List[str]], content_type: Optional[str] = None):
#     conditions = []
#     if scope:
#         conditions.append({"source": {"$in": list(scope)}})
#     if content_type:
#         conditions.append({"content_type": content_type})
#     if not conditions:
#         return None
#     return conditions[0] if len(conditions) == 1 else {"$and": conditions}


# def _vector_search(embedding, k: int, where) -> List[int]:
#     index = get_index()
#     try:
#         results = get_collection().query(query_embeddings=[embedding], n_results=k,
#                                          where=where, include=["metadatas"])
#     except Exception as e:
#         print(f"  [Vector search failed]: {e}")
#         return []
#     positions = []
#     for chunk_id in (results.get("ids") or [[]])[0]:
#         if chunk_id in index.position:
#             positions.append(index.position[chunk_id])
#     return positions


# def retrieve_documents(analysis: Dict) -> Tuple[List[Dict], List[str]]:
#     index = get_index()
#     scope = analysis["scope"] or None
#     notes = []
#     if scope:
#         notes.append("Search restricted to: " + ", ".join(scope))

#     # 1. exact label matches — "pull Figure 4.2"
#     pinned: List[int] = []
#     for label in analysis["labels"]:
#         elements = [i for i in index.label_map.get(label, [])
#                     if not scope or index.metas[i].get("source") in scope]
#         captions = [i for i in index.caption_map.get(label, [])
#                     if not scope or index.metas[i].get("source") in scope]
#         pinned += elements[: C.MAX_PINNED_PER_LABEL] + captions[:3]
#         if not elements and not captions:
#             notes.append(f"No element labelled '{pretty_label(label)}' exists in the index"
#                          + (f" for {', '.join(scope)}" if scope else "") + ".")

#     # 2. hybrid search
#     embedding = get_embedding(analysis["standalone"], is_query=True)
#     ranked = [(_vector_search(embedding, C.VECTOR_K, _where(scope)), 1.0)]
#     ranked.append((index.bm25.search(tokenize(analysis["standalone"]), C.BM25_K,
#                                      index.mask_for(scope)), 1.0))
#     for content_type in analysis["types"]:
#         ranked.append((_vector_search(embedding, 8, _where(scope, content_type)), 0.8))

#     fused = defaultdict(float)
#     for positions, weight in ranked:
#         for rank, position in enumerate(positions):
#             factor = weight
#             if index.metas[position].get("content_type") == "document_card":
#                 factor *= 0.6
#             fused[position] += factor / (C.RRF_K + rank + 1)

#     pinned_set = set(pinned)
#     candidates = [i for i, _ in sorted(fused.items(), key=lambda kv: -kv[1]) if i not in pinned_set]
#     top_k = C.DETAILED_TOP_K if analysis["detailed"] else C.TOP_K
#     selected = candidates[:max(3, top_k - min(len(pinned), top_k // 2))]

#     # 3. neighbouring text chunks around the best hits
#     ordered: List[int] = []
#     for rank, position in enumerate(pinned + selected):
#         meta = index.metas[position]
#         group = [position]
#         if rank >= len(pinned) and rank - len(pinned) < 4 and meta.get("content_type") == "text":
#             chunk = meta.get("chunk", -1)
#             if isinstance(chunk, int) and chunk >= 0:
#                 for offset in range(1, C.NEIGHBOR_CHUNKS + 1):
#                     before = index.chunk_map.get((meta.get("source"), meta.get("page"), chunk - offset))
#                     after = index.chunk_map.get((meta.get("source"), meta.get("page"), chunk + offset))
#                     if before is not None:
#                         group.insert(0, before)
#                     if after is not None:
#                         group.append(after)
#         # a text chunk that says "[[TABLE: Table 3 …]]" pulls that table in
#         if meta.get("content_type") == "text":
#             for tag in re.findall(r"\[\[TABLE: (.+?) —", index.docs[position]):
#                 for label in find_labels(tag):
#                     group += [j for j in index.label_map.get(label, [])
#                               if index.metas[j].get("source") == meta.get("source")][:2]
#         ordered.extend(group)

#     documents, seen = [], set()
#     for position in ordered:
#         if position in seen:
#             continue
#         seen.add(position)
#         documents.append({"text": index.docs[position], "metadata": index.metas[position]})
#         if len(documents) >= C.MAX_CONTEXT_CHUNKS:
#             break
#     return documents, notes


# # ─────────────────────────────────────────────────────────────────────────────
# # CONTEXT + CITATIONS
# # ─────────────────────────────────────────────────────────────────────────────
# TYPE_LABELS = {
#     "figure_description": "FIGURE DESCRIPTION",
#     "table": "TABLE DATA",
#     "equation": "MATHEMATICAL CONTENT",
#     "algorithm": "ALGORITHM",
#     "document_card": "DOCUMENT CARD",
#     "text": "TEXT",
# }


# def build_context(documents: List[Dict]) -> Tuple[str, Dict[int, Dict]]:
#     parts, source_map, used = [], {}, 0
#     for document in documents:
#         meta = document["metadata"]
#         number = len(source_map) + 1
#         label = pretty_label(meta.get("element_label", ""))
#         head = (f"[S{number}] file: {meta.get('source')} | page: {meta.get('page', 1)} | "
#                 f"type: {TYPE_LABELS.get(meta.get('content_type', 'text'), 'TEXT')}"
#                 + (f" ({label})" if label else ""))
#         block = f"{head}\n{document['text']}"
#         cost = count_tokens(block)
#         if used + cost > C.MAX_CONTEXT_TOKENS:
#             continue
#         used += cost
#         parts.append(block)
#         source_map[number] = {
#             "source": meta.get("source", ""),
#             "page": int(meta.get("page", 1) or 1),
#             "label": label,
#             "content_type": meta.get("content_type", "text"),
#             "image_path": meta.get("image_path", ""),
#             "caption": meta.get("caption", ""),
#             "local_path": pdf_path(meta.get("source", "")),
#         }
#     return ("\n\n".join(parts) if parts else "(nothing relevant found)"), source_map


# CITE_RE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S?\d+)*\s*\]")


# class CitationFormatter:
#     """Turns [S3] in the stream into '(file.pdf, p. 12)' using the chunk metadata."""

#     def __init__(self, source_map: Dict[int, Dict], citation_style: str):
#         self.map = source_map
#         self.style = citation_style
#         self.buf = ""
#         self.last_char = ""
#         self.cited: List[int] = []

#     def _convert(self, tag: str) -> str:
#         numbers = [int(n) for n in re.findall(r"\d+", tag) if int(n) in self.map]
#         for number in numbers:
#             if number not in self.cited:
#                 self.cited.append(number)
#         if not numbers or self.style == "No citation":
#             return ""
#         by_file = {}
#         for number in numbers:
#             entry = self.map[number]
#             pages = by_file.setdefault(entry["source"], [])
#             page = int(entry.get("page", 0) or 0)
#             if page > 0 and page not in pages:
#                 pages.append(page)
#         if self.style == "File only":
#             return "(" + "; ".join(by_file) + ")"
#         rendered = []
#         for name, pages in by_file.items():
#             # web / arXiv sources have no page number — only the title is shown
#             rendered.append(f"{name}, p. {', '.join(map(str, sorted(pages)))}" if pages else name)
#         return "(" + "; ".join(rendered) + ")"

#     def feed(self, token: str) -> str:
#         text = self._feed(token)
#         if text:
#             self.last_char = text[-1]
#         return text

#     def _feed(self, token: str) -> str:
#         self.buf += token
#         out = []
#         while self.buf:
#             start = self.buf.find("[")
#             if start == -1:
#                 out.append(self.buf)
#                 self.buf = ""
#                 break
#             out.append(self.buf[:start])
#             self.buf = self.buf[start:]
#             end = self.buf.find("]")
#             if end == -1:
#                 if len(self.buf) > 40:
#                     out.append(self.buf[0])
#                     self.buf = self.buf[1:]
#                     continue
#                 break
#             candidate = self.buf[:end + 1]
#             if CITE_RE.fullmatch(candidate):
#                 converted = self._convert(candidate)
#                 previous = "".join(out)[-1:] or self.last_char
#                 if converted and previous and not previous.isspace() and previous not in "([":
#                     converted = " " + converted
#                 out.append(converted)
#                 self.buf = self.buf[end + 1:]
#             else:
#                 out.append("[")
#                 self.buf = self.buf[1:]
#         return "".join(out)

#     def flush(self) -> str:
#         rest, self.buf = self.buf, ""
#         if rest:
#             self.last_char = rest[-1]
#         return rest

#     def cited_sources(self, fallback: int = 3) -> List[Dict]:
#         order = self.cited or list(self.map.keys())[:fallback]
#         merged, result = {}, []
#         for number in order:
#             entry = self.map[number]
#             key = (entry["source"], entry["page"], entry["label"])
#             if key in merged:
#                 continue
#             merged[key] = True
#             item = dict(entry)
#             item["explicitly_cited"] = bool(self.cited)
#             result.append(item)
#         return result


# # ─────────────────────────────────────────────────────────────────────────────
# # PROMPT
# # ─────────────────────────────────────────────────────────────────────────────
# _RAG_PROMPT = PromptTemplate(
#     input_variables=["tone", "detail_level", "audience", "answer_format", "language",
#                      "conversation_history", "context", "notes", "question"],
#     template="""You are an expert technical research assistant working with the user's own documents.

# The DOCUMENT CONTEXT below contains numbered sources [S1], [S2] ... Each states its file, page and type:
# TEXT, TABLE DATA (Markdown), FIGURE DESCRIPTION (a vision model's reading of the cropped figure),
# MATHEMATICAL CONTENT, ALGORITHM, or DOCUMENT CARD.

# =========================================================
# USER PREFERENCES
# =========================================================
# Tone: {tone} | Detail level: {detail_level} | Audience: {audience}
# Answer format: {answer_format} | Language: {language}

# =========================================================
# GROUNDING AND CITATION RULES
# =========================================================
# 1. Use ONLY the document context. The conversation history is only for understanding what the user means.
# 2. End every factual sentence with the source tags that support it, e.g. [S3] or [S2][S5].
#    NEVER write file names or page numbers yourself — the tags are replaced with the exact file and page.
# 3. Never invent numbers, values, equations, steps, labels or references. Copy numbers exactly.
# 4. If the context answers only part of the question, answer that part, say what is missing and ask ONE
#    specific clarifying question (which document, which figure/table number, which section).
# 5. If nothing relevant is present, say "I could not find this in your documents." and ask a short
#    clarifying question.
# 6. The same label in different files refers to different items — keep them separate and name each file.

# =========================================================
# TABLE RULES
# =========================================================
# Reproduce the relevant rows and columns as a Markdown table with the exact values, then explain what it
# shows. Never summarise a table without showing it.

# =========================================================
# FIGURE RULES
# =========================================================
# State the figure label and type, then walk through every element: boxes, nodes, arrows in flow order
# (A -> B -> C), axes, legends, values, trends, and what the figure demonstrates. If the user asked for a
# specific figure number and it is not in the context, say so plainly.

# =========================================================
# EQUATION RULES
# =========================================================
# Always write mathematics in LaTeX: $inline$ and $$display$$ on its own line. Never as plain text.
# Define every symbol, and show related equations that appear in the context.

# =========================================================
# ALGORITHM RULES
# =========================================================
# Give the steps as a numbered list faithful to the source, then explain inputs, outputs and each step.

# =========================================================
# CONVERSATION HISTORY
# =========================================================
# {conversation_history}

# =========================================================
# DOCUMENT CONTEXT
# =========================================================
# {context}

# =========================================================
# RETRIEVAL NOTES
# =========================================================
# {notes}

# =========================================================
# QUESTION
# =========================================================
# {question}

# Answer now, using only the document context, citing with [S#] tags.""",
# )


# # ─────────────────────────────────────────────────────────────────────────────
# # PDF OPENING ON THE HOST (optional)
# # ─────────────────────────────────────────────────────────────────────────────
# def open_pdf_at_page(local_path: str, page_number: int):
#     if not local_path or not os.path.exists(local_path):
#         print(f"  [PDF not found]: {local_path}")
#         return
#     url = f"file:///{local_path.replace(os.sep, '/')}#page={max(1, int(page_number))}"
#     try:
#         subprocess.Popen([C.BROWSER_PATH, url])
#     except Exception as e:
#         print(f"  [Could not open PDF]: {e}")


# # ─────────────────────────────────────────────────────────────────────────────
# # LOCAL PIPELINE (streaming)
# # ─────────────────────────────────────────────────────────────────────────────
# def status(label: str, state: str = "running") -> Dict:
#     return {"__status__": True, "label": label, "state": state}


# def local_stream(question: str, tone: str, detail_level: str, audience: str,
#                  answer_format: str, language: str, citation_style: str,
#                  session_id: str = "default",
#                  selected_sources: Optional[List[str]] = None,
#                  store: bool = True) -> Iterator:
#     """Yields status dicts, answer tokens, then {'__final__': {...}}."""
#     index = get_index()
#     if not index.ids:
#         message = ("No documents are indexed yet. Put files in the documents folder "
#                    "and run `python ingest.py`.")
#         yield status("⚠️ Empty index", "error")
#         yield message
#         yield {"__status__": True, "__final__": {"answer": message, "sources": []}}
#         return

#     yield status("📝 Understanding the question...")
#     analysis = analyze_question(question, session_id, selected_sources)
#     if analysis["standalone"] != question:
#         yield status(f"🔍 Interpreted as: *{analysis['standalone'][:140]}*")
#     if analysis["labels"]:
#         yield status("🏷️ Looking for: " + ", ".join(pretty_label(l) for l in analysis["labels"]))

#     yield status("🧮 Vector + keyword search over your documents...")
#     documents, notes = retrieve_documents(analysis)
#     counts = defaultdict(int)
#     for document in documents:
#         counts[document["metadata"].get("content_type", "text")] += 1
#     yield status("📄 " + " · ".join(f"{value} {key}" for key, value in counts.items()))

#     context, source_map = build_context(documents)
#     prompt = _RAG_PROMPT.format(
#         tone=tone, detail_level=detail_level, audience=audience,
#         answer_format=answer_format, language=language,
#         conversation_history=analysis["history"], context=context,
#         notes="\n".join(notes) if notes else "none", question=question,
#     )

#     yield status("⏳ Generating answer...", "generating")
#     citations = CitationFormatter(source_map, citation_style)
#     parts, first = [], True
#     try:
#         for token in stream_tokens([{"role": "user", "content": prompt}]):
#             visible = citations.feed(token)
#             if not visible:
#                 continue
#             if first:
#                 yield status("💬 Streaming answer...", "complete")
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
#     sources = [] if answer.lower().startswith("i could not find") else citations.cited_sources()
#     if store:
#         add_message("user", question, session_id=session_id)
#         add_message("assistant", answer, session_id=session_id, meta={"sources": sources,
#                                                                      "mode": "local"})
#     if C.AUTO_OPEN_PDF_ON_HOST and sources:
#         open_pdf_at_page(sources[0]["local_path"], sources[0]["page"])

#     yield {"__status__": True, "__pdf_info__": sources}
#     yield {"__status__": True, "__final__": {"answer": answer, "sources": sources}}


# # Backwards-compatible name used by the older Streamlit app
# def ask_streaming(question, tone, detail_level, audience, answer_format, language,
#                   citation_style, session_id: str = "default",
#                   selected_sources: Optional[List[str]] = None) -> Iterator:
#     yield from local_stream(question, tone, detail_level, audience, answer_format,
#                             language, citation_style, session_id, selected_sources)


# def ask(question, tone="Professional", detail_level="Moderate", audience="Student",
#         answer_format="Structured explanation", language="English",
#         citation_style="File and page", session_id="default",
#         selected_sources=None, echo=False) -> Dict:
#     final = {"answer": "", "sources": []}
#     for item in local_stream(question, tone, detail_level, audience, answer_format,
#                              language, citation_style, session_id, selected_sources):
#         if isinstance(item, dict):
#             if item.get("__final__"):
#                 final = item["__final__"]
#         elif echo:
#             print(item, end="", flush=True)
#     return final


# # ─────────────────────────────────────────────────────────────────────────────
# # CLI
# # ─────────────────────────────────────────────────────────────────────────────
# def main():
#     import uuid
#     session_id = str(uuid.uuid4())
#     print("=" * 70 + "\n              LOCAL DOCUMENT RAG\n" + "=" * 70)
#     warmup()
#     print("Commands: exit | memory | clear | docs\n")
#     try:
#         while True:
#             question = input("\nYou: ").strip()
#             if not question:
#                 continue
#             if question.lower() in ("exit", "quit"):
#                 break
#             if question.lower() == "memory":
#                 show_memory(session_id=session_id)
#                 continue
#             if question.lower() == "clear":
#                 clear_memory(session_id=session_id)
#                 print("Session memory cleared.")
#                 continue
#             if question.lower() == "docs":
#                 for document in list_documents():
#                     print(f"  - {document['source']}  {document['title'][:60]}")
#                 continue
#             print("\nAssistant:\n", end="")
#             result = ask(question, session_id=session_id, echo=True)
#             print()
#             for source in result["sources"]:
#                 extra = f" ({source['label']})" if source.get("label") else ""
#                 print(f"  📄 {source['source']} — page {source['page']}{extra}")
#     finally:
#         clear_memory(session_id=session_id)


# if __name__ == "__main__":
#     main()



##############################################  22nd Sept Update ########################################## 

"""
rag.py  —  LOCAL RAG over your ingested documents

Retrieval = exact label pins ("pull Figure 4.2")  +  vector search  +  BM25 keyword
search (pure python, nothing is downloaded)  →  rank fusion  →  neighbour expansion
→  token-budgeted context with [S1], [S2] ... source ids.

The model cites [S3]; the code turns that into the exact file and page from the
chunk's metadata, so page numbers are never invented. Figures carry the path of
their cropped image, so the UI can show the actual figure.
"""

import os
import re
import math
import subprocess
import threading
from collections import defaultdict
from typing import Iterator, List, Dict, Optional, Tuple

import numpy as np
from langchain_core.prompts import PromptTemplate

import config as C
from rag_common import (
    get_collection, get_embedding, stream_tokens, chat_text, count_tokens,
    find_labels, caption_label, pretty_label, pdf_url, pdf_path, render_pdf_page,
)
from session_memory import (
    initialize_memory, add_message, format_history, clear_memory, show_memory,
    compact_in_background,
)

initialize_memory()


# ─────────────────────────────────────────────────────────────────────────────
# TOKENIZER + BM25  (in-memory, no external packages)
# ─────────────────────────────────────────────────────────────────────────────
STOPWORDS = set("""a an the of and or in on to for with by is are was were be been this that these
those it its as at from which what how does do did explain describe show give me tell about please
can could would should you your i we our their there than then into over under between also any all
each other such not no yes if so may might will shall has have had get got using used""".split())


def tokenize(text: str) -> List[str]:
    tokens = []
    for token in re.findall(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*|\d+(?:\.\d+)*", (text or "").lower()):
        if token in STOPWORDS or (len(token) == 1 and not token.isdigit()):
            continue
        tokens.append(token)
        if "-" in token:
            tokens.extend(part for part in token.split("-") if part and part not in STOPWORDS)
    tokens.extend(label.replace(" ", "_") for label in find_labels(text))
    return tokens


class BM25:
    def __init__(self, documents_tokens: List[List[str]], k1: float = 1.4, b: float = 0.75):
        self.n = len(documents_tokens)
        self.k1, self.b = k1, b
        self.doc_len = np.array([len(t) for t in documents_tokens], dtype=np.float32)
        self.avgdl = float(self.doc_len.mean()) if self.n else 1.0
        postings = defaultdict(lambda: defaultdict(int))
        for index, tokens in enumerate(documents_tokens):
            for token in tokens:
                postings[token][index] += 1
        self.postings, self.idf = {}, {}
        for term, entries in postings.items():
            ids = np.fromiter(entries.keys(), dtype=np.int32, count=len(entries))
            tfs = np.fromiter(entries.values(), dtype=np.float32, count=len(entries))
            self.postings[term] = (ids, tfs)
            self.idf[term] = math.log(1 + (self.n - len(entries) + 0.5) / (len(entries) + 0.5))

    def search(self, query_tokens: List[str], k: int,
               mask: Optional[np.ndarray] = None) -> List[int]:
        if not self.n:
            return []
        scores = np.zeros(self.n, dtype=np.float32)
        for term in set(query_tokens):
            if term not in self.postings:
                continue
            ids, tfs = self.postings[term]
            denominator = tfs + self.k1 * (1 - self.b + self.b * self.doc_len[ids] / self.avgdl)
            scores[ids] += self.idf[term] * tfs * (self.k1 + 1) / denominator
        if mask is not None:
            scores[~mask] = 0
        k = min(k, self.n)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [int(i) for i in top if scores[i] > 0]


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT INDEX
# ─────────────────────────────────────────────────────────────────────────────
class DocumentIndex:
    def __init__(self):
        collection = get_collection()
        ids, documents, metadatas = [], [], []
        offset, page_size = 0, 5000
        while True:
            batch = collection.get(include=["documents", "metadatas"],
                                   limit=page_size, offset=offset)
            batch_ids = batch.get("ids") or []
            if not batch_ids:
                break
            ids += batch_ids
            documents += batch["documents"]
            metadatas += batch["metadatas"]
            offset += len(batch_ids)
            if len(batch_ids) < page_size:
                break

        self.ids = ids
        self.docs = documents
        self.metas = [m or {} for m in metadatas]
        self.position = {chunk_id: i for i, chunk_id in enumerate(ids)}
        self.bm25 = BM25([tokenize(text) for text in documents])

        self.sources = sorted({m.get("source", "") for m in self.metas if m.get("source")})
        self.source_code = {source: index for index, source in enumerate(self.sources)}
        self.source_array = np.array([self.source_code.get(m.get("source", ""), -1)
                                      for m in self.metas], dtype=np.int32)

        self.label_map = defaultdict(list)     # "figure 4.2" → element chunks
        self.caption_map = defaultdict(list)   # "figure 4.2" → text chunks with that caption
        self.chunk_map = {}                    # (source, page, chunk) → position
        self.titles = {}
        self.elements = defaultdict(list)

        for index, meta in enumerate(self.metas):
            source = meta.get("source", "")
            content_type = meta.get("content_type", "text")
            label = meta.get("element_label", "")
            if label:
                self.label_map[label].append(index)
                self.elements[source].append(index)
            if content_type == "document_card":
                self.titles[source] = meta.get("title", "")
            if content_type == "text":
                self.chunk_map[(source, meta.get("page"), meta.get("chunk"))] = index
                for line in documents[index].split("\n"):
                    found = caption_label(line)
                    if found:
                        self.caption_map[found].append(index)
        print(f"[Index] {len(ids)} chunks from {len(self.sources)} document(s) loaded.")

    def mask_for(self, scope: Optional[List[str]]) -> Optional[np.ndarray]:
        if not scope:
            return None
        codes = [self.source_code[s] for s in scope if s in self.source_code]
        return np.isin(self.source_array, codes)


_index_lock = threading.Lock()
_index = {"value": None}


def get_index() -> DocumentIndex:
    with _index_lock:
        if _index["value"] is None:
            _index["value"] = DocumentIndex()
        return _index["value"]


def reload_index():
    with _index_lock:
        _index["value"] = None
    return get_index()


def list_documents() -> List[Dict]:
    index = get_index()
    return [{"source": source, "title": index.titles.get(source, "")} for source in index.sources]


def warmup():
    get_index()


# ─────────────────────────────────────────────────────────────────────────────
# QUESTION ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
DETAILED_KEYWORDS = [
    "in detail", "detailed", "step by step", "step-by-step", "in depth", "thoroughly",
    "elaborate", "how does", "how do", "algorithm", "methodology", "proposed method",
    "proposed algorithm", "architecture", "derivation", "compare", "comparison",
    "which paper", "all papers", "list all",
]
TYPE_WORDS = {
    "figure_description": re.compile(r"\b(fig\w*|diagram|flow ?chart|chart|graph|plot|image|picture|"
                                     r"illustration|visuali[sz]ation|architecture|block diagram|"
                                     r"photo\w*|show me|pull|display|draw)\b", re.I),
    "table": re.compile(r"\b(tab(le)?s?|tabular|rows?|columns?|cell)\b", re.I),
    "equation": re.compile(r"\b(equations?|formula\w*|math\w*|derive|derivation|loss function|"
                           r"objective function|symbols?|notation|latex|eq)\b", re.I),
    "algorithm": re.compile(r"\b(algorithm\w*|pseudo-?code|procedure|steps of)\b", re.I),
}

_REWRITE_PROMPT = PromptTemplate(
    input_variables=["history", "question"],
    template="""You rewrite the user's latest message into ONE standalone question for document search.

Resolve pronouns (it, this, that figure, the previous table) using the conversation.
Keep labels exactly as written (Figure 4.2, Table II, Eq. (5), Algorithm 1) and keep any
document/file name the user is referring to. Do NOT answer. If it is already standalone,
return it unchanged.

CONVERSATION HISTORY:
{history}

CURRENT QUESTION:
{question}

Return ONLY the standalone question.""",
)


def create_standalone_question(question: str, session_id: str = "default") -> str:
    # summary of this chat + its most recent messages (enough to resolve "it", "that figure")
    history = format_history(session_id=session_id, token_budget=C.CHAT_REWRITE_HISTORY_TOKENS)
    if history == "No previous conversation.":
        return question
    try:
        rewritten = chat_text(
            [{"role": "user", "content": _REWRITE_PROMPT.format(history=history, question=question)}],
            max_tokens=300, temperature=0.0).strip().strip('"')
        return rewritten if 3 <= len(rewritten) <= 600 else question
    except Exception as e:
        print(f"  [Rewrite failed]: {e}")
        return question


def detect_sources(question: str) -> List[str]:
    """Documents named in the question ('from the radar paper', 'in report2.pdf')."""
    index = get_index()
    lowered = question.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    found = []
    for source in index.sources:
        stem = os.path.splitext(source)[0].lower()
        if source.lower() in lowered or (len(stem) >= 4 and stem in lowered):
            found.append(source)
            continue
        name_words = {w for w in re.findall(r"[a-z0-9]+", stem)
                      if len(w) > 3 and w not in STOPWORDS}
        if len(name_words) >= 2 and len(name_words & words) >= max(2, math.ceil(0.6 * len(name_words))):
            found.append(source)
            continue
        title_words = {w for w in re.findall(r"[a-z0-9]+", index.titles.get(source, "").lower())
                       if len(w) > 3 and w not in STOPWORDS}
        if len(title_words) >= 3 and len(title_words & words) >= max(3, math.ceil(0.6 * len(title_words))):
            found.append(source)
    return found


def analyze_question(question: str, session_id: str,
                     selected_sources: Optional[List[str]] = None) -> Dict:
    standalone = create_standalone_question(question, session_id)
    lowered = standalone.lower()
    labels = find_labels(standalone)
    types = {ctype for ctype, regex in TYPE_WORDS.items() if regex.search(standalone)}
    for label in labels:
        kind = label.split()[0]
        types.add({"figure": "figure_description", "table": "table",
                   "equation": "equation", "algorithm": "algorithm"}.get(kind, "text"))
    mentioned = detect_sources(standalone)
    if selected_sources:
        scope = [s for s in mentioned if s in selected_sources] or list(selected_sources)
    else:
        scope = mentioned
    return {
        "question": question,
        "standalone": standalone,
        "history": format_history(session_id=session_id),     # the whole chat
        "labels": labels,
        "types": types,
        "scope": scope,
        "detailed": any(keyword in lowered for keyword in DETAILED_KEYWORDS),
    }


# ─────────────────────────────────────────────────────────────────────────────
# RETRIEVAL
# ─────────────────────────────────────────────────────────────────────────────
def _where(scope: Optional[List[str]], content_type: Optional[str] = None):
    conditions = []
    if scope:
        conditions.append({"source": {"$in": list(scope)}})
    if content_type:
        conditions.append({"content_type": content_type})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _vector_search(embedding, k: int, where) -> List[int]:
    index = get_index()
    try:
        results = get_collection().query(query_embeddings=[embedding], n_results=k,
                                         where=where, include=["metadatas"])
    except Exception as e:
        print(f"  [Vector search failed]: {e}")
        return []
    positions = []
    for chunk_id in (results.get("ids") or [[]])[0]:
        if chunk_id in index.position:
            positions.append(index.position[chunk_id])
    return positions


def retrieve_documents(analysis: Dict) -> Tuple[List[Dict], List[str]]:
    index = get_index()
    scope = analysis["scope"] or None
    notes = []
    if scope:
        notes.append("Search restricted to: " + ", ".join(scope))

    # 1. exact label matches — "pull Figure 4.2"
    pinned: List[int] = []
    for label in analysis["labels"]:
        elements = [i for i in index.label_map.get(label, [])
                    if not scope or index.metas[i].get("source") in scope]
        captions = [i for i in index.caption_map.get(label, [])
                    if not scope or index.metas[i].get("source") in scope]
        pinned += elements[: C.MAX_PINNED_PER_LABEL] + captions[:3]
        if not elements and not captions:
            notes.append(f"No element labelled '{pretty_label(label)}' exists in the index"
                         + (f" for {', '.join(scope)}" if scope else "") + ".")

    # 2. hybrid search
    embedding = get_embedding(analysis["standalone"], is_query=True)
    ranked = [(_vector_search(embedding, C.VECTOR_K, _where(scope)), 1.0)]
    ranked.append((index.bm25.search(tokenize(analysis["standalone"]), C.BM25_K,
                                     index.mask_for(scope)), 1.0))
    for content_type in analysis["types"]:
        ranked.append((_vector_search(embedding, 8, _where(scope, content_type)), 0.8))

    fused = defaultdict(float)
    for positions, weight in ranked:
        for rank, position in enumerate(positions):
            factor = weight
            if index.metas[position].get("content_type") == "document_card":
                factor *= 0.6
            fused[position] += factor / (C.RRF_K + rank + 1)

    pinned_set = set(pinned)
    candidates = [i for i, _ in sorted(fused.items(), key=lambda kv: -kv[1]) if i not in pinned_set]
    top_k = C.DETAILED_TOP_K if analysis["detailed"] else C.TOP_K
    selected = candidates[:max(3, top_k - min(len(pinned), top_k // 2))]

    # 3. neighbouring text chunks around the best hits
    ordered: List[int] = []
    for rank, position in enumerate(pinned + selected):
        meta = index.metas[position]
        group = [position]
        if rank >= len(pinned) and rank - len(pinned) < 4 and meta.get("content_type") == "text":
            chunk = meta.get("chunk", -1)
            if isinstance(chunk, int) and chunk >= 0:
                for offset in range(1, C.NEIGHBOR_CHUNKS + 1):
                    before = index.chunk_map.get((meta.get("source"), meta.get("page"), chunk - offset))
                    after = index.chunk_map.get((meta.get("source"), meta.get("page"), chunk + offset))
                    if before is not None:
                        group.insert(0, before)
                    if after is not None:
                        group.append(after)
        # a text chunk that says "[[TABLE: Table 3 …]]" pulls that table in
        if meta.get("content_type") == "text":
            for tag in re.findall(r"\[\[TABLE: (.+?) —", index.docs[position]):
                for label in find_labels(tag):
                    group += [j for j in index.label_map.get(label, [])
                              if index.metas[j].get("source") == meta.get("source")][:2]
        ordered.extend(group)

    documents, seen = [], set()
    for position in ordered:
        if position in seen:
            continue
        seen.add(position)
        documents.append({"text": index.docs[position], "metadata": index.metas[position]})
        if len(documents) >= C.MAX_CONTEXT_CHUNKS:
            break
    return documents, notes


# ─────────────────────────────────────────────────────────────────────────────
# CONTEXT + CITATIONS
# ─────────────────────────────────────────────────────────────────────────────
TYPE_LABELS = {
    "figure_description": "FIGURE DESCRIPTION",
    "table": "TABLE DATA",
    "equation": "MATHEMATICAL CONTENT",
    "algorithm": "ALGORITHM",
    "document_card": "DOCUMENT CARD",
    "text": "TEXT",
}


def build_context(documents: List[Dict]) -> Tuple[str, Dict[int, Dict]]:
    parts, source_map, used = [], {}, 0
    for document in documents:
        meta = document["metadata"]
        number = len(source_map) + 1
        label = pretty_label(meta.get("element_label", ""))
        head = (f"[S{number}] file: {meta.get('source')} | page: {meta.get('page', 1)} | "
                f"type: {TYPE_LABELS.get(meta.get('content_type', 'text'), 'TEXT')}"
                + (f" ({label})" if label else ""))
        block = f"{head}\n{document['text']}"
        cost = count_tokens(block)
        if used + cost > C.MAX_CONTEXT_TOKENS:
            continue
        used += cost
        parts.append(block)
        source_map[number] = {
            "source": meta.get("source", ""),
            "page": int(meta.get("page", 1) or 1),
            "label": label,
            "content_type": meta.get("content_type", "text"),
            "image_path": meta.get("image_path", ""),
            "caption": meta.get("caption", ""),
            "local_path": pdf_path(meta.get("source", "")),
        }
    return ("\n\n".join(parts) if parts else "(nothing relevant found)"), source_map


CITE_RE = re.compile(r"\[\s*S\d+(?:\s*[,;]\s*S?\d+)*\s*\]")


class CitationFormatter:
    """Turns [S3] in the stream into '(file.pdf, p. 12)' using the chunk metadata."""

    def __init__(self, source_map: Dict[int, Dict], citation_style: str):
        self.map = source_map
        self.style = citation_style
        self.buf = ""
        self.last_char = ""
        self.cited: List[int] = []

    def _convert(self, tag: str) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", tag) if int(n) in self.map]
        for number in numbers:
            if number not in self.cited:
                self.cited.append(number)
        if not numbers or self.style == "No citation":
            return ""
        by_file = {}
        for number in numbers:
            entry = self.map[number]
            pages = by_file.setdefault(entry["source"], [])
            page = int(entry.get("page", 0) or 0)
            if page > 0 and page not in pages:
                pages.append(page)
        if self.style == "File only":
            return "(" + "; ".join(by_file) + ")"
        rendered = []
        for name, pages in by_file.items():
            # web / arXiv sources have no page number — only the title is shown
            rendered.append(f"{name}, p. {', '.join(map(str, sorted(pages)))}" if pages else name)
        return "(" + "; ".join(rendered) + ")"

    def feed(self, token: str) -> str:
        text = self._feed(token)
        if text:
            self.last_char = text[-1]
        return text

    def _feed(self, token: str) -> str:
        self.buf += token
        out = []
        while self.buf:
            start = self.buf.find("[")
            if start == -1:
                out.append(self.buf)
                self.buf = ""
                break
            out.append(self.buf[:start])
            self.buf = self.buf[start:]
            end = self.buf.find("]")
            if end == -1:
                if len(self.buf) > 40:
                    out.append(self.buf[0])
                    self.buf = self.buf[1:]
                    continue
                break
            candidate = self.buf[:end + 1]
            if CITE_RE.fullmatch(candidate):
                converted = self._convert(candidate)
                previous = "".join(out)[-1:] or self.last_char
                if converted and previous and not previous.isspace() and previous not in "([":
                    converted = " " + converted
                out.append(converted)
                self.buf = self.buf[end + 1:]
            else:
                out.append("[")
                self.buf = self.buf[1:]
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = self.buf, ""
        if rest:
            self.last_char = rest[-1]
        return rest

    def cited_sources(self, fallback: int = 3) -> List[Dict]:
        order = self.cited or list(self.map.keys())[:fallback]
        merged, result = {}, []
        for number in order:
            entry = self.map[number]
            key = (entry["source"], entry["page"], entry["label"])
            if key in merged:
                continue
            merged[key] = True
            item = dict(entry)
            item["explicitly_cited"] = bool(self.cited)
            result.append(item)
        return result


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT
# ─────────────────────────────────────────────────────────────────────────────
_RAG_PROMPT = PromptTemplate(
    input_variables=["tone", "detail_level", "audience", "answer_format", "language",
                     "conversation_history", "context", "notes", "question"],
    template="""You are an expert technical research assistant working with the user's own documents.

The DOCUMENT CONTEXT below contains numbered sources [S1], [S2] ... Each states its file, page and type:
TEXT, TABLE DATA (Markdown), FIGURE DESCRIPTION (a vision model's reading of the cropped figure),
MATHEMATICAL CONTENT, ALGORITHM, or DOCUMENT CARD.

=========================================================
USER PREFERENCES
=========================================================
Tone: {tone} | Detail level: {detail_level} | Audience: {audience}
Answer format: {answer_format} | Language: {language}

=========================================================
GROUNDING AND CITATION RULES
=========================================================
1. Use ONLY the document context. The conversation history is only for understanding what the user means.
2. End every factual sentence with the source tags that support it, e.g. [S3] or [S2][S5].
   NEVER write file names or page numbers yourself — the tags are replaced with the exact file and page.
3. Never invent numbers, values, equations, steps, labels or references. Copy numbers exactly.
4. If the context answers only part of the question, answer that part, say what is missing and ask ONE
   specific clarifying question (which document, which figure/table number, which section).
5. If nothing relevant is present, say "I could not find this in your documents." and ask a short
   clarifying question.
6. The same label in different files refers to different items — keep them separate and name each file.

=========================================================
TABLE RULES
=========================================================
Reproduce the relevant rows and columns as a Markdown table with the exact values, then explain what it
shows. Never summarise a table without showing it.

=========================================================
FIGURE RULES
=========================================================
State the figure label and type, then walk through every element: boxes, nodes, arrows in flow order
(A -> B -> C), axes, legends, values, trends, and what the figure demonstrates. If the user asked for a
specific figure number and it is not in the context, say so plainly.

=========================================================
EQUATION RULES
=========================================================
Always write mathematics in LaTeX: $inline$ and $$display$$ on its own line. Never as plain text.
Define every symbol, and show related equations that appear in the context.

=========================================================
ALGORITHM RULES
=========================================================
Give the steps as a numbered list faithful to the source, then explain inputs, outputs and each step.

=========================================================
CONVERSATION HISTORY
=========================================================
{conversation_history}

=========================================================
DOCUMENT CONTEXT
=========================================================
{context}

=========================================================
RETRIEVAL NOTES
=========================================================
{notes}

=========================================================
QUESTION
=========================================================
{question}

Answer now, using only the document context, citing with [S#] tags.""",
)


# ─────────────────────────────────────────────────────────────────────────────
# PDF OPENING ON THE HOST (optional)
# ─────────────────────────────────────────────────────────────────────────────
def open_pdf_at_page(local_path: str, page_number: int):
    if not local_path or not os.path.exists(local_path):
        print(f"  [PDF not found]: {local_path}")
        return
    url = f"file:///{local_path.replace(os.sep, '/')}#page={max(1, int(page_number))}"
    try:
        subprocess.Popen([C.BROWSER_PATH, url])
    except Exception as e:
        print(f"  [Could not open PDF]: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# LOCAL PIPELINE (streaming)
# ─────────────────────────────────────────────────────────────────────────────
def status(label: str, state: str = "running") -> Dict:
    return {"__status__": True, "label": label, "state": state}


def local_stream(question: str, tone: str, detail_level: str, audience: str,
                 answer_format: str, language: str, citation_style: str,
                 session_id: str = "default",
                 selected_sources: Optional[List[str]] = None,
                 store: bool = True) -> Iterator:
    """Yields status dicts, answer tokens, then {'__final__': {...}}."""
    index = get_index()
    if not index.ids:
        message = ("No documents are indexed yet. Put files in the documents folder "
                   "and run `python ingest.py`.")
        yield status("⚠️ Empty index", "error")
        yield message
        yield {"__status__": True, "__final__": {"answer": message, "sources": []}}
        return

    yield status("📝 Understanding the question...")
    analysis = analyze_question(question, session_id, selected_sources)
    if analysis["standalone"] != question:
        yield status(f"🔍 Interpreted as: *{analysis['standalone'][:140]}*")
    if analysis["labels"]:
        yield status("🏷️ Looking for: " + ", ".join(pretty_label(l) for l in analysis["labels"]))

    yield status("🧮 Vector + keyword search over your documents...")
    documents, notes = retrieve_documents(analysis)
    counts = defaultdict(int)
    for document in documents:
        counts[document["metadata"].get("content_type", "text")] += 1
    yield status("📄 " + " · ".join(f"{value} {key}" for key, value in counts.items()))

    context, source_map = build_context(documents)
    prompt = _RAG_PROMPT.format(
        tone=tone, detail_level=detail_level, audience=audience,
        answer_format=answer_format, language=language,
        conversation_history=analysis["history"], context=context,
        notes="\n".join(notes) if notes else "none", question=question,
    )

    yield status("⏳ Generating answer...", "generating")
    citations = CitationFormatter(source_map, citation_style)
    parts, first = [], True
    try:
        for token in stream_tokens([{"role": "user", "content": prompt}]):
            visible = citations.feed(token)
            if not visible:
                continue
            if first:
                yield status("💬 Streaming answer...", "complete")
                first = False
            parts.append(visible)
            yield visible
        tail = citations.flush()
        if tail:
            parts.append(tail)
            yield tail
    except Exception as e:
        error = f"\n\n⚠️ The language model failed: {e}"
        parts.append(error)
        yield error

    answer = "".join(parts).strip()
    sources = [] if answer.lower().startswith("i could not find") else citations.cited_sources()
    if store:
        add_message("user", question, session_id=session_id)
        add_message("assistant", answer, session_id=session_id, meta={"sources": sources,
                                                                     "mode": "local"})
        compact_in_background(session_id)
    if C.AUTO_OPEN_PDF_ON_HOST and sources:
        open_pdf_at_page(sources[0]["local_path"], sources[0]["page"])

    yield {"__status__": True, "__pdf_info__": sources}
    yield {"__status__": True, "__final__": {"answer": answer, "sources": sources}}


# Backwards-compatible name used by the older Streamlit app
def ask_streaming(question, tone, detail_level, audience, answer_format, language,
                  citation_style, session_id: str = "default",
                  selected_sources: Optional[List[str]] = None) -> Iterator:
    yield from local_stream(question, tone, detail_level, audience, answer_format,
                            language, citation_style, session_id, selected_sources)


def ask(question, tone="Professional", detail_level="Moderate", audience="Student",
        answer_format="Structured explanation", language="English",
        citation_style="File and page", session_id="default",
        selected_sources=None, echo=False) -> Dict:
    final = {"answer": "", "sources": []}
    for item in local_stream(question, tone, detail_level, audience, answer_format,
                             language, citation_style, session_id, selected_sources):
        if isinstance(item, dict):
            if item.get("__final__"):
                final = item["__final__"]
        elif echo:
            print(item, end="", flush=True)
    return final


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    import uuid
    session_id = str(uuid.uuid4())
    print("=" * 70 + "\n              LOCAL DOCUMENT RAG\n" + "=" * 70)
    warmup()
    print("Commands: exit | memory | clear | docs\n")
    try:
        while True:
            question = input("\nYou: ").strip()
            if not question:
                continue
            if question.lower() in ("exit", "quit"):
                break
            if question.lower() == "memory":
                show_memory(session_id=session_id)
                continue
            if question.lower() == "clear":
                clear_memory(session_id=session_id)
                print("Session memory cleared.")
                continue
            if question.lower() == "docs":
                for document in list_documents():
                    print(f"  - {document['source']}  {document['title'][:60]}")
                continue
            print("\nAssistant:\n", end="")
            result = ask(question, session_id=session_id, echo=True)
            print()
            for source in result["sources"]:
                extra = f" ({source['label']})" if source.get("label") else ""
                print(f"  📄 {source['source']} — page {source['page']}{extra}")
    finally:
        clear_memory(session_id=session_id)


if __name__ == "__main__":
    main()