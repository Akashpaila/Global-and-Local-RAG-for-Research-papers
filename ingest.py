# """
# ingest.py  —  Caption-driven multimodal ingestion

#     python ingest.py            # incremental (new / changed files only)
#     python ingest.py --reset    # wipe the collection and re-ingest everything

# For every PDF page it stores, always with the exact page number:
#   • text chunks          (token-aware, table-aware)
#   • TABLE chunks         every Markdown table kept whole, with its caption label
#   • FIGURE chunks        each captioned figure / flowchart / chart is CROPPED to a PNG
#                          and described by the vision model — labelled with the caption
#                          number, so "pull Figure 4.2" matches exactly
#   • ALGORITHM chunks     captioned pseudocode blocks
#   • EQUATION chunks      numbered equations transcribed to LaTeX
#   • a document card      title + list of every labelled element in the file
# """

# import os
# import re
# import io
# import sys
# import glob
# import json
# import time
# import uuid
# import base64
# import hashlib
# from typing import List, Dict, Iterator, Optional, Tuple

# import pymupdf as fitz
# import pymupdf4llm

# from langchain_community.document_loaders import TextLoader, Docx2txtLoader

# import config as C
# from rag_common import (
#     get_collection, get_chroma_client, embed_texts, chat, strip_reasoning,
#     count_tokens, encode_text, find_labels, caption_label, pretty_label,
# )

# MANIFEST_PATH = os.path.join(C.CHROMA_DIR, "ingestion_manifest.json")
# SETTINGS_KEY = "__settings__"


# # ─────────────────────────────────────────────────────────────────────────────
# # MANIFEST
# # ─────────────────────────────────────────────────────────────────────────────
# def settings_fingerprint() -> Dict:
#     return {
#         "version": C.INGEST_VERSION,
#         "collection": C.COLLECTION_NAME,
#         "embedding_model": C.EMBEDDING_MODEL,
#         "embed_doc_prefix": C.EMBED_DOC_PREFIX,
#         "chunk_size": C.CHUNK_SIZE,
#         "chunk_overlap": C.CHUNK_OVERLAP,
#     }


# def load_manifest() -> Dict:
#     if os.path.exists(MANIFEST_PATH):
#         try:
#             with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
#                 return json.load(f)
#         except Exception as e:
#             print(f"  [Manifest read error: {e}] — starting fresh")
#     return {}


# def save_manifest(manifest: Dict):
#     os.makedirs(C.CHROMA_DIR, exist_ok=True)
#     tmp = MANIFEST_PATH + ".tmp"
#     with open(tmp, "w", encoding="utf-8") as f:
#         json.dump(manifest, f, indent=2, ensure_ascii=False)
#     os.replace(tmp, MANIFEST_PATH)


# def get_file_hash(file_path: str) -> str:
#     hasher = hashlib.md5()
#     with open(file_path, "rb") as f:
#         while block := f.read(65536):
#             hasher.update(block)
#     return hasher.hexdigest()


# def remove_source_chunks(collection, source_name: str):
#     try:
#         collection.delete(where={"source": source_name})
#     except Exception as e:
#         print(f"  [Could not remove chunks for {source_name}]: {e}")


# def get_document_files() -> List[str]:
#     files = []
#     for ext in ("*.pdf", "*.txt", "*.docx", "*.md"):
#         files.extend(glob.glob(os.path.join(C.DOCUMENTS_DIR, ext)))
#     return sorted(files)


# # ─────────────────────────────────────────────────────────────────────────────
# # CHUNK WRITER  (batched embedding → much faster than one call per chunk)
# # ─────────────────────────────────────────────────────────────────────────────
# class ChunkWriter:
#     def __init__(self, collection, source: str):
#         self.collection = collection
#         self.source = source
#         self.buffer: List[Tuple[str, Dict]] = []
#         self.counter = 0
#         self.elements: List[Dict] = []
#         self.stats = {"text": 0, "table": 0, "figure_description": 0,
#                       "equation": 0, "algorithm": 0, "document_card": 0}

#     def add(self, text: str, metadata: Dict):
#         if not text or not text.strip():
#             return
#         metadata = dict(metadata)
#         metadata.setdefault("content_type", "text")
#         metadata.setdefault("element_label", "")
#         metadata.setdefault("caption", "")
#         metadata.setdefault("image_path", "")
#         metadata.setdefault("chunk", -1)
#         metadata["source"] = self.source
#         metadata["token_count"] = count_tokens(text)
#         clean = {}
#         for key, value in metadata.items():
#             if value is None:
#                 continue
#             clean[key] = value[:900] if isinstance(value, str) else value
#         self.buffer.append((text, clean))
#         content_type = clean["content_type"]
#         self.stats[content_type] = self.stats.get(content_type, 0) + 1
#         self.counter += 1
#         if len(self.buffer) >= 32:
#             self.flush()

#     def flush(self):
#         if not self.buffer:
#             return
#         texts = [b[0] for b in self.buffer]
#         metadatas = [b[1] for b in self.buffer]
#         embeddings = embed_texts(texts)
#         self.collection.add(
#             ids=[str(uuid.uuid4()) for _ in texts],
#             embeddings=embeddings,
#             documents=texts,
#             metadatas=metadatas,
#         )
#         print(f"    ↳ stored {len(texts)} chunk(s)")
#         self.buffer = []


# # ─────────────────────────────────────────────────────────────────────────────
# # TEXT CLEANING / PARAGRAPHS / CHUNKING  (table-aware, as before)
# # ─────────────────────────────────────────────────────────────────────────────
# def clean_markdown_page(text: str) -> str:
#     text = text or ""
#     text = re.sub(r"<!--\s*Start of picture text\s*-->", "\n[Text inside figure]: ", text)
#     text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
#     text = re.sub(r"<br\s*/?>", " ", text)
#     lines = [line for line in text.split("\n")
#              if not re.fullmatch(r"\s*(?:page\s*)?\d{1,4}\s*", line, re.IGNORECASE)]
#     return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# def extract_markdown_tables(markdown: str) -> Tuple[str, List[Dict]]:
#     """Pulls Markdown tables out of the page text so chunking cannot break them."""
#     lines = markdown.split("\n")
#     tables, kept = [], []
#     index = 0
#     while index < len(lines):
#         if lines[index].strip().startswith("|"):
#             end = index
#             while end < len(lines) and lines[end].strip().startswith("|"):
#                 end += 1
#             block = lines[index:end]
#             if len(block) >= 2:
#                 caption, label = _caption_near(lines, index, end, "table")
#                 tables.append({"markdown": "\n".join(block), "caption": caption, "label": label})
#                 kept.append(f"[[TABLE: {pretty_label(label) or 'unlabelled table'} "
#                             f"— stored as a separate chunk]]")
#             else:
#                 kept.extend(block)
#             index = end
#         else:
#             kept.append(lines[index])
#             index += 1
#     return "\n".join(kept), tables


# def _caption_near(lines: List[str], start: int, end: int, kind: str) -> Tuple[str, str]:
#     def grab(i):
#         caption = re.sub(r"[*_]", "", lines[i]).strip()
#         if len(caption.split()) <= 3 and i + 1 < len(lines) and lines[i + 1].strip() \
#                 and not lines[i + 1].strip().startswith("|"):
#             caption += " " + re.sub(r"[*_]", "", lines[i + 1]).strip()
#         return caption[:400]

#     for i in range(start - 1, max(-1, start - 9), -1):
#         label = caption_label(lines[i])
#         if label and label.startswith(kind):
#             return grab(i), label
#     for i in range(end, min(len(lines), end + 5)):
#         label = caption_label(lines[i])
#         if label and label.startswith(kind):
#             return grab(i), label
#     return "", ""


# def split_into_paragraphs(text: str) -> List[str]:
#     """Tables stay intact; plain text has its whitespace collapsed."""
#     lines = text.split("\n")
#     segments, buffer, in_table = [], [], False
#     for line in lines:
#         is_table_line = line.strip().startswith("|")
#         if is_table_line:
#             if not in_table and buffer:
#                 segments.append(("text", "\n".join(buffer)))
#                 buffer = []
#             in_table = True
#             buffer.append(line)
#         else:
#             if in_table:
#                 segments.append(("table", "\n".join(buffer)))
#                 buffer, in_table = [], False
#             buffer.append(line)
#     if buffer:
#         segments.append(("table" if in_table else "text", "\n".join(buffer)))

#     paragraphs = []
#     for segment_type, segment in segments:
#         if segment_type == "table":
#             if segment.strip():
#                 paragraphs.append(segment.strip())
#         else:
#             for para in re.split(r"\n\s*\n+", segment):
#                 cleaned = " ".join(para.split())
#                 if cleaned:
#                     paragraphs.append(cleaned)
#     return paragraphs


# def chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> List[str]:
#     chunk_size = chunk_size or C.CHUNK_SIZE
#     overlap = overlap or C.CHUNK_OVERLAP
#     paragraphs = split_into_paragraphs(text)
#     if not paragraphs:
#         return []

#     chunks, current, current_tokens = [], [], 0
#     for paragraph in paragraphs:
#         tokens = len(encode_text(paragraph))
#         if tokens > C.MAX_CHUNK_SIZE:
#             if current:
#                 chunks.append("\n\n".join(current))
#                 current, current_tokens = [], 0
#             sentences = re.split(r"(?<=[.!?])\s+|\n", paragraph)
#             piece, piece_tokens = [], 0
#             for sentence in sentences:
#                 sentence_tokens = len(encode_text(sentence))
#                 if piece and piece_tokens + sentence_tokens > chunk_size:
#                     chunks.append(" ".join(piece))
#                     piece, piece_tokens = [], 0
#                 piece.append(sentence)
#                 piece_tokens += sentence_tokens
#             if piece:
#                 chunks.append(" ".join(piece))
#             continue

#         if current and current_tokens + tokens > chunk_size:
#             chunks.append("\n\n".join(current))
#             overlap_paras, overlap_tokens = [], 0
#             for previous in reversed(current):
#                 previous_tokens = len(encode_text(previous))
#                 if overlap_tokens + previous_tokens > overlap:
#                     break
#                 overlap_paras.insert(0, previous)
#                 overlap_tokens += previous_tokens
#             current, current_tokens = overlap_paras, overlap_tokens
#         current.append(paragraph)
#         current_tokens += tokens

#     if current:
#         chunks.append("\n\n".join(current))
#     return [c for c in chunks if c.strip()]


# def split_table_rows(markdown: str, max_tokens: int) -> List[str]:
#     rows = markdown.split("\n")
#     if count_tokens(markdown) <= max_tokens or len(rows) <= 3:
#         return [markdown]
#     header = rows[:2] if len(rows) > 1 and re.fullmatch(r"\|?[\s:\-|]+\|?", rows[1]) else rows[:1]
#     parts, current = [], []
#     for row in rows[len(header):]:
#         if current and count_tokens("\n".join(header + current + [row])) > max_tokens:
#             parts.append("\n".join(header + current))
#             current = []
#         current.append(row)
#     if current:
#         parts.append("\n".join(header + current))
#     return parts


# # ─────────────────────────────────────────────────────────────────────────────
# # CAPTION DETECTION ON THE PAGE (with positions)
# # ─────────────────────────────────────────────────────────────────────────────
# def find_page_captions(page) -> List[Dict]:
#     """Every caption on the page: label, kind, full caption text and its rectangle."""
#     captions = []
#     try:
#         blocks = page.get_text("blocks")
#     except Exception:
#         return captions
#     for block in blocks:
#         x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
#         if not text or not text.strip():
#             continue
#         lines = [line for line in text.strip().split("\n") if line.strip()]
#         label = caption_label(lines[0])
#         if not label and len(lines) > 1:
#             label = caption_label(" ".join(lines[:2]))
#         if not label:
#             continue
#         caption_text = " ".join(" ".join(lines).split())[:400]
#         captions.append({
#             "label": label,
#             "kind": label.split()[0],
#             "caption": caption_text,
#             "rect": fitz.Rect(x0, y0, x1, y1),
#         })
#     return captions


# def visual_rects(page) -> List[fitz.Rect]:
#     """Rectangles of images and vector-drawing clusters (flowcharts, plots, diagrams)."""
#     rects = []
#     try:
#         for info in page.get_image_info():
#             rect = fitz.Rect(info["bbox"])
#             if rect.width >= C.MIN_FIGURE_WIDTH and rect.height >= C.MIN_FIGURE_HEIGHT:
#                 rects.append(rect)
#     except Exception:
#         pass
#     clusters = []
#     try:
#         clusters = page.cluster_drawings()          # PyMuPDF ≥ 1.24
#     except Exception:
#         try:
#             clusters = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
#         except Exception:
#             clusters = []
#     for rect in clusters:
#         rect = fitz.Rect(rect)
#         if rect.width >= 40 and rect.height >= 30:
#             rects.append(rect)
#     return rects


# def _horizontal_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
#     width = min(a.x1, b.x1) - max(a.x0, b.x0)
#     return width / max(1.0, min(a.width, b.width))


# def region_for_caption(page, caption_rect: fitz.Rect, kind: str,
#                        rects: List[fitz.Rect]) -> fitz.Rect:
#     """The area of the figure/table that belongs to a caption."""
#     page_rect = page.rect
#     above = [r for r in rects if r.y1 <= caption_rect.y0 + 6
#              and caption_rect.y0 - r.y1 < 460 and _horizontal_overlap(r, caption_rect) > 0.15]
#     below = [r for r in rects if r.y0 >= caption_rect.y1 - 6
#              and r.y0 - caption_rect.y1 < 460 and _horizontal_overlap(r, caption_rect) > 0.15]

#     # figures: caption normally below the graphic; tables: caption normally above
#     ordered = (below + above) if kind == "table" else (above + below)
#     if ordered:
#         region = fitz.Rect(ordered[0])
#         for rect in ordered:
#             if _horizontal_overlap(rect, region) > 0.1 or abs(rect.y0 - region.y1) < 60:
#                 region |= rect
#     else:
#         # nothing detected → take a band above (or below for tables) the caption
#         if kind == "table":
#             region = fitz.Rect(caption_rect.x0 - 20, caption_rect.y1,
#                                caption_rect.x1 + 20, caption_rect.y1 + 330)
#         else:
#             region = fitz.Rect(caption_rect.x0 - 20, caption_rect.y0 - 340,
#                                caption_rect.x1 + 20, caption_rect.y0)
#     region |= caption_rect                     # always include the caption itself
#     region = fitz.Rect(region.x0 - 8, region.y0 - 8, region.x1 + 8, region.y1 + 8)
#     return region & page_rect


# def crop_png(page, rect: fitz.Rect, dpi: int = None) -> Optional[bytes]:
#     try:
#         zoom = (dpi or C.FIGURE_CROP_DPI) / 72.0
#         pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
#         if pixmap.width < 20 or pixmap.height < 20:
#             return None
#         return pixmap.tobytes("png")
#     except Exception as e:
#         print(f"    [Crop failed]: {e}")
#         return None


# def save_figure_image(png: bytes, source: str, page_number: int, label: str, index: int) -> str:
#     if not C.SAVE_FIGURE_IMAGES or not png:
#         return ""
#     folder = os.path.join(C.FIGURE_IMAGE_DIR, os.path.splitext(source)[0])
#     os.makedirs(folder, exist_ok=True)
#     safe_label = re.sub(r"[^A-Za-z0-9.]+", "_", label or f"item{index}")
#     name = f"p{page_number:03d}_{safe_label}.png"
#     path = os.path.join(folder, name)
#     with open(path, "wb") as f:
#         f.write(png)
#     return os.path.relpath(path, C.BASE_DIR).replace(os.sep, "/")


# # ─────────────────────────────────────────────────────────────────────────────
# # VISION
# # ─────────────────────────────────────────────────────────────────────────────
# VISION_PROMPT = """You are analysing one element cropped from a research document.

# Document: "{paper}"
# Page: {page}
# {label_line}Caption as printed: "{caption}"

# Describe it with maximum technical detail so it can be found by search later.

# 1. IDENTIFIER: start with exactly "This is {display} from {paper}, page {page}."
# 2. TYPE: flowchart / block diagram / architecture / bar chart / line graph / scatter plot /
#    confusion matrix / photograph / schematic / table / pseudocode / equation / other.
# 3. ALL VISIBLE TEXT: every label, axis title, tick value, legend entry and annotation, quoted exactly.
# 4. STRUCTURE: every box, node, arrow and connection IN FLOW ORDER (A -> B -> C), decision branches,
#    loops, inputs and outputs, sub-panels (a), (b).
# 5. DATA AND VALUES: all numbers, percentages, units and ranges you can read.
# 6. If it is a TABLE: reproduce it as a GitHub Markdown table with every row, column and exact number.
# 7. If it contains EQUATIONS: write them in LaTeX (no $ delimiters) and define each symbol.
# 8. If it is PSEUDOCODE: transcribe it line by line as printed.
# 9. KEY FINDING: what this element demonstrates or proves.

# Write dense, searchable prose. Never invent numbers or labels that are not visible."""


# def describe_element(png: bytes, source: str, page_number: int,
#                      label: str, caption: str) -> Optional[str]:
#     display = pretty_label(label) if label else "an unlabelled figure"
#     prompt = VISION_PROMPT.format(
#         paper=os.path.splitext(source)[0].replace("_", " "),
#         page=page_number,
#         label_line=f"Printed label: {display}\n" if label else "",
#         caption=caption or "(no caption found)",
#         display=display,
#     )
#     b64 = base64.b64encode(png).decode("ascii")
#     try:
#         response = chat(
#             [{"role": "user", "content": [
#                 {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
#                 {"type": "text", "text": prompt},
#             ]}],
#             max_tokens=C.VISION_MAX_TOKENS, temperature=0.1,
#         )
#         return strip_reasoning(response.choices[0].message.content or "").strip()
#     except Exception as e:
#         print(f"    [Vision failed — {source} p{page_number} {label}]: {e}")
#         return None


# EQUATION_PROMPT = """This is a page of the document "{paper}" (page {page}).

# List EVERY displayed or numbered mathematical equation on this page.
# For each one write:
#   Equation <number as printed, e.g. (5)> :
#   LaTeX: <the equation in LaTeX, no $ delimiters>
#   Meaning: <what it computes and what every symbol means>

# Use only equations that are actually visible. If an equation has no printed number, write "Equation (unnumbered)".
# If the page has no equations, answer exactly: NONE."""


# def describe_equations(png: bytes, source: str, page_number: int) -> Optional[str]:
#     b64 = base64.b64encode(png).decode("ascii")
#     try:
#         response = chat(
#             [{"role": "user", "content": [
#                 {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
#                 {"type": "text", "text": EQUATION_PROMPT.format(
#                     paper=os.path.splitext(source)[0].replace("_", " "), page=page_number)},
#             ]}],
#             max_tokens=C.VISION_MAX_TOKENS, temperature=0.1,
#         )
#         text = strip_reasoning(response.choices[0].message.content or "").strip()
#         return None if text.upper().startswith("NONE") else text
#     except Exception as e:
#         print(f"    [Equation vision failed — {source} p{page_number}]: {e}")
#         return None


# EQUATION_HINT = re.compile(r"\(\s*\d{1,3}[a-z]?\s*\)\s*$", re.MULTILINE)
# MATH_SYMBOLS = re.compile(r"[∑∏∫∂∇≈≠≤≥∈∀∃√∞λθσμαβγδεπφψωΣΩ]")


# def page_has_equations(text: str) -> bool:
#     numbered = len(EQUATION_HINT.findall(text or ""))
#     symbols = len(MATH_SYMBOLS.findall(text or ""))
#     return numbered >= 1 or symbols >= 8


# # ─────────────────────────────────────────────────────────────────────────────
# # PDF INGESTION
# # ─────────────────────────────────────────────────────────────────────────────
# def markdown_pages(pdf_path: str) -> Dict[int, str]:
#     try:
#         chunks = pymupdf4llm.to_markdown(pdf_path, page_chunks=True, show_progress=False)
#     except TypeError:
#         chunks = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)
#     except Exception as e:
#         print(f"  [pymupdf4llm failed: {e}] — using plain text extraction")
#         return {}
#     pages = {}
#     for index, chunk in enumerate(chunks):
#         metadata = chunk.get("metadata") or {}
#         number = metadata.get("page_number") or metadata.get("page") or (index + 1)
#         pages[int(number)] = chunk.get("text", "")
#     return pages


# def ingest_pdf(file_path: str, source: str, writer: ChunkWriter):
#     markdown_by_page = markdown_pages(file_path)
#     document = fitz.open(file_path)
#     title_meta = (document.metadata or {}).get("title", "") or ""
#     first_pages = []

#     try:
#         for page_index in range(document.page_count):
#             page_number = page_index + 1
#             page = document[page_index]
#             raw_text = page.get_text("text") or ""
#             markdown = clean_markdown_page(markdown_by_page.get(page_number) or raw_text)
#             if page_number <= 2:
#                 first_pages.append(markdown)

#             body, tables = extract_markdown_tables(markdown)
#             captions = find_page_captions(page)
#             rects = visual_rects(page)

#             labels_here = ", ".join(pretty_label(c["label"]) for c in captions) or "-"
#             print(f"  Page {page_number}/{document.page_count} | captions: {labels_here}")

#             # ── 1. text chunks ────────────────────────────────────────────────
#             for chunk_index, chunk in enumerate(chunk_text(body)):
#                 writer.add(f"[{source} | Page {page_number}]\n{chunk}", {
#                     "page": page_number,
#                     "page_index": page_index,
#                     "chunk": chunk_index,
#                     "content_type": "text",
#                     "loader": "pymupdf4llm",
#                 })

#             # ── 2. table chunks (Markdown kept intact) ────────────────────────
#             table_labels_done = set()
#             for table_index, table in enumerate(tables):
#                 label = table["label"]
#                 table_labels_done.add(label)
#                 name = pretty_label(label) or f"Table on page {page_number}"
#                 for part_index, part in enumerate(
#                         split_table_rows(table["markdown"], C.MAX_CHUNK_SIZE)):
#                     header = (f"[TABLE — {name} — {source}, Page {page_number}]"
#                               + (f" (part {part_index + 1})" if part_index else ""))
#                     caption_line = f"Caption: {table['caption']}\n" if table["caption"] else ""
#                     writer.add(f"{header}\n{caption_line}\n{part}", {
#                         "page": page_number,
#                         "page_index": page_index,
#                         "content_type": "table",
#                         "element_label": label,
#                         "caption": table["caption"],
#                         "loader": "markdown_table",
#                         "chunk": -3,
#                     })
#                 if label:
#                     writer.elements.append({"label": label, "page": page_number,
#                                             "caption": table["caption"], "kind": "table"})

#             # ── 3. captioned figures / tables / algorithms → crop + vision ────
#             used_rects = []
#             for caption_index, caption in enumerate(captions):
#                 kind = caption["kind"]
#                 if kind not in ("figure", "table", "algorithm"):
#                     continue
#                 # a table already captured as Markdown does not need a second pass
#                 if kind == "table" and caption["label"] in table_labels_done:
#                     continue

#                 region = region_for_caption(page, caption["rect"], kind, rects)
#                 png = crop_png(page, region)
#                 if not png:
#                     continue
#                 used_rects.append(region)

#                 print(f"    Reading {pretty_label(caption['label'])} "
#                       f"({int(region.width)}×{int(region.height)}pt)...")
#                 description = describe_element(png, source, page_number,
#                                                caption["label"], caption["caption"])
#                 if not description:
#                     continue

#                 image_path = save_figure_image(png, source, page_number,
#                                                caption["label"], caption_index)
#                 content_type = {"figure": "figure_description", "table": "table",
#                                 "algorithm": "algorithm"}[kind]
#                 name = pretty_label(caption["label"])
#                 writer.add(
#                     f"[{content_type.upper()} — {name} — {source}, Page {page_number}]\n"
#                     f"Caption: {caption['caption']}\n\n{description}",
#                     {
#                         "page": page_number,
#                         "page_index": page_index,
#                         "content_type": content_type,
#                         "element_label": caption["label"],
#                         "caption": caption["caption"],
#                         "image_path": image_path,
#                         "loader": "qwen_vision",
#                         "chunk": -1,
#                     })
#                 writer.elements.append({"label": caption["label"], "page": page_number,
#                                         "caption": caption["caption"], "kind": kind})

#             # ── 4. images with no caption at all ──────────────────────────────
#             if C.VISION_ON_PAGES_WITHOUT_CAPTION:
#                 extra = 0
#                 for rect in rects:
#                     if extra >= C.MAX_FIGURES_PER_PAGE:
#                         break
#                     if rect.width < 120 or rect.height < 90:
#                         continue
#                     if any(_horizontal_overlap(rect, used) > 0.5
#                            and abs(rect.y0 - used.y0) < 40 for used in used_rects):
#                         continue
#                     png = crop_png(page, rect)
#                     if not png:
#                         continue
#                     extra += 1
#                     print(f"    Reading unlabelled visual #{extra} on page {page_number}...")
#                     description = describe_element(png, source, page_number, "", "")
#                     if not description:
#                         continue
#                     image_path = save_figure_image(png, source, page_number,
#                                                    f"unlabelled{extra}", extra)
#                     writer.add(
#                         f"[FIGURE_DESCRIPTION — unlabelled visual {extra} — "
#                         f"{source}, Page {page_number}]\n\n{description}",
#                         {
#                             "page": page_number,
#                             "page_index": page_index,
#                             "content_type": "figure_description",
#                             "element_label": "",
#                             "image_path": image_path,
#                             "loader": "qwen_vision",
#                             "chunk": -1,
#                         })
#                     used_rects.append(rect)

#             # ── 5. equations ──────────────────────────────────────────────────
#             if page_has_equations(raw_text):
#                 page_png = crop_png(page, page.rect, dpi=150)
#                 equations = describe_equations(page_png, source, page_number) if page_png else None
#                 if equations:
#                     labels = find_labels(equations)
#                     writer.add(
#                         f"[EQUATIONS — {source}, Page {page_number}]\n\n{equations}",
#                         {
#                             "page": page_number,
#                             "page_index": page_index,
#                             "content_type": "equation",
#                             "element_label": labels[0] if labels else "",
#                             "loader": "qwen_vision",
#                             "chunk": -2,
#                         })
#                     for label in labels:
#                         if label.startswith("equation"):
#                             writer.elements.append({"label": label, "page": page_number,
#                                                     "caption": "", "kind": "equation"})
#     finally:
#         page_count = document.page_count
#         document.close()

#     add_document_card(writer, source, title_meta, first_pages, page_count)


# def add_document_card(writer: ChunkWriter, source: str, title_meta: str,
#                       first_pages: List[str], page_count: int):
#     text = "\n\n".join(first_pages)
#     title = (title_meta or "").strip()
#     junk = ("untitled", "anonymous", "(anonymous)", "unknown", "title", "document")
#     if len(title) < 6 or title.lower() in junk or title.lower().startswith("microsoft word") \
#             or title.lower().endswith((".doc", ".docx", ".pdf", ".tex")):
#         title = ""
#         for line in text.split("\n"):
#             candidate = re.sub(r"[*_#]", "", line).strip()
#             if len(candidate) > 8:
#                 title = candidate[:200]
#                 break

#     match = re.search(r"abstract[\s*:.—\-]*\n?(.{100,1800}?)(?:\n#|\n\s*\n\s*(?:\d\.?\s+)?introduction|$)",
#                       text, re.IGNORECASE | re.DOTALL)
#     abstract = (match.group(1) if match else text[:1200]).strip()

#     seen, inventory = set(), []
#     for element in sorted(writer.elements, key=lambda e: (e["page"], e["label"])):
#         key = (element["label"], element["page"])
#         if key in seen:
#             continue
#         seen.add(key)
#         caption = f" — {element['caption'][:120]}" if element.get("caption") else ""
#         inventory.append(f"- {pretty_label(element['label'])} (page {element['page']}){caption}")

#     card = (f"[DOCUMENT CARD — {source} — {page_count} page(s)]\n"
#             f"File: {source}\nTitle: {title}\n\nAbstract / opening text:\n{abstract}\n\n"
#             f"Labelled elements in this document ({len(inventory)}):\n" + "\n".join(inventory[:300]))
#     writer.add(card, {"page": 1, "page_index": 0, "content_type": "document_card",
#                       "title": title[:300], "page_count": page_count, "chunk": -4,
#                       "loader": "summary"})


# def ingest_plain_file(file_path: str, source: str, writer: ChunkWriter):
#     extension = os.path.splitext(file_path)[1].lower()
#     if extension == ".docx":
#         pages = [d.page_content for d in Docx2txtLoader(file_path).load()]
#     else:
#         pages = [d.page_content for d in
#                  TextLoader(file_path, encoding="utf-8", autodetect_encoding=True).load()]
#     text = clean_markdown_page("\n\n".join(pages))
#     body, tables = extract_markdown_tables(text)
#     for chunk_index, chunk in enumerate(chunk_text(body)):
#         writer.add(f"[{source} | Page 1]\n{chunk}",
#                    {"page": 1, "page_index": 0, "chunk": chunk_index,
#                     "content_type": "text", "loader": extension.lstrip(".")})
#     for table in tables:
#         name = pretty_label(table["label"]) or "Table"
#         writer.add(f"[TABLE — {name} — {source}]\nCaption: {table['caption']}\n\n{table['markdown']}",
#                    {"page": 1, "page_index": 0, "content_type": "table",
#                     "element_label": table["label"], "caption": table["caption"],
#                     "loader": "markdown_table", "chunk": -3})
#     add_document_card(writer, source, "", [text[:4000]], 1)


# # ─────────────────────────────────────────────────────────────────────────────
# # ONE FILE
# # ─────────────────────────────────────────────────────────────────────────────
# def ingest_one_file(file_path: str, collection) -> Dict:
#     source = os.path.basename(file_path)
#     print("\n" + "-" * 70 + f"\nIngesting: {source}\n" + "-" * 70)
#     started = time.time()
#     writer = ChunkWriter(collection, source)

#     if file_path.lower().endswith(".pdf"):
#         ingest_pdf(file_path, source, writer)
#     else:
#         ingest_plain_file(file_path, source, writer)

#     writer.flush()
#     elapsed = time.time() - started
#     print(f"  Done in {elapsed:.0f}s — {writer.counter} chunks {writer.stats}")
#     return {"chunks": writer.counter, **writer.stats}


# # ─────────────────────────────────────────────────────────────────────────────
# # MAIN
# # ─────────────────────────────────────────────────────────────────────────────
# def check_setup() -> bool:
#     try:
#         get_collection()
#         embed_texts(["connection test"])
#         return True
#     except Exception as e:
#         print(f"\nSetup problem — stopping before ingestion: {e}")
#         print("Check that LM Studio's server is running, both models are loaded, "
#               "and EMBEDDING_MODEL matches http://localhost:1234/v1/models")
#         return False


# def ingest_documents(force_full: bool = False):
#     os.makedirs(C.DOCUMENTS_DIR, exist_ok=True)
#     if not check_setup():
#         return

#     manifest = load_manifest()
#     fingerprint = settings_fingerprint()
#     if manifest.get(SETTINGS_KEY) and manifest[SETTINGS_KEY] != fingerprint and not force_full:
#         print("Ingestion settings changed → full re-ingestion required.")
#         force_full = True

#     print("=" * 70)
#     print("     CAPTION-DRIVEN MULTIMODAL INGESTION")
#     print(f"     Mode        : {'FULL RESET' if force_full else 'INCREMENTAL'}")
#     print(f"     Text+Tables : pymupdf4llm (Markdown)")
#     print(f"     Figures     : cropped by caption + {C.LLM_MODEL} vision")
#     print(f"     Embeddings  : {C.EMBEDDING_MODEL}")
#     print("=" * 70)

#     if force_full:
#         try:
#             get_chroma_client().delete_collection(C.COLLECTION_NAME)
#             print(f"  Deleted collection {C.COLLECTION_NAME}")
#         except Exception:
#             pass
#         if os.path.exists(MANIFEST_PATH):
#             os.remove(MANIFEST_PATH)
#         manifest = {}

#     collection = get_collection()
#     manifest[SETTINGS_KEY] = fingerprint

#     files = {os.path.basename(f): f for f in get_document_files()}
#     if not files:
#         print(f"\nNo documents found in {C.DOCUMENTS_DIR}")
#         save_manifest(manifest)
#         return

#     for source in [s for s in manifest if s != SETTINGS_KEY and s not in files]:
#         print(f"  − REMOVED    {source}")
#         remove_source_chunks(collection, source)
#         del manifest[source]
#     save_manifest(manifest)

#     counts = {"new": 0, "changed": 0, "skipped": 0, "failed": 0}
#     for source, file_path in files.items():
#         file_hash = get_file_hash(file_path)
#         record = manifest.get(source)
#         if record and record.get("hash") == file_hash:
#             print(f"  ✓ UNCHANGED  {source} ({record.get('chunks', '?')} chunks)")
#             counts["skipped"] += 1
#             continue
#         if record:
#             print(f"  ↻ CHANGED    {source}")
#             counts["changed"] += 1
#         else:
#             print(f"  + NEW        {source}")
#             counts["new"] += 1
#         remove_source_chunks(collection, source)
#         try:
#             summary = ingest_one_file(file_path, collection)
#         except KeyboardInterrupt:
#             remove_source_chunks(collection, source)
#             raise
#         except Exception as e:
#             print(f"  ✗ FAILED {source}: {e}")
#             remove_source_chunks(collection, source)
#             manifest.pop(source, None)
#             save_manifest(manifest)
#             counts["failed"] += 1
#             continue
#         manifest[source] = {"hash": file_hash, "size": os.path.getsize(file_path),
#                             "ingested_at": time.strftime("%Y-%m-%d %H:%M:%S"), **summary}
#         save_manifest(manifest)

#     print("\n" + "=" * 70)
#     print(f"New: {counts['new']} | Changed: {counts['changed']} | "
#           f"Unchanged: {counts['skipped']} | Failed: {counts['failed']}")
#     print(f"Total chunks in collection: {collection.count()}")
#     print(f"Figure images: {C.FIGURE_IMAGE_DIR}")
#     print("Restart the Streamlit app (or press 'Reload index') to use the new data.")
#     print("=" * 70)


# if __name__ == "__main__":
#     ingest_documents(force_full="--reset" in sys.argv)






#######################################  22nd Update ######################################### 
"""
ingest.py  —  Caption-driven multimodal ingestion

    python ingest.py            # incremental (new / changed files only)
    python ingest.py --reset    # wipe the collection and re-ingest everything

For every PDF page it stores, always with the exact page number:
  • text chunks          (token-aware, table-aware)
  • TABLE chunks         every Markdown table kept whole, with its caption label
  • FIGURE chunks        each captioned figure / flowchart / chart is CROPPED to a PNG
                         and described by the vision model — labelled with the caption
                         number, so "pull Figure 4.2" matches exactly
  • ALGORITHM chunks     captioned pseudocode blocks
  • EQUATION chunks      numbered equations transcribed to LaTeX
  • a document card      title + list of every labelled element in the file

LAZY LOADING
  Documents are read with generators (lazy_load_documents → lazy_load_pdf_pages,
  LangChain's loader.lazy_load()): pages are converted, chunked, embedded and stored
  PDF_PAGE_BATCH_SIZE pages at a time and then released, instead of first loading
  the whole document into memory. Memory use stays flat even for 1000-page PDFs,
  and work starts on page 1 immediately. The stored chunks are identical to the
  previous (eager) version, so existing ingestions stay valid.
"""

import os
import re
import io
import sys
import glob
import json
import time
import uuid
import base64
import hashlib
from typing import List, Dict, Iterator, Optional, Tuple, Any

import pymupdf as fitz
import pymupdf4llm

from langchain_community.document_loaders import TextLoader, Docx2txtLoader

import config as C
from rag_common import (
    get_collection, get_chroma_client, embed_texts, chat, strip_reasoning,
    count_tokens, encode_text, find_labels, caption_label, pretty_label,
)

MANIFEST_PATH = os.path.join(C.CHROMA_DIR, "ingestion_manifest.json")
SETTINGS_KEY = "__settings__"


# ─────────────────────────────────────────────────────────────────────────────
# MANIFEST
# ─────────────────────────────────────────────────────────────────────────────
def settings_fingerprint() -> Dict:
    return {
        "version": C.INGEST_VERSION,
        "collection": C.COLLECTION_NAME,
        "embedding_model": C.EMBEDDING_MODEL,
        "embed_doc_prefix": C.EMBED_DOC_PREFIX,
        "chunk_size": C.CHUNK_SIZE,
        "chunk_overlap": C.CHUNK_OVERLAP,
    }


def load_manifest() -> Dict:
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"  [Manifest read error: {e}] — starting fresh")
    return {}


def save_manifest(manifest: Dict):
    os.makedirs(C.CHROMA_DIR, exist_ok=True)
    tmp = MANIFEST_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MANIFEST_PATH)


def get_file_hash(file_path: str) -> str:
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while block := f.read(65536):
            hasher.update(block)
    return hasher.hexdigest()


def remove_source_chunks(collection, source_name: str):
    try:
        collection.delete(where={"source": source_name})
    except Exception as e:
        print(f"  [Could not remove chunks for {source_name}]: {e}")


def get_document_files() -> List[str]:
    files = []
    for ext in ("*.pdf", "*.txt", "*.docx", "*.md"):
        files.extend(glob.glob(os.path.join(C.DOCUMENTS_DIR, ext)))
    return sorted(files)


# ─────────────────────────────────────────────────────────────────────────────
# CHUNK WRITER  (batched embedding → much faster than one call per chunk)
# ─────────────────────────────────────────────────────────────────────────────
class ChunkWriter:
    def __init__(self, collection, source: str):
        self.collection = collection
        self.source = source
        self.buffer: List[Tuple[str, Dict]] = []
        self.counter = 0
        self.elements: List[Dict] = []
        self.stats = {"text": 0, "table": 0, "figure_description": 0,
                      "equation": 0, "algorithm": 0, "document_card": 0}

    def add(self, text: str, metadata: Dict):
        if not text or not text.strip():
            return
        metadata = dict(metadata)
        metadata.setdefault("content_type", "text")
        metadata.setdefault("element_label", "")
        metadata.setdefault("caption", "")
        metadata.setdefault("image_path", "")
        metadata.setdefault("chunk", -1)
        metadata["source"] = self.source
        metadata["token_count"] = count_tokens(text)
        clean = {}
        for key, value in metadata.items():
            if value is None:
                continue
            clean[key] = value[:900] if isinstance(value, str) else value
        self.buffer.append((text, clean))
        content_type = clean["content_type"]
        self.stats[content_type] = self.stats.get(content_type, 0) + 1
        self.counter += 1
        if len(self.buffer) >= 32:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        texts = [b[0] for b in self.buffer]
        metadatas = [b[1] for b in self.buffer]
        embeddings = embed_texts(texts)
        self.collection.add(
            ids=[str(uuid.uuid4()) for _ in texts],
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        print(f"    ↳ stored {len(texts)} chunk(s)")
        self.buffer = []


# ─────────────────────────────────────────────────────────────────────────────
# TEXT CLEANING / PARAGRAPHS / CHUNKING  (table-aware, as before)
# ─────────────────────────────────────────────────────────────────────────────
def clean_markdown_page(text: str) -> str:
    text = text or ""
    text = re.sub(r"<!--\s*Start of picture text\s*-->", "\n[Text inside figure]: ", text)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<br\s*/?>", " ", text)
    lines = [line for line in text.split("\n")
             if not re.fullmatch(r"\s*(?:page\s*)?\d{1,4}\s*", line, re.IGNORECASE)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_markdown_tables(markdown: str) -> Tuple[str, List[Dict]]:
    """Pulls Markdown tables out of the page text so chunking cannot break them."""
    lines = markdown.split("\n")
    tables, kept = [], []
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("|"):
            end = index
            while end < len(lines) and lines[end].strip().startswith("|"):
                end += 1
            block = lines[index:end]
            if len(block) >= 2:
                caption, label = _caption_near(lines, index, end, "table")
                tables.append({"markdown": "\n".join(block), "caption": caption, "label": label})
                kept.append(f"[[TABLE: {pretty_label(label) or 'unlabelled table'} "
                            f"— stored as a separate chunk]]")
            else:
                kept.extend(block)
            index = end
        else:
            kept.append(lines[index])
            index += 1
    return "\n".join(kept), tables


def _caption_near(lines: List[str], start: int, end: int, kind: str) -> Tuple[str, str]:
    def grab(i):
        caption = re.sub(r"[*_]", "", lines[i]).strip()
        if len(caption.split()) <= 3 and i + 1 < len(lines) and lines[i + 1].strip() \
                and not lines[i + 1].strip().startswith("|"):
            caption += " " + re.sub(r"[*_]", "", lines[i + 1]).strip()
        return caption[:400]

    for i in range(start - 1, max(-1, start - 9), -1):
        label = caption_label(lines[i])
        if label and label.startswith(kind):
            return grab(i), label
    for i in range(end, min(len(lines), end + 5)):
        label = caption_label(lines[i])
        if label and label.startswith(kind):
            return grab(i), label
    return "", ""


def split_into_paragraphs(text: str) -> List[str]:
    """Tables stay intact; plain text has its whitespace collapsed."""
    lines = text.split("\n")
    segments, buffer, in_table = [], [], False
    for line in lines:
        is_table_line = line.strip().startswith("|")
        if is_table_line:
            if not in_table and buffer:
                segments.append(("text", "\n".join(buffer)))
                buffer = []
            in_table = True
            buffer.append(line)
        else:
            if in_table:
                segments.append(("table", "\n".join(buffer)))
                buffer, in_table = [], False
            buffer.append(line)
    if buffer:
        segments.append(("table" if in_table else "text", "\n".join(buffer)))

    paragraphs = []
    for segment_type, segment in segments:
        if segment_type == "table":
            if segment.strip():
                paragraphs.append(segment.strip())
        else:
            for para in re.split(r"\n\s*\n+", segment):
                cleaned = " ".join(para.split())
                if cleaned:
                    paragraphs.append(cleaned)
    return paragraphs


def chunk_text(text: str, chunk_size: int = None, overlap: int = None) -> List[str]:
    chunk_size = chunk_size or C.CHUNK_SIZE
    overlap = overlap or C.CHUNK_OVERLAP
    paragraphs = split_into_paragraphs(text)
    if not paragraphs:
        return []

    chunks, current, current_tokens = [], [], 0
    for paragraph in paragraphs:
        tokens = len(encode_text(paragraph))
        if tokens > C.MAX_CHUNK_SIZE:
            if current:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            sentences = re.split(r"(?<=[.!?])\s+|\n", paragraph)
            piece, piece_tokens = [], 0
            for sentence in sentences:
                sentence_tokens = len(encode_text(sentence))
                if piece and piece_tokens + sentence_tokens > chunk_size:
                    chunks.append(" ".join(piece))
                    piece, piece_tokens = [], 0
                piece.append(sentence)
                piece_tokens += sentence_tokens
            if piece:
                chunks.append(" ".join(piece))
            continue

        if current and current_tokens + tokens > chunk_size:
            chunks.append("\n\n".join(current))
            overlap_paras, overlap_tokens = [], 0
            for previous in reversed(current):
                previous_tokens = len(encode_text(previous))
                if overlap_tokens + previous_tokens > overlap:
                    break
                overlap_paras.insert(0, previous)
                overlap_tokens += previous_tokens
            current, current_tokens = overlap_paras, overlap_tokens
        current.append(paragraph)
        current_tokens += tokens

    if current:
        chunks.append("\n\n".join(current))
    return [c for c in chunks if c.strip()]


def split_table_rows(markdown: str, max_tokens: int) -> List[str]:
    rows = markdown.split("\n")
    if count_tokens(markdown) <= max_tokens or len(rows) <= 3:
        return [markdown]
    header = rows[:2] if len(rows) > 1 and re.fullmatch(r"\|?[\s:\-|]+\|?", rows[1]) else rows[:1]
    parts, current = [], []
    for row in rows[len(header):]:
        if current and count_tokens("\n".join(header + current + [row])) > max_tokens:
            parts.append("\n".join(header + current))
            current = []
        current.append(row)
    if current:
        parts.append("\n".join(header + current))
    return parts


# ─────────────────────────────────────────────────────────────────────────────
# CAPTION DETECTION ON THE PAGE (with positions)
# ─────────────────────────────────────────────────────────────────────────────
def find_page_captions(page) -> List[Dict]:
    """Every caption on the page: label, kind, full caption text and its rectangle."""
    captions = []
    try:
        blocks = page.get_text("blocks")
    except Exception:
        return captions
    for block in blocks:
        x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
        if not text or not text.strip():
            continue
        lines = [line for line in text.strip().split("\n") if line.strip()]
        label = caption_label(lines[0])
        if not label and len(lines) > 1:
            label = caption_label(" ".join(lines[:2]))
        if not label:
            continue
        caption_text = " ".join(" ".join(lines).split())[:400]
        captions.append({
            "label": label,
            "kind": label.split()[0],
            "caption": caption_text,
            "rect": fitz.Rect(x0, y0, x1, y1),
        })
    return captions


def visual_rects(page) -> List[fitz.Rect]:
    """Rectangles of images and vector-drawing clusters (flowcharts, plots, diagrams)."""
    rects = []
    try:
        for info in page.get_image_info():
            rect = fitz.Rect(info["bbox"])
            if rect.width >= C.MIN_FIGURE_WIDTH and rect.height >= C.MIN_FIGURE_HEIGHT:
                rects.append(rect)
    except Exception:
        pass
    clusters = []
    try:
        clusters = page.cluster_drawings()          # PyMuPDF ≥ 1.24
    except Exception:
        try:
            clusters = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
        except Exception:
            clusters = []
    for rect in clusters:
        rect = fitz.Rect(rect)
        if rect.width >= 40 and rect.height >= 30:
            rects.append(rect)
    return rects


def _horizontal_overlap(a: fitz.Rect, b: fitz.Rect) -> float:
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    return width / max(1.0, min(a.width, b.width))


def region_for_caption(page, caption_rect: fitz.Rect, kind: str,
                       rects: List[fitz.Rect]) -> fitz.Rect:
    """The area of the figure/table that belongs to a caption."""
    page_rect = page.rect
    above = [r for r in rects if r.y1 <= caption_rect.y0 + 6
             and caption_rect.y0 - r.y1 < 460 and _horizontal_overlap(r, caption_rect) > 0.15]
    below = [r for r in rects if r.y0 >= caption_rect.y1 - 6
             and r.y0 - caption_rect.y1 < 460 and _horizontal_overlap(r, caption_rect) > 0.15]

    # figures: caption normally below the graphic; tables: caption normally above
    ordered = (below + above) if kind == "table" else (above + below)
    if ordered:
        region = fitz.Rect(ordered[0])
        for rect in ordered:
            if _horizontal_overlap(rect, region) > 0.1 or abs(rect.y0 - region.y1) < 60:
                region |= rect
    else:
        # nothing detected → take a band above (or below for tables) the caption
        if kind == "table":
            region = fitz.Rect(caption_rect.x0 - 20, caption_rect.y1,
                               caption_rect.x1 + 20, caption_rect.y1 + 330)
        else:
            region = fitz.Rect(caption_rect.x0 - 20, caption_rect.y0 - 340,
                               caption_rect.x1 + 20, caption_rect.y0)
    region |= caption_rect                     # always include the caption itself
    region = fitz.Rect(region.x0 - 8, region.y0 - 8, region.x1 + 8, region.y1 + 8)
    return region & page_rect


def crop_png(page, rect: fitz.Rect, dpi: int = None) -> Optional[bytes]:
    try:
        zoom = (dpi or C.FIGURE_CROP_DPI) / 72.0
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
        if pixmap.width < 20 or pixmap.height < 20:
            return None
        return pixmap.tobytes("png")
    except Exception as e:
        print(f"    [Crop failed]: {e}")
        return None


def save_figure_image(png: bytes, source: str, page_number: int, label: str, index: int) -> str:
    if not C.SAVE_FIGURE_IMAGES or not png:
        return ""
    folder = os.path.join(C.FIGURE_IMAGE_DIR, os.path.splitext(source)[0])
    os.makedirs(folder, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9.]+", "_", label or f"item{index}")
    name = f"p{page_number:03d}_{safe_label}.png"
    path = os.path.join(folder, name)
    with open(path, "wb") as f:
        f.write(png)
    return os.path.relpath(path, C.BASE_DIR).replace(os.sep, "/")


# ─────────────────────────────────────────────────────────────────────────────
# VISION
# ─────────────────────────────────────────────────────────────────────────────
VISION_PROMPT = """You are analysing one element cropped from a research document.

Document: "{paper}"
Page: {page}
{label_line}Caption as printed: "{caption}"

Describe it with maximum technical detail so it can be found by search later.

1. IDENTIFIER: start with exactly "This is {display} from {paper}, page {page}."
2. TYPE: flowchart / block diagram / architecture / bar chart / line graph / scatter plot /
   confusion matrix / photograph / schematic / table / pseudocode / equation / other.
3. ALL VISIBLE TEXT: every label, axis title, tick value, legend entry and annotation, quoted exactly.
4. STRUCTURE: every box, node, arrow and connection IN FLOW ORDER (A -> B -> C), decision branches,
   loops, inputs and outputs, sub-panels (a), (b).
5. DATA AND VALUES: all numbers, percentages, units and ranges you can read.
6. If it is a TABLE: reproduce it as a GitHub Markdown table with every row, column and exact number.
7. If it contains EQUATIONS: write them in LaTeX (no $ delimiters) and define each symbol.
8. If it is PSEUDOCODE: transcribe it line by line as printed.
9. KEY FINDING: what this element demonstrates or proves.

Write dense, searchable prose. Never invent numbers or labels that are not visible."""


def describe_element(png: bytes, source: str, page_number: int,
                     label: str, caption: str) -> Optional[str]:
    display = pretty_label(label) if label else "an unlabelled figure"
    prompt = VISION_PROMPT.format(
        paper=os.path.splitext(source)[0].replace("_", " "),
        page=page_number,
        label_line=f"Printed label: {display}\n" if label else "",
        caption=caption or "(no caption found)",
        display=display,
    )
    b64 = base64.b64encode(png).decode("ascii")
    try:
        response = chat(
            [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ]}],
            max_tokens=C.VISION_MAX_TOKENS, temperature=0.1,
        )
        return strip_reasoning(response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"    [Vision failed — {source} p{page_number} {label}]: {e}")
        return None


EQUATION_PROMPT = """This is a page of the document "{paper}" (page {page}).

List EVERY displayed or numbered mathematical equation on this page.
For each one write:
  Equation <number as printed, e.g. (5)> :
  LaTeX: <the equation in LaTeX, no $ delimiters>
  Meaning: <what it computes and what every symbol means>

Use only equations that are actually visible. If an equation has no printed number, write "Equation (unnumbered)".
If the page has no equations, answer exactly: NONE."""


def describe_equations(png: bytes, source: str, page_number: int) -> Optional[str]:
    b64 = base64.b64encode(png).decode("ascii")
    try:
        response = chat(
            [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": EQUATION_PROMPT.format(
                    paper=os.path.splitext(source)[0].replace("_", " "), page=page_number)},
            ]}],
            max_tokens=C.VISION_MAX_TOKENS, temperature=0.1,
        )
        text = strip_reasoning(response.choices[0].message.content or "").strip()
        return None if text.upper().startswith("NONE") else text
    except Exception as e:
        print(f"    [Equation vision failed — {source} p{page_number}]: {e}")
        return None


EQUATION_HINT = re.compile(r"\(\s*\d{1,3}[a-z]?\s*\)\s*$", re.MULTILINE)
MATH_SYMBOLS = re.compile(r"[∑∏∫∂∇≈≠≤≥∈∀∃√∞λθσμαβγδεπφψωΣΩ]")


def page_has_equations(text: str) -> bool:
    numbered = len(EQUATION_HINT.findall(text or ""))
    symbols = len(MATH_SYMBOLS.findall(text or ""))
    return numbered >= 1 or symbols >= 8


# ─────────────────────────────────────────────────────────────────────────────
# PDF INGESTION
# ─────────────────────────────────────────────────────────────────────────────
def _markdown_for_pages(pdf_path: str, pages: List[int]) -> Dict[int, str]:
    """pymupdf4llm Markdown for ONLY the given pages (0-based) → {page_number: markdown}.
    If conversion of this batch fails, an empty dict is returned and the caller falls
    back to plain text for these pages only (the rest of the document is unaffected)."""
    try:
        chunks = pymupdf4llm.to_markdown(pdf_path, pages=pages, page_chunks=True,
                                         show_progress=False)
    except TypeError:
        chunks = pymupdf4llm.to_markdown(pdf_path, pages=pages, page_chunks=True)
    except Exception as e:
        print(f"  [pymupdf4llm failed on pages {pages[0] + 1}-{pages[-1] + 1}: {e}] "
              f"— using plain text for these pages")
        return {}
    result = {}
    for index, chunk in enumerate(chunks):
        metadata = chunk.get("metadata") or {}
        number = metadata.get("page_number") or metadata.get("page") \
            or (pages[index] + 1 if index < len(pages) else None)
        if number:
            result[int(number)] = chunk.get("text", "")
    return result


def lazy_load_pdf_pages(file_path: str) -> Iterator[Dict[str, Any]]:
    """Yields one record per PDF page, converting only PDF_PAGE_BATCH_SIZE pages at a time.

    Each record: page_index, page_number, page_count, page (PyMuPDF page object, valid
    until the next record), raw_text, markdown, title_meta.
    """
    batch_size = max(1, int(getattr(C, "PDF_PAGE_BATCH_SIZE", 10)))
    document = fitz.open(file_path)                  # opening a PDF does not load its pages
    try:
        page_count = document.page_count
        title_meta = (document.metadata or {}).get("title", "") or ""
        for start in range(0, page_count, batch_size):
            batch = list(range(start, min(page_count, start + batch_size)))
            markdown_by_page = _markdown_for_pages(file_path, batch)
            for page_index in batch:
                page_number = page_index + 1
                page = document[page_index]
                raw_text = page.get_text("text") or ""
                yield {
                    "page_index": page_index,
                    "page_number": page_number,
                    "page_count": page_count,
                    "page": page,
                    "raw_text": raw_text,
                    "markdown": clean_markdown_page(markdown_by_page.get(page_number) or raw_text),
                    "title_meta": title_meta,
                }
            del markdown_by_page                     # release this batch before the next one
    finally:
        document.close()


def lazy_load_plain_documents(file_path: str) -> Iterator[str]:
    """Yields the text of a TXT / MD / DOCX file through LangChain's lazy_load()."""
    extension = os.path.splitext(file_path)[1].lower()
    if extension == ".docx":
        loader = Docx2txtLoader(file_path)
    else:
        loader = TextLoader(file_path, encoding="utf-8", autodetect_encoding=True)
    for document in loader.lazy_load():
        if document.page_content and document.page_content.strip():
            yield document.page_content


def lazy_load_documents(file_path: str) -> Iterator[Dict[str, Any]]:
    """One entry point for every supported file type — a generator, never a full list.
    PDFs yield page records; other files yield {"kind": "text", "text": ...}."""
    if file_path.lower().endswith(".pdf"):
        for record in lazy_load_pdf_pages(file_path):
            record["kind"] = "pdf_page"
            yield record
    else:
        for text in lazy_load_plain_documents(file_path):
            yield {"kind": "text", "text": text}


def ingest_pdf(file_path: str, source: str, writer: ChunkWriter):
    """Processes a PDF page by page as the lazy loader yields them."""
    first_pages: List[str] = []
    title_meta, page_count = "", 0

    pages = lazy_load_pdf_pages(file_path)
    try:
        for record in pages:
            page_index = record["page_index"]
            page_number = record["page_number"]
            page_count = record["page_count"]
            title_meta = record["title_meta"]
            page = record["page"]
            raw_text = record["raw_text"]
            markdown = record["markdown"]
            if page_number <= 2:
                first_pages.append(markdown)

            body, tables = extract_markdown_tables(markdown)
            captions = find_page_captions(page)
            rects = visual_rects(page)

            labels_here = ", ".join(pretty_label(c["label"]) for c in captions) or "-"
            print(f"  Page {page_number}/{page_count} | captions: {labels_here}")

            # ── 1. text chunks ────────────────────────────────────────────────
            for chunk_index, chunk in enumerate(chunk_text(body)):
                writer.add(f"[{source} | Page {page_number}]\n{chunk}", {
                    "page": page_number,
                    "page_index": page_index,
                    "chunk": chunk_index,
                    "content_type": "text",
                    "loader": "pymupdf4llm",
                })

            # ── 2. table chunks (Markdown kept intact) ────────────────────────
            table_labels_done = set()
            for table_index, table in enumerate(tables):
                label = table["label"]
                table_labels_done.add(label)
                name = pretty_label(label) or f"Table on page {page_number}"
                for part_index, part in enumerate(
                        split_table_rows(table["markdown"], C.MAX_CHUNK_SIZE)):
                    header = (f"[TABLE — {name} — {source}, Page {page_number}]"
                              + (f" (part {part_index + 1})" if part_index else ""))
                    caption_line = f"Caption: {table['caption']}\n" if table["caption"] else ""
                    writer.add(f"{header}\n{caption_line}\n{part}", {
                        "page": page_number,
                        "page_index": page_index,
                        "content_type": "table",
                        "element_label": label,
                        "caption": table["caption"],
                        "loader": "markdown_table",
                        "chunk": -3,
                    })
                if label:
                    writer.elements.append({"label": label, "page": page_number,
                                            "caption": table["caption"], "kind": "table"})

            # ── 3. captioned figures / tables / algorithms → crop + vision ────
            used_rects = []
            for caption_index, caption in enumerate(captions):
                kind = caption["kind"]
                if kind not in ("figure", "table", "algorithm"):
                    continue
                # a table already captured as Markdown does not need a second pass
                if kind == "table" and caption["label"] in table_labels_done:
                    continue

                region = region_for_caption(page, caption["rect"], kind, rects)
                png = crop_png(page, region)
                if not png:
                    continue
                used_rects.append(region)

                print(f"    Reading {pretty_label(caption['label'])} "
                      f"({int(region.width)}×{int(region.height)}pt)...")
                description = describe_element(png, source, page_number,
                                               caption["label"], caption["caption"])
                if not description:
                    continue

                image_path = save_figure_image(png, source, page_number,
                                               caption["label"], caption_index)
                content_type = {"figure": "figure_description", "table": "table",
                                "algorithm": "algorithm"}[kind]
                name = pretty_label(caption["label"])
                writer.add(
                    f"[{content_type.upper()} — {name} — {source}, Page {page_number}]\n"
                    f"Caption: {caption['caption']}\n\n{description}",
                    {
                        "page": page_number,
                        "page_index": page_index,
                        "content_type": content_type,
                        "element_label": caption["label"],
                        "caption": caption["caption"],
                        "image_path": image_path,
                        "loader": "qwen_vision",
                        "chunk": -1,
                    })
                writer.elements.append({"label": caption["label"], "page": page_number,
                                        "caption": caption["caption"], "kind": kind})

            # ── 4. images with no caption at all ──────────────────────────────
            if C.VISION_ON_PAGES_WITHOUT_CAPTION:
                extra = 0
                for rect in rects:
                    if extra >= C.MAX_FIGURES_PER_PAGE:
                        break
                    if rect.width < 120 or rect.height < 90:
                        continue
                    if any(_horizontal_overlap(rect, used) > 0.5
                           and abs(rect.y0 - used.y0) < 40 for used in used_rects):
                        continue
                    png = crop_png(page, rect)
                    if not png:
                        continue
                    extra += 1
                    print(f"    Reading unlabelled visual #{extra} on page {page_number}...")
                    description = describe_element(png, source, page_number, "", "")
                    if not description:
                        continue
                    image_path = save_figure_image(png, source, page_number,
                                                   f"unlabelled{extra}", extra)
                    writer.add(
                        f"[FIGURE_DESCRIPTION — unlabelled visual {extra} — "
                        f"{source}, Page {page_number}]\n\n{description}",
                        {
                            "page": page_number,
                            "page_index": page_index,
                            "content_type": "figure_description",
                            "element_label": "",
                            "image_path": image_path,
                            "loader": "qwen_vision",
                            "chunk": -1,
                        })
                    used_rects.append(rect)

            # ── 5. equations ──────────────────────────────────────────────────
            if page_has_equations(raw_text):
                page_png = crop_png(page, page.rect, dpi=150)
                equations = describe_equations(page_png, source, page_number) if page_png else None
                if equations:
                    labels = find_labels(equations)
                    writer.add(
                        f"[EQUATIONS — {source}, Page {page_number}]\n\n{equations}",
                        {
                            "page": page_number,
                            "page_index": page_index,
                            "content_type": "equation",
                            "element_label": labels[0] if labels else "",
                            "loader": "qwen_vision",
                            "chunk": -2,
                        })
                    for label in labels:
                        if label.startswith("equation"):
                            writer.elements.append({"label": label, "page": page_number,
                                                    "caption": "", "kind": "equation"})
    finally:
        pages.close()          # closes the PDF even if processing stopped half-way

    add_document_card(writer, source, title_meta, first_pages, page_count)


def add_document_card(writer: ChunkWriter, source: str, title_meta: str,
                      first_pages: List[str], page_count: int):
    text = "\n\n".join(first_pages)
    title = (title_meta or "").strip()
    junk = ("untitled", "anonymous", "(anonymous)", "unknown", "title", "document")
    if len(title) < 6 or title.lower() in junk or title.lower().startswith("microsoft word") \
            or title.lower().endswith((".doc", ".docx", ".pdf", ".tex")):
        title = ""
        for line in text.split("\n"):
            candidate = re.sub(r"[*_#]", "", line).strip()
            if len(candidate) > 8:
                title = candidate[:200]
                break

    match = re.search(r"abstract[\s*:.—\-]*\n?(.{100,1800}?)(?:\n#|\n\s*\n\s*(?:\d\.?\s+)?introduction|$)",
                      text, re.IGNORECASE | re.DOTALL)
    abstract = (match.group(1) if match else text[:1200]).strip()

    seen, inventory = set(), []
    for element in sorted(writer.elements, key=lambda e: (e["page"], e["label"])):
        key = (element["label"], element["page"])
        if key in seen:
            continue
        seen.add(key)
        caption = f" — {element['caption'][:120]}" if element.get("caption") else ""
        inventory.append(f"- {pretty_label(element['label'])} (page {element['page']}){caption}")

    card = (f"[DOCUMENT CARD — {source} — {page_count} page(s)]\n"
            f"File: {source}\nTitle: {title}\n\nAbstract / opening text:\n{abstract}\n\n"
            f"Labelled elements in this document ({len(inventory)}):\n" + "\n".join(inventory[:300]))
    writer.add(card, {"page": 1, "page_index": 0, "content_type": "document_card",
                      "title": title[:300], "page_count": page_count, "chunk": -4,
                      "loader": "summary"})


def ingest_plain_file(file_path: str, source: str, writer: ChunkWriter):
    """TXT / MD / DOCX: each document yielded by lazy_load() is processed as it arrives."""
    extension = os.path.splitext(file_path)[1].lower()
    opening_text = ""
    chunk_index = 0
    for raw in lazy_load_plain_documents(file_path):
        text = clean_markdown_page(raw)
        if len(opening_text) < 4000:
            opening_text += text[: 4000 - len(opening_text)]
        body, tables = extract_markdown_tables(text)
        for chunk in chunk_text(body):
            writer.add(f"[{source} | Page 1]\n{chunk}",
                       {"page": 1, "page_index": 0, "chunk": chunk_index,
                        "content_type": "text", "loader": extension.lstrip(".")})
            chunk_index += 1
        for table in tables:
            name = pretty_label(table["label"]) or "Table"
            writer.add(f"[TABLE — {name} — {source}]\nCaption: {table['caption']}\n\n{table['markdown']}",
                       {"page": 1, "page_index": 0, "content_type": "table",
                        "element_label": table["label"], "caption": table["caption"],
                        "loader": "markdown_table", "chunk": -3})
    add_document_card(writer, source, "", [opening_text], 1)


# ─────────────────────────────────────────────────────────────────────────────
# ONE FILE
# ─────────────────────────────────────────────────────────────────────────────
def ingest_one_file(file_path: str, collection) -> Dict:
    source = os.path.basename(file_path)
    print("\n" + "-" * 70 + f"\nIngesting: {source}\n" + "-" * 70)
    started = time.time()
    writer = ChunkWriter(collection, source)

    if file_path.lower().endswith(".pdf"):
        ingest_pdf(file_path, source, writer)
    else:
        ingest_plain_file(file_path, source, writer)

    writer.flush()
    elapsed = time.time() - started
    print(f"  Done in {elapsed:.0f}s — {writer.counter} chunks {writer.stats}")
    return {"chunks": writer.counter, **writer.stats}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def check_setup() -> bool:
    try:
        get_collection()
        embed_texts(["connection test"])
        return True
    except Exception as e:
        print(f"\nSetup problem — stopping before ingestion: {e}")
        print("Check that LM Studio's server is running, both models are loaded, "
              "and EMBEDDING_MODEL matches http://localhost:1234/v1/models")
        return False


def ingest_documents(force_full: bool = False):
    os.makedirs(C.DOCUMENTS_DIR, exist_ok=True)
    if not check_setup():
        return

    manifest = load_manifest()
    fingerprint = settings_fingerprint()
    if manifest.get(SETTINGS_KEY) and manifest[SETTINGS_KEY] != fingerprint and not force_full:
        print("Ingestion settings changed → full re-ingestion required.")
        force_full = True

    print("=" * 70)
    print("     CAPTION-DRIVEN MULTIMODAL INGESTION")
    print(f"     Mode        : {'FULL RESET' if force_full else 'INCREMENTAL'}")
    print(f"     Text+Tables : pymupdf4llm (Markdown)")
    print(f"     Figures     : cropped by caption + {C.LLM_MODEL} vision")
    print(f"     Embeddings  : {C.EMBEDDING_MODEL}")
    print("=" * 70)

    if force_full:
        try:
            get_chroma_client().delete_collection(C.COLLECTION_NAME)
            print(f"  Deleted collection {C.COLLECTION_NAME}")
        except Exception:
            pass
        if os.path.exists(MANIFEST_PATH):
            os.remove(MANIFEST_PATH)
        manifest = {}

    collection = get_collection()
    manifest[SETTINGS_KEY] = fingerprint

    files = {os.path.basename(f): f for f in get_document_files()}
    if not files:
        print(f"\nNo documents found in {C.DOCUMENTS_DIR}")
        save_manifest(manifest)
        return

    for source in [s for s in manifest if s != SETTINGS_KEY and s not in files]:
        print(f"  − REMOVED    {source}")
        remove_source_chunks(collection, source)
        del manifest[source]
    save_manifest(manifest)

    counts = {"new": 0, "changed": 0, "skipped": 0, "failed": 0}
    for source, file_path in files.items():
        file_hash = get_file_hash(file_path)
        record = manifest.get(source)
        if record and record.get("hash") == file_hash:
            print(f"  ✓ UNCHANGED  {source} ({record.get('chunks', '?')} chunks)")
            counts["skipped"] += 1
            continue
        if record:
            print(f"  ↻ CHANGED    {source}")
            counts["changed"] += 1
        else:
            print(f"  + NEW        {source}")
            counts["new"] += 1
        remove_source_chunks(collection, source)
        try:
            summary = ingest_one_file(file_path, collection)
        except KeyboardInterrupt:
            remove_source_chunks(collection, source)
            raise
        except Exception as e:
            print(f"  ✗ FAILED {source}: {e}")
            remove_source_chunks(collection, source)
            manifest.pop(source, None)
            save_manifest(manifest)
            counts["failed"] += 1
            continue
        manifest[source] = {"hash": file_hash, "size": os.path.getsize(file_path),
                            "ingested_at": time.strftime("%Y-%m-%d %H:%M:%S"), **summary}
        save_manifest(manifest)

    print("\n" + "=" * 70)
    print(f"New: {counts['new']} | Changed: {counts['changed']} | "
          f"Unchanged: {counts['skipped']} | Failed: {counts['failed']}")
    print(f"Total chunks in collection: {collection.count()}")
    print(f"Figure images: {C.FIGURE_IMAGE_DIR}")
    print("Restart the Streamlit app (or press 'Reload index') to use the new data.")
    print("=" * 70)


if __name__ == "__main__":
    ingest_documents(force_full="--reset" in sys.argv)