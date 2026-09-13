"""data.py — THE ONLY FILE THAT TOUCHES REAL DATA.

One environment variable decides everything:

    JARVIS_DEMO=1  (default)  -> index the invented fixtures in data/vault/
    JARVIS_DEMO=0             -> index the real folders listed in REAL_FOLDERS

You have to opt IN to your real life. A fresh clone, or a forgotten env var,
reads demo data — never your actual files.

Nothing in the project imports real folder paths except this module. If you
ever want to audit what JARVIS can read, read exactly this file.
"""
from __future__ import annotations

import os
from pathlib import Path

from vault import Vault, build_vault

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DEMO_VAULT = PROJECT / "data" / "vault"

# --- Your real folders -----------------------------------------------------
# Only read when JARVIS_DEMO=0. Absolute paths. Edit this list to point at
# your real notes. Leave it as-is to stay in demo mode safely.
#
# Example:
#   REAL_FOLDERS = [
#       "/Users/me/Documents/Clients",
#       "/Users/me/Notes",
#   ]
REAL_FOLDERS: list[str] = [
    # (none configured yet — add your absolute paths here, then set JARVIS_DEMO=0)
]


def is_demo() -> bool:
    return os.environ.get("JARVIS_DEMO", "1").strip() != "0"


def data_roots() -> list[Path]:
    """The folders the indexer will read. Demo vault, or your real folders."""
    if is_demo():
        return [DEMO_VAULT]
    roots = [Path(p).expanduser() for p in REAL_FOLDERS]
    return [r for r in roots if r]


def describe_source() -> dict:
    """Human-facing summary of what is being indexed and why."""
    if is_demo():
        return {
            "mode": "demo",
            "reason": "JARVIS_DEMO is not 0 — reading invented fixtures, safe to screen-record.",
            "roots": [str(DEMO_VAULT)],
        }
    return {
        "mode": "real",
        "reason": "JARVIS_DEMO=0 — reading your configured real folders, read-only.",
        "roots": [str(p) for p in data_roots()],
    }


def load_vault() -> Vault:
    roots = data_roots()
    return build_vault(roots)


def _report() -> None:
    """`python3 agent/data.py` — Step 1: index the folders and print findings."""
    src = describe_source()
    print(f"MODE: {src['mode']} — {src['reason']}")
    for r in src["roots"]:
        print(f"  root: {r}")
    if not src["roots"]:
        print("  (no folders configured — set REAL_FOLDERS and JARVIS_DEMO=0,"
              " or run data/generate.py for demo data)")
        return
    v = load_vault()
    print(f"\nIndexed {len(v.notes)} notes, {len(v.graph_payload()['edges'])} links.\n")
    print("Counts by type:")
    for t, c in v.type_counts().items():
        print(f"  {t:10s} {c}")
    print("\nTop 10 hubs (by connection count):")
    for n in v.hubs(10):
        print(f"  {n.degree:3d}  {n.type:9s} {n.title}")
    unreadable = [n for n in v.notes.values() if n.note]
    if unreadable:
        print(f"\n{len(unreadable)} file(s) indexed by name only:")
        for n in unreadable:
            print(f"  - {n.title}: {n.note}")


if __name__ == "__main__":
    _report()
