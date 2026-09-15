"""tools.py — conversation, routing, and the six tools.

Design decisions that matter:

* JARVIS is a person who happens to have tools. Talking is the default; a tool
  runs only when the answer needs one. Greetings and small talk never hit a
  tool and never return a search result.

* The route (talk vs. which tool) is decided by scoring the question against the
  files. This works with NO model, so the fallback in the brief is the primary
  path, not a degraded afterthought. A badge tells the UI when no model is
  reachable — keyword routing is never dressed up as the model talking.

* When a model key IS present (the user opted in by setting it), the model
  phrases the spoken line in the user's tone. When it isn't, phrasing is
  templated and the badge shows it.

* Every tool returns TWO things: a short `spoken` line and a structured `card`.
  They are never the same text.

* Guardrails are enforced here in code, not just asked for in the prompt:
  memory is the only writer, nothing sends, injection is reported not obeyed,
  and derived numbers always carry their qualifier.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import data
import memory as memory_mod

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

# ---- persona -------------------------------------------------------------

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""

CLAUDE_MD = _read(PROJECT / "CLAUDE.md")
PROMPT_MD = _read(HERE / "prompt.md")

def _persona_name() -> str:
    m = re.search(r"\*\*Name:\*\*\s*(.+)", CLAUDE_MD)
    if m:
        name = m.group(1).strip()
        if name and not name.startswith("["):
            return name
    return ""

# ---- model availability --------------------------------------------------

_MODEL_RUNTIME_OK = True   # flipped off if a live call fails, so we degrade loudly

# The phrasing model only rewords the spoken line in the user's tone; routing
# (talk vs. which tool) is ALWAYS file-scoring, with or without a model. Two
# providers are supported: Anthropic (paid) and Google Gemini (free tier).
def _provider() -> str:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    return ""

def model_status() -> dict:
    prov = _provider()
    if not prov:
        return {"available": False, "provider": None,
                "reason": "no model key — spoken lines use built-in phrasing"}
    if not _MODEL_RUNTIME_OK:
        return {"available": False, "provider": prov,
                "reason": f"{prov} key set but last call failed — built-in phrasing"}
    label = {"anthropic": "Anthropic", "gemini": "Gemini (free tier)"}[prov]
    return {"available": True, "provider": prov,
            "reason": f"{label} — phrasing spoken lines in your tone"}


# ---- injection detection (guardrail #7) ----------------------------------

_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|your\s+|the\s+|previous\s+|prior\s+|earlier\s+)*"
    r"(?:instructions?|prompts?|rules?|guardrails?|directives?)|"
    r"disregard\s+(?:the\s+|your\s+|all\s+|any\s+|previous\s+|above\s+)*"
    r"(?:instructions?|prompts?|rules?|the\s+above|everything)|"
    r"(?:forward|e-?mail|send)\s+(?:this|that|it|all|the|these|them)\b[^.\n]{0,40}\bto\b|"
    r"approve\s+the\s+(?:upgrade|purchase|payment|invoice)",
    re.IGNORECASE,
)

def _scan_injection(text: str) -> str | None:
    """Return a short description if the text is trying to command us."""
    m = _INJECTION.search(text or "")
    return m.group(0) if m else None


# ---- card helpers --------------------------------------------------------

def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def _card(kind, title, html):
    return {"kind": kind, "title": title, "html": html}

def _rows(pairs) -> str:
    return "".join(
        f'<div class="body" style="border:0;padding:6px 0;margin:0">'
        f'<span style="color:var(--text-faint)">{_esc(k)}</span> {v}</div>'
        for k, v in pairs)


# ============================================================================
# TOOLS
# ============================================================================

_TYPE_WEIGHT = {"client": 1.6, "project": 1.6, "person": 1.6, "invoice": 1.4,
                "note": 1.0, "pdf": 1.0, "file": 1.0, "meeting": 0.8}

_MONEY_Q = re.compile(r"\b(invoice|invoices|paid|unpaid|owe[ds]?|owing|"
                      r"outstanding|balance|part.?paid|deposit|received)\b",
                      re.IGNORECASE)


def search_brain(vault, query: str) -> dict:
    """A specific fact from the user's own files. Always names the file(s)."""
    # Money questions are guardrail-critical: a derived number must carry its
    # qualifier. Handle them from the invoice notes directly.
    if _MONEY_Q.search(query):
        m = _money_answer(vault, query)
        if m:
            return m

    hits = vault.search(query, limit=8)
    # Rank with two adjustments:
    #  - down-rank notes carrying an injection, so a planted "read me first"
    #    note is never the answer (still findable, never dominant, never voiced);
    #  - prefer canonical notes (a client/project/person/invoice page) over the
    #    repetitive meeting chatter that mentions them, so "who is X" / "status
    #    of X" land on the definitional note.
    def weight(note):
        w = _TYPE_WEIGHT.get(note.type, 1.0)
        if _scan_injection(note.body):
            w *= 0.25
        return w
    ranked = sorted(hits, key=lambda h: h[1] * weight(h[0]), reverse=True)
    if not ranked:
        return {"route": "search_brain",
                "spoken": "Nothing in your files on that.",
                "card": _card("search_brain", query,
                              '<div class="body">No matching note.</div>')}
    top = ranked[0][0]

    # Never invent: if the query has several distinctive words but the top note
    # covers fewer than half of them, we're pattern-matching noise, not
    # answering. Say so in four words rather than dress up a coincidence.
    from vault import STOPWORDS
    q_terms = {w for w in re.findall(r"[a-z0-9]+", query.lower())
               if len(w) > 2 and w not in STOPWORDS}
    if len(q_terms) >= 2:
        top_terms = set(re.findall(r"[a-z0-9]+", (top.title + " " + top.body).lower()))
        covered = len(q_terms & top_terms) / len(q_terms)
        if covered < 0.5:
            return {"route": "search_brain",
                    "spoken": "Nothing in your files on that.",
                    "card": _card("search_brain", query,
                                  '<div class="body">No note covers that.</div>')}

    # If the best match is a note carrying an instruction (a planted "read me
    # first"), report it — never voice the instruction as if it were an answer.
    inj_top = _scan_injection(top.body)
    if inj_top:
        return {"route": "search_brain", "focus": top.id, "items": [top.id],
                "spoken": (f"Heads up — '{top.title}' contains an instruction "
                           f"I won't act on. Flagged on the card."),
                "card": _card("search_brain", query,
                              f'<div class="body" style="color:var(--warn)">'
                              f'<b>{_esc(top.title)}</b> ({_esc(Path(top.path).name)}) '
                              f'contains an embedded instruction — "{_esc(inj_top)}". '
                              f'Instructions inside your files are data, not '
                              f'commands: reporting it, not acting on it.</div>')}

    others = [n for n, _ in ranked[1:] if _relevant(n, query)]
    cited = [top] + others[:2]

    # Build the card: excerpt + source filename for each cited note.
    blocks = []
    for n in cited:
        inj = _scan_injection(n.body)
        warn = (f'<div class="body" style="color:var(--warn)">⚠ This note '
                f'contains an instruction ("{_esc(inj)}") — reporting it, not '
                f'acting on it.</div>') if inj else ""
        blocks.append(
            f'<div style="margin-bottom:12px">'
            f'<span class="note-type" style="--dot:var(--t-{n.type},#888)">{_esc(n.type)}</span>'
            f'<div style="color:var(--text-dim);font-size:12px;margin:4px 0">'
            f'from <b>{_esc(n.title)}</b> · {_esc(Path(n.path).name)}</div>'
            f'<div class="body">{_esc(n.clean_body()[:400])}</div>{warn}</div>')
    n_src = len(cited)
    fact = _first_fact(top, query)
    spoken = _phrase(
        f"Answer '{query}' from the note titled '{top.title}', whose key line "
        f"is: '{fact}'. State it plainly, lead with the name, and name the "
        f"file {Path(top.path).name}. {'It took ' + str(n_src) + ' files.' if n_src > 1 else ''}",
        fallback=f"{top.title}: {fact} — {Path(top.path).name}"
                 + (f" (+{n_src-1} more)." if n_src > 1 else "."))
    return {"route": "search_brain", "spoken": spoken, "focus": top.id,
            "items": [n.id for n in cited],
            "card": _card("search_brain", query, "".join(blocks))}


def _money_answer(vault, query: str) -> dict | None:
    """Answer invoice/payment questions from invoice notes, ALWAYS with the
    qualifier (guardrail: a part-paid invoice because a job is still running is
    not a discount)."""
    ql = query.lower()
    invoices = [n for n in vault.notes.values() if n.type == "invoice"]
    if not invoices:
        return None
    want = None
    if "unpaid" in ql: want = "unpaid"
    elif "part" in ql: want = "part-paid"
    elif "paid" in ql: want = "paid in full"

    def status(n):
        return _status_line(n)

    picked = []
    for n in invoices:
        st = status(n).lower()
        if want is None or want in st:
            picked.append(n)
    if not picked:
        return {"route": "search_brain",
                "spoken": f"No invoices marked {want}." if want else "No invoices on file.",
                "card": _card("search_brain", query,
                              '<div class="body">Nothing matches.</div>')}
    picked.sort(key=lambda n: n.title)
    rows = "".join(
        f'<div class="body" style="border:0;padding:6px 0;margin:0">'
        f'<b>{_esc(n.title)}</b> — {_esc(status(n))} '
        f'<span style="color:var(--text-faint)">· {_esc(Path(n.path).name)}</span></div>'
        for n in picked)
    names = ", ".join(n.title for n in picked)
    # The spoken line names them and carries the qualifier verbatim.
    if want == "part-paid":
        spoken = _phrase(
            f"These invoices are part-paid, each because the job is still "
            f"running or sign-off is pending — not discounts: {names}. Say it "
            f"in one dry line and make clear it is not a discount.",
            fallback=f"{len(picked)} part-paid — {names}. Each because the job's "
                     f"still running, not a discount.")
    else:
        label = want or "on file"
        spoken = _phrase(
            f"Invoices {label}: {names}. One dry line, lead with the count.",
            fallback=f"{len(picked)} {label}: {names}.")
    return {"route": "search_brain", "spoken": spoken,
            "items": [n.id for n in picked],
            "card": _card("search_brain", query, rows)}


def _relevant(note, query) -> bool:
    ql = set(re.findall(r"[a-z0-9]+", query.lower()))
    tl = set(re.findall(r"[a-z0-9]+", (note.title + " " + note.body).lower()))
    return len(ql & tl) >= 1

def _clean_speech(line: str) -> str:
    """Strip wikilink brackets so speech reads naturally."""
    return re.sub(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]", r"\1", line).strip()


def _first_fact(note, query: str = "") -> str:
    """The most relevant substantive line — skipping headings and any injection
    line. If the query names a labelled field (e.g. 'status'), prefer that
    line so 'status of X' answers with the Status."""
    lines = [l.strip() for l in note.clean_body().splitlines()
             if l.strip() and not l.strip().startswith("#") and not _scan_injection(l)]
    if not lines:
        return note.title
    q_terms = {w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2}
    for line in lines:
        m = re.match(r"([A-Za-z][A-Za-z ]{1,20}):", line)
        if m and m.group(1).strip().lower() in q_terms:
            return _clean_speech(line)[:140]
    return _clean_speech(lines[0])[:140]


def read_inbox(vault) -> dict:
    """Read-only. Who wrote, about what, and whether they exist in the files."""
    msgs, err = data.load_inbox()
    if err:
        return {"route": "read_inbox",
                "spoken": "Inbox isn't wired up — " + err.split(" — ")[0] + ".",
                "card": _card("read_inbox", "Inbox",
                              f'<div class="body" style="color:var(--warn)">{_esc(err)}</div>')}
    unread = [m for m in msgs if m.get("unread")]
    rows = []
    known = 0
    for m in msgs:
        who = m["from"]
        # do they exist in the files? — the whole value
        match = _person_in_vault(vault, who, m.get("email", ""))
        tag = (f'<span style="color:var(--good)">in your files</span>'
               if match else '<span style="color:var(--warn)">new — not in your files</span>')
        if match:
            known += 1
        inj = _scan_injection(m.get("preview", ""))
        warn = (f'<div style="color:var(--warn);font-size:12px">⚠ contains an '
                f'instruction — reported, not obeyed</div>') if inj else ""
        rows.append(
            f'<div style="margin-bottom:10px">'
            f'<div><b>{_esc(who)}</b> · {tag}{" · unread" if m.get("unread") else ""}</div>'
            f'<div style="color:var(--text)">{_esc(m["subject"])}</div>'
            f'<div class="body">{_esc(m["preview"][:160])}</div>{warn}</div>')
    new_people = [m["from"] for m in msgs if not _person_in_vault(vault, m["from"], m.get("email", ""))]
    spoken = _phrase(
        f"{len(unread)} unread. {len(new_people)} from people not in the files"
        f"{': ' + ', '.join(new_people) if new_people else ''}. Summarise in one dry line.",
        fallback=(f"{len(unread)} unread. "
                  + (f"{len(new_people)} new face"
                     + ("s" if len(new_people) != 1 else "")
                     + f": {new_people[0]}." if new_people else "All known.")))
    return {"route": "read_inbox", "spoken": spoken,
            "card": _card("read_inbox", f"Inbox · {len(unread)} unread", "".join(rows))}


def _person_in_vault(vault, name, email) -> bool:
    name_l = name.lower()
    for n in vault.notes.values():
        if n.type in ("person", "client"):
            if name_l == n.title.lower() or name_l in n.title.lower() or n.title.lower() in name_l:
                return True
        if email and email.lower() in n.body.lower():
            return True
    return False


def brief_me(vault) -> dict:
    """Calendar, unread, and what slipped."""
    cal, cerr = data.load_calendar()
    msgs, ierr = data.load_inbox()
    unread = [m for m in msgs if m.get("unread")]
    # "what slipped": unpaid / part-paid invoices and stalled projects
    slipped = []
    for n in vault.notes.values():
        if n.type == "invoice" and ("unpaid" in n.body.lower() or "part-paid" in n.body.lower()):
            slipped.append(n)
        if n.type == "project" and "stalled" in n.body.lower():
            slipped.append(n)

    cal_rows = "".join(
        f'<div class="body" style="border:0;padding:4px 0;margin:0">'
        f'<b>{_esc(c["when"][11:])}</b> {_esc(c["title"])}'
        f'{" · " + _esc(c["with"]) if c.get("with") else ""}</div>'
        for c in cal[:5]) or '<div class="body">Nothing on the calendar.</div>'
    slip_rows = "".join(
        f'<div class="body" style="border:0;padding:4px 0;margin:0;color:var(--warn)">'
        f'{_esc(n.title)} — {_esc(_status_line(n))}</div>' for n in slipped[:5]
    ) or '<div class="body">Nothing obviously slipped.</div>'

    html = (f'<div style="color:var(--text-faint);font-size:11px;letter-spacing:.1em;'
            f'text-transform:uppercase;margin:8px 0 4px">Today</div>{cal_rows}'
            f'<div style="color:var(--text-faint);font-size:11px;letter-spacing:.1em;'
            f'text-transform:uppercase;margin:12px 0 4px">Slipped</div>{slip_rows}'
            f'<div style="color:var(--text-faint);font-size:11px;letter-spacing:.1em;'
            f'text-transform:uppercase;margin:12px 0 4px">Inbox</div>'
            f'<div class="body">{len(unread)} unread.</div>')
    first_cal = cal[0]["title"] if cal else "nothing booked"
    spoken = _phrase(
        f"Brief: first up today is '{first_cal}'. {len(unread)} unread. "
        f"{len(slipped)} things slipped. One or two dry sentences.",
        fallback=(f"First up: {first_cal}. {len(unread)} unread, "
                  f"{len(slipped)} slipped."))
    return {"route": "brief_me", "spoken": spoken,
            "card": _card("brief_me", "Brief", html)}


def _status_line(note) -> str:
    for line in note.body.splitlines():
        if line.lower().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return note.type


def plan_day(vault) -> dict:
    """At most five items, ordered by what moves money."""
    scored = []
    for n in vault.notes.values():
        s = 0
        b = n.body.lower()
        if n.type == "invoice":
            if "unpaid" in b: s = 100
            elif "part-paid" in b: s = 80
        if n.type == "project":
            if "proposal" in b: s = 90       # closing new money
            elif "active" in b: s = 60
            elif "stalled" in b: s = 40
        if s:
            scored.append((s, n))
    scored.sort(key=lambda x: (-x[0], x[1].title))
    top = scored[:5]
    rows = "".join(
        f'<div class="body" style="border:0;padding:6px 0;margin:0">'
        f'<b>{i+1}.</b> {_esc(n.title)} '
        f'<span style="color:var(--text-faint)">— {_esc(_status_line(n))}</span></div>'
        for i, (s, n) in enumerate(top)
    ) or '<div class="body">Nothing pressing. Rare.</div>'
    lead = top[0][1].title if top else "nothing pressing"
    spoken = _phrase(
        f"Day plan, money first. Top item: '{lead}'. Give one dry sentence.",
        fallback=f"Start with {lead}. Five items on the card, money first.")
    return {"route": "plan_day", "spoken": spoken,
            "items": [n.id for _, n in top],
            "card": _card("plan_day", "Plan — money first", rows)}


def remember_fact(fact: str) -> dict:
    """Write ONE fact to memory/ and say exactly what was written."""
    # Guardrail: if the "fact" is actually an instruction, treat it as data.
    res = memory_mod.remember(fact)
    if not res.get("ok"):
        return {"route": "remember",
                "spoken": "Nothing to remember.",
                "card": _card("remember", "Memory",
                              f'<div class="body">{_esc(res.get("error",""))}</div>')}
    spoken = f'Written to {res["filename"]}: "{res["fact"]}"'
    html = (f'<div class="body">Wrote one file, and nothing else:</div>'
            f'<div class="body"><b>{_esc(res["filename"])}</b></div>'
            f'<div class="body">{_esc(res["fact"])}</div>'
            f'<div style="color:var(--text-faint);font-size:12px;margin-top:6px">'
            f'{_esc(res["when"])} · memory/ only</div>')
    return {"route": "remember", "spoken": spoken,
            "card": _card("remember", "Remembered", html)}


def research_web(vault, query: str) -> dict:
    """Look something up, then land it back on the user's numbers.

    Best-effort and free (a plain GET, no paid API — the 'never spend' rule
    holds). If the web can't be reached it degrades loudly rather than
    inventing a figure."""
    snippet, err = _web_snippet(query)
    my_number = _my_relevant_number(vault, query)
    if err:
        spoken = "Couldn't reach the web just now."
        html = (f'<div class="body" style="color:var(--warn)">{_esc(err)}</div>'
                + (f'<div class="body">Your own figure that bears on it: {_esc(my_number)}</div>'
                   if my_number else ""))
        return {"route": "research_web", "spoken": spoken,
                "card": _card("research_web", query, html)}
    spoken = _phrase(
        f"Web says: {snippet[:200]}. Land it against the user's numbers "
        f"({my_number or 'no matching figure on file'}). One dry sentence.",
        fallback=(snippet[:140] + (f" — against your {my_number}." if my_number else ".")))
    html = (f'<div class="body">{_esc(snippet[:600])}</div>'
            + (f'<div class="body" style="margin-top:8px;color:var(--accent)">'
               f'Your figure: {_esc(my_number)}</div>' if my_number else ""))
    return {"route": "research_web", "spoken": spoken,
            "card": _card("research_web", query, html)}


def _web_snippet(query: str) -> tuple[str, str | None]:
    try:
        url = "https://duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 JARVIS"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read().decode("utf-8", "ignore")
        m = re.search(r'result__snippet[^>]*>(.*?)</a>', html, re.DOTALL)
        if not m:
            return "", "no snippet found"
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = re.sub(r"\s+", " ", text).strip()
        return text, None
    except Exception as exc:
        return "", f"web unreachable ({type(exc).__name__})"


def _my_relevant_number(vault, query) -> str:
    hits = vault.search(query, limit=1)
    if not hits:
        return ""
    note = hits[0][0]
    m = re.search(r"£[\d,]+", note.body)
    if m:
        return f"{m.group(0)} on {note.title}"
    return ""


# ============================================================================
# ROUTER  (file-scoring; works with no model)
# ============================================================================

_GREETING = re.compile(r"^\s*(hi|hey|hello|yo|morning|good morning|good evening|"
                       r"howdy|sup|hiya)\b", re.IGNORECASE)
_HEARME = re.compile(r"can you hear me|are you (there|listening|on)|you there|"
                     r"(mic|microphone) (check|working)|is this (on|working)|test",
                     re.IGNORECASE)
_THANKS = re.compile(r"\b(thanks|thank you|cheers|ta|nice one|appreciate)\b", re.IGNORECASE)
_OPINION = re.compile(r"what do you think|your (opinion|view|take)|should i\b|"
                      r"do you reckon|thoughts\??$", re.IGNORECASE)
_WHY = re.compile(r"^\s*(why|how come|says who|really\??)\s*\??\s*$", re.IGNORECASE)
_WHOAMI = re.compile(r"who are you|what are you|your name", re.IGNORECASE)
_WHOFOR = re.compile(r"who do you work for|whose assistant|who do you serve|"
                     r"^\s*who am i\b|what do you know about me", re.IGNORECASE)
_CANDO = re.compile(r"what can you do|help$|what do you do|how do you work", re.IGNORECASE)

SCORE_THRESHOLD = 6.0   # tune: below this a query is treated as chat, not lookup


def classify(text: str, history: list, vault) -> dict:
    """Decide what to do. Explicit tool words first, then small talk, then
    score against the files."""
    t = (text or "").strip()
    tl = t.lower()

    # explicit tools
    if re.match(r"^(remember|note that|make a note|don'?t let me forget)\b", tl):
        fact = re.sub(r"^(remember( that)?|note that|make a note( that)?|"
                      r"don'?t let me forget( that)?)[:,\s]*", "", t, flags=re.IGNORECASE)
        return {"kind": "tool", "tool": "remember", "arg": fact.strip()}
    if re.search(r"\b(brief me|brief$|catch me up|what'?s (happening|new|up today)|"
                 r"where are we|what did i miss)\b", tl):
        return {"kind": "tool", "tool": "brief_me"}
    if re.search(r"\b(plan (my )?day|what should i (do|work on)|priorities|"
                 r"what'?s (important|first) today)\b", tl):
        return {"kind": "tool", "tool": "plan_day"}
    if re.search(r"\b(inbox|unread|any (mail|email)|who (wrote|emailed)|"
                 r"check (my )?(mail|email))\b", tl):
        return {"kind": "tool", "tool": "read_inbox"}
    if re.search(r"\b(look up|research|google|search the web|going rate|market rate|"
                 r"how much (does|is|are)|what'?s the price)\b", tl):
        return {"kind": "tool", "tool": "research_web", "arg": t}

    # follow-ups referring to the last result
    if _WHY.search(tl):
        return {"kind": "followup", "sub": "why"}
    m = re.search(r"\b(the )?(first|second|third|fourth|fifth|last) one\b", tl)
    if m:
        return {"kind": "followup", "sub": "ordinal", "ord": m.group(2)}

    # small talk / meta — never a tool, never a search result
    if _GREETING.search(t) or _HEARME.search(t) or _THANKS.search(t) or \
       _OPINION.search(t) or _WHOAMI.search(t) or _WHOFOR.search(t) or _CANDO.search(t):
        return {"kind": "chat"}

    # score against files to decide talk vs. lookup
    hits = vault.search(t, limit=1)
    top_score = hits[0][1] if hits else 0.0
    if top_score >= SCORE_THRESHOLD and len(t.split()) >= 2:
        return {"kind": "tool", "tool": "search_brain", "arg": t, "score": top_score}
    return {"kind": "chat", "weak_score": top_score}


# ---- conversation (the person, not the search box) ------------------------

def converse(text: str, history: list, decision: dict, vault) -> dict:
    t = (text or "").strip()
    name = _persona_name()
    first = name.split()[0] if name else ""
    who = f", {first}" if first else ""

    if _HEARME.search(t):
        base = "Loud and clear."
    elif _GREETING.search(t):
        base = f"Go ahead{who}."
    elif _THANKS.search(t):
        base = "Anytime."
    elif _WHOFOR.search(t):
        base = (f"You, {name}." if name
                else "You. I read your files and keep your week straight.")
    elif _WHOAMI.search(t):
        base = ("JARVIS. I read your files and keep your week straight."
                if not name else f"JARVIS — I work for you{who}.")
    elif _CANDO.search(t):
        base = ("Ask about your files, your inbox, or say 'brief me' or "
                "'plan my day'. I draft; I never send.")
    elif _OPINION.search(t):
        base = _opinion(t, vault)
    else:
        # weak/no file match: don't fake a search result
        base = "Not following — say a bit more?"
    spoken = _phrase(f"Reply to '{t}' in the user's tone. One short line. "
                     f"Do not use a tool. Suggested: {base}", fallback=base)
    return {"route": "conversation", "spoken": spoken, "card": None}


def _opinion(t, vault) -> str:
    # a view grounded in the files, not a search dump
    unpaid = [n for n in vault.notes.values()
              if n.type == "invoice" and "unpaid" in n.body.lower()]
    if unpaid:
        return f"Chase the money first — {unpaid[0].title} is still unpaid."
    return "Depends what moves money. Say 'plan my day' and I'll rank it."


def _followup(decision, history, last, vault) -> dict:
    if not last:
        return {"route": "conversation",
                "spoken": "Nothing to go back to yet.", "card": None}
    if decision["sub"] == "why":
        # explain the last answer — cite the file it came from, don't re-read it
        prev = last.get("spoken", "")
        items = last.get("items") or []
        src = ""
        if items:
            n = vault.notes.get(items[0])
            if n:
                src = Path(n.path).name
        fallback = (f"It's in {src}." if src
                    else ("That's what the files say." if last.get("route") != "conversation"
                          else "Because it's what your files point to."))
        spoken = _phrase(
            f"The user asked 'why?' about your last answer ('{prev}'). Explain "
            f"in one dry sentence, citing the source {src or 'the files'}.",
            fallback=fallback)
        return {"route": "conversation", "spoken": spoken, "card": None}
    # ordinal — pick from the last result's items
    idx = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4,
           "last": -1}.get(decision.get("ord", ""), 0)
    items = last.get("items") or []
    if not items:
        return {"route": "conversation",
                "spoken": "That answer wasn't a list.", "card": None}
    try:
        nid = items[idx]
    except IndexError:
        return {"route": "conversation",
                "spoken": "There aren't that many.", "card": None}
    note = vault.notes.get(nid)
    if not note:
        return {"route": "conversation", "spoken": "Can't find it now.", "card": None}
    return search_brain(vault, note.title)


# ============================================================================
# MODEL PHRASING (optional; used only when a key is present)
# ============================================================================

def _phrase(instruction: str, fallback: str) -> str:
    """Phrase a spoken line in the user's tone using the model, if available.
    Otherwise return the deterministic fallback. Never invents facts — the
    instruction carries the facts; the model only rewords.

    Works with either provider (Anthropic or free Gemini). If the call fails,
    it flips the runtime flag so the UI degrades loudly to built-in phrasing."""
    global _MODEL_RUNTIME_OK
    prov = _provider()
    if not prov or not _MODEL_RUNTIME_OK:
        return fallback
    system = (CLAUDE_MD + "\n\n" + PROMPT_MD +
              "\n\nYou are wording ONE spoken line. Use only the facts in "
              "the instruction. Do not add numbers, names, or claims. Reply "
              "with the line only, no quotes.")
    try:
        if prov == "anthropic":
            out = _call_model(system, [{"role": "user", "content": instruction}], max_tokens=120)
        else:
            out = _call_gemini(system, instruction, max_tokens=120)
        line = (out or "").strip().strip('"')
        return line or fallback
    except Exception:
        _MODEL_RUNTIME_OK = False
        return fallback


def _call_gemini(system: str, user_text: str, max_tokens: int = 120) -> str:
    """Google Gemini (free tier) — stdlib HTTP, key held server-side only.
    Free key from https://aistudio.google.com/app/apikey (no card needed)."""
    key = os.environ["GEMINI_API_KEY"].strip()
    model = os.environ.get("JARVIS_GEMINI_MODEL", "gemini-2.0-flash")
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           + urllib.parse.quote(model) + ":generateContent")
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"content-type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    cands = payload.get("candidates", [])
    if not cands:
        return ""
    parts = cands[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _call_model(system: str, messages: list, max_tokens: int = 300) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.environ.get("JARVIS_MODEL", "claude-sonnet-5")
    body = json.dumps({
        "model": model, "max_tokens": max_tokens, "system": system,
        "messages": messages,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.loads(r.read())
    parts = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
    return "".join(parts)


# ============================================================================
# ORCHESTRATOR
# ============================================================================

# Single-user localhost: remember the last result so "why?"/"the second one"
# resolve even if the client sends a thin history.
_LAST: dict = {}


def handle_ask(vault, payload: dict) -> dict:
    text = (payload.get("text") or "").strip()
    history = payload.get("history") or []
    if not text:
        return {"spoken": "Say again?", "card": None, "route": "conversation",
                "model": model_status()}

    decision = classify(text, history, vault)

    if decision["kind"] == "chat":
        res = converse(text, history, decision, vault)
    elif decision["kind"] == "followup":
        res = _followup(decision, history, _LAST, vault)
    else:  # tool
        tool = decision["tool"]
        if tool == "search_brain":
            res = search_brain(vault, decision["arg"])
        elif tool == "read_inbox":
            res = read_inbox(vault)
        elif tool == "brief_me":
            res = brief_me(vault)
        elif tool == "plan_day":
            res = plan_day(vault)
        elif tool == "remember":
            res = remember_fact(decision["arg"])
        elif tool == "research_web":
            res = research_web(vault, decision["arg"])
        else:
            res = converse(text, history, decision, vault)

    res["model"] = model_status()
    # stash for follow-ups
    _LAST.clear()
    _LAST.update({"spoken": res.get("spoken", ""),
                  "items": res.get("items"), "route": res.get("route")})
    return res
