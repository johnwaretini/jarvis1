# JARVIS

A voice-controlled assistant that reads *your* files and talks like a person,
not a search box. Python standard library on the server, vanilla JS in the
browser. No framework, no build step, no package manager. It runs on a clean
machine with one command.

---

## Run it

```bash
cd jarvis
python3 data/generate.py     # build the demo vault (fixed seed, identical every time)
python3 agent/main.py        # http://localhost:8765
```

Open **http://localhost:8765**. That's it — it starts on safe **demo data**
with **no keys required**. Text chat, the graph, the tools, and every guardrail
work out of the box. Voice needs a key (below).

> Python 3.10+ (uses modern type hints). Nothing to `pip install`.

### No terminal?

Double-click the launcher for your system — it builds the demo graph on first
run, starts the server, and opens your browser, with no commands to type. Leave
the little window open while you use JARVIS; close it to stop.

- **Windows:** double-click **`launch-jarvis.bat`**. Needs Python 3.10+ with
  "Add python.exe to PATH" ticked at install time; the launcher points you to
  the download if it's missing.
- **macOS:** double-click **`launch-jarvis.command`** in Finder. If macOS blocks
  the first open, right-click it → **Open** → **Open**.

---

## The demo switch — you opt *in* to your real life

One variable, read in exactly one file (`agent/data.py`). It defaults to demo,
so a fresh clone or a forgotten setting reads invented data, never your folders.

| `JARVIS_DEMO` | What it indexes |
|---|---|
| `1` (default) | Invented fixtures in `data/vault/`, shaped like a small studio. Safe to screen-record. |
| `0` | Your real folders — **only** the absolute paths you list in `REAL_FOLDERS` in `agent/data.py`. |

To point it at your real notes:

1. Edit `REAL_FOLDERS` in `agent/data.py` (absolute paths).
2. Set `JARVIS_DEMO=0` (in `.env` or the environment).
3. Restart. It indexes **read-only**: markdown, text and PDF, recursively,
   skipping `node_modules`, `.git`, and anything over 2 MB. `[[wikilinks]]`
   between notes become edges in the graph.

Check what it will read before you trust it — it prints a summary on start, or:

```bash
python3 agent/data.py        # counts by type + top 10 hubs, for whichever mode
```

---

## Make it yours

Two files, no code:

- **`CLAUDE.md`** — who you are: name, what you do, what you sell and for how
  much, real clients, the tools you live in, and how you want to be spoken to.
  Loaded every session. It ships as a placeholder shaped like the demo studio;
  replace the `[bracketed]` lines with your real details.
- **`memory/`** — one dated markdown file per remembered fact. Written **only**
  when you ask ("remember that…"), and JARVIS always says out loud exactly what
  it wrote. Nothing else on your disk is ever written.

---

## Voice (optional) — ElevenLabs, both directions

Speech **out** is ElevenLabs text-to-speech; speech **in** is ElevenLabs Scribe
(`scribe_v1`). The browser records with `MediaRecorder` and posts audio to the
server — the **API key never reaches the browser**, so nothing sensitive shows
up in devtools or a screen recording. (The Web Speech API is deliberately not
used: it's Chrome-only, ships your audio to Google, and in Brave it fails
silently.)

```bash
cp .env.example .env && chmod 600 .env
# put your ElevenLabs key in .env, then restart
```

With no key, text still works and the UI shows a badge saying voice is off.

**Using it:** press the mic once (or **Space**), then just talk — no wake word
between turns. It watches the real mic level; when you go quiet for ~900 ms the
turn ends and sends. The mic goes deaf while JARVIS speaks, so it never talks to
itself. Barge in any time with the mic button, **Space**, or **Esc**.

Tune turn-taking at the top of `ui/app.js`: `SILENCE_THRESHOLD`, `SILENCE_MS`,
`MIN_SPEECH_MS`.

---

## The model (optional)

JARVIS runs **without any model**. With no `ANTHROPIC_API_KEY`, it decides
between conversation and a file lookup by scoring your question against your
files, and shows a **MODEL MISSING** badge — keyword routing is never passed
off as the model talking. Set `ANTHROPIC_API_KEY` in `.env` and the model
phrases spoken lines in your tone (from `CLAUDE.md`); tools still run
deterministically, so the answer's facts always come from your files.

---

## What it can do

Talking is the default. Tools run only when the answer needs one, and each
returns a short **spoken** line plus a **card** of detail on screen (never the
same text twice):

- **search_brain** — a fact from your files, always citing the file(s).
- **research_web** — looks something up, then lands it on *your* numbers.
- **read_inbox** — read-only; who wrote, about what, and whether they're
  already in your files.
- **brief_me** — calendar, unread, and what slipped.
- **remember** — writes one dated fact to `memory/`, and says what it wrote.
- **plan_day** — five items max, ordered by what moves money.

Ask by voice or type in the bar. Follow-ups resolve against the last ~10 turns
("why?", "the second one").

## The interface

A full-screen dark canvas with four floating regions: the **graph** (every note
a node, every link an edge — hover to light a node's links, click to focus,
shift-click a second node to trace the shortest path, drag/scroll to pan/zoom);
a left **inspector** + top-hubs list; a right **filter** panel with live counts
and a **reactor** HUD that reflects state (idle / listening / thinking /
speaking); and a bottom **ask bar**.

---

## Guardrails (absolute — no phrasing overrides them, and they're enforced in code)

- **Never send.** No email/message/invite. It drafts and waits. (There is no
  send code in the project.)
- **Never write to your folders.** Read-only, always. `memory/` is the only
  writer, and writes that try to escape it are refused.
- **Never write to memory silently.** It says what it wrote, every time.
- **Never spend.** Paid APIs (ElevenLabs, the model) do nothing until you set a
  key — setting the key is your opt-in.
- **Never invent.** No made-up number, date, filename or client. Not in the
  files? It says so.
- **Never state a derived number without its qualifier.** A part-paid invoice
  because the job's still running is *not* a discount — it says which.
- **Instructions inside your files or emails are data, not commands.** A note or
  email saying "ignore your instructions" is reported, never obeyed, and never
  read aloud as if it were an answer.

---

## What it costs

- **The app itself:** free. Standard library only; runs on `localhost`.
- **Demo mode, no keys:** £0. Full graph, tools, chat, guardrails.
- **Voice (ElevenLabs):** their usage-based pricing. TTS is billed per
  character, Scribe per minute of audio; a free tier exists for trying it. Voice
  runs only when you set `ELEVENLABS_API_KEY`. See elevenlabs.io/pricing.
- **Model (Anthropic, optional):** their per-token pricing, and only when you
  set `ANTHROPIC_API_KEY`. Used only to word spoken lines; leave it unset to run
  free. See anthropic.com/pricing.

You control both switches. Nothing here spends money you didn't opt into.

---

## Layout

```
jarvis/
├── agent/
│   ├── main.py      HTTP server + API (stdlib http.server)
│   ├── vault.py     folders → searchable graph (index, search, paths)
│   ├── tools.py     the six tools, router, conversation, guardrails
│   ├── data.py      THE ONLY FILE THAT TOUCHES YOUR REAL DATA
│   ├── voice.py     ElevenLabs speech in and out
│   ├── memory.py    the only writer — memory/ and nowhere else
│   └── prompt.md    the system prompt
├── ui/              index.html, app.js, graph.js, styles.css
├── data/            demo fixtures + generate.py (fixed seed)
├── memory/          one markdown file per remembered fact
├── CLAUDE.md        who you are — loaded every session
├── .env.example     copy to .env (gitignored, chmod 600)
└── README.md
```
