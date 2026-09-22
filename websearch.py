"""
websearch.py  —  tools for GLOBAL RAG (public research articles + the open web)

Nothing here needs an API key by default and nothing is downloaded from HuggingFace.
Sources:
  • arXiv      (export.arxiv.org Atom API)        — abstracts + PDF links
  • OpenAlex   (api.openalex.org)                 — papers from every publisher
  • Crossref   (api.crossref.org)                 — DOI metadata + abstracts
  • Web search (DuckDuckGo HTML, SearXNG or Tavily) + page download

If your company network blocks a source, the tool returns an empty list and says so
in the status line instead of crashing; switch backends in config.py.
"""

import re
import html
import json
import urllib.parse
from typing import List, Dict, Optional

import requests

import config as C

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LocalRAG/1.0; research assistant)",
    "Accept-Language": "en-US,en;q=0.9",
}


def _proxies():
    return {"http": C.HTTP_PROXY, "https": C.HTTP_PROXY} if C.HTTP_PROXY else None


def _get(url: str, params: Optional[Dict] = None, timeout: Optional[int] = None):
    return requests.get(url, params=params, headers=HEADERS,
                        timeout=timeout or C.HTTP_TIMEOUT, proxies=_proxies())


def _clean(text: str, limit: int = 1500) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return " ".join(text.split())[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# arXiv
# ─────────────────────────────────────────────────────────────────────────────
def search_arxiv(query: str, max_results: Optional[int] = None) -> List[Dict]:
    if not C.ARXIV_ENABLED:
        return []
    import xml.etree.ElementTree as ET
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max_results or C.ARXIV_MAX_RESULTS,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        response = _get(C.ARXIV_API_URL, params=params)
        response.raise_for_status()
        root = ET.fromstring(response.text)
    except Exception as e:
        print(f"  [arXiv search failed]: {e}")
        return []

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    results = []
    for entry in root.findall("atom:entry", namespace):
        def text_of(tag):
            node = entry.find(f"atom:{tag}", namespace)
            return _clean(node.text if node is not None else "", 4000)

        link = ""
        pdf_link = ""
        for node in entry.findall("atom:link", namespace):
            if node.get("title") == "pdf":
                pdf_link = node.get("href", "")
            elif node.get("rel") == "alternate":
                link = node.get("href", "")
        authors = [_clean(a.findtext("atom:name", "", namespace), 80)
                   for a in entry.findall("atom:author", namespace)][:6]
        results.append({
            "kind": "arxiv",
            "title": text_of("title"),
            "abstract": text_of("summary"),
            "authors": authors,
            "published": text_of("published")[:10],
            "url": link,
            "pdf_url": pdf_link,
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# OpenAlex  /  Crossref
# ─────────────────────────────────────────────────────────────────────────────
def search_openalex(query: str, max_results: int = 5) -> List[Dict]:
    if not C.OPENALEX_ENABLED:
        return []
    try:
        response = _get(C.OPENALEX_API_URL,
                        params={"search": query, "per-page": max_results})
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"  [OpenAlex search failed]: {e}")
        return []

    results = []
    for work in data.get("results", [])[:max_results]:
        inverted = work.get("abstract_inverted_index") or {}
        abstract = ""
        if inverted:
            positions = {}
            for word, spots in inverted.items():
                for spot in spots:
                    positions[spot] = word
            abstract = " ".join(positions[k] for k in sorted(positions))[:3000]
        location = (work.get("primary_location") or {})
        results.append({
            "kind": "openalex",
            "title": _clean(work.get("title", ""), 300),
            "abstract": _clean(abstract, 3000),
            "authors": [a.get("author", {}).get("display_name", "")
                        for a in (work.get("authorships") or [])[:6]],
            "published": str(work.get("publication_year", "")),
            "venue": (location.get("source") or {}).get("display_name", ""),
            "url": work.get("doi") or location.get("landing_page_url") or "",
            "pdf_url": (location.get("pdf_url") or ""),
            "citations": work.get("cited_by_count", 0),
        })
    return results


def search_crossref(query: str, max_results: int = 5) -> List[Dict]:
    if not C.CROSSREF_ENABLED:
        return []
    try:
        response = _get(C.CROSSREF_API_URL, params={"query": query, "rows": max_results})
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
    except Exception as e:
        print(f"  [Crossref search failed]: {e}")
        return []

    results = []
    for item in items[:max_results]:
        results.append({
            "kind": "crossref",
            "title": _clean(" ".join(item.get("title") or []), 300),
            "abstract": _clean(item.get("abstract", ""), 3000),
            "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip()
                        for a in (item.get("author") or [])[:6]],
            "published": str((item.get("issued", {}).get("date-parts") or [[""]])[0][0]),
            "venue": _clean(" ".join(item.get("container-title") or []), 150),
            "url": item.get("URL", ""),
            "pdf_url": "",
            "citations": item.get("is-referenced-by-count", 0),
        })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# GENERAL WEB SEARCH
# ─────────────────────────────────────────────────────────────────────────────
def _duckduckgo(query: str, max_results: int) -> List[Dict]:
    try:
        response = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                                 headers=HEADERS, timeout=C.HTTP_TIMEOUT, proxies=_proxies())
        response.raise_for_status()
    except Exception as e:
        print(f"  [DuckDuckGo search failed]: {e}")
        return []

    results = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
        r'(?:.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
        re.DOTALL)
    for match in pattern.finditer(response.text):
        url = html.unescape(match.group("url"))
        if "duckduckgo.com/l/?uddg=" in url:            # unwrap redirect links
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            url = urllib.parse.unquote(parsed.get("uddg", [url])[0])
        results.append({
            "kind": "web",
            "title": _clean(match.group("title"), 200),
            "snippet": _clean(match.group("snippet") or "", 500),
            "url": url,
        })
        if len(results) >= max_results:
            break
    return results


def _searxng(query: str, max_results: int) -> List[Dict]:
    try:
        response = _get(C.SEARXNG_URL, params={"q": query, "format": "json"})
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"  [SearXNG search failed]: {e}")
        return []
    return [{"kind": "web", "title": _clean(item.get("title", ""), 200),
             "snippet": _clean(item.get("content", ""), 500), "url": item.get("url", "")}
            for item in data.get("results", [])[:max_results]]


def _tavily(query: str, max_results: int) -> List[Dict]:
    if not C.TAVILY_API_KEY:
        return []
    try:
        response = requests.post("https://api.tavily.com/search",
                                 json={"api_key": C.TAVILY_API_KEY, "query": query,
                                       "max_results": max_results,
                                       "include_raw_content": False},
                                 timeout=C.HTTP_TIMEOUT, proxies=_proxies())
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"  [Tavily search failed]: {e}")
        return []
    return [{"kind": "web", "title": _clean(item.get("title", ""), 200),
             "snippet": _clean(item.get("content", ""), 500), "url": item.get("url", "")}
            for item in data.get("results", [])[:max_results]]


def search_web(query: str, max_results: Optional[int] = None) -> List[Dict]:
    max_results = max_results or C.WEB_SEARCH_MAX_RESULTS
    backend = (C.WEB_SEARCH_BACKEND or "off").lower()
    if backend == "duckduckgo":
        return _duckduckgo(query, max_results)
    if backend == "searxng":
        return _searxng(query, max_results)
    if backend == "tavily":
        return _tavily(query, max_results)
    return []


# ─────────────────────────────────────────────────────────────────────────────
# PAGE / PDF DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────
def fetch_page(url: str, max_chars: Optional[int] = None) -> str:
    """Readable text of a web page or a PDF (first pages), or '' if it cannot be read."""
    max_chars = max_chars or C.WEB_FETCH_MAX_CHARS
    try:
        response = _get(url, timeout=C.HTTP_TIMEOUT)
        response.raise_for_status()
    except Exception as e:
        print(f"  [Fetch failed {url}]: {e}")
        return ""

    content_type = (response.headers.get("content-type") or "").lower()
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        try:
            import pymupdf as fitz
            with fitz.open(stream=response.content, filetype="pdf") as document:
                text = []
                for page in document:
                    text.append(page.get_text("text"))
                    if sum(len(t) for t in text) > max_chars:
                        break
            return " ".join(" ".join(text).split())[:max_chars]
        except Exception as e:
            print(f"  [PDF parse failed {url}]: {e}")
            return ""

    body = response.text
    body = re.sub(r"(?is)<(script|style|nav|footer|header|form).*?</\1>", " ", body)
    body = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    lines = [line.strip() for line in body.split("\n")]
    return "\n".join(line for line in lines if len(line) > 2)[:max_chars]


# ─────────────────────────────────────────────────────────────────────────────
# ONE CALL USED BY THE GRAPH
# ─────────────────────────────────────────────────────────────────────────────
def gather_sources(queries: List[str], want_papers: bool = True,
                   want_web: bool = True) -> List[Dict]:
    """Runs every query against the enabled sources and returns de-duplicated results."""
    results, seen = [], set()

    def push(item):
        url = (item.get("url") or item.get("pdf_url") or "").split("#")[0]
        key = (item.get("title", "").lower()[:80], url)
        if not item.get("title") or key in seen:
            return
        seen.add(key)
        results.append(item)

    for query in queries:
        if want_papers:
            for item in search_arxiv(query):
                push(item)
            for item in search_openalex(query, 4):
                push(item)
            for item in search_crossref(query, 3):
                push(item)
        if want_web:
            for item in search_web(query):
                push(item)
    return results
