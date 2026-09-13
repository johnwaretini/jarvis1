"""main.py — JARVIS HTTP server + API.

Standard library only. No framework, no build step. One command:

    python3 agent/main.py         # http://localhost:8765

Routes (built up across the build steps):
    GET  /                 -> the UI
    GET  /ui/<file>        -> static assets
    GET  /api/source       -> demo/real + model availability (for the badges)
    GET  /api/graph        -> {nodes, edges, types} for the canvas
    GET  /api/note?id=     -> one note's detail for the inspector
    GET  /api/path?a=&b=   -> shortest path between two nodes
    POST /api/ask          -> conversation + tools        (step 3)
    POST /api/speak        -> ElevenLabs TTS, mp3 bytes   (step 4)
    POST /api/listen       -> ElevenLabs Scribe transcript (step 4)

The server holds all keys. No key ever reaches the browser.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
UI_DIR = PROJECT / "ui"

sys.path.insert(0, str(HERE))
import data  # noqa: E402  (local module, after sys.path tweak)

# Optional later-step modules — import lazily so step 2 runs before they exist.
try:
    import tools as tools_mod  # noqa: E402
except Exception:
    tools_mod = None
try:
    import voice as voice_mod  # noqa: E402
except Exception:
    voice_mod = None

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_PORT", "8765"))

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Jarvis:
    """Holds the indexed vault so we build the graph once, not per request."""

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        self.vault = data.load_vault()
        self.source = data.describe_source()

    def model_status(self) -> dict:
        """Whether an LLM is reachable. Step 3 wires the real check; for now
        report what the environment implies so the UI can show the badge."""
        if tools_mod is not None and hasattr(tools_mod, "model_status"):
            return tools_mod.model_status()
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return {
            "available": has_key,
            "reason": "ANTHROPIC_API_KEY set" if has_key
            else "no model key — routing falls back to file-scoring",
        }

    def voice_status(self) -> dict:
        if voice_mod is not None and hasattr(voice_mod, "available"):
            return voice_mod.available()
        return {"available": False, "reason": "voice module not loaded"}


JARVIS = Jarvis()


class Handler(BaseHTTPRequestHandler):
    server_version = "JARVIS/0.2"

    # --- helpers ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), MIME[".json"])

    def _error(self, code: int, msg: str) -> None:
        # Degrade loudly: the UI shows this text, never a silent blank.
        self._json({"error": msg}, code)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        return self.rfile.read(length) if length else b""

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))

    # --- static ----
    def _serve_file(self, path: Path) -> None:
        if not path.is_file():
            self._error(404, f"not found: {path.name}")
            return
        ctype = MIME.get(path.suffix.lower(), "application/octet-stream")
        self._send(200, path.read_bytes(), ctype)

    # --- routing ----
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        qs = parse_qs(parsed.query)
        try:
            if route == "/" or route == "/index.html":
                self._serve_file(UI_DIR / "index.html")
            elif route.startswith("/ui/"):
                rel = route[len("/ui/"):]
                target = (UI_DIR / rel).resolve()
                if UI_DIR.resolve() not in target.parents:
                    self._error(403, "forbidden")
                    return
                self._serve_file(target)
            elif route == "/api/source":
                self._json({**JARVIS.source, "model": JARVIS.model_status(),
                            "voice": JARVIS.voice_status(),
                            "count": len(JARVIS.vault.notes)})
            elif route == "/api/graph":
                self._json(JARVIS.vault.graph_payload())
            elif route == "/api/note":
                nid = (qs.get("id") or [""])[0].lower()
                note = JARVIS.vault.notes.get(nid)
                if not note:
                    self._error(404, f"no note: {nid}")
                    return
                pub = note.to_public()
                pub["body"] = note.clean_body()[:4000]
                self._json(pub)
            elif route == "/api/path":
                a = (qs.get("a") or [""])[0]
                b = (qs.get("b") or [""])[0]
                self._json({"path": JARVIS.vault.shortest_path(a, b)})
            else:
                self._error(404, f"no route: {route}")
        except BrokenPipeError:
            pass
        except Exception as exc:  # never die silently
            self._error(500, f"server error: {exc}")

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            if route == "/api/ask":
                self._handle_ask()
            elif route == "/api/speak":
                self._handle_speak()
            elif route == "/api/listen":
                self._handle_listen()
            else:
                self._error(404, f"no route: {route}")
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._error(500, f"server error: {exc}")

    # --- API stubs, filled in by later steps ----
    def _handle_ask(self) -> None:
        if tools_mod is None or not hasattr(tools_mod, "handle_ask"):
            self._error(503, "conversation not wired yet (build step 3)")
            return
        payload = json.loads(self._read_body() or b"{}")
        self._json(tools_mod.handle_ask(JARVIS.vault, payload))

    def _handle_speak(self) -> None:
        if voice_mod is None or not hasattr(voice_mod, "speak"):
            self._error(503, "voice not wired yet (build step 4)")
            return
        payload = json.loads(self._read_body() or b"{}")
        audio, err = voice_mod.speak(payload.get("text", ""))
        if err:
            self._error(502, err)
            return
        self._send(200, audio, "audio/mpeg")

    def _handle_listen(self) -> None:
        if voice_mod is None or not hasattr(voice_mod, "listen"):
            self._error(503, "voice not wired yet (build step 4)")
            return
        transcript, err = voice_mod.listen(self._read_body(),
                                           self.headers.get("Content-Type", ""))
        if err:
            self._error(502, err)
            return
        self._json({"transcript": transcript})


def main() -> None:
    src = JARVIS.source
    print(f"JARVIS — mode: {src['mode']} ({len(JARVIS.vault.notes)} notes indexed)")
    print(f"  {src['reason']}")
    ms = JARVIS.model_status()
    print(f"  model: {'available' if ms['available'] else 'MISSING'} — {ms['reason']}")
    print(f"\n  http://localhost:{PORT}\n")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
