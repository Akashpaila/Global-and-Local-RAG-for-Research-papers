# """
# rag_common.py  —  shared helpers

#   • LM Studio client (chat, vision, embeddings with nomic prefixes)
#   • Caption / label detection:  "Figure 4.2", "Fig. 3(a)", "TABLE II",
#     "Eq. (5)", "Algorithm 1"  →  normalised labels like "figure 4.2"
#   • Token estimation, ChromaDB access, PDF links and page rendering
# """

# import os
# import re
# import functools
# import threading
# import urllib.parse
# from typing import List, Optional, Dict, Tuple

# from openai import OpenAI

# import config as C


# # ─────────────────────────────────────────────────────────────────────────────
# # LM STUDIO
# # ─────────────────────────────────────────────────────────────────────────────
# @functools.lru_cache(maxsize=1)
# def get_client() -> OpenAI:
#     return OpenAI(base_url=C.LM_STUDIO_BASE_URL, api_key=C.LM_STUDIO_API_KEY,
#                   timeout=C.LLM_TIMEOUT_SECONDS, max_retries=1)


# _reasoning_ok = {"value": True}


# def _extra_body() -> Optional[Dict]:
#     if C.REASONING_EFFORT and _reasoning_ok["value"]:
#         return {"reasoning_effort": C.REASONING_EFFORT}
#     return None


# def _is_param_error(exc: Exception) -> bool:
#     msg = str(exc).lower()
#     return "reasoning" in msg or "unrecognized" in msg or "extra_body" in msg


# def chat(messages, max_tokens=2000, temperature=0.1, stream=False):
#     """Chat completion against LM Studio, with reasoning_effort fallback."""
#     client = get_client()
#     kwargs = dict(model=C.LLM_MODEL, messages=messages,
#                   max_tokens=max_tokens, temperature=temperature, stream=stream)
#     try:
#         return client.chat.completions.create(extra_body=_extra_body(), **kwargs)
#     except Exception as e:
#         if _is_param_error(e):
#             _reasoning_ok["value"] = False
#             return client.chat.completions.create(**kwargs)
#         raise


# def chat_text(messages, max_tokens=2000, temperature=0.1) -> str:
#     response = chat(messages, max_tokens=max_tokens, temperature=temperature)
#     return strip_reasoning(response.choices[0].message.content or "")


# def stream_tokens(messages, max_tokens=None, temperature=None):
#     """Yields visible answer tokens, with <think>...</think> removed."""
#     response = chat(messages,
#                     max_tokens=max_tokens or C.MAX_ANSWER_TOKENS,
#                     temperature=C.TEMPERATURE if temperature is None else temperature,
#                     stream=True)
#     think = ThinkFilter()
#     for chunk in response:
#         if not chunk.choices:
#             continue
#         token = getattr(chunk.choices[0].delta, "content", None)
#         if token:
#             visible = think.feed(token)
#             if visible:
#                 yield visible
#     tail = think.flush()
#     if tail:
#         yield tail


# # ─────────────────────────────────────────────────────────────────────────────
# # REASONING TAGS
# # ─────────────────────────────────────────────────────────────────────────────
# _THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


# def strip_reasoning(text: str) -> str:
#     text = _THINK_RE.sub("", text or "")
#     low = text.lower()
#     if "</think>" in low:
#         text = text[low.rindex("</think>") + 8:]
#     elif "<think>" in low:
#         text = text[: low.index("<think>")]
#     return text.strip()


# class ThinkFilter:
#     def __init__(self):
#         self.buf = ""
#         self.in_think = False

#     def feed(self, token: str) -> str:
#         self.buf += token
#         out = []
#         while True:
#             if self.in_think:
#                 idx = self.buf.lower().find("</think>")
#                 if idx == -1:
#                     self.buf = self.buf[-8:]
#                     break
#                 self.buf = self.buf[idx + 8:]
#                 self.in_think = False
#             else:
#                 idx = self.buf.lower().find("<think>")
#                 if idx == -1:
#                     safe = len(self.buf) - 7
#                     if safe > 0:
#                         out.append(self.buf[:safe])
#                         self.buf = self.buf[safe:]
#                     break
#                 out.append(self.buf[:idx])
#                 self.buf = self.buf[idx + 7:]
#                 self.in_think = True
#         return "".join(out)

#     def flush(self) -> str:
#         rest = "" if self.in_think else self.buf
#         self.buf = ""
#         return rest


# # ─────────────────────────────────────────────────────────────────────────────
# # TOKENS  (same word-based estimate as the previous version)
# # ─────────────────────────────────────────────────────────────────────────────
# def encode_text(text: str) -> List[str]:
#     return re.findall(r"\w+|[^\w\s]", text, re.UNICODE)


# def count_tokens(text: str) -> int:
#     return max(1, int(len(encode_text(text)) * 1.3))


# estimate_tokens = count_tokens


# # ─────────────────────────────────────────────────────────────────────────────
# # EMBEDDINGS  (nomic prefixes + batching)
# # ─────────────────────────────────────────────────────────────────────────────
# def embed_texts(texts: List[str], is_query: bool = False) -> List[List[float]]:
#     prefix = C.EMBED_QUERY_PREFIX if is_query else C.EMBED_DOC_PREFIX
#     vectors = []
#     batch_size = max(1, C.EMBEDDING_BATCH_SIZE)
#     for start in range(0, len(texts), batch_size):
#         batch = [prefix + (t or " ")[:8000] for t in texts[start:start + batch_size]]
#         try:
#             response = get_client().embeddings.create(model=C.EMBEDDING_MODEL, input=batch)
#             vectors.extend(d.embedding for d in sorted(response.data, key=lambda d: d.index))
#         except Exception as e:
#             print(f"    [Embedding batch failed, retrying one by one]: {e}")
#             for text in batch:
#                 response = get_client().embeddings.create(model=C.EMBEDDING_MODEL, input=[text])
#                 vectors.append(response.data[0].embedding)
#     return vectors


# def get_embedding(text: str, is_query: bool = False):
#     return embed_texts([text], is_query=is_query)[0]


# # ─────────────────────────────────────────────────────────────────────────────
# # CHROMA
# # ─────────────────────────────────────────────────────────────────────────────
# _chroma_lock = threading.Lock()
# _chroma = {"client": None}


# def get_chroma_client():
#     with _chroma_lock:
#         if _chroma["client"] is None:
#             import chromadb
#             os.makedirs(C.CHROMA_DIR, exist_ok=True)
#             _chroma["client"] = chromadb.PersistentClient(path=C.CHROMA_DIR)
#         return _chroma["client"]


# def get_collection(create: bool = True):
#     client = get_chroma_client()
#     try:
#         return client.get_collection(name=C.COLLECTION_NAME)
#     except Exception:
#         if not create:
#             raise
#         return client.create_collection(name=C.COLLECTION_NAME,
#                                         metadata={"hnsw:space": "cosine"})


# # ─────────────────────────────────────────────────────────────────────────────
# # LABELS:  "Figure 4.2", "Fig. 3(a)", "TABLE II", "Eq. (5)", "Algorithm 1"
# # ─────────────────────────────────────────────────────────────────────────────
# _KINDS = [
#     ("figure",    r"fig(?:ure)?s?\.?"),
#     ("table",     r"tab(?:le)?s?\.?"),
#     ("algorithm", r"alg(?:orithm)?s?\.?|procedure|pseudo-?code"),
#     ("equation",  r"eq(?:uation)?s?\.?|eqn\.?|formula"),
#     ("chart",     r"chart|graph|plot"),
#     ("theorem",   r"theorem|lemma|definition|proposition|corollary"),
# ]
# # numbers: 4, 4.2, 4.2.1, II, A1 — optional sub-letter (3a) and brackets
# _NUM = r"\(?\s*((?:[IVXLC]{1,6})|(?:[A-Z]?\d{1,3}(?:\.\d{1,3}){0,2}))\s*([a-z])?\s*\)?"

# _LABEL_RES = [(kind, re.compile(rf"(?<![A-Za-z])(?i:{pat})\s*{_NUM}(?![A-Za-z0-9])"))
#               for kind, pat in _KINDS]

# _CAPTION_RES = [(kind, re.compile(rf"^\W{{0,4}}(?i:{pat})\s*{_NUM}(?=\s*[:.\-—–|)]|\s+[A-Z(]|\s*$)"))
#                 for kind, pat in _KINDS if kind in ("figure", "table", "algorithm", "chart")]

# _ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


# def roman_to_int(text: str) -> Optional[int]:
#     if not text or any(ch not in _ROMAN for ch in text):
#         return None
#     total, prev = 0, 0
#     for ch in reversed(text):
#         value = _ROMAN[ch]
#         total = total - value if value < prev else total + value
#         prev = max(prev, value)
#     return total


# def normalize_number(number: str) -> str:
#     number = number.strip()
#     roman = roman_to_int(number)
#     return str(roman) if roman is not None else number


# def find_labels(text: str) -> List[str]:
#     """Every label mentioned in the text, normalised: ['figure 4.2', 'table 3']."""
#     found = []
#     if not text:
#         return found
#     for kind, regex in _LABEL_RES:
#         for match in regex.finditer(text):
#             kind_name = "figure" if kind == "chart" else kind
#             label = f"{kind_name} {normalize_number(match.group(1))}"
#             if label not in found:
#                 found.append(label)
#     return found


# def caption_label(line: str) -> Optional[str]:
#     """Label if the line STARTS like a caption ('Figure 4.2: ...', 'TABLE II')."""
#     clean = re.sub(r"[*_#`]", " ", line or "").strip()
#     for kind, regex in _CAPTION_RES:
#         match = regex.match(clean)
#         if match:
#             kind_name = "figure" if kind == "chart" else kind
#             return f"{kind_name} {normalize_number(match.group(1))}"
#     return None


# def pretty_label(label: str) -> str:
#     if not label:
#         return ""
#     kind, _, number = label.partition(" ")
#     return f"{kind.capitalize()} {number}"


# # ─────────────────────────────────────────────────────────────────────────────
# # PDF LINKS AND PAGE RENDERING
# # ─────────────────────────────────────────────────────────────────────────────
# def pdf_url(source: str, page: int, token: str = "") -> str:
#     quoted = urllib.parse.quote(source.replace("\\", "/"))
#     query = f"?token={urllib.parse.quote(token)}" if token else ""
#     return f"http://{C.PDF_SERVER_IP}:{C.PDF_SERVER_PORT}/{quoted}{query}#page={max(1, int(page))}"


# def pdf_path(source: str) -> str:
#     return os.path.join(C.DOCUMENTS_DIR, source.replace("/", os.sep))


# def render_pdf_page(source: str, page: int, highlight: str = "", zoom: float = 1.6):
#     """PNG bytes of one PDF page, optionally highlighting a label such as 'Figure 4.2'."""
#     import pymupdf as fitz
#     path = pdf_path(source)
#     if not os.path.exists(path):
#         return None
#     try:
#         with fitz.open(path) as document:
#             if page < 1 or page > document.page_count:
#                 return None
#             pdf_page = document[page - 1]
#             if highlight:
#                 for variant in (highlight, highlight.replace("Figure", "Fig."),
#                                 highlight.replace("Equation", "Eq."), highlight.upper()):
#                     rects = pdf_page.search_for(variant)
#                     if rects:
#                         for rect in rects[:3]:
#                             pdf_page.add_highlight_annot(rect)
#                         break
#             pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True)
#             return pixmap.tobytes("png")
#     except Exception as e:
#         print(f"  [Page render failed {source} p{page}]: {e}")
#         return None



################################# 21st Sept Update ############################ 

"""
rag_common.py  —  shared helpers

  • LM Studio client (chat, vision, embeddings with nomic prefixes)
  • Caption / label detection:  "Figure 4.2", "Fig. 3(a)", "TABLE II",
    "Eq. (5)", "Algorithm 1"  →  normalised labels like "figure 4.2"
  • Token estimation, ChromaDB access, PDF links and page rendering
"""

import os
import re
import functools
import threading
import urllib.parse
from typing import List, Optional, Dict, Tuple

from openai import OpenAI

import config as C


# ─────────────────────────────────────────────────────────────────────────────
# LM STUDIO
# ─────────────────────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=1)
def get_client() -> OpenAI:
    return OpenAI(base_url=C.LM_STUDIO_BASE_URL, api_key=C.LM_STUDIO_API_KEY,
                  timeout=C.LLM_TIMEOUT_SECONDS, max_retries=1)


_reasoning_ok = {"value": True}


def _extra_body() -> Optional[Dict]:
    if C.REASONING_EFFORT and _reasoning_ok["value"]:
        return {"reasoning_effort": C.REASONING_EFFORT}
    return None


def _is_param_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "reasoning" in msg or "unrecognized" in msg or "extra_body" in msg


def chat(messages, max_tokens=2000, temperature=0.1, stream=False):
    """Chat completion against LM Studio, with reasoning_effort fallback."""
    client = get_client()
    kwargs = dict(model=C.LLM_MODEL, messages=messages,
                  max_tokens=max_tokens, temperature=temperature, stream=stream)
    try:
        return client.chat.completions.create(extra_body=_extra_body(), **kwargs)
    except Exception as e:
        if _is_param_error(e):
            _reasoning_ok["value"] = False
            return client.chat.completions.create(**kwargs)
        raise


def chat_text(messages, max_tokens=2000, temperature=0.1) -> str:
    response = chat(messages, max_tokens=max_tokens, temperature=temperature)
    return strip_reasoning(response.choices[0].message.content or "")


def stream_tokens(messages, max_tokens=None, temperature=None):
    """Yields visible answer tokens, with <think>...</think> removed."""
    response = chat(messages,
                    max_tokens=max_tokens or C.MAX_ANSWER_TOKENS,
                    temperature=C.TEMPERATURE if temperature is None else temperature,
                    stream=True)
    think = ThinkFilter()
    try:
        for chunk in response:
            if not chunk.choices:
                continue
            token = getattr(chunk.choices[0].delta, "content", None)
            if token:
                visible = think.feed(token)
                if visible:
                    yield visible
        tail = think.flush()
        if tail:
            yield tail
    finally:
        # Closing the HTTP stream tells LM Studio to stop generating (Stop button)
        try:
            response.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# REASONING TAGS
# ─────────────────────────────────────────────────────────────────────────────
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    text = _THINK_RE.sub("", text or "")
    low = text.lower()
    if "</think>" in low:
        text = text[low.rindex("</think>") + 8:]
    elif "<think>" in low:
        text = text[: low.index("<think>")]
    return text.strip()


class ThinkFilter:
    def __init__(self):
        self.buf = ""
        self.in_think = False

    def feed(self, token: str) -> str:
        self.buf += token
        out = []
        while True:
            if self.in_think:
                idx = self.buf.lower().find("</think>")
                if idx == -1:
                    self.buf = self.buf[-8:]
                    break
                self.buf = self.buf[idx + 8:]
                self.in_think = False
            else:
                idx = self.buf.lower().find("<think>")
                if idx == -1:
                    safe = len(self.buf) - 7
                    if safe > 0:
                        out.append(self.buf[:safe])
                        self.buf = self.buf[safe:]
                    break
                out.append(self.buf[:idx])
                self.buf = self.buf[idx + 7:]
                self.in_think = True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self.in_think else self.buf
        self.buf = ""
        return rest


# ─────────────────────────────────────────────────────────────────────────────
# TOKENS  (same word-based estimate as the previous version)
# ─────────────────────────────────────────────────────────────────────────────
def encode_text(text: str) -> List[str]:
    return re.findall(r"\w+|[^\w\s]", text, re.UNICODE)


def count_tokens(text: str) -> int:
    return max(1, int(len(encode_text(text)) * 1.3))


estimate_tokens = count_tokens


# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDINGS  (nomic prefixes + batching)
# ─────────────────────────────────────────────────────────────────────────────
def embed_texts(texts: List[str], is_query: bool = False) -> List[List[float]]:
    prefix = C.EMBED_QUERY_PREFIX if is_query else C.EMBED_DOC_PREFIX
    vectors = []
    batch_size = max(1, C.EMBEDDING_BATCH_SIZE)
    for start in range(0, len(texts), batch_size):
        batch = [prefix + (t or " ")[:8000] for t in texts[start:start + batch_size]]
        try:
            response = get_client().embeddings.create(model=C.EMBEDDING_MODEL, input=batch)
            vectors.extend(d.embedding for d in sorted(response.data, key=lambda d: d.index))
        except Exception as e:
            print(f"    [Embedding batch failed, retrying one by one]: {e}")
            for text in batch:
                response = get_client().embeddings.create(model=C.EMBEDDING_MODEL, input=[text])
                vectors.append(response.data[0].embedding)
    return vectors


def get_embedding(text: str, is_query: bool = False):
    return embed_texts([text], is_query=is_query)[0]


# ─────────────────────────────────────────────────────────────────────────────
# CHROMA
# ─────────────────────────────────────────────────────────────────────────────
_chroma_lock = threading.Lock()
_chroma = {"client": None}


def get_chroma_client():
    with _chroma_lock:
        if _chroma["client"] is None:
            import chromadb
            os.makedirs(C.CHROMA_DIR, exist_ok=True)
            _chroma["client"] = chromadb.PersistentClient(path=C.CHROMA_DIR)
        return _chroma["client"]


def get_collection(create: bool = True):
    client = get_chroma_client()
    try:
        return client.get_collection(name=C.COLLECTION_NAME)
    except Exception:
        if not create:
            raise
        return client.create_collection(name=C.COLLECTION_NAME,
                                        metadata={"hnsw:space": "cosine"})


# ─────────────────────────────────────────────────────────────────────────────
# LABELS:  "Figure 4.2", "Fig. 3(a)", "TABLE II", "Eq. (5)", "Algorithm 1"
# ─────────────────────────────────────────────────────────────────────────────
_KINDS = [
    ("figure",    r"fig(?:ure)?s?\.?"),
    ("table",     r"tab(?:le)?s?\.?"),
    ("algorithm", r"alg(?:orithm)?s?\.?|procedure|pseudo-?code"),
    ("equation",  r"eq(?:uation)?s?\.?|eqn\.?|formula"),
    ("chart",     r"chart|graph|plot"),
    ("theorem",   r"theorem|lemma|definition|proposition|corollary"),
]
# numbers: 4, 4.2, 4.2.1, II, A1 — optional sub-letter (3a) and brackets
_NUM = r"\(?\s*((?:[IVXLC]{1,6})|(?:[A-Z]?\d{1,3}(?:\.\d{1,3}){0,2}))\s*([a-z])?\s*\)?"

_LABEL_RES = [(kind, re.compile(rf"(?<![A-Za-z])(?i:{pat})\s*{_NUM}(?![A-Za-z0-9])"))
              for kind, pat in _KINDS]

_CAPTION_RES = [(kind, re.compile(rf"^\W{{0,4}}(?i:{pat})\s*{_NUM}(?=\s*[:.\-—–|)]|\s+[A-Z(]|\s*$)"))
                for kind, pat in _KINDS if kind in ("figure", "table", "algorithm", "chart")]

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def roman_to_int(text: str) -> Optional[int]:
    if not text or any(ch not in _ROMAN for ch in text):
        return None
    total, prev = 0, 0
    for ch in reversed(text):
        value = _ROMAN[ch]
        total = total - value if value < prev else total + value
        prev = max(prev, value)
    return total


def normalize_number(number: str) -> str:
    number = number.strip()
    roman = roman_to_int(number)
    return str(roman) if roman is not None else number


def find_labels(text: str) -> List[str]:
    """Every label mentioned in the text, normalised: ['figure 4.2', 'table 3']."""
    found = []
    if not text:
        return found
    for kind, regex in _LABEL_RES:
        for match in regex.finditer(text):
            kind_name = "figure" if kind == "chart" else kind
            label = f"{kind_name} {normalize_number(match.group(1))}"
            if label not in found:
                found.append(label)
    return found


def caption_label(line: str) -> Optional[str]:
    """Label if the line STARTS like a caption ('Figure 4.2: ...', 'TABLE II')."""
    clean = re.sub(r"[*_#`]", " ", line or "").strip()
    for kind, regex in _CAPTION_RES:
        match = regex.match(clean)
        if match:
            kind_name = "figure" if kind == "chart" else kind
            return f"{kind_name} {normalize_number(match.group(1))}"
    return None


def pretty_label(label: str) -> str:
    if not label:
        return ""
    kind, _, number = label.partition(" ")
    return f"{kind.capitalize()} {number}"


# ─────────────────────────────────────────────────────────────────────────────
# PDF LINKS AND PAGE RENDERING
# ─────────────────────────────────────────────────────────────────────────────
def pdf_url(source: str, page: int, token: str = "") -> str:
    quoted = urllib.parse.quote(source.replace("\\", "/"))
    query = f"?token={urllib.parse.quote(token)}" if token else ""
    return f"http://{C.PDF_SERVER_IP}:{C.PDF_SERVER_PORT}/{quoted}{query}#page={max(1, int(page))}"


def pdf_path(source: str) -> str:
    return os.path.join(C.DOCUMENTS_DIR, source.replace("/", os.sep))


def render_pdf_page(source: str, page: int, highlight: str = "", zoom: float = 1.6):
    """PNG bytes of one PDF page, optionally highlighting a label such as 'Figure 4.2'."""
    import pymupdf as fitz
    path = pdf_path(source)
    if not os.path.exists(path):
        return None
    try:
        with fitz.open(path) as document:
            if page < 1 or page > document.page_count:
                return None
            pdf_page = document[page - 1]
            if highlight:
                for variant in (highlight, highlight.replace("Figure", "Fig."),
                                highlight.replace("Equation", "Eq."), highlight.upper()):
                    rects = pdf_page.search_for(variant)
                    if rects:
                        for rect in rects[:3]:
                            pdf_page.add_highlight_annot(rect)
                        break
            pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True)
            return pixmap.tobytes("png")
    except Exception as e:
        print(f"  [Page render failed {source} p{page}]: {e}")
        return None