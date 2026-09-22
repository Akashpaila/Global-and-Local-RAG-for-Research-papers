# # =========================================================
# # CONFIGURATION  —  Local + Global RAG (auth, no HuggingFace downloads)
# # =========================================================
# import os

# # ── LM Studio (same models as before) ──────────────────────
# LM_STUDIO_BASE_URL = "http://localhost:1234/v1"
# LM_STUDIO_API_KEY  = "lm-studio"
# LLM_MODEL          = "qwen/qwen3.8-27b"        # vision-capable, handles text+images
# EMBEDDING_MODEL    = "text-embedding-nomic-embed-text-v1.5"
# BROWSER_PATH       = r"C:\Program Files\Mozilla Firefox\firefox.exe"

# # Qwen3.8 thinks at "xhigh" by default → slow. "low" | "medium" | "xhigh" | None
# REASONING_EFFORT = "low"
# LLM_TIMEOUT_SECONDS = 900

# # nomic-embed needs these prefixes; without them retrieval quality drops a lot
# EMBED_DOC_PREFIX   = "search_document: "
# EMBED_QUERY_PREFIX = "search_query: "
# EMBEDDING_BATCH_SIZE = 16

# # ── Paths ──────────────────────────────────────────────────
# BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
# DOCUMENTS_DIR   = os.path.join(BASE_DIR, "documents")
# CHROMA_DIR      = os.path.join(BASE_DIR, "chroma_db")
# COLLECTION_NAME = "my_documents_v3"            # new name: labelled multimodal index
# FIGURE_IMAGE_DIR = os.path.join(BASE_DIR, "figure_images")   # cropped figures/tables
# SESSION_DB_PATH = os.path.join(BASE_DIR, "session_memory.db")

# # ── PDF server (run `python pdf_server.py` in a second terminal) ──
# PDF_SERVER_HOST = "0.0.0.0"
# PDF_SERVER_PORT = 8765
# PDF_SERVER_IP   = "10.135.114.113"    # THIS machine's IPv4 (ipconfig)
# PDF_SERVER_REQUIRE_TOKEN = True     # only signed links from the app may open PDFs
# AUTO_OPEN_PDF_ON_HOST = False       # True = also open Firefox on the host machine

# # ── Retrieval ───────────────────────────────────────────────
# TOP_K                = 10   # general questions
# DETAILED_TOP_K       = 20   # algorithm / method / comparison questions
# MAX_CONTEXT_CHUNKS   = 25
# MAX_CONTEXT_TOKENS   = 14000
# BM25_K               = 30   # keyword-search candidates (pure python, no downloads)
# VECTOR_K             = 30
# RRF_K                = 60
# MAX_PINNED_PER_LABEL = 6    # "Figure 4.2" → exact matches pinned into the context
# NEIGHBOR_CHUNKS      = 1
# SIMILARITY_THRESHOLD = 0.30

# # ── Token-aware chunking ────────────────────────────────────
# CHUNK_SIZE     = 800
# CHUNK_OVERLAP  = 120
# MAX_CHUNK_SIZE = 1100

# # ── Figure / table / equation extraction ────────────────────
# MIN_FIGURE_WIDTH     = 10
# MIN_FIGURE_HEIGHT    = 10
# MAX_FIGURES_PER_PAGE = 100
# FIGURE_CROP_DPI      = 200    # resolution of saved figure crops
# VISION_MAX_TOKENS    = 2000
# SAVE_FIGURE_IMAGES   = True   # crop each captioned figure/table to a PNG
# VISION_ON_PAGES_WITHOUT_CAPTION = True   # also read images that have no caption

# # ── Session memory ──────────────────────────────────────────
# MAX_MEMORY_MESSAGES = 10

# # ── Generation ──────────────────────────────────────────────
# TEMPERATURE       = 0.1
# MAX_ANSWER_TOKENS = 6000

# # =========================================================
# # AUTHENTICATION
# # =========================================================
# AUTH_ENABLED = True
# # Only bootstraps the FIRST admin; afterwards users live in the database
# # and are managed in the app (sidebar → 🛠️ Users).   tcsadmin / tcs54321
# AUTH_BOOTSTRAP_ADMINS = {
#     "tcsadmin": "pbkdf2_sha256$240000$be9cc0c4e522379c99e484a6d030cd1d$8ecf8388141d8e072a026f80c173c84a411af02296b6150488b45e105fb88529",
# }
# AUTH_USERS = AUTH_BOOTSTRAP_ADMINS
# AUTH_SECRET_KEY = ""                 # auto-generated once into ".auth_secret"
# AUTH_MIN_PASSWORD_LENGTH = 8
# AUTH_NEW_USERS_MUST_CHANGE_PASSWORD = True
# AUTH_SESSION_TIMEOUT_MINUTES = 480
# AUTH_MAX_FAILED_ATTEMPTS = 5
# AUTH_LOCKOUT_MINUTES = 5
# PDF_LINK_TOKEN_HOURS = 12

# # =========================================================
# # GLOBAL RAG  (public research articles + web search)
# # =========================================================
# GLOBAL_SEARCH_ENABLED = True
# # Ask the user every time, or force one mode:
# #   "ask" | "local" | "global" | "both"
# DEFAULT_SEARCH_MODE = "ask"

# ARXIV_ENABLED   = True
# ARXIV_MAX_RESULTS = 6
# ARXIV_API_URL   = "http://export.arxiv.org/api/query"

# # Extra sources (all free, no API key)
# CROSSREF_ENABLED = True
# CROSSREF_API_URL = "https://api.crossref.org/works"
# OPENALEX_ENABLED = True
# OPENALEX_API_URL = "https://api.openalex.org/works"

# # General web search backend: "duckduckgo" | "searxng" | "tavily" | "off"
# WEB_SEARCH_BACKEND = "duckduckgo"
# SEARXNG_URL   = "http://localhost:8080/search"       # if you host SearXNG
# TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
# WEB_SEARCH_MAX_RESULTS = 6
# WEB_FETCH_PAGES        = 3      # how many result pages are downloaded in full
# WEB_FETCH_MAX_CHARS    = 6000   # characters kept per fetched page
# GLOBAL_SEARCH_ROUNDS   = 2      # agent may refine its queries this many times
# HTTP_TIMEOUT           = 25
# HTTP_PROXY             = ""     # e.g. "http://proxy.company.com:8080" if needed

# INGEST_VERSION = "3.0"






###################################  22nd Sept Update ########################################## 

# =========================================================
# CONFIGURATION  —  Local + Global RAG (auth, no HuggingFace downloads)
# =========================================================
import os

# ── LM Studio (same models as before) ──────────────────────
LM_STUDIO_BASE_URL = "http://localhost:1234/v1"
LM_STUDIO_API_KEY  = "lm-studio"
LLM_MODEL          = "qwen/qwen3.8-27b"        # vision-capable, handles text+images
EMBEDDING_MODEL    = "text-embedding-nomic-embed-text-v1.5"
BROWSER_PATH       = r"C:\Program Files\Mozilla Firefox\firefox.exe"

# Qwen3.8 thinks at "xhigh" by default → slow. "low" | "medium" | "xhigh" | None
REASONING_EFFORT = "low"
LLM_TIMEOUT_SECONDS = 900

# nomic-embed needs these prefixes; without them retrieval quality drops a lot
EMBED_DOC_PREFIX   = "search_document: "
EMBED_QUERY_PREFIX = "search_query: "
EMBEDDING_BATCH_SIZE = 16

# ── Paths ──────────────────────────────────────────────────
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DOCUMENTS_DIR   = os.path.join(BASE_DIR, "documents")
CHROMA_DIR      = os.path.join(BASE_DIR, "chroma_db")
COLLECTION_NAME = "my_documents_v3"            # new name: labelled multimodal index
FIGURE_IMAGE_DIR = os.path.join(BASE_DIR, "figure_images")   # cropped figures/tables
SESSION_DB_PATH = os.path.join(BASE_DIR, "session_memory.db")

# ── PDF server (run `python pdf_server.py` in a second terminal) ──
PDF_SERVER_HOST = "0.0.0.0"
PDF_SERVER_PORT = 8765
PDF_SERVER_IP   = "10.135.114.113"    # THIS machine's IPv4 (ipconfig)
PDF_SERVER_REQUIRE_TOKEN = True     # only signed links from the app may open PDFs
AUTO_OPEN_PDF_ON_HOST = False       # True = also open Firefox on the host machine

# ── Retrieval ───────────────────────────────────────────────
TOP_K                = 10   # general questions
DETAILED_TOP_K       = 20   # algorithm / method / comparison questions
MAX_CONTEXT_CHUNKS   = 25
MAX_CONTEXT_TOKENS   = 14000
BM25_K               = 30   # keyword-search candidates (pure python, no downloads)
VECTOR_K             = 30
RRF_K                = 60
MAX_PINNED_PER_LABEL = 6    # "Figure 4.2" → exact matches pinned into the context
NEIGHBOR_CHUNKS      = 1
SIMILARITY_THRESHOLD = 0.30

# ── Token-aware chunking ────────────────────────────────────
CHUNK_SIZE     = 800
CHUNK_OVERLAP  = 120
MAX_CHUNK_SIZE = 1100

# ── Figure / table / equation extraction ────────────────────
MIN_FIGURE_WIDTH     = 10
MIN_FIGURE_HEIGHT    = 10
MAX_FIGURES_PER_PAGE = 100
FIGURE_CROP_DPI      = 200    # resolution of saved figure crops
VISION_MAX_TOKENS    = 2000
SAVE_FIGURE_IMAGES   = True   # crop each captioned figure/table to a PNG
VISION_ON_PAGES_WITHOUT_CAPTION = True   # also read images that have no caption

# ── Chat memory ─────────────────────────────────────────────
# Each chat remembers its ENTIRE conversation; a new chat starts with nothing.
# The recent part of the chat is sent word for word; once a chat grows beyond
# CHAT_HISTORY_TOKEN_BUDGET, its oldest messages are folded into a running
# summary of that chat, so nothing from the chat is ever dropped.
#
# Keep this sum below the context length loaded in LM Studio:
#   MAX_CONTEXT_TOKENS + CHAT_HISTORY_TOKEN_BUDGET + CHAT_SUMMARY_MAX_TOKENS
#   + MAX_ANSWER_TOKENS + ~2500 (instructions)
#   → 32k context: history budget ~6000   |  64k: ~30000   |  128k: ~90000
CHAT_HISTORY_TOKEN_BUDGET   = 6000   # word-for-word part of the chat sent with each question
CHAT_KEEP_RECENT_TOKENS     = 2500   # after summarising, this much stays word for word
CHAT_SUMMARY_MAX_TOKENS     = 1500   # size of the running summary of older messages
CHAT_SUMMARY_BATCH_TOKENS   = 6000   # old messages are summarised in batches of this size
CHAT_REWRITE_HISTORY_TOKENS = 2500   # smaller slice used to rewrite follow-up questions
MAX_MEMORY_MESSAGES = 10             # no longer limits memory (kept for compatibility)

# ── Generation ──────────────────────────────────────────────
TEMPERATURE       = 0.1
MAX_ANSWER_TOKENS = 6000

# =========================================================
# AUTHENTICATION
# =========================================================
AUTH_ENABLED = True
# Only bootstraps the FIRST admin; afterwards users live in the database
# and are managed in the app (sidebar → 🛠️ Users).   tcsadmin / tcs54321
AUTH_BOOTSTRAP_ADMINS = {
    "tcsadmin": "pbkdf2_sha256$240000$be9cc0c4e522379c99e484a6d030cd1d$8ecf8388141d8e072a026f80c173c84a411af02296b6150488b45e105fb88529",
}
AUTH_USERS = AUTH_BOOTSTRAP_ADMINS
AUTH_SECRET_KEY = ""                 # auto-generated once into ".auth_secret"
AUTH_MIN_PASSWORD_LENGTH = 8
AUTH_NEW_USERS_MUST_CHANGE_PASSWORD = True
AUTH_SESSION_TIMEOUT_MINUTES = 480
AUTH_MAX_FAILED_ATTEMPTS = 5
AUTH_LOCKOUT_MINUTES = 5
PDF_LINK_TOKEN_HOURS = 12

# =========================================================
# GLOBAL RAG  (public research articles + web search)
# =========================================================
GLOBAL_SEARCH_ENABLED = True
# Ask the user every time, or force one mode:
#   "ask" | "local" | "global" | "both"
DEFAULT_SEARCH_MODE = "ask"

ARXIV_ENABLED   = True
ARXIV_MAX_RESULTS = 6
ARXIV_API_URL   = "http://export.arxiv.org/api/query"

# Extra sources (all free, no API key)
CROSSREF_ENABLED = True
CROSSREF_API_URL = "https://api.crossref.org/works"
OPENALEX_ENABLED = True
OPENALEX_API_URL = "https://api.openalex.org/works"

# General web search backend: "duckduckgo" | "searxng" | "tavily" | "off"
WEB_SEARCH_BACKEND = "duckduckgo"
SEARXNG_URL   = "http://localhost:8080/search"       # if you host SearXNG
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
WEB_SEARCH_MAX_RESULTS = 6
WEB_FETCH_PAGES        = 3      # how many result pages are downloaded in full
WEB_FETCH_MAX_CHARS    = 6000   # characters kept per fetched page
GLOBAL_SEARCH_ROUNDS   = 2      # agent may refine its queries this many times
HTTP_TIMEOUT           = 25
HTTP_PROXY             = ""     # e.g. "http://proxy.company.com:8080" if needed

INGEST_VERSION = "3.0"