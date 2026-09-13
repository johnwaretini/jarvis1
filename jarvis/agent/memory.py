"""memory.py — the ONLY writer in JARVIS, and it writes to memory/ ONLY.

One remembered fact per dated markdown file. Nothing here can touch the vault,
the inbox, or anywhere else on disk. Every write returns the exact text and
path so the caller can say out loud what it wrote — the guardrail forbids
writing to memory silently.
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEMORY_DIR = HERE.parent / "memory"


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:48] or "note").rstrip("-")


def remember(fact: str) -> dict:
    """Write one fact to its own dated file under memory/. Returns what was
    written so the assistant can read it back verbatim."""
    fact = fact.strip()
    if not fact:
        return {"ok": False, "error": "nothing to remember"}
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    base = f"{today}-{_slug(fact)}"
    path = MEMORY_DIR / f"{base}.md"
    n = 2
    while path.exists():
        path = MEMORY_DIR / f"{base}-{n}.md"
        n += 1
    # Guardrail: memory writes stay strictly inside MEMORY_DIR.
    if MEMORY_DIR.resolve() not in path.resolve().parents:
        return {"ok": False, "error": "refused: path escapes memory/"}
    content = f"---\nremembered: {now}\n---\n\n{fact}\n"
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(path), "filename": path.name,
            "fact": fact, "when": now}


def recall(limit: int = 50) -> list[dict]:
    """Read remembered facts back, newest first."""
    if not MEMORY_DIR.exists():
        return []
    out = []
    for p in sorted(MEMORY_DIR.glob("*.md"), reverse=True)[:limit]:
        text = p.read_text(encoding="utf-8", errors="replace")
        body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL).strip()
        out.append({"filename": p.name, "fact": body})
    return out
