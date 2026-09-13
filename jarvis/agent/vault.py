"""vault.py — turn a set of folders into a searchable graph.

Reads markdown, text and PDF, RECURSIVELY and READ-ONLY. One node per file,
one edge per [[wikilink]]. Connection count drives node size; a tiny TF-style
scorer drives search.

Nothing here writes to the indexed folders. The only module allowed to name a
real folder is data.py — this module just indexes whatever paths it is handed.

PDF support is pure stdlib (zlib on FlateDecode streams) and therefore
best-effort: a PDF it cannot read is still indexed by filename, with its body
left empty and a note left on the node, rather than crashing the index.
"""
from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024  # skip anything over 2 MB, per spec
SKIP_DIRS = {"node_modules", ".git", ".obsidian", ".trash", "__pycache__", ".venv", "venv"}
TEXT_EXT = {".md", ".markdown", ".txt"}
PDF_EXT = {".pdf"}

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
FRONT_BLOCK = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
LEAD_H1 = re.compile(r"^\s*#\s+.*\n")
FRONT_TYPE = re.compile(r"^---\s*\n.*?\btype:\s*([^\n]+).*?\n---", re.DOTALL)
FRONT_TITLE = re.compile(r"^---\s*\n.*?\btitle:\s*([^\n]+).*?\n---", re.DOTALL)
WORD = re.compile(r"[a-z0-9£$]+")


@dataclass
class Note:
    id: str               # stem, lowercased — what wikilinks resolve against
    title: str            # display name
    path: str             # absolute path on disk
    type: str             # client / project / meeting / ... or "file"
    body: str             # extracted text (may be empty for unreadable pdf)
    links: list[str] = field(default_factory=list)   # outgoing, resolved ids
    raw_links: list[str] = field(default_factory=list)  # as written
    degree: int = 0       # total connections (in + out), for node radius
    note: str = ""        # indexing remark, e.g. "pdf: could not extract text"

    def clean_body(self) -> str:
        """Body without YAML frontmatter or the redundant leading # heading."""
        text = FRONT_BLOCK.sub("", self.body, count=1)
        text = LEAD_H1.sub("", text, count=1)
        return text.strip()

    def to_public(self) -> dict:
        """Card-safe view: no absolute path leaks to the browser."""
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "degree": self.degree,
            "links": self.links,
            "excerpt": self.clean_body()[:280],
            "note": self.note,
        }


# --- PDF: minimal pure-stdlib text extraction ------------------------------

def _pdf_text(data: bytes) -> str:
    """Best-effort text out of a PDF using only zlib. Returns "" on failure."""
    chunks: list[str] = []
    # Pull every stream, inflate the FlateDecode ones, scan for text operators.
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        raw = m.group(1)
        content = None
        try:
            content = zlib.decompress(raw)
        except Exception:
            # Some streams are not compressed, or use a filter we don't do.
            if b"Tj" in raw or b"TJ" in raw:
                content = raw
        if content is None:
            continue
        chunks.append(_pdf_ops_to_text(content))
    text = "\n".join(c for c in chunks if c).strip()
    return text


def _pdf_ops_to_text(content: bytes) -> str:
    try:
        s = content.decode("latin-1", "ignore")
    except Exception:
        return ""
    out: list[str] = []
    # (literal) Tj
    for lit in re.findall(r"\(((?:[^()\\]|\\.)*)\)\s*Tj", s):
        out.append(_unescape_pdf(lit))
    # [ (a) (b) ] TJ  — array showing
    for arr in re.findall(r"\[(.*?)\]\s*TJ", s, re.DOTALL):
        parts = re.findall(r"\(((?:[^()\\]|\\.)*)\)", arr)
        out.append("".join(_unescape_pdf(p) for p in parts))
    return " ".join(t for t in out if t.strip())


def _unescape_pdf(s: str) -> str:
    return (
        s.replace(r"\(", "(").replace(r"\)", ")")
        .replace(r"\\", "\\").replace(r"\n", "\n").replace(r"\r", "\r")
        .replace(r"\t", "\t")
    )


# --- Reading a single file -------------------------------------------------

def _read_note(path: Path) -> Note | None:
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_BYTES:
        return None

    node_id = path.stem.lower()
    title = path.stem
    body = ""
    ntype = "file"
    remark = ""

    if ext in TEXT_EXT:
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        mt = FRONT_TYPE.search(body)
        if mt:
            ntype = mt.group(1).strip()
        else:
            ntype = path.parent.name.rstrip("s") or "file"
        mtl = FRONT_TITLE.search(body)
        if mtl:
            title = mtl.group(1).strip()
    elif ext in PDF_EXT:
        ntype = "pdf"
        try:
            body = _pdf_text(path.read_bytes())
        except OSError:
            return None
        if not body:
            remark = "pdf: could not extract text (indexed by filename only)"
    else:
        return None

    raw_links = [m.strip() for m in WIKILINK.findall(body)]
    return Note(id=node_id, title=title, path=str(path), type=ntype,
                body=body, raw_links=raw_links, note=remark)


# --- The vault -------------------------------------------------------------

class Vault:
    def __init__(self, notes: dict[str, Note]):
        self.notes = notes
        self._index_terms()

    # ---- search ----
    def _index_terms(self) -> None:
        self._terms: dict[str, list[tuple[str, int]]] = {}
        for note in self.notes.values():
            counts: dict[str, int] = {}
            for w in WORD.findall((note.title + " " + note.body).lower()):
                if len(w) < 2:
                    continue
                counts[w] = counts.get(w, 0) + 1
            for w, c in counts.items():
                self._terms.setdefault(w, []).append((note.id, c))

    def search(self, query: str, limit: int = 8) -> list[tuple[Note, float]]:
        """Score notes against the query. Used by search_brain AND by the
        model-free router to decide conversation vs. lookup."""
        q_terms = [w for w in WORD.findall(query.lower()) if len(w) >= 2]
        if not q_terms:
            return []
        scores: dict[str, float] = {}
        n = max(1, len(self.notes))
        for w in q_terms:
            postings = self._terms.get(w)
            if not postings:
                continue
            idf = 1.0 + (n / (1 + len(postings)))
            for nid, c in postings:
                scores[nid] = scores.get(nid, 0.0) + c * idf
        # small bump for title hits
        for nid, note in self.notes.items():
            tl = note.title.lower()
            for w in q_terms:
                if w in tl:
                    scores[nid] = scores.get(nid, 0.0) + 5.0
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out = []
        for nid, sc in ranked[:limit]:
            out.append((self.notes[nid], sc))
        return out

    # ---- graph ----
    def hubs(self, limit: int = 10) -> list[Note]:
        return sorted(self.notes.values(), key=lambda x: x.degree, reverse=True)[:limit]

    def type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for note in self.notes.values():
            counts[note.type] = counts.get(note.type, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def shortest_path(self, a: str, b: str) -> list[str]:
        """BFS over the undirected link graph. Returns node ids, or []."""
        a, b = a.lower(), b.lower()
        if a not in self.notes or b not in self.notes:
            return []
        adj: dict[str, set[str]] = {nid: set() for nid in self.notes}
        for nid, note in self.notes.items():
            for t in note.links:
                if t in adj:
                    adj[nid].add(t)
                    adj[t].add(nid)
        from collections import deque
        seen = {a}
        q = deque([[a]])
        while q:
            path = q.popleft()
            if path[-1] == b:
                return path
            for nxt in adj[path[-1]]:
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(path + [nxt])
        return []

    def graph_payload(self) -> dict:
        """Everything the browser needs to draw the graph, path-free."""
        nodes = [n.to_public() for n in self.notes.values()]
        edges = []
        seen = set()
        for nid, note in self.notes.items():
            for t in note.links:
                key = tuple(sorted((nid, t)))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": nid, "target": t})
        return {"nodes": nodes, "edges": edges, "types": self.type_counts()}


def _resolve_links(notes: dict[str, Note]) -> None:
    """Turn raw [[names]] into edges to real node ids where they exist.

    Matches by id (stem) first, then by case-insensitive title.
    """
    by_title: dict[str, str] = {}
    for nid, note in notes.items():
        by_title.setdefault(note.title.lower(), nid)
    for note in notes.values():
        resolved: list[str] = []
        for raw in note.raw_links:
            key = raw.lower()
            target = None
            if key in notes:
                target = key
            elif key in by_title:
                target = by_title[key]
            else:
                # wikilinks often omit the extension / use spaces for dashes
                alt = key.replace(" ", "-")
                if alt in notes:
                    target = alt
            if target and target != note.id and target not in resolved:
                resolved.append(target)
        note.links = resolved
    # degree = unique undirected neighbours
    neigh: dict[str, set[str]] = {nid: set() for nid in notes}
    for nid, note in notes.items():
        for t in note.links:
            neigh[nid].add(t)
            neigh[t].add(nid)
    for nid, note in notes.items():
        note.degree = len(neigh[nid])


def build_vault(roots: list[Path]) -> Vault:
    """Index the given folders into a Vault. READ-ONLY."""
    notes: dict[str, Note] = {}
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            note = _read_note(path)
            if note is None:
                continue
            # last one wins on id collision, but keep it stable by sorting
            notes[note.id] = note
    _resolve_links(notes)
    return Vault(notes)
