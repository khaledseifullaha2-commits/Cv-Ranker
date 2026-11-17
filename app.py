#!/usr/bin/env python3
"""
app.py  --  Streamlit web UI for the CV <-> JD matcher
========================================================

A recruiter-facing front end around the *same* scoring engine the CLI uses
(`evaluator.py`).  Nothing about the scoring model is re-implemented here:
this file only collects input, applies recruiter overrides/filters, calls
`evaluator`, and renders the result.

What this UI adds over the CLI
--------------------------------
1.  **JD Configuration Panel**   -- override experience years, education level
    and add custom bonus criteria without editing the JD text.
2.  **Blacklisting & filtering** -- apply `blacklist.csv` (names + phones) and
    ad-hoc keyword filters (exclude-if-contains / must-contain).
3.  **Scorecard & remarks**      -- per-candidate strengths, weaknesses and a
    structured Recruiter Summary Remark.
4.  **Export**                   -- the full ranked report as CSV or Excel.

Security posture (see SECURITY.md for the full write-up)
--------------------------------------------------------
* uploads are validated by **content** (magic bytes / ZIP structure), never by
  the file name or the browser-supplied MIME type;
* file names are sanitised (path components, traversal and illegal characters
  removed) -- and nothing is ever written to disk, so there is no path for a
  traversal to take;
* per-file and whole-batch size/count budgets are enforced in code on top of
  Streamlit's own `server.maxUploadSize`;
* every file is read inside its own `try/except`: a corrupted or
  password-protected PDF becomes a status row, never a crashed batch;
* no HTML is rendered from user data (`unsafe_allow_html` is never used).

Run it
------
    streamlit run app.py
"""

from __future__ import annotations

import copy
import dataclasses
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="CV ↔ JD Matcher",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═════════════════════════════════════════════════════════════════════════
# SECTION 0 -- CUSTOM CSS INJECTOR  (Apple‑style glassmorphic polish)
# ═════════════════════════════════════════════════════════════════════════
# Inject a clean stylesheet so the app renders with frosted‑glass cards, subtle
# 1 px border highlights, modern typography and slate / executive‑blue accents.
# This block runs after `st.set_page_config()` and does not affect any layout
# components because it only adds a `<style>` tag into the Streamlit header.
_CSS = """
<style>
/* ── Deep Slate / Obsidian Futuristic Theme ──────────────────────────── */
.stApp {
    background-color: #0b0f19;
    color: #f3f4f6;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}

/* ── Glowing Glassmorphic Cards & Containers ──────────────────────────── */
.stMarkdown, .stExpander, .stInfo, .stSuccess, .stWarning, .stError, div[data-testid="stVerticalBlock"] > div[style*="background"] {
    background: rgba(17, 24, 39, 0.75) !important;
    border: 1px solid rgba(59, 130, 246, 0.18) !important;
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 1rem;
    box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
}

/* ── Crisp Accent Borders & Glowing Hovers ───────────────────────────── */
.stButton > button {
    border-radius: 8px;
    border: 1px solid rgba(59, 130, 246, 0.4);
    background: linear-gradient(135deg, rgba(37, 99, 235, 0.2), rgba(15, 23, 42, 0.8));
    color: #ffffff;
    font-weight: 600;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.15);
}

.stButton > button:hover {
    background: linear-gradient(135deg, rgba(37, 99, 235, 0.4), rgba(30, 58, 138, 0.9));
    border-color: rgba(96, 165, 250, 0.8);
    box-shadow: 0 0 20px rgba(59, 130, 246, 0.4);
    transform: translateY(-1px);
}

/* ── Executive Typography & Headers ──────────────────────────────────── */
h1, h2, h3, .main-title {
    color: #ffffff !important;
    font-weight: 700;
    letter-spacing: -0.03em;
}

p, span, label, .stMetric > label {
    color: #9ca3af !important;
}

/* ── Metric Cards ────────────────────────────────────────────────────── */
.stMetric {
    background: rgba(17, 24, 39, 0.8);
    border: 1px solid rgba(59, 130, 246, 0.2);
    border-radius: 12px;
    padding: 1rem;
    box-shadow: inset 0 1px 0 0 rgba(255, 255, 255, 0.05);
}

.stMetric > div > div > div {
    color: #60a5fa !important;
    font-weight: 700;
}

/* ── Sidebar Styling ─────────────────────────────────────────────────── */
section[data-testid="stSidebar"] {
    background-color: #070a13;
    border-right: 1px solid rgba(59, 130, 246, 0.15);
}

section[data-testid="stSidebar"] .stMarkdown {
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
}

/* ── Futuristic Tabs ─────────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background-color: rgba(15, 23, 42, 0.6);
    padding: 6px;
    border-radius: 10px;
    border: 1px solid rgba(59, 130, 246, 0.15);
}

.stTabs [data-baseweb="tab"] {
    background: transparent;
    border-radius: 6px;
    color: #9ca3af;
    font-weight: 600;
    padding: 8px 16px;
    border: none !important;
}

.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
    color: #ffffff !important;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.4);
}

/* ── Dataframes & Tables ─────────────────────────────────────────────── */
dataframe, table {
    background-color: rgba(17, 24, 39, 0.9) !important;
    border-radius: 8px;
}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)

try:  # the engine must not be able to take the whole app down
    # Make sure the engine is importable no matter how this file was started
    # (`streamlit run app.py`, a symlinked script, a test harness, ...).
    _APP_DIR = str(Path(__file__).resolve().parent)
    if _APP_DIR not in sys.path:
        sys.path.insert(0, _APP_DIR)
    import evaluator as ev
except Exception as _engine_exc:  # pragma: no cover - defensive
    st.error(
        "The scoring engine (`evaluator.py`) could not be imported. "
        f"Details: `{_engine_exc}`"
    )
    st.stop()


# ══════════════════════════════════════════════════════════════════════════
# SECTION 0 -- LIMITS & CONSTANTS   (edit here)
# ══════════════════════════════════════════════════════════════════════════

MAX_FILE_MB = 10                 # per uploaded file
MAX_JD_MB = 10                   # per job-description file
MAX_TOTAL_MB = 150               # whole batch
MAX_FILES = 2000                 # per batch — increased from 300 to support 2000+ CVs
MAX_JD_CHARS = 120_000           # JD text cap (JDs are a few kB)
MAX_PAGES = 25                   # pages read per PDF (same as the CLI)
ALLOWED_EXTS = (".pdf", ".docx")

EDU_LEVELS = ("phd", "masters", "bachelor", "diploma", "ssc/hsc", "vocational")

JD_EXTS = (".txt", ".docx", ".pdf")

# Verdict bands used by the recruiter remark.
BANDS = (
    (70.0, "STRONG FIT", "Shortlist for interview."),
    (55.0, "POTENTIAL FIT", "Shortlist for a screening call."),
    (45.0, "BORDERLINE", "Read the scorecard manually before shortlisting."),
    (0.0, "WEAK FIT", "Do not shortlist unless the applicant pool is very small."),
)


class CVReadError(Exception):
    """A single file could not be read. Never fatal for the batch."""


# ══════════════════════════════════════════════════════════════════════════
# SECTION 1 -- INPUT HARDENING
# ══════════════════════════════════════════════════════════════════════════

def safe_filename(raw: str, default_ext: str = ".pdf") -> str:
    """Reduce an attacker-controlled name to a safe, readable display name.

    Strips directories, drive letters, traversal segments, NUL bytes and every
    character that is not obviously safe.  The result is used for display and
    export only -- uploads are never written to disk.
    """
    name = unicodedata.normalize("NFKC", str(raw or ""))
    name = unquote(name)                    # "%2F..%2F" must not survive as text
    name = name.replace("\x00", "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]      # drop any path
    name = name.split(":", 1)[-1] if ":" in name else name  # drop drive letter
    name = name.strip().lstrip(".")                        # no "..", no dotfiles

    if "." in name:
        stem, ext = name.rsplit(".", 1)
        ext = "." + re.sub(r"[^a-z]", "", ext.lower())
    else:
        stem, ext = name, default_ext
    if ext not in ALLOWED_EXTS:
        ext = default_ext

    stem = re.sub(r"[^A-Za-z0-9 _()\-]+", "_", stem)
    stem = re.sub(r"[_\s]+", " ", stem).strip()[:100].strip()
    return f"{stem or 'candidate'}{ext}"


def _looks_like_pdf(data: bytes) -> bool:
    """The PDF header may sit anywhere in the first 1024 bytes (PDF 3.4+)."""
    return b"%PDF-" in data[:1024]


def _looks_like_docx(data: bytes) -> bool:
    """A DOCX must be a ZIP container holding word/document.xml."""
    if data[:4] not in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "word/document.xml" not in zf.namelist():
                return False
            return zf.testzip() is None      # None == every entry decompresses
    except Exception:
        return False


def _short(exc: object, limit: int = 200) -> str:
    msg = " ".join(str(exc).split())
    return (msg or exc.__class__.__name__)[:limit]


@dataclasses.dataclass
class Upload:
    name: str     # sanitised display name
    data: bytes
    kind: str     # "pdf" | "docx"


def validate_uploads(files: Sequence) -> Tuple[List[Upload], List[str]]:
    """Turn raw Streamlit uploads into safe in-memory files (or rejections)."""
    uploads: List[Upload] = []
    errors: List[str] = []
    used: Dict[str, int] = {}
    budget = MAX_TOTAL_MB * 1024 * 1024
    spent = 0

    for fh in files:
        if len(uploads) >= MAX_FILES:
            errors.append(
                f"Batch limit reached ({MAX_FILES} files) — remaining files "
                "were ignored. Use `evaluator.py` for larger batches."
            )
            break

        raw_name = getattr(fh, "name", "") or "candidate"
        try:
            data = fh.getvalue()
        except Exception as exc:
            errors.append(f"{raw_name}: could not be read ({_short(exc)}).")
            continue

        if not data:
            errors.append(f"{raw_name}: file is empty.")
            continue

        size_mb = len(data) / (1024 * 1024)
        if size_mb > MAX_FILE_MB:
            errors.append(
                f"{raw_name}: {size_mb:.1f} MB exceeds the {MAX_FILE_MB} MB "
                "per-file limit."
            )
            continue
        if spent + len(data) > budget:
            errors.append(
                f"{raw_name}: total upload budget of {MAX_TOTAL_MB} MB exceeded."
            )
            continue

        ext = os.path.splitext(raw_name)[1].lower()
        if ext not in ALLOWED_EXTS:
            errors.append(f"{raw_name}: only PDF and DOCX files are accepted.")
            continue

        # Content-based validation: the extension and MIME type are attacker
        # controlled, so neither is trusted.
        if ext == ".pdf" and not _looks_like_pdf(data):
            errors.append(f"{raw_name}: content is not a real PDF (no %PDF header).")
            continue
        if ext == ".docx" and not _looks_like_docx(data):
            errors.append(f"{raw_name}: content is not a real DOCX document.")
            continue

        name = safe_filename(raw_name, ext)
        if name in used:                       # de-duplicate display names
            used[name] += 1
            stem, e = os.path.splitext(name)
            name = f"{stem} ({used[name]}){e}"
        used.setdefault(name, 1)

        spent += len(data)
        uploads.append(Upload(name=name, data=data, kind=ext[1:]))

    return uploads, errors


# ═════════════════════════════════════════════════════════════════════════
# SECTION 1.5 -- PRIVACY LAYER + GOOGLE GEMINI (40% qualitative score)
# ═════════════════════════════════════════════════════════════════════════
# Contact details are pulled out of the raw CV locally and kept in the
# recruiter report only. Only REDACTED text is ever sent to Gemini; the raw
# text still goes to the local evaluator.py engine that supplies 60%.

_RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_RE_PHONE = re.compile(
    r"(?:(?:\+|00)\d{1,3}[\s.\-]?)?"
    r"(?:\(?\d{2,5}\)?[\s.\-]?)"
    r"\d{3,5}[\s.\-]?\d{3,6}"
)
_RE_ADDRESS = re.compile(
    r"(?:present\s+address|permanent\s+address|address|house|village|"
    r"post\s+office|thana|district|street|road)\s*[:\-]?\s*[^\n]{5,120}",
    re.IGNORECASE,
)


def _digit_count(value: str) -> int:
    return len(re.sub(r"\D", "", value or ""))


def redact_pii(text: str) -> Tuple[str, Dict[str, int]]:
    """Redact emails, phone numbers and postal addresses before any API call.

    Returns the redacted text plus per-category counts so the UI can show
    exactly what was removed. Year ranges such as ``2019 - 2023`` survive
    because only numbers with 8+ digits are treated as phone numbers.
    """
    if not text:
        return "", {"emails": 0, "phones": 0, "addresses": 0}

    emails = _RE_EMAIL.findall(text)
    redacted = _RE_EMAIL.sub("[REDACTED_EMAIL]", text)

    phones = [m.group(0) for m in _RE_PHONE.finditer(redacted)]
    phones = [p for p in phones if _digit_count(p) >= 8]
    redacted = _RE_PHONE.sub(
        lambda m: "[REDACTED_PHONE]" if _digit_count(m.group(0)) >= 8 else m.group(0),
        redacted,
    )

    addresses = _RE_ADDRESS.findall(redacted)
    redacted = _RE_ADDRESS.sub("[REDACTED_ADDRESS]", redacted)

    return redacted, {
        "emails": len(emails),
        "phones": len(phones),
        "addresses": len(addresses),
    }


def extract_contact_fields(raw_text: str, fallback_name: str = "") -> Dict[str, str]:
    """Pull Name / Email / Phone out of the RAW text, locally.

    These values never leave the machine: Gemini receives the redacted text,
    so the report can show real contact details without exposing them to the
    API.
    """
    raw_text = raw_text or ""

    email = ""
    match = _RE_EMAIL.search(raw_text)
    if match:
        email = match.group(0)

    phone = ""
    for candidate in _RE_PHONE.finditer(raw_text):
        digits = _digit_count(candidate.group(0))
        if 8 <= digits <= 15:
            phone = candidate.group(0).strip()
            break

    name = ""
    for line in raw_text.splitlines():
        value = line.strip(" \t|,;:")
        if not 2 <= len(value) <= 60 or re.search(r"\d", value):
            continue
        lowered = value.lower()
        if any(token in lowered for token in (
            "@", "http", "curriculum", "resume", "cv", "phone", "email",
            "mobile", "experience", "education", "profile", "summary",
        )):
            continue
        words = value.split()
        if 1 <= len(words) <= 5 and value[0].isupper():
            name = value
            break

    stem = os.path.splitext(os.path.basename(fallback_name or ""))[0]
    return {
        "name": name or stem or "Unknown candidate",
        "email": email,
        "phone": phone,
    }


GEMINI_API_KEY = ""
try:
    GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", "") or ""
except Exception:
    GEMINI_API_KEY = ""
GEMINI_API_KEY = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")

GEMINI_MODEL = ""
try:
    GEMINI_MODEL = st.secrets.get("GEMINI_MODEL", "") or ""
except Exception:
    GEMINI_MODEL = ""
GEMINI_MODEL = GEMINI_MODEL or os.environ.get("GEMINI_MODEL", "")

# Google shut down gemini-1.5-pro on 29 Sep 2025 and the 2.0 family on
# 1 Jun 2026, so a retired name now returns 404. The app walks the list below
# and settles on the first model this key can actually call. Pin one with
# GEMINI_MODEL (secrets.toml or environment) — it is always tried first.
_models: List[str] = []
if GEMINI_MODEL:
    _models.append(GEMINI_MODEL)
_models += [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-1.5-pro",
]
GEMINI_MODEL_CANDIDATES: Tuple[str, ...] = tuple(dict.fromkeys(_models))

gemini_model = None
_genai_module = None
_gemini_active_name = ""
_gemini_active_model = None

if GEMINI_API_KEY:
    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMINI_API_KEY)
        _genai_module = genai
        gemini_model = genai.GenerativeModel(GEMINI_MODEL_CANDIDATES[0])
    except Exception as _gemini_exc:                       # pragma: no cover
        gemini_model = None
        st.warning(
            "⚠️ Google Gemini could not be initialised — the local engine "
            f"carries the whole score. Details: `{_short(_gemini_exc)}`"
        )
else:
    st.warning(
        "⚠️ Google Gemini API key not configured. Add `GEMINI_API_KEY` to "
        "`.streamlit/secrets.toml` (or the environment) to enable the 40% AI "
        "score; the local engine still runs."
    )


def _is_model_unavailable(exc: BaseException) -> bool:
    """True when the failure means "this model name is not served"."""
    message = str(exc).lower()
    return any(marker in message for marker in (
        "not found", "not supported", "does not exist", "unsupported",
        "no longer available", "invalid model", "404",
    ))


def _gemini_generate(prompt: str):
    """Generate content, rolling forward through models Google still serves.

    A quota, auth or network error is raised immediately — only a retired or
    unknown model name moves on to the next candidate. The model that worked
    is remembered, so the walk costs at most one round trip per session.
    """
    global _gemini_active_name, _gemini_active_model

    if _gemini_active_model is not None:
        return _gemini_active_model.generate_content(prompt)

    last_exc: Optional[BaseException] = None
    for name in GEMINI_MODEL_CANDIDATES:
        try:
            candidate = _genai_module.GenerativeModel(name)
            reply = candidate.generate_content(prompt)
        except Exception as exc:                          # pragma: no cover
            last_exc = exc
            if not _is_model_unavailable(exc):
                raise
            continue
        _gemini_active_name = name
        _gemini_active_model = candidate
        if name != GEMINI_MODEL_CANDIDATES[0]:
            st.info(
                f"ℹ️ Using Gemini model `{name}` — `{GEMINI_MODEL_CANDIDATES[0]}` "
                "is not available to this API key."
            )
        return reply

    raise last_exc if last_exc else RuntimeError("no Gemini model is available")


def gemini_model_name() -> str:
    """The model actually answering, for the UI."""
    return _gemini_active_name or GEMINI_MODEL_CANDIDATES[0]


GEMINI_UNAVAILABLE = {
    "candidate_name": "",
    "email": "",
    "phone": "",
    "years_experience": 0.0,
    "skills_summary": "",
    "match_score": 0.0,
    "strengths": [],
    "gaps": [],
    "core_skills": [],
}


def _parse_json_reply(reply: str) -> Dict:
    """Parse a JSON object out of a model reply, tolerating ```json fences."""
    body = (reply or "").strip()
    if "```" in body:
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body, flags=re.MULTILINE).strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(body[start:end + 1])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r"[;\n]+", value) if v.strip()]
    return []


@st.cache_data(show_spinner=False)
def gemini_screen(redacted_cv_text: str, jd_text: str) -> Dict:
    """Gemini 1.5 Pro screening on REDACTED text, cached per CV + JD pair.

    Returns the structured candidate fields plus the qualitative score that
    forms 40% of the hybrid score. Re-running the same PDF against the same
    job description hits this cache and issues no API request at all.
    """
    if gemini_model is None:
        return dict(GEMINI_UNAVAILABLE)

    prompt = f"""You are an expert recruitment analyst. Read the candidate CV and the job description, then reply with ONE JSON object and nothing else.

The CV text has already been privacy-redacted: emails appear as [REDACTED_EMAIL], phone numbers as [REDACTED_PHONE] and postal addresses as [REDACTED_ADDRESS]. Return empty strings for email and phone and never try to guess them.

Return exactly these keys:
{{
  "candidate_name": "the candidate's full name, empty if not visible",
  "email": "empty string - redacted before sending",
  "phone": "empty string - redacted before sending",
  "years_experience": 0.0,
  "skills_summary": "one or two sentences naming the core skills, max 40 words",
  "match_score": 0,
  "strengths": ["3-5 short strengths relevant to this job"],
  "gaps": ["3-5 short gaps against this job"],
  "core_skills": ["5-8 core skill names"]
}}

Scoring rules for match_score (integer 0-100, be strict):
- required skills evidenced in the CV: 40% of the score
- years of experience versus what the job description asks: 30%
- education and certifications the job description requires: 30%
Only count requirements that actually appear in the job description.

JOB DESCRIPTION:
{jd_text}

CANDIDATE CV (redacted):
{redacted_cv_text}
"""

    try:
        reply = _gemini_generate(prompt)
        data = _parse_json_reply(getattr(reply, "text", "") or "")
    except Exception as exc:                              # pragma: no cover
        st.warning(
            "Gemini request failed — this candidate is scored by the local "
            f"engine only. ({_short(exc, 120)})"
        )
        return dict(GEMINI_UNAVAILABLE)

    if not data:
        st.warning("Gemini returned an unreadable reply — local engine only for this CV.")
        return dict(GEMINI_UNAVAILABLE)

    return {
        "candidate_name": str(data.get("candidate_name") or "").strip(),
        "email": str(data.get("email") or "").strip(),
        "phone": str(data.get("phone") or "").strip(),
        "years_experience": max(0.0, _as_float(data.get("years_experience"), 0.0)),
        "skills_summary": str(data.get("skills_summary") or "").strip(),
        "match_score": max(0.0, min(100.0, _as_float(data.get("match_score"), 0.0))),
        "strengths": _as_list(data.get("strengths")),
        "gaps": _as_list(data.get("gaps")),
        "core_skills": _as_list(data.get("core_skills")),
    }


def hybrid_score(engine_score: float, ai_score: float, bonus: float = 0.0) -> float:
    """60% local engine + 40% Gemini qualitative score, plus bonus, capped at 100."""
    if gemini_model is None:
        combined = engine_score + bonus
    else:
        combined = 0.6 * engine_score + 0.4 * ai_score + bonus
    return round(max(0.0, min(100.0, combined)), 1)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 2 -- TEXT EXTRACTION (in memory, per-file failures isolated)
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def read_pdf_bytes(data: bytes) -> str:
    """Extract PDF text (cached per file content)."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise CVReadError(f"corrupted or unreadable PDF ({_short(exc)})") from exc

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = False
        if not unlocked:
            raise CVReadError(
                "password-protected PDF — ask the candidate for an unlocked copy"
            )

    try:
        pages = reader.pages[:MAX_PAGES]
    except Exception as exc:
        raise CVReadError(f"could not open the document body ({_short(exc)})") from exc

    parts: List[str] = []
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            continue                      # one bad page must not kill the file
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


@st.cache_data(show_spinner=False)
def read_docx_bytes(data: bytes) -> str:
    """Extract DOCX text (cached per file content)."""
    import docx  # python-docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise CVReadError(f"corrupted or unreadable DOCX ({_short(exc)})") from exc

    parts: List[str] = []
    try:
        for para in document.paragraphs:
            if para.text and para.text.strip():
                parts.append(para.text)
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    except Exception as exc:
        raise CVReadError(f"could not read the document body ({_short(exc)})") from exc
    return "\n".join(parts)


def read_upload(up: Upload) -> str:
    text = read_pdf_bytes(up.data) if up.kind == "pdf" else read_docx_bytes(up.data)
    if len(text.strip()) < 80:
        # Mirror evaluator's own judgement: near-empty means scanned/image PDF.
        raise CVReadError(
            "almost no text extracted — likely a scanned or image-only file, "
            "manual review required"
        )
    return text


def jd_text_from_upload(fh) -> Tuple[str, Optional[str]]:
    """Read a job description from a .txt, .docx or .pdf upload.

    Returns ``(text, error)``.  Recruiters mostly receive postings as Word or
    PDF, so all three formats are accepted; the same magic-byte checks used
    for CVs apply here.
    """
    name = getattr(fh, "name", "job description") or "job description"
    ext = os.path.splitext(name)[1].lower()
    try:
        data = fh.getvalue()
    except Exception as exc:
        return "", f"{name}: could not be read ({_short(exc)})."

    if not data:
        return "", f"{name}: the job-description file is empty."
    if len(data) > MAX_JD_MB * 1024 * 1024:
        return "", f"{name}: larger than the {MAX_JD_MB} MB job-description limit."

    try:
        if ext == ".pdf":
            if not _looks_like_pdf(data):
                return "", f"{name}: content is not a real PDF."
            return ev.clean_control_chars(read_pdf_bytes(data)), None
        if ext == ".docx":
            if not _looks_like_docx(data):
                return "", f"{name}: content is not a real DOCX document."
            return ev.clean_control_chars(read_docx_bytes(data)), None
        # .txt and unknown: job-board exports carry stray NULs in the
        # Bengali "report this job" block, which break keyword matching.
        return (ev.clean_control_chars(
            data.decode("utf-8", errors="replace")), None)
    except CVReadError as exc:
        return "", f"{name}: {exc}."
    except Exception as exc:                                     # noqa: BLE001
        return "", f"{name}: could not read the job description ({_short(exc)})."


# ══════════════════════════════════════════════════════════════════════════
# SECTION 3 -- JD PROFILE + RECRUITER OVERRIDES
# ══════════════════════════════════════════════════════════════════════════

def _default_jd() -> str:
    """The user must provide their own job description."""
    return ""


@st.cache_data(show_spinner=False)
def build_profile(jd_text: str):
    """Parse the JD with the engine's own extractor (cached per JD text)."""
    if not jd_text.strip():
        return None
    with tempfile.TemporaryDirectory() as tmp:          # engine reads a Path
        path = Path(tmp) / "current_jd.txt"
        path.write_text(jd_text, encoding="utf-8")
        return ev.extract_jd_profile(path)


def split_keywords(raw: str) -> List[str]:
    """Split on commas/semicolons/newlines; keep it case-insensitive later."""
    if not raw:
        return []
    return [k.strip() for k in re.split(r"[,;\n]+", raw) if k.strip()][:40]


def keyword_filters(text_lower: str, exclude: Sequence[str], required: Sequence[str]) -> Optional[str]:
    """Return a human reason to drop the CV, or None to keep it."""
    hits = [k for k in exclude if k.lower() in text_lower]
    if hits:
        return "blacklist keyword(s) present in CV: " + ", ".join(hits)
    missing = [k for k in required if k.lower() not in text_lower]
    if missing:
        return "missing required keyword(s): " + ", ".join(missing)
    return None


def bonus_points(text_lower: str, criteria: Sequence[str], per_hit: float, cap: float) -> Tuple[float, List[str]]:
    hits = [c for c in criteria if c.lower() in text_lower]
    return round(min(len(hits) * per_hit, cap), 2), hits


def verdict_of(score: float) -> Tuple[str, str]:
    for floor, label, action in BANDS:
        if score >= floor:
            return label, action
    return BANDS[-1][1], BANDS[-1][2]


def recruiter_remark(score: float, strengths: List[str], gaps: List[str],
                     bonus_hits: Sequence[str] = (),
                     core_met: bool = False) -> str:
    """The structured 'Recruiter Summary Remark' shown on every scorecard.

    For strong candidates it foregrounds the core strengths (first in the
    strengths list); when the engine's core-requirements audit passed
    (core_met), the remark can never read as a weak-fit verdict.
    """
    label, action = verdict_of(score)
    lead = strengths[0] if strengths else "no clear strength could be extracted from the CV text"
    gap = gaps[0] if gaps else "no significant gap detected against the JD"
    parts = [
        f"{label} — score {score:.1f}/100. {action}",
        f"Strongest evidence: {lead}",
        f"Main concern: {gap}",
    ]
    if score >= 70 and strengths:
        top = [s for s in strengths[1:4] or strengths[:1] if s]
        if top:
            parts.append("Core strengths: " + " ; ".join(top) + ".")
    if core_met:
        parts.append(
            "Core requirements (required qualification, professional "
            "registration and direct role history) verified — this "
            "candidate is not a weak fit.")
    if bonus_hits:
        parts.append("Bonus criteria matched: " + ", ".join(bonus_hits) + ".")
    elif score < 45:
        parts.append("None of the custom bonus criteria were matched.")
    return " ".join(parts)


def exclusion_remark(reason: str) -> str:
    return (
        f"EXCLUDED — {reason}. This candidate was filtered out before scoring, "
        "so the score is not comparable with the ranked list. Remove the "
        "filter (or the blacklist row) to evaluate them."
    )


def error_remark(reason: str) -> str:
    return (
        f"NOT EVALUATED — {reason}. The rest of the batch continued normally; "
        "request a fresh copy of this file."
    )


# ── Scorecard row shaping (shared by the UI, the table and the exporters) ──

# The engine's own CV-education detector, so the scorecard and the
# pre-scoring filter can never disagree with each other.
_education_match = getattr(ev, "_education_match", None)


def education_verdict(text: str, accepted: Sequence[str]) -> Optional[Tuple[bool, List[str]]]:
    """Does the CV's highest detected level satisfy *accepted*?

    Delegates to the engine's detector so the scorecard and the filter can
    never disagree with each other.  Returns None when nothing can be
    determined.
    """
    if _education_match is None or not accepted:
        return None
    try:
        meets, levels = _education_match(text, list(accepted))
    except Exception:
        return None
    if not levels:
        return None
    return meets, levels


def customize_instructions(score: float, strengths: Sequence[str],
                           core_met: bool = False,
                           facts: Optional[Dict] = None) -> str:
    """Step-4 aware audit line prepended to the candidate's strengths.

    - score >= 70  -> foreground the core strengths a recruiter must see
      first (active registration, specialised training, direct tenure).
    - core_met only -> explain in plain words WHY this is not a weak fit
      even though other buckets scored low.
    """
    facts = facts or {}
    if score >= 70:
        heads: List[str] = []
        if facts.get("registration"):
            heads.append(
                ("active " if facts.get("registration_active") else "")
                + f"professional registration ({facts['registration']})")
        if facts.get("certifications"):
            heads.append("specialised training ("
                         + ", ".join(facts["certifications"][:3]) + ")")
        yrs = facts.get("on_role_years") or facts.get("experience_years")
        if yrs:
            heads.append(f"{yrs:g} yrs of direct experience in this role line")
        if facts.get("education_levels"):
            heads.append("qualification verified ("
                         + ", ".join(facts["education_levels"][:3]) + ")")
        if not heads:
            heads = [s.split(".")[0] for s in strengths[:3] if s]
        if not heads:
            return ""
        return ("Core strengths for this role: " + "; ".join(heads)
                + ". These are the decisive factors behind the strong score.")
    if core_met:
        return ("Core requirements fulfilled — the required qualification, "
                "professional registration and direct role history are all "
                "present; treat as a viable candidate, not a weak fit.")
    return ""


def export_frame(rows: Sequence[Dict]) -> pd.DataFrame:
    """Flatten the scorecard rows into an export-ready table."""
    return pd.DataFrame([
        {
            "Rank": r.get("Rank", ""),
            "Candidate_File_Name": r["Candidate_File_Name"],
            "Candidate_Name": r.get("Candidate_Name", ""),
            "Email": r.get("Email", ""),
            "Phone_Number": r.get("Phone_Number", ""),
            "Years_of_Experience": r.get("Years_of_Experience", 0.0),
            "Skills_Summary": r.get("Skills_Summary", ""),
            "AI_Score": r.get("AI_Score", 0.0),
            "Match_Score": r["Match_Score"],
            "Bonus_Points": r.get("Bonus_Points", 0.0),
            "Status": r["Status"],
            "Strengths": " ".join(r.get("Strengths", [])),
            "Weaknesses": " ".join(r.get("Weaknesses", [])),
            "Recruiter_Remark": r.get("Recruiter_Remark", ""),
            "Summary_Reasoning": r.get("Summary_Reasoning", ""),
        }
        for r in rows
    ])


def _row(name: str, score: float, status: str, remark: str,
         reasoning: str, strengths: Optional[List[str]] = None,
         gaps: Optional[List[str]] = None, bonus: float = 0.0,
         profile: Optional[Dict] = None) -> Dict:
    profile = profile or {}
    return {
        "Rank": "",
        "Candidate_File_Name": name,
        "Candidate_Name": profile.get("name", ""),
        "Email": profile.get("email", ""),
        "Phone_Number": profile.get("phone", ""),
        "Years_of_Experience": profile.get("years_experience", 0.0),
        "Skills_Summary": profile.get("skills_summary", ""),
        "AI_Score": profile.get("ai_score", 0.0),
        "Match_Score": score,
        "Bonus_Points": bonus,
        "Status": status,
        "Strengths": strengths or [],
        "Weaknesses": gaps or [],
        "Recruiter_Remark": remark,
        "Summary_Reasoning": reasoning,
    }


# ══════════════════════════════════════════════════════════════════════════
# SECTION 4 -- THE SIDEBAR (JD configuration + filtering)
# ══════════════════════════════════════════════════════════════════════════

if "jd_area" not in st.session_state:
    st.session_state.jd_area = _default_jd()
if "results" not in st.session_state:
    st.session_state.results = None
if "meta" not in st.session_state:
    st.session_state.meta = {}
scored = []  # initialized for safe referencing outside the run block
_OVERRIDE_KEYS = ("exp_min", "exp_max", "edu_levels", "strict_edu",
                  "bonus_kw", "bonus_per", "bonus_cap")

with st.sidebar:
    st.title("🗂️ CV ↔ JD Matcher")
    st.caption(
        "Every requirement is read from the job description you paste — "
        "nothing is hardcoded."
    )
    st.divider()

    # ── 1. JD source ────────────────────────────────────────────────────
    st.subheader("1 · Job description")
    jd_file = st.file_uploader(
        "Load a JD (.txt, .docx or .pdf)", type=list(JD_EXTS),
        key="jd_upload", label_visibility="collapsed",
    )
    if jd_file is not None and st.session_state.get("_jd_loaded") != jd_file.name:
        body, jd_error = jd_text_from_upload(jd_file)
        if jd_error:
            st.error(jd_error)
        st.session_state["_jd_loaded"] = jd_file.name
        st.session_state.jd_area = body[:MAX_JD_CHARS]
        st.rerun()

    jd_text = st.text_area(
        "Paste the job description",
        key="jd_area",
        height=240,
        max_chars=MAX_JD_CHARS,
        help="Everything downstream (skills, education, experience, location, "
             "languages, reporting line) is mined from this text.",
    )

    profile = build_profile(jd_text or "")
    is_preview_mode = False

    if profile is None:
        from types import SimpleNamespace
        profile = SimpleNamespace(
            title="Recruitment Specialist (Sample)",
            min_experience_years=3.0,
            max_experience_years=8.0,
            education_levels=["bachelor"],
            hard_skills=["Sourcing", "Screening", "ATS"]
        )
        is_preview_mode = True
        st.info("💡 Paste a Job Description to generate a custom scoring profile. Currently showing illustrative filters below.")

    st.caption(
        f"**JD Profile:** {profile.title or 'untitled'} · "
        f"{profile.min_experience_years:g}–{profile.max_experience_years:g} yrs · "
        f"{', '.join(profile.education_levels) or 'no level stated'} · "
        f"{len(profile.hard_skills)} skills"
    )

    st.divider()

    # ── 2. Experience / education / bonus overrides ─────────────────────
    st.subheader("2 · JD configuration")
    st.caption("Override what the JD says — the score reacts immediately.")

    auto_min = float(profile.min_experience_years or 0.0)
    auto_max = float(profile.max_experience_years or auto_min)

    def _snap(value: float, step: float = 0.5) -> float:
        """Land on the widget's step grid.

        HTML number inputs reject values that are not min_value + k*step
        ("the two nearest valid values are 91 and 101"), so a JD that says
        3.25 years would otherwise render an aria-invalid field.
        """
        return round(round(value / step) * step, 2)

    c1, c2 = st.columns(2)
    exp_min = c1.number_input(
        "Experience required — min (yrs)", 0.0, 50.0,
        value=_snap(st.session_state.get("exp_min", min(auto_min, 50.0))),
        step=0.5, key="exp_min",
        help=f"JD says {auto_min:g} yr(s).",
    )
    exp_max = c2.number_input(
        "Experience required — max (yrs)", 0.0, 50.0,
        value=_snap(st.session_state.get("exp_max", min(auto_max, 50.0))),
        step=0.5, key="exp_max",
        help=f"JD says {auto_max:g} yr(s) (0 = open-ended).",
    )

    edu_levels = st.multiselect(
        "Education level accepted",
        list(EDU_LEVELS),
        default=st.session_state.get("edu_levels") or [
            e for e in profile.education_levels if e in EDU_LEVELS
        ] or ["bachelor"],
        key="edu_levels",
        help="Leave as-is to accept whatever the JD states.",
    )
    strict_edu = st.checkbox(
        "Enforce education level as a hard filter",
        value=st.session_state.get("strict_edu", False),
        key="strict_edu",
        help="Drop CVs whose detected highest qualification is below the "
             "lowest level selected above (instead of merely scoring them lower).",
    )

    st.markdown("**Custom / bonus criteria**")
    bonus_kw = st.text_area(
        "One per line (or comma separated)",
        key="bonus_kw",
        height=88,
        placeholder="community mobilization\nRohingya\nCox's Bazar\nreport writing",
        help="Every matched criterion adds points on top of the engine score.",
    )
    b1, b2 = st.columns(2)
    bonus_per = b1.slider("Points per match", 0.0, 5.0, 1.0, 0.5, key="bonus_per")
    bonus_cap = b2.slider("Max bonus", 0.0, 20.0, 6.0, 1.0, key="bonus_cap")

    st.markdown("**Custom recruiter instructions**")
    st.text_area(
        "Priority weighting for this run (optional)",
        key="custom_instructions",
        height=88,
        placeholder=("Prefer sector/NGO field experience rather than total "
                     "years of experience\nMust hold active BNMC registration"),
        help=("Step 4 of the deep scan parses this text and adjusts that "
              "category's weight for every candidate in the run: "
              "sector/NGO preference, licence/registration, location, "
              "education, skills and language mentions. Blank = engine "
              "defaults (weights unchanged)."),
    )

    if st.button("↺ Reset overrides to the JD's own values", use_container_width=True):
        for k in _OVERRIDE_KEYS:
            st.session_state.pop(k, None)
        st.rerun()

    st.divider()

    # ── 3. Blacklist & keyword filtering ────────────────────────────────
    st.subheader("3 · Blacklist & filtering")
    use_blacklist = st.toggle(
        "Apply `blacklist.csv` (names + phones)", value=True, key="use_bl",
        help="A matching phone number excludes the CV outright. A name-only "
             "match is flagged for review unless you change the mode below.",
    )
    st.divider()

    # ── Custom blacklist upload ────────────────────────────────────────
    st.caption("Upload custom blacklist (.csv / .xlsx / .xls)")
    bl_upload = st.file_uploader(
        "Choose blacklist file",
        type=["csv", "xlsx"],
        help="Names and phone numbers in this file will be added to the blacklist.",
        key="custom_blacklist_uploader",
    )

    # Load custom blacklist if uploaded, otherwise fall back to default
    if bl_upload is not None:
        import os
        from pathlib import Path
        ext = os.path.splitext(bl_upload.name)[1].lower()
        import pandas as pd
        if ext == ".csv":
            df = pd.read_csv(bl_upload)
        else:  # .xlsx
            df = pd.read_excel(bl_upload, engine="openpyxl")
        # Assume columns: Name, Phone (or just Name)
        custom_entries = []
        for _, row in df.iterrows():
            name = str(row.get("Name", row.iloc[0] if len(row) > 0 else "")).strip()
            phone = str(row.get("Phone", "")).strip() if "Phone" in df.columns else ""
            if name:
                custom_entries.append((name, phone))
        # Merge with default blacklist
        default_entries = ev.load_blacklist()
        # Combine: custom entries take precedence
        merged = default_entries + [Be for Be in custom_entries if Be not in default_entries]
        entries = merged
    else:
        entries = ev.load_blacklist() if use_blacklist else []

    name_mode = st.selectbox(
        "When only the *name* matches",
        ["flag", "exclude", "off"],
        key="bl_mode",
        format_func=lambda v: {
            "flag": "flag — keep it, but mark it for review",
            "exclude": "exclude — drop the CV entirely",
            "off": "off — match phone numbers only",
        }[v],
    )
    exclude_kw = st.text_input(
        "Auto-exclude if the CV contains",
        key="excl_kw",
        placeholder="e.g. Dhaka, Chittagong",
        help="Comma separated. Use this for unwanted locations or deal-breakers.",
    )
    require_kw = st.text_input(
        "Auto-exclude if the CV is missing",
        key="req_kw",
        placeholder="e.g. site management, community mobilization",
        help="Comma separated. Use this for core skills a CV must show.",
    )

    st.divider()

    # ── 4. Output controls ──────────────────────────────────────────────
    st.subheader("4 · Output")
    top_n = st.number_input("Show top N candidates", 1, 1000, 100, step=1, key="top_n")
    min_score = st.slider("Minimum score to display", 0, 100, 0, key="min_score")


# ══════════════════════════════════════════════════════════════════════════
# SECTION 5 -- MAIN AREA: upload, screen, present
# ══════════════════════════════════════════════════════════════════════════

import streamlit.components.v1 as components

# TechText interactive canvas banner from React Bits
components.html("""
<!DOCTYPE html>
<html>
<head>
<style>
  body {
    margin: 0;
    background: transparent;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    overflow: hidden;
  }
  #tech-container {
    width: 100%;
    height: 100px;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(255, 255, 255, 0.03);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    backdrop-filter: blur(8px);
    cursor: pointer;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
</head>
<body>
<div id="tech-container" title="Interactive Dashboard Header — Drag, Hover, or Click!">
  <canvas id="canvas"></canvas>
</div>
<script>
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const container = document.getElementById('tech-container');

let width = container.clientWidth;
let height = container.clientHeight;
let dpr = window.devicePixelRatio || 1;

canvas.width = width * dpr;
canvas.height = height * dpr;

const text = "CV ↔ JD MATCHER";
let fontSize = Math.min(42, Math.floor(width / 15));
let pointer = { x: width / 2, y: height / 2, inside: false };

window.addEventListener('resize', () => {
  width = container.clientWidth;
  height = container.clientHeight;
  canvas.width = width * dpr;
  canvas.height = height * dpr;
  fontSize = Math.min(42, Math.floor(width / 15));
});

container.addEventListener('pointermove', e => {
  const rect = container.getBoundingClientRect();
  pointer.x = e.clientX - rect.left;
  pointer.y = e.clientY - rect.top;
  pointer.inside = true;
});

container.addEventListener('pointerleave', () => {
  pointer.inside = false;
});

function draw() {
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  ctx.font = `600 ${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Draw background text in Executive Gray/Blue
  ctx.fillStyle = '#1e293b';
  ctx.fillText(text, width / 2, height / 2);

  // If pointer inside, draw interactive reveal effect (Executive Blue accents)
  if (pointer.inside) {
    ctx.save();
    ctx.beginPath();
    ctx.arc(pointer.x, pointer.y, 100, 0, Math.PI * 2);
    ctx.clip();

    ctx.fillStyle = '#2563eb';
    ctx.fillText(text, width / 2, height / 2);

    ctx.strokeStyle = '#2563eb';
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 2]);
    ctx.strokeText(text, width / 2, height / 2);
    ctx.restore();
  }

  requestAnimationFrame(draw);
}

draw();
</script>
</body>
</html>
""", height=110)

st.caption(
    f"PDF or DOCX · up to {MAX_FILE_MB} MB each · {MAX_FILES} files and "
    f"{MAX_TOTAL_MB} MB per batch. Nothing is stored: files are read in memory "
    "and discarded when the page reloads."
)
st.caption(
    "🔒 Privacy: phone numbers, email addresses and postal addresses are "
    "redacted from the text before it is sent to Gemini. The local engine "
    "still evaluates the full CV, and contact details stay on this machine."
)

files = st.file_uploader(
    "Upload CVs",
    type=["pdf", "docx"],          # browser-side hint only; content is verified below
    accept_multiple_files=True,
    key="cv_uploads",
)
files = files or []
total_mb = sum(int(getattr(f, "size", 0) or 0) for f in files) / (1024 * 1024)

if files:
    c1, c2, c3 = st.columns(3)
    c1.metric("Files selected", len(files))
    c2.metric("Total size", f"{total_mb:.1f} MB")
    c3.metric("Per-file limit", f"{MAX_FILE_MB} MB")
    if len(files) > MAX_FILES or total_mb > MAX_TOTAL_MB:
        st.warning(
            f"This batch exceeds the limits ({MAX_FILES} files / "
            f"{MAX_TOTAL_MB} MB). Extra files will be rejected on the next run."
        )

run = st.button(
    "🚀  Run screening", type="primary", disabled=not files, key="run_screening",
)

if run:
    uploads, rejected = validate_uploads(files)

    if not uploads:
        st.error("No usable file survived validation.")
    else:
        entries = ev.load_blacklist() if use_blacklist else []
        excl_list = split_keywords(exclude_kw)
        req_list = split_keywords(require_kw)
        bonus_list = split_keywords(bonus_kw)
        custom_text = st.session_state.get("custom_instructions", "") or ""

        work = copy.deepcopy(profile)
        lo, hi = float(exp_min), float(exp_max)
        if hi < lo:
            hi = lo
        work.min_experience_years = lo
        work.max_experience_years = hi
        work.education_levels = list(edu_levels) or list(profile.education_levels)

        st.info(
            f"Screening **{len(uploads)}** file(s) · experience **{lo:g}–{hi:g} yrs** · "
            f"education **{', '.join(work.education_levels) or 'any'}** · "
            f"blacklist **{'on' if use_blacklist else 'off'}** · "
            f"keyword filters **{len(excl_list) + len(req_list)}** · "
            f"bonus criteria **{len(bonus_list)}**"
        )

        bar = st.progress(0.0, text="Starting…")
        status_box = st.empty()

        rows: List[Dict] = []
        n_ranked = n_excluded = n_errors = n_flagged = 0
        redactions = {"emails": 0, "phones": 0, "addresses": 0}
        n = len(uploads)

        for idx, up in enumerate(uploads, 1):
            status_box.markdown(f"**{idx}/{n}** · `{up.name}`")
            bar.progress(idx / n)

            # ── 1. read (per-file failures never stop the batch) ─────────
            try:
                text = read_upload(up)
            except CVReadError as exc:
                n_errors += 1
                rows.append(_row(up.name, 0.0, "READ ERROR",
                                 error_remark(str(exc)),
                                 f"Not evaluated: {exc}"))
                continue
            except Exception as exc:                      # noqa: BLE001
                n_errors += 1
                rows.append(_row(up.name, 0.0, "READ ERROR",
                                 error_remark(_short(exc)),
                                 f"Not evaluated: {_short(exc)}"))
                continue

            # ── 2. blacklist gate (phones exclude, names may only flag) ──
            try:
                verdict = ev.blacklist_check(text, up.name, entries, name_mode)
            except Exception:
                verdict = ev.BlacklistVerdict()
            if verdict.excluded:
                n_excluded += 1
                rows.append(_row(up.name, 0.0, "EXCLUDED",
                                 exclusion_remark(f"blacklisted — {verdict.excluded}"),
                                 f"EXCLUDED -- blacklisted candidate: {verdict.excluded}"))
                continue

            low = text.lower()

            # ── 3. recruiter keyword filters ─────────────────────────────
            reason = keyword_filters(low, excl_list, req_list)
            if reason is None and strict_edu:
                edu = education_verdict(text, work.education_levels)
                if edu is not None and not edu[0]:
                    reason = ("education below the required level "
                              f"(CV shows: {', '.join(edu[1])})")
            if reason:
                n_excluded += 1
                rows.append(_row(up.name, 0.0, "EXCLUDED",
                                 exclusion_remark(reason),
                                 f"EXCLUDED -- {reason}"))
                continue

            # ── 4. score: 60% local engine + 40% Gemini (redacted) ───────
            try:
                result = ev.score_candidate(text, work, custom_text)
            except Exception as exc:                      # noqa: BLE001
                n_errors += 1
                rows.append(_row(up.name, 0.0, "SCORING ERROR",
                                 error_remark(_short(exc)),
                                 f"Not evaluated: {_short(exc)}"))
                continue

            # Privacy layer: the raw text stays local (engine + contact
            # lookup). Gemini only ever sees the redacted copy.
            redacted, redaction_counts = redact_pii(text)
            for key, value in redaction_counts.items():
                redactions[key] = redactions.get(key, 0) + value
            contacts = extract_contact_fields(text, up.name)
            ai = gemini_screen(redacted, jd_text or "")

            ai_score = float(ai.get("match_score", 0.0))
            bonus, hits = bonus_points(low, bonus_list, bonus_per, bonus_cap)
            final = hybrid_score(result.score, ai_score, bonus)
            final_result = dataclasses.replace(result, score=final)
            reasoning = ev.build_reasoning(final_result)

            profile_data = {
                "name": ai.get("candidate_name") or contacts["name"],
                "email": contacts["email"] or ai.get("email", ""),
                "phone": contacts["phone"] or ai.get("phone", ""),
                "years_experience": ai.get("years_experience", 0.0),
                "skills_summary": ai.get("skills_summary", ""),
                "ai_score": round(ai_score, 1),
            }

            status_label = "RANKED"
            strengths, gaps = list(result.strengths), list(result.gaps)
            strengths += [s for s in ai.get("strengths", []) if s not in strengths]
            gaps += [g for g in ai.get("gaps", []) if g not in gaps]
            if verdict.review:
                status_label = "REVIEW"
                n_flagged += 1
                reasoning = (f"BLACKLIST REVIEW NEEDED -- {verdict.review}. "
                             f"{reasoning}")
                gaps = [f"Possible blacklisted candidate: {verdict.review}."] + gaps
            n_ranked += 1

            # Core-strengths / audit line leads the scorecard narrative.
            audit_line = customize_instructions(
                final, strengths, final_result.core_met, final_result.facts)
            if audit_line:
                strengths = [audit_line] + strengths

            rows.append(_row(
                up.name, final, status_label,
                recruiter_remark(final, strengths, gaps, hits,
                                 final_result.core_met),
                reasoning, strengths, gaps, bonus, profile_data,
            ))

        bar.progress(1.0, text="Done")
        status_box.markdown(
            f"**Processed {n} file(s)** — {n_ranked} scored · "
            f"{n_excluded} excluded · {n_errors} could not be read."
        )
        if any(redactions.values()):
            st.caption(
                "🔒 Privacy: "
                f"{redactions['emails']} email(s), {redactions['phones']} phone "
                f"number(s) and {redactions['addresses']} address line(s) were "
                "redacted before the Gemini call; the local engine used the raw text."
            )
        if gemini_model is None:
            st.caption("ℹ️ Gemini unavailable — this run was scored by the local engine alone.")
        else:
            st.caption(f"🤖 AI score by `{gemini_model_name()}` (40% of the final score).")

        # Rank the scored rows (excluded / unreadable rows keep a blank rank).
        scored = [r for r in rows if r["Status"] in ("RANKED", "REVIEW")]
        scored.sort(key=lambda r: r["Match_Score"], reverse=True)
        for i, r in enumerate(scored, 1):
            r["Rank"] = i

        # ── Position-Based Output Folders ──────────────────────────────────────
        # Create TOP_CV folder named after the job position
        position_title = profile.title or "Untitled_Position"
        safe_pos = "".join(c if c.isalnum() or c in " _-" else "_" for c in position_title).strip()
        topcv_dir = Path("3_Evaluation_Output") / safe_pos / "TOP_CV"
        topcv_dir.mkdir(parents=True, exist_ok=True)

        # Copy top-ranked candidates to TOP_CV with structured filenames
        for i, r in enumerate(scored, 1):
            status = r["Status"]
            if status in ("RANKED", "REVIEW"):
                # Find the original uploaded file by name
                cv_name = r["Candidate_File_Name"]
                # Determine original extension
                orig_ext = os.path.splitext(cv_name)[1].lower() or ".pdf"
                if orig_ext not in ALLOWED_EXTS:
                    orig_ext = ".pdf"
                clean_name = re.sub(r"[^\w\s\-]", "", cv_name.replace(".pdf", "").replace(".docx", ""))
                clean_name = re.sub(r"\s+", "_", clean_name).strip("_")
                new_filename = f"Rank_{i}_Score_{int(r['Match_Score']):02d}_{clean_name}{orig_ext}"
                dest_path = topcv_dir / new_filename
                # Save the CV file - read uploaded bytes and write to destination
                # Match by candidate name to the original upload
                for up in uploads:
                    if up.name == cv_name or up.name.replace(".pdf", "").replace(".docx", "") == clean_name:
                        try:
                            # Write bytes directly instead of shutil.copy2 with UploadedFile
                            dest_path.write_bytes(up.data)
                        except Exception:
                            pass
                        break

        st.session_state.results = rows
        st.session_state.rejected = rejected
        st.session_state.meta = {
            "n": n,
            "ranked": n_ranked,
            "excluded": n_excluded,
            "errors": n_errors,
            "flagged": n_flagged,
            "title": profile.title,
            "experience": f"{lo:g}–{hi:g}",
            "redactions": redactions,
            "ai_enabled": gemini_model is not None,
            "model": gemini_model_name(),
        }


# ══════════════════════════════════════════════════════════════════════════
# SECTION 6 -- RESULTS, SCORECARDS & EXPORT
# ══════════════════════════════════════════════════════════════════════════

rejected = st.session_state.get("rejected") or []
if rejected:
    with st.expander(f"⚠️ {len(rejected)} file(s) rejected during validation", expanded=False):
        for msg in rejected:
            st.markdown(f"- {msg}")

rows = st.session_state.get("results") or []
meta = st.session_state.get("meta", {})
ranked = [r for r in rows if r["Status"] in ("RANKED", "REVIEW")]
rest = [r for r in rows if r["Status"] not in ("RANKED", "REVIEW")]

st.divider()
sub_title = meta.get('title', profile.title or 'Job Description')
sub_exp = meta.get('experience', f"{exp_min:g}–{exp_max:g}")
sub = f"{sub_title} · experience {sub_exp} yrs"
st.subheader(f"Executive Candidate Shortlist — {sub}")

if not rows:
    st.info("💡 **Dashboard Ready**: Upload candidate CVs in the section above and click **🚀 Run screening** to calculate live candidate scores, detailed scorecards, and audit trails.")

scores = [r["Match_Score"] for r in ranked]
m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Evaluated", meta.get("n", len(rows)))
m2.metric("Ranked", len(ranked))
m3.metric("Excluded", meta.get("excluded", 0))
m4.metric("Unreadable", meta.get("errors", 0))
m5.metric("Avg Score", f"{sum(scores) / len(scores):.1f}" if scores else "—")
m6.metric("≥ 70 Points", sum(1 for s in scores if s >= 70))

if meta.get("flagged"):
    st.warning(
        f"{meta['flagged']} candidate(s) matched a blacklisted **name** only and "
        "are marked REVIEW — a name alone is not proof. Confirm manually."
    )

if rows and not ranked:
    st.warning("No candidate passed the active filters. Loosen the keyword or education filters in the sidebar.")

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 Ranked Table",
    "📋 Candidate Scorecards",
    "🚫 Audit Trail & Excluded",
    "📥 Export Reports"
])

# ── Tab 1: Table ──────────────────────────────────────────────────────────
with tab1:
    if ranked:
        table = export_frame(ranked)
        st.dataframe(table, hide_index=True, use_container_width=True)
    elif rows:
        st.info("No candidates passed the screening criteria.")
    else:
        st.info("📊 **Ranked Table Preview**: Upload CVs and run screening to populate the ranked candidate shortlist here.")

# ── Tab 2: Scorecards ─────────────────────────────────────────────────────
with tab2:
    visible = [r for r in ranked if r["Match_Score"] >= min_score][:int(top_n)]
    if visible:
        for r in visible:
            label, _ = verdict_of(r["Match_Score"])
            badge = "🟩" if r["Status"] == "REVIEW" else ""
            head = (f"#{r['Rank']} · {r['Candidate_File_Name']} · "
                    f"{r['Match_Score']:.1f}/100 · {label} {badge}")
            with st.expander(head, expanded=False):
                a, b, c = st.columns([1, 1, 2])
                a.metric("Score", f"{r['Match_Score']:.1f}")
                b.metric("Bonus", f"+{r['Bonus_Points']:g}")
                c.metric("Status", r["Status"])

                if r.get("Candidate_Name") or r.get("Email") or r.get("Phone_Number"):
                    st.markdown(
                        f"**👤 {r.get('Candidate_Name') or '—'}** · "
                        f"{r.get('Email') or 'no email'} · "
                        f"{r.get('Phone_Number') or 'no phone'} · "
                        f"{float(r.get('Years_of_Experience') or 0):g} yrs experience"
                    )
                if r.get("Skills_Summary"):
                    st.caption(f"🧠 {r['Skills_Summary']}")
                if r.get("AI_Score"):
                    st.caption(f"AI qualitative score (40% weight): {r['AI_Score']:g}/100")

                st.markdown("**✅ Strengths**")
                for s in r["Strengths"] or ["None recorded."]:
                    st.markdown(f"- {s}")

                st.markdown("**⚠️ Weaknesses / gaps**")
                for g in r["Weaknesses"] or ["None recorded."]:
                    st.markdown(f"- {g}")

                st.markdown("**📝 Recruiter summary remark**")
                st.info(r["Recruiter_Remark"])

                with st.expander("Full engine reasoning"):
                    st.caption(r["Summary_Reasoning"])
    elif rows:
        st.info("No candidate scorecards match the minimum score and top N criteria.")
    else:
        st.info("📋 **Scorecards Preview**: Detailed candidate strengths, weaknesses, and structured recruiter summary remarks will appear here after screening.")

# ── Tab 3: Excluded / unreadable ──────────────────────────────────────────
with tab3:
    if rest:
        for r in rest:
            st.markdown(f"**{r['Candidate_File_Name']}** — `{r['Status']}`")
            st.caption(r["Recruiter_Remark"])
            st.divider()
    elif rows:
        st.success("No candidates were excluded from this batch.")
    else:
        st.info("🚫 **Audit Trail Preview**: Any blacklisted or auto-excluded CVs will be audited here.")

# ── Tab 4: Export ─────────────────────────────────────────────────────────
with tab4:
    if rows:
        export_df = export_frame(rows)          # ranked first, then excluded/errors
        export_df = pd.concat(
            [export_df[export_df["Status"].isin(["RANKED", "REVIEW"])],
             export_df[~export_df["Status"].isin(["RANKED", "REVIEW"])]],
            ignore_index=True,
        )

        csv_bytes = export_df.to_csv(index=False).encode("utf-8-sig")
        xlsx_buf = io.BytesIO()
        try:
            export_df.to_excel(xlsx_buf, index=False, engine="openpyxl",
                               sheet_name="Candidates")
            xlsx_bytes = xlsx_buf.getvalue()
            xlsx_ok = True
        except Exception as exc:                      # noqa: BLE001
            xlsx_bytes, xlsx_ok = b"", False
            st.warning(f"Excel export unavailable: {_short(exc)}")

        d1, d2, d3 = st.columns([1, 1, 2])
        d1.download_button(
            "Download CSV Report", data=csv_bytes, file_name="ranked_candidates_report.csv",
            mime="text/csv", disabled=not len(export_df),
        )
        d2.download_button(
            "Download Excel Report", data=xlsx_bytes, file_name="ranked_candidates_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            disabled=not xlsx_ok,
        )
        d3.caption(
            f"{len(export_df)} rows — every scored candidate first, followed by audit trail exclusions."
        )
    else:
        st.info("📥 **Export Preview**: CSV and Excel download reports will be generated upon running the candidate screening.")

st.divider()

# ═════════════════════════════════════════════════════════════════════════
# SECTION 7 — FAQ & HR WISDOM BANNERS
# ═════════════════════════════════════════════════════════════════════════

st.markdown("""
## Frequently Asked Questions

<details>
<summary>How are candidates scored?</summary>
The score combines multiple buckets: skills match, years of experience, 
education level, credentials, focus keywords, role alignment, industry relevance,
and language proficiency. Each bucket has a weight configured from the job 
description, and custom recruiter instructions can adjust those weights (Step 4).
</details>

<details>
<summary>What if a candidate is marked REVIEW (blacklisted name only)?</summary>
A name-only blacklist match means the candidate's name appeared in the blacklist 
but no phone number matched. This is a flag for manual review - a name alone is 
not sufficient evidence to exclude. Confirm the candidate's identity and 
credentials before making a decision.
</details>

<details>
<summary>Can I upload a password-protected PDF?</summary>
No. Password-protected PDFs are rejected during upload so the engine cannot 
read their content. Request an unlocked copy from the candidate.
</details>
""", unsafe_allow_html=True)

st.markdown("""
<details>
<summary>Which file formats are supported?</summary>
PDF (any text-based PDF) and DOCX (Microsoft Word 2007+) are supported. 
Scanned image-only PDFs will be flagged as "almost no text extracted" and 
require manual review.
</details>

### HR Wisdom Banners

<div class="wisdom-banner">
  <strong>Important:</strong> Always verify a candidate's claimed experience 
  against documented tenure in their CV. Automated scoring may miss nuanced 
  gaps between stated years and actual project involvement.
</div>

<div class="wisdom-banner">
  <strong>Pro tip:</strong> Use the education-level filter strategically. 
  Requiring a specific degree can dramatically shrink your pool - consider 
  whether the role truly needs that level, or if relevant experience outweighs 
  formal credentials.
</div>

<div class="wisdom-banner">
  <strong>Pro tip:</strong> Bonus criteria are additive - they boost a score 
  that already meets the core JD requirements. If a candidate scores below 45,
  bonus points won't pull them above the BORDERLINE threshold.
</div>

<style>
  .wisdom-banner {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.08);
    backdrop-filter: blur(8px);
    border-radius: 8px;
    padding: 0.8rem 1rem;
    margin: 0.5rem 0;
    font-size: 0.875rem;
    color: #cbd5e1;
  }
  .wisdom-banner strong {
    color: #0f0f23;
    font-weight: 500;
  }
</style>
""", unsafe_allow_html=True)

st.divider()

st.caption(
    "Scoring is done by `evaluator.py` - the same engine as the command line, "
    "so the web and CLI results are identical. See SECURITY.md for the threat "
    "model."
)