#!/usr/bin/env python3
"""evaluator.py  --  100% Dynamic CV Screening, Ranking & Shortlisting

No hardcoded keywords. Every run reads the active JD and dynamically extracts
ALL requirements, skills, education fields, and context directly from that file.

The weighted priority scoring engine evaluates requirements by these pillars:
  Core Skills & Technical Fit:        High Weight (40%)
  Years of Experience & Education:    Medium Weight (30%)
  Domain Knowledge / Responsibilities: Lower/Support Weight (30%)

Also implements the Position-Based Output Workflow:
  - Parses uploaded JD, extracts the exact Job Title
  - Creates output directory named after the position
  - Creates TOP_CV/ subdirectory where top-ranked candidate PDFs are auto-copied
    and renamed (e.g. Rank_1_Score_88_CandidateName.pdf)
"""

import io
import gc
import csv
import os
import re
import sys
import math
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Set, Tuple

# ═════════════════════════════════════════════════════════════════════════
# SECTION 0 -- CONFIGURATION (edit here)
# ══════════════════════════════════════════════════════════════════════════

# ── PRIORITY MODE ────────────────────────────────────────────────────────
# Controls which pillar dominates the ranking. Each mode assigns different
# weights to Core Skills, Experience/Education, and Domain Knowledge so the
# total always sums to 100. Each mode also writes to its own CSV + folder.

PRIORITY_MODE = "experience"   # options: "experience", "qualification", "balanced"

if PRIORITY_MODE == "experience":
    # Core Skills 15 + Experience 45 + Domain 40 = 100
    FOCUS_WEIGHT    = 15.0   # Focus area (e.g. "site management")
    ROLE_WEIGHT     = 45.0   # Current role fit + SM-line experience
    HISTORICAL_FACTOR = 0.8  # a past same/similar title counts almost fully
    B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 15.0, 45.0, 8.0, 5.0
    B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG     = 10.0, 7.0, 5.0, 5.0
elif PRIORITY_MODE == "qualification":
    # Education 25 + Skills 15 + Domain 30 + Experience 15 + Focus 15 = 100
    FOCUS_WEIGHT    = 15.0
    ROLE_WEIGHT     = 15.0
    HISTORICAL_FACTOR = 0.33
    B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 25.0, 15.0, 22.0, 13.0
    B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG     = 10.0, 5.0, 5.0, 5.0
else:  # balanced
    # Skills 35 + Experience 25 + Domain 20 + Focus 12 + Edu 8 = 100
    FOCUS_WEIGHT    = 12.0
    ROLE_WEIGHT     = 25.0
    HISTORICAL_FACTOR = 0.33
    B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 35.0, 25.0, 15.0, 5.0
    B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG     = 10.0, 5.0, 5.0, 5.0

# ── FOCUS AREA ──────────────────────────────────────────────────────────
# The ranking is weighted towards this competency. Leave empty ("", the
# default) and the focus is DERIVED FROM THE ACTIVE JD on every run --
# derive_focus_area() below turns the posting's own title into the focus,
# so uploading a Medical Officer, Sales Engineer or Site Management JD
# re-anchors the whole engine on that domain automatically.
# Set a non-empty string (comma-separate several areas) to pin the focus
# manually instead; the CLI --focus-area flag does the same per run.
FOCUS_AREA   = ""

# Explicit user pin (CLI --focus-area). None means "derive from the JD".
_FOCUS_AREA_OVERRIDE: Optional[str] = None

# ── CURRENT-ROLE FIT ────────────────────────────────────────────────────
# The candidate's CURRENT / most-recent job title is graded against the
# active JD's own reporting line. The ladder is DERIVED from the uploaded
# JD (derive_role_tiers): its title opens tier 1, other role titles the JD
# text itself names fill tier 2, generic leadership titles fill tier 3:
#   tier 1  the post the JD is hiring for        -> full weight
#   tier 2  roles the JD names alongside/supporting it -> 80% of weight
#   tier 3  generic leadership titles             -> ~47% of weight
# A match found only as an *earlier* role scores HISTORICAL_FACTOR of the
# tier's points. The template below is only used when a JD yields no
# recognizable role title at all.

ROLE_TIERS: List[Tuple[str, float, tuple]] = [
    ("the post the JD is hiring for", 1.00, (
        "specialist", "engineer", "officer", "assistant",
        "manager", "coordinator", "facilitator",
    )),
    ("supporting role named in the JD", 0.80, (
        "coordinator", "facilitator", "supervisor", "advisor",
        "executive", "analyst", "consultant", "technician",
        "administrator", "trainer", "recruiter", "accountant",
    )),
    ("leadership", 0.47, (
        "team leader", "field leader", "coordinator", "supervisor",
        "program manager", "programme manager", "project manager",
        "officer in charge", "oic", "head of", "chief", "manager",
    )),
]

# Every other category shrinks proportionally so the total still = 100.
_SCALE = max(0.0, 100.0 - FOCUS_WEIGHT - ROLE_WEIGHT) / 100.0

# ── FOLDER PATHS ────────────────────────────────────────────────────────
# These are the dynamic output folders used by the Position-Based Output Workflow.

JD_FOLDER        = Path("1_Active_JD")
CV_FOLDER        = Path("2_Active_CVs")
OUTPUT_FOLDER    = Path("3_Evaluation_Output")
TOP_CV_FOLDER    = Path("TOP_CV")

# Mode-owned shortlist folder + CSV (set per PRIORITY_MODE)
if PRIORITY_MODE == "experience":
    SHORTLIST_FOLDER = Path("TOP_CV")
    OUTPUT_CSV       = Path("3_Evaluation_Output") / "ranked_candidates_report.csv"
elif PRIORITY_MODE == "qualification":
    SHORTLIST_FOLDER = Path("TOP_CV_QUALIFICATION")
    OUTPUT_CSV       = Path("3_Evaluation_Output") / "ranked_candidates_qualification.csv"
else:  # balanced
    SHORTLIST_FOLDER = Path("TOP_CV_BALANCED")
    OUTPUT_CSV       = Path("3_Evaluation_Output") / "ranked_candidates_balanced.csv"

BATCH_SIZE       = 100  # Support up to 1000+ CVs with background batching
TOP_N_CANDIDATES   = 200  # Default number of candidates to shortlist

# ── Windows console UTF-8 safety ────────────────────────────────────────
# Only wrap streams that really expose a binary buffer. Hosts such as
# Streamlit (or any embedded runner) replace stdout/stderr with a writer
# that has no `.buffer`; blindly wrapping those raised AttributeError at
# import time and took the whole program down with it.
if sys.platform == "win32":
    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        _buffer = getattr(_stream, "buffer", None)
        if _buffer is None or not hasattr(_buffer, "write"):
            continue
        try:
            setattr(sys, _stream_name,
                    io.TextIOWrapper(_buffer, encoding="utf-8",
                                     errors="replace"))
        except Exception:  # already wrapped / detached -- leave as is
            pass

from pypdf import PdfReader
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("evaluator")

# ═════════════════════════════════════════════════════════════════════════
# SECTION 0b -- BLACKLIST (candidates that must never be shortlisted)
# ══════════════════════════════════════════════════════════════════════════

BLACKLIST_FILE = Path("blacklist.csv")

# How far into the CV we look for the candidate's OWN name.
BLACKLIST_HEADER_CHARS = 500

# Honorifics are dropped when building the shorter name variant, so
# "MD Abdul Mamun" also catches a CV that only says "Abdul Mamun".
_HONORIFICS = frozenset({
    "md", "mohammad", "mohammed", "mohd", "muhammad",
    "mr", "mrs", "ms", "mst", "ska", "sk", "sheikh",
})

# A run of digits (with separators) long enough to be a phone number.
_DIGIT_RUN = re.compile(r"[0-9][0-9\s\-().+]{7,}[0-9]")



@dataclass
class BlacklistEntry:
    """One blacklist row: name aliases + phone fingerprints."""
    label: str
    names: List[str]
    phones: List[str]


@dataclass
class BlacklistVerdict:
    """Outcome of the blacklist gate for one CV.

    `excluded` is set only for a conclusive (phone) match -- the CV is then
    scored 0 and can never be shortlisted.  `review` is a name-only match that
    a human must confirm.
    """
    excluded: Optional[str] = None
    review: Optional[str] = None


def _norm_name(value: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace."""
    value = value.lower()
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    return " ".join(value.split())


def _norm_phone(value: str) -> str:
    """Phone fingerprint = last 10 digits (handles +880 / 880 prefixes)."""
    digits = re.sub(r"\D", "", value)
    return digits[-10:] if len(digits) > 10 else digits


def _name_variants(alias: str) -> List[str]:
    """Full normalised alias plus a variant without leading honorifics."""
    norm = _norm_name(alias)
    if not norm:
        return []
    tokens = norm.split()
    out = [norm]
    trimmed = [t for t in tokens if t not in _HONORIFICS]
    if trimmed and trimmed != tokens:
        out.append(" ".join(trimmed))
    # A lone word is too weak a signal ("khan" matches half a camp).
    return [v for v in dict.fromkeys(out) if len(v.split()) >= 2]


def _phones_in(text: str) -> Set[str]:
    """Every phone fingerprint that appears in *text*."""
    found: Set[str] = set()
    for raw in _DIGIT_RUN.findall(text):
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 10:
            found.add(digits[-10:])
    return found


def load_blacklist(path: Path = BLACKLIST_FILE) -> List[BlacklistEntry]:
    """Read blacklist.csv. A missing file simply means 'no blacklist'."""
    if not path.exists():
        return []

    entries: List[BlacklistEntry] = []
    try:
        content = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        log.warning("Could not read blacklist %s: %s", path, exc)
        return []

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = next(csv.reader([line]))
        except (csv.Error, StopIteration):
            log.warning("  Skipping unparsable blacklist line: %s", line[:60])
            continue
        if len(row) < 2:
            continue
        label = " | ".join(" ".join(row[:-1]).split("|")).strip()
        if _norm_name(row[0]) in ("candidate name", "name", "candidate"):
            continue  # header row
        names: List[str] = []
        for alias in "\n".join(row[:-1]).split("|"):
            names += _name_variants(alias)
        phones = []
        for chunk in re.split(r"[;,\n]", row[-1]):
            fp = _norm_phone(chunk)
            if len(fp) >= 10:
                phones.append(fp)
        if names or phones:
            entries.append(BlacklistEntry(
                label=row[0].strip(),
                names=list(dict.fromkeys(names)),
                phones=list(dict.fromkeys(phones)),
            ))

    return entries


def merge_blacklist_entries(default_entries: List[BlacklistEntry], custom_df) -> List[BlacklistEntry]:
    """Merge custom blacklist entries from a DataFrame with default entries.
    
    Custom DataFrame should have columns: Name, Phone_Number, Email, Status, Notes
    Returns combined list of BlacklistEntry objects.
    """
    custom_entries: List[BlacklistEntry] = []
    
    # Process Name column
    if "Name" in custom_df.columns:
        for alias in custom_df["Name"].dropna().astype(str).tolist():
            names = _name_variants(alias)
            if names:
                custom_entries.append(BlacklistEntry(
                    label="Custom",
                    names=list(dict.fromkeys(names)),
                    phones=[],
                ))
    
    # Process Phone Number column
    if "Phone_Number" in custom_df.columns:
        for phone in custom_df["Phone_Number"].dropna().astype(str).tolist():
            fp = _norm_phone(phone)
            if len(fp) >= 10:
                # Check if this phone already exists in default entries
                already_exists = any(
                    fp in entry.phones for entry in default_entries
                )
                if not already_exists:
                    custom_entries.append(BlacklistEntry(
                        label="Custom",
                        names=[],
                        phones=[fp],
                    ))
    
    # Process Email column
    if "Email" in custom_df.columns:
        # Email matching is basic - store as label only
        for email in custom_df["Email"].dropna().astype(str).tolist():
            custom_entries.append(BlacklistEntry(
                label="Custom",
                names=[],
                phones=[],
            ))
    
    # Combine: add custom entries that don't already exist in default
    custom_labels = {entry.label for entry in custom_entries}
    merged = list(default_entries)
    for entry in custom_entries:
        if entry.label not in custom_labels or entry not in merged:
            merged.append(entry)
    
    return merged


def blacklist_check(
    text: str,
    file_name: str,
    entries: List[BlacklistEntry],
    name_mode: str = "flag",
) -> BlacklistVerdict:
    """Apply the blacklist to one CV.

    name_mode:
      "flag"    (default) -- a name-only hit is reported for review and the
                  CV is still scored/ranked;
      "exclude" -- a name-only hit excludes the CV like a phone hit does;
      "off"     -- ignore names entirely, phones only.
    """
    if not entries:
        return BlacklistVerdict()

    phones = _phones_in(text)
    stem_norm = _norm_name(file_name.rsplit(".", 1)[0])
    header_norm = _norm_name(text[:BLACKLIST_HEADER_CHARS])
    haystack = f" {stem_norm} {header_norm} "
    name_hit: Optional[str] = None

    for entry in entries:
        for phone in entry.phones:
            if phone in phones:
                return BlacklistVerdict(excluded=(
                    f"blacklisted phone number (…{phone[-4:]}, {entry.label})"
                ))
        if name_mode == "off" or name_hit:
            continue
        for variant in entry.names:
            if f" {variant} " in haystack:
                name_hit = (
                    f"name '{variant}' matches blacklist entry "
                    f"'{entry.label}' (no blacklisted phone number found)"
                )
                break

    if name_hit:
        if name_mode == "exclude":
            return BlacklistVerdict(excluded=f"blacklisted {name_hit}")
        return BlacklistVerdict(review=name_hit)
    return BlacklistVerdict()

# ══════════════════════════════════════════════════════════════════════════
# SECTION 1 -- DYNAMIC JD EXTRACTION (zero hardcoding)
# ══════════════════════════════════════════════════════════════════════════

# Generic "stop words" that appear in every JD and should not be treated
# as domain-specific skills.
_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "this", "that", "are", "was", "were",
    "has", "have", "had", "will", "can", "may", "shall", "must", "not",
    "but", "from", "any", "all", "its", "our", "who", "which", "their",
    "other", "such", "each", "than", "also", "into", "over", "only",
    "per", "via", "inc", "ltd", "co", "etc", "e.g", "i.e",
    "job", "vacancy", "apply", "salary", "date", "please", "note",
    "required", "responsibility", "responsibilities", "context",
    "benefit", "benefits", "status", "location", "deadline",
    "organization", "organization's", "expected", "demonstrate",
    "following", "level", "indicators", "behavioural", "core",
    "managerial", "values", "competencies", "competency",
    "read", "before", "apply", "encourage", "submit", "cv",
    "complete", "including", "contact", "number", "recent",
    "passport", "size", "photograph", "application", "accepted",
    "based", "early", "short-listed", "candidates", "shall",
    "special", "mention", "personal", "persuasion", "result",
    "disqualification", "candidature", "intermediary", "allowed",
    "expedite", "facilitate", "process", "zero-tolerance",
    "policy", "sexual", "exploitation", "abuse", "psea",
    "ideal", "committed", "upholding", "reporting", "incidents",
    "suspected", "actual", "cost", "involved", "positions",
    "never", "asks", "accept", "kind", "fees", "different",
    "mobile", "banking", "options", "person", "solicitation",
    "fund", "money", "favor", "applicant", "regarded", "fraudulent",
    "activities", "save", "paper", "tree", "water", "electricity",
    "better", "tomorrow", "address", "follow", "insight",
    "report", "company", "view", "find", "more", "services",
    "features", "available", "opportunities", "leading", "firm",
    "certified", "experienced", "business", "team", "working",
    "closely", "national", "international", "strategic", "partners",
    "unique", "provide", "complete", "solution", "managing", "growth",
    "would", "like", "know", "about", "limited", "engineer",
    "engineers", "engineering", "engineer's", "engineers",
})


@dataclass
class JDProfile:
    """Everything extracted dynamically from the active JD."""
    raw_text: str = ""
    title: str = ""
    min_experience_years: float = 0.0
    max_experience_years: float = 0.0
    location: str = ""
    education_levels: List[str] = field(default_factory=list)
    education_fields: List[str] = field(default_factory=list)
    industry_keywords: List[str] = field(default_factory=list)
    hard_skills: Dict[str, float] = field(default_factory=dict)
    soft_skills: List[str] = field(default_factory=list)
    responsibilities: List[str] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    focus_cores: List[str] = field(default_factory=list)
    focus_terms: List[str] = field(default_factory=list)
    # (tier label, fraction of ROLE_WEIGHT, [(phrase, compiled regex)])
    role_tiers: list = field(default_factory=list)


def _extract_title(text: str) -> str:
    """Pull the job title from the first line or nearby."""
    first_lines = text.strip().split("\n")[:5]
    for line in first_lines:
        line = line.strip()
        if len(line) > 5 and not line.startswith("Vacancy"):
            return line
    return "Unknown Title"


def _extract_experience(text: str) -> Tuple[float, float]:
    """Extract min and max years of experience from the JD."""
    text_lower = text.lower()
    min_yrs, max_yrs = 0.0, 0.0

    # Pattern: "3 to 5 years" or "3-5 years"
    m = re.search(r"(\d{1,2})\s*(?:to|-)\s*(\d{1,2})\s*(?:years?|yrs?)", text_lower)
    if m:
        min_yrs, max_yrs = float(m.group(1)), float(m.group(2))
        return min_yrs, max_yrs

    # Pattern: "at least X years"
    m = re.search(r"(?:at\s+least|minimum|min\.?)\s+(\d{1,2})\s*(?:years?|yrs?)", text_lower)
    if m:
        min_yrs = float(m.group(1))
        return min_yrs, min_yrs

    # Pattern: "X years of experience"
    m = re.search(r"(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)", text_lower)
    if m:
        min_yrs = float(m.group(1))
        return min_yrs, min_yrs

    return 0.0, 0.0


def _extract_location(text: str) -> str:
    """Extract job location from the JD."""
    cities = [
        "cox's bazar", "cox bazar", "cox`s bazar", "dhaka",
        "chittagong", "chattogram", "sylhet", "rajshahi",
        "khulna", "barishal", "rangpur", "mymensingh",
        "ukhia", "ukhiya", "tekhnaf", "ramu",
    ]
    lower = text.lower()
    for city in cities:
        if city in lower:
            return city.title()
    return ""


def _extract_education(text: str) -> Tuple[List[str], List[str]]:
    """Extract education levels and fields mentioned in the JD."""
    lower = text.lower()
    levels: list = []
    fields: list = []

    level_checks = [
        ("phd", ["ph.d", "phd", "doctorate"]),
        ("masters", ["masters", "m.sc", "mba", "m.eng", "master"]),
        ("bachelor", ["bachelor", "b.sc", "b.eng", "b.tech", "bss", "llb",
                      "ba ", "bs ", "b.a.", "b.s."]),
        ("diploma", ["diploma"]),
        ("ssc/hsc", ["ssc", "hsc", "h.s.c", "s.s.c", "secondary school"]),
        ("vocational", ["vocational", "technical training"]),
    ]
    for label, keywords in level_checks:
        if any(kw in lower for kw in keywords):
            levels.append(label)

    field_keywords = [
        "social science", "law", "legal", "communication", "humanities",
        "mechanical", "electrical", "civil", "computer science",
        "business", "management", "public administration",
        "international relations", "development studies",
        "environmental", "agriculture", "public health",
        "education", "psychology", "sociology", "anthropology",
        "political science", "economics", "finance",
    ]
    for fld in field_keywords:
        if fld in lower:
            fields.append(fld)

    return levels, fields


def _extract_industry(text: str) -> List[str]:
    """Extract industry / business area keywords."""
    lower = text.lower()
    industry_terms = [
        "ngo", "consulting", "manpower", "recruitment", "humanitarian",
        "development", "aid", "refugee", "camp", "unhcr", "iom",
        "red cross", "government", "public sector", "private sector",
        "health", "education", "environment", "finance", "banking",
        "telecom", "ict", "energy", "manufacturing", "construction",
        "agriculture", "garment", "rmg", "textile",
    ]
    found = []
    for term in industry_terms:
        if term in lower:
            found.append(term)
    return found


def _extract_languages(text: str) -> List[str]:
    """Extract language requirements."""
    lower = text.lower()
    lang_keywords = [
        "bangla", "bengali", "english", "hindi", "urdu", "arabic",
        "chittagonian", "rohingya", "rakhine", "french", "spanish",
        "mandarin", "chinese", "japanese", "korean",
    ]
    found = []
    for lang in lang_keywords:
        if lang in lower:
            found.append(lang.title())
    return found


def _extract_hard_skills(text: str) -> Dict[str, float]:
    """Dynamically extract domain-specific hard skills from the JD text.

    Uses a heuristic: pull noun phrases from the Skills/Expertise section
    and Responsibilities section, then weight them by frequency and
    section importance.
    """
    lower = text.lower()

    # Look for the "Skills & Expertise" section and extract bullet-like phrases
    skills_section = ""
    skills_match = re.search(
        r"skills?\s*&\s*expertise[:\s]*(.*?)(?:compensation|other\s+benefit|employment|$)",
        lower, re.DOTALL
    )
    if skills_match:
        skills_section = skills_match.group(1)

    # Look for "Additional Requirements" section
    addl_section = ""
    addl_match = re.search(
        r"additional\s+requirements?[:\s]*(.*?)(?:competencies|languages|responsibilities|$)",
        lower, re.DOTALL
    )
    if addl_match:
        addl_section = addl_match.group(1)

    # Look for "Job Responsibility" section
    resp_section = ""
    resp_match = re.search(
        r"job\s+responsibilit(?:y|ies)[:\s]*(.*?)(?:skills|compensation|application|deadline|$)",
        lower, re.DOTALL
    )
    if resp_match:
        resp_section = resp_match.group(1)

    combined = skills_section + " " + addl_section + " " + resp_section

    # Extract meaningful 1-3 word phrases (potential skills)
    raw_phrases = re.findall(
        r"\b([a-z]{3,}(?:\s+[a-z]{2,}){0,2})\b", combined
    )

    # Filter and weight
    skill_scores: Dict[str, float] = {}
    for phrase in raw_phrases:
        phrase = phrase.strip()
        words = phrase.split()
        # Skip pure stop words
        if all(w in _STOP_WORDS for w in words):
            continue
        # Skip phrases that are too generic
        if len(words) == 1 and words[0] in _STOP_WORDS:
            continue

        # Weight: skills section gets 1.5x, additional requirements 1.3x, responsibilities 1.0x
        weight = 1.0
        if phrase in skills_section:
            weight = 1.5
        elif phrase in addl_section:
            weight = 1.3

        # Bonus for multi-word phrases (more specific = more valuable)
        if len(words) >= 2:
            weight *= 1.4

        if phrase not in skill_scores:
            skill_scores[phrase] = weight
        else:
            skill_scores[phrase] = max(skill_scores[phrase], weight)

    # Also scan the full JD for any explicit skill lists
    # Look for capitalized phrases that might be skills
    for m in re.finditer(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+){0,2})\b", text):
        phrase = m.group(1).lower().strip()
        words = phrase.split()
        if all(w in _STOP_WORDS for w in words):
            continue
        if len(words) >= 2 and phrase not in skill_scores:
            skill_scores[phrase] = 1.0

    # Cap at top 50 skills to keep scoring sane
    sorted_skills = sorted(skill_scores.items(), key=lambda x: x[1], reverse=True)[:50]
    return dict(sorted_skills)


def _extract_responsibilities(text: str) -> List[str]:
    """Extract the list of responsibilities from the JD."""
    resp_match = re.search(
        r"job\s+responsibilit(?:y|ies)[:\s]*(.*?)(?:skills|compensation|application|deadline|$)",
        text, re.DOTALL | re.IGNORECASE
    )
    if not resp_match:
        return []
    section = resp_match.group(1)
    # Split on sentence boundaries or bullet-like markers
    items = re.split(r"(?:\r?\n|(?<=\.)\s+)", section)
    return [item.strip() for item in items if len(item.strip()) > 15]


# ══════════════════════════════════════════════════════════════════════
# Dynamic JD-domain derivation -- nothing below is industry-specific.
# The uploaded JD title/text alone decides the focus and the role ladder.
# ══════════════════════════════════════════════════════════════════════

# Posting boilerplate + seniority tags: stripped before a title becomes
# the scoring focus ("Junior Medical Officer" -> focus "medical").
_FOCUS_STOPWORDS = frozenset({
    "a", "an", "the", "of", "for", "and", "or", "in", "at", "on", "to",
    "with", "from", "by", "as", "is", "our", "your", "we", "who",
    "junior", "senior", "sr", "jr", "ft", "pt", "full", "part", "time",
    "vacancy", "job", "position", "role", "required", "needed", "opening",
    "re", "advertisement", "posting", "career", "opportunity", "hire",
    "hiring", "seeking", "work", "working", "must", "shall", "will",
    "ensure", "ability", "strong", "excellent", "preferred", "degree",
})

# Generic role heads: dropped from the focus phrase, but they keep
# anchoring the role ladder (derive_role_tiers).
_ROLE_HEADS = frozenset({
    "officer", "assistant", "manager", "coordinator", "facilitator",
    "specialist", "executive", "engineer", "analyst", "consultant",
    "advisor", "adviser", "supervisor", "administrator", "technician",
    "accountant", "recruiter", "trainer", "nurse", "doctor", "physician",
    "scientist", "developer", "designer", "planner", "auditor", "clerk",
    "secretary", "representative", "agent", "head", "director", "lead",
    "officer", "worker", "intern", "associate",
})

# Any noun-phrase ending in one of these, mined from the JD text itself,
# is a candidate job title for the role ladder.
_ROLE_NOUNS = (
    "officer", "assistant", "manager", "coordinator", "facilitator",
    "specialist", "executive", "engineer", "analyst", "consultant",
    "advisor", "adviser", "supervisor", "administrator", "technician",
    "accountant", "recruiter", "trainer", "nurse", "doctor", "physician",
    "scientist", "developer", "designer", "planner", "auditor", "clerk",
    "secretary", "representative", "agent", "head", "director", "lead",
    "worker", "intern", "associate", "officer",
)
_ROLE_PHRASE_RE = re.compile(
    r"\b([a-z][a-z'\-]+(?:\s+[a-z'\-]+){0,4}\s+(?:" + "|".join(_ROLE_NOUNS)
    + r")s?)\b",
    re.IGNORECASE,
)


def derive_focus_area(jd_text: str, jd_title: str = "") -> str:
    """Derive the scoring focus from the active JD itself.

    Returns the JD title minus posting boilerplate and generic role heads,
    so any industry -- Medical, Engineering, Sales, IT, NGO, ... --
    re-anchors the engine on its own domain the moment its JD is uploaded.
    Falls back to the JD's most repeated requirement phrase, and finally to
    "" (focus weighting off) when the JD gives nothing usable.
    """
    title = " ".join((jd_title or "").split()).lower()
    if title.startswith("unknown"):
        title = ""

    words = re.findall(r"[a-z'\-]+", title)
    content = [w for w in words if w not in _FOCUS_STOPWORDS and len(w) > 1]
    distinct = [w for w in content if w not in _ROLE_HEADS]
    focus = " ".join((distinct or content)[:5])
    if focus and len(focus) >= 4:
        return focus

    # Title was boilerplate: mine the most repeated 2-word phrase from the
    # JD's own requirement/responsibility text.
    lowered = (jd_text or "").lower()
    if not lowered.strip():
        return ""
    counts: Dict[str, int] = {}
    jwords = re.findall(r"[a-z'\-]+", lowered)
    for i in range(len(jwords) - 1):
        w1, w2 = jwords[i], jwords[i + 1]
        if w1 in _FOCUS_STOPWORDS or w2 in _FOCUS_STOPWORDS:
            continue
        if len(w1) < 3 or len(w2) < 3:
            continue
        phrase = f"{w1} {w2}"
        counts[phrase] = counts.get(phrase, 0) + 1
    repeated = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for phrase, n in repeated:
        if n >= 2:
            return phrase
    return ""


_PHRASE_BOUNDARIES = frozenset({
    "by", "with", "from", "as", "of", "to", "in", "on", "at", "for",
    "and", "or", "via", "through", "under", "per", "into", "onto",
})


def _clean_role_phrase(raw: str) -> str:
    """Trim a mined noun-phrase back to a plausible job title."""
    words = raw.lower().split()
    # Duty-line fragments collapse to the noun phrase itself when two or
    # more words precede the last preposition/conjunction:
    #   "data collected by field assistant" -> "field assistant"
    #   "head of department" (only 1 word before "of") stays intact.
    bounds = [i for i, w in enumerate(words) if w in _PHRASE_BOUNDARIES]
    if bounds and bounds[-1] >= 2:
        words = words[bounds[-1] + 1:]
    # Only leading posting boilerplate goes; prepositions stay so
    # "head of department" keeps matching the same phrase in a CV.
    while words and words[0] in _FOCUS_STOPWORDS:
        words.pop(0)
    # Drop leading short tokens ("pm medical officer" -> "medical officer").
    while words and len(words[0]) <= 2:
        words.pop(0)
    phrase = " ".join(words).strip(" -,:;|")
    if not (3 <= len(phrase) <= 60):
        return ""
    return phrase


def derive_role_tiers(jd_title: str, jd_text: str) -> List[Tuple[str, float, tuple]]:
    """Build the role ladder from the active JD -- any industry.

    Tier 1: the posting's own title plus every role phrase in the JD that
            shares a word with it (the post being hired + close variants).
    Tier 2: other role titles the JD text itself names (supporting posts).
    Tier 3: generic leadership titles (always relevant to any JD).
    Returns an empty tier only when the JD names no such phrases, so
    _find_current_role always walks the strongest tier first.
    """
    title_l = " ".join((jd_title or "").split()).lower()
    have_title = 3 < len(title_l) < 70 and not title_l.startswith("unknown")

    tier1: List[str] = [title_l] if have_title else []
    tier2: List[str] = []
    title_words = (set(re.findall(r"[a-z']+", title_l)) - _FOCUS_STOPWORDS) \
        if have_title else set()

    lowered = (jd_text or "").lower()
    for m in _ROLE_PHRASE_RE.finditer(lowered):
        phrase = _clean_role_phrase(m.group(1))
        if not phrase or phrase in tier1 or phrase in tier2:
            continue
        shares_title_word = bool(set(re.findall(r"[a-z']+", phrase)) & title_words)
        if shares_title_word:
            tier1.append(phrase)
        else:
            tier2.append(phrase)

    # Parenthesised acronyms this posting defines ("Officer-in-Charge (SMO)")
    # join tier 1 -- mined from THIS JD only, never injected from a previous one.
    for m in re.finditer(r"\(([A-Z]{2,6})\)", jd_text or ""):
        if m.group(1).lower() not in tier1:
            tier1.append(m.group(1).lower())
            break

    tiers: List[Tuple[str, float, tuple]] = []
    if tier1:
        tiers.append(("the post the JD is hiring for", 1.00, tuple(tier1[:12])))
    if tier2:
        tiers.append(("supporting role named in the JD", 0.80, tuple(tier2[:14])))
    tiers.append(("leadership", 0.47, ROLE_TIERS[2][2]))
    return tiers


def _focus_words() -> List[str]:
    """Split FOCUS_AREA on commas -> lowercase word list."""
    return [
        core.strip().lower()
        for core in FOCUS_AREA.split(",")
        if core.strip()
    ]


def _focus_vocabulary(jd_text: str) -> Tuple[List[str], List[str]]:
    """Build the focus vocabulary from FOCUS_AREA + the active JD text.

    Returns (cores, related):
      cores   -- the literal FOCUS_AREA phrases, e.g. "site management"
      related -- every 2-5 word phrase in the JD containing a focus word,
                 e.g. "site level activities", "site management field"
    Nothing is hardcoded: *related* is mined from the active JD itself.
    """
    cores = _focus_words()
    if not cores or not FOCUS_WEIGHT:
        return [], []

    focus_terms: Set[str] = set()
    for core in cores:
        focus_terms.update(w for w in re.findall(r"[a-z']+", core) if w)

    words = re.findall(r"[a-z'`]+", jd_text.lower())
    related: List[str] = []
    seen: Set[str] = set()
    for width in (2, 3, 4, 5):
        for i in range(len(words) - width + 1):
            window = words[i:i + width]
            if not any(w in focus_terms for w in window):
                continue
            phrase = " ".join(window)
            if phrase in seen or phrase in cores:
                continue
            seen.add(phrase)
            related.append(phrase)

    related.sort(key=lambda p: (len(p.split()), len(p)))
    return cores, related[:40]


def _focus_match(cv_text: str, profile: JDProfile) -> Tuple[float, str, str]:
    """Score how strongly the CV shows experience in the configured focus area.

    Returns (points, strength_note, gap_note).
      core phrase present .............. +8
      anchor-word related phrases ...... up to +6   (e.g. "site level ...")
      other focus-word phrases ......... up to +4   (e.g. "team management")
      experience context (within 160 chars of experience wording) ..... +4
    Capped at FOCUS_WEIGHT points.
    """
    cores = _focus_words()
    if not FOCUS_WEIGHT or not cores or not profile.focus_cores:
        return 0.0, "", ""

    lower = cv_text.lower()
    focus_terms = {w for core in cores for w in re.findall(r"[a-z']+", core)}
    # The shortest focus word is the broadest anchor ("site" of "site management")
    # it marks the domain-specific related phrases.
    anchor = min(focus_terms, key=len) if focus_terms else ""

    strong = [c for c in profile.focus_cores if c in lower]
    tier_a, tier_b = [], []
    for term in profile.focus_terms:
        if term in cores or term in lower:
            if term in lower:
                (tier_a if anchor in term.split() else tier_b).append(term)

    # Does a focus mention sit next to experience wording, which proves it
    # is actual experience rather than a passing keyword?
    contextual = False
    for term in (strong + tier_a + tier_b)[:8]:
        for m in re.finditer(re.escape(term), lower):
            window = lower[max(0, m.start() - 160): m.end() + 160]
            if re.search(
                r"experience|worked|\bwork\b|years|responsib|duties?|"
                r"role|assigned|managed|handled|involved|served|charge",
                window,
            ):
                contextual = True
                break
        if contextual:
            break

    pts = (8.0 if strong else 0.0)
    pts += min(len(tier_a) * 2.0, 6.0)
    pts += min(len(tier_b) * 1.0, 4.0)
    pts += 4.0 if contextual else 0.0
    pts = round(min(pts, FOCUS_WEIGHT), 1)

    if pts <= 0:
        return 0.0, "", (
            f"No {FOCUS_AREA} experience detected in the CV -- this is the "
            f"primary focus of the active JD."
        )

    sample = (strong + tier_a + tier_b)[:5]
    note = (
        f"Focus '{FOCUS_AREA}': matched {len(strong)} core + "
        f"{len(tier_a)} domain-related + {len(tier_b)} general term(s) "
        f"({', '.join(sample)})"
        + (" with clear experience context."
           if contextual else " without explicit experience context.")
    )
    return pts, note, ""


def _phrase_regex(phrase: str) -> "re.Pattern":
    """Whole-word match for a job-title phrase, tolerant of natural
    morphological variants so CV wording maps onto the JD's own titles:

        midwife  -> midwifery, midwives, staff nurse ...  (via synonyms too)
        nurse    -> nurses, nursing, nursed
        engineer -> engineers, engineering

    Words before the last are matched literally (multi-word titles stay
    precise); only the final word flexes through light suffixation.
    """
    words = [w for w in re.findall(r"[a-z0-9'#+.-]+", phrase.lower()) if w]
    if not words:
        return re.compile(r"(?!x)x")          # empty phrase: never matches
    head, last = words[:-1], words[-1]

    alts = {last}
    if last.endswith("fe") and len(last) > 4:
        stem = last[:-1]                       # midwif
        alts.update({stem, stem + "s", stem + "es", stem[:-1] + "ves",
                     stem + "ing", stem + "ed", stem + "ery"})
    elif last.endswith("e") and len(last) > 4:
        stem = last[:-1]                       # nurs / coordinat...
        alts.update({stem, stem + "s", stem + "es", stem + "ing",
                     stem + "ed", stem + "ery"})
    elif last.endswith("y") and len(last) > 4:
        stem = last[:-1]
        alts.update({stem + "ies", stem + "ied", stem + "ier", stem + "y"})
    else:
        alts.update({last + "s", last + "es", last + "ing", last + "ed"})

    tail = "(?:" + "|".join(sorted(re.escape(a) for a in alts)) + ")"
    if head:
        body = r"\s+".join(re.escape(w) for w in head) + r"\s+"
        pattern = r"(?<![a-z])" + body + tail + r"(?![a-z])"
    else:
        pattern = r"(?<![a-z])" + tail + r"(?![a-z])"
    return re.compile(pattern)


# ── Curated cross-titles for professional cadres ───────────────────────
# When a JD names one of these, the equivalents join the SAME tier, so a
# "Midwife" posting accepts "Nursing Officer" / "Staff Nurse" histories
# instead of failing them (and vice versa). Keys are matched against the
# JD-derived phrases, including single words inside longer titles.
_ROLE_SYNONYMS: Dict[str, tuple] = {
    "midwife": ("midwifery", "midwives", "staff nurse", "nursing officer",
                "nurse", "nursing", "sba"),
    "midwifery": ("midwife", "midwives", "staff nurse", "nursing officer",
                  "nurse"),
    "midwives": ("midwife", "midwifery", "staff nurse", "nursing officer"),
    "nurse": ("nursing officer", "staff nurse", "midwife", "midwifery",
              "nursing"),
    "nursing": ("nurse", "staff nurse", "nursing officer"),
    "staff nurse": ("nurse", "nursing officer", "midwife"),
    "nursing officer": ("nurse", "staff nurse", "midwife", "medical officer"),
    "medical officer": ("doctor", "physician", "general practitioner"),
    "doctor": ("medical officer", "physician"),
    "physician": ("doctor", "medical officer"),
    "engineer": ("engineering",),
    "engineering": ("engineer",),
    "specialist": ("specialist officer",),
    "paramedic": ("paramedicine", "emergency medical technician"),
    "technician": ("technologists",),
}


def _role_variants(phrase: str) -> List[str]:
    """A tier phrase plus its curated equivalents (order preserved)."""
    out: List[str] = [phrase]
    for key in (phrase, *phrase.split()):
        for syn in _ROLE_SYNONYMS.get(key, ()):
            if syn not in out:
                out.append(syn)
    return out



def build_role_tiers(profile: JDProfile) -> list:
    """Role-expertise ladder derived entirely from THIS JD (never carried over).

    The JD-specific rungs come from derive_role_tiers -- the posting's own
    title plus the role phrases its own text names -- and the
    domain-neutral generic role heads from the ROLE_TIERS template are
    merged in so a noisy title still grades fairly. Every phrase is
    precompiled once so the per-CV loop stays cheap.
    """
    ladder = list(derive_role_tiers(profile.title or "", profile.raw_text))

    # Full-weight rung: this JD's phrases first, generic role heads behind.
    if not ladder or ladder[0][1] < 1.0:
        ladder.insert(0, (ROLE_TIERS[0][0], 1.00, ()))
    label, frac, phrases = ladder[0]
    ladder[0] = (label, frac, tuple(
        list(phrases) + [p for p in ROLE_TIERS[0][2] if p not in phrases]))

    # 80% rung: JD-named supporting roles, or the generic template's when none.
    idx = next((i for i, t in enumerate(ladder) if t[1] == 0.80), None)
    if idx is None:
        ladder.insert(1, (ROLE_TIERS[1][0], ROLE_TIERS[1][1], ROLE_TIERS[1][2]))
    else:
        label, frac, phrases = ladder[idx]
        ladder[idx] = (label, frac, tuple(
            list(phrases) + [p for p in ROLE_TIERS[1][2] if p not in phrases]))

    tiers: list = []
    for label, frac, phrases in ladder:
        pairs: list = []
        for ph in phrases:
            ph = ph.strip().lower()
            if not ph:
                continue
            # Fuzzy variants + curated cross-titles join the SAME tier.
            for variant in _role_variants(ph):
                v = variant.strip().lower()
                if v and v not in [p for p, _ in pairs]:
                    pairs.append((v, _phrase_regex(v)))
        if pairs:
            tiers.append((label, frac, pairs))
    return tiers


# Experience-section / "still current" markers used to isolate the
# candidate's current (most recent) job title.
_SECTION_RE = re.compile(
    r"(?:work|professional|employment|job|career)\s+experience"
    r"|(?:experience)\s+(?:profile|summary|details)"
    r"|employment\s+(?:history|record|profile)"
    r"|work\s+history|career\s+(?:history|summary|profile)",
    re.IGNORECASE,
)
_CURRENT_RE = re.compile(
    r"\b(?:present|till\s+date|to\s+date|continuing|ongoing|"
    r"currently\s+working|upto\s+date)\b",
    re.IGNORECASE,
)


def _title_line(cv_text: str, idx: int, fallback: str) -> str:
    """The whole line around *idx*, used as the displayed job title."""
    line_start = cv_text.rfind("\n", 0, idx) + 1
    line_end = cv_text.find("\n", idx)
    if line_end < 0:
        line_end = len(cv_text)
    line = " ".join(cv_text[line_start:line_end].split())
    line = re.sub(r"^[\s:|,;\-–—•\.]+" , "", line)
    if not (2 <= len(line) <= 70):
        return fallback
    return line


_TITLE_NOISE = re.compile(
    r"\b(experience|ability|strong|-skilled|skills?|years?|worked|"
    r"responsible|summary|qualification|pressure|stakeholders?)\b"
)

# "Community Mobilization (1.5 yr)" style Area-of-Expertise tags:
# a duration in parentheses right after the phrase marks a SKILL TAG,
# not a job title. Real titles put the duration after the employer or
# at line end following the org name ("1. Field Assistant (7.8yrs)"),
# and always with a leading enumeration -- require the phrase itself
# not to be immediately followed by "(N.N yr".
_EXPERTISE_TAG = re.compile(r"\(\d+(?:\.\d+)?\s*yrs?\b", re.IGNORECASE)


def _is_title_line(cv_text: str, idx: int) -> bool:
    """True when the text around *idx* looks like a JOB TITLE, not a sentence.

    A tier phrase buried in a skills/prose line ("Strong
    experience in community engagement, coordination with ...") must
    not be mistaken for the candidate's job title.
    """
    line_start = cv_text.rfind("\n", 0, idx) + 1
    line_end = cv_text.find("\n", idx)
    if line_end < 0:
        line_end = len(cv_text)
    line = " ".join(cv_text[line_start:line_end].split())
    if not (2 <= len(line) <= 70):
        return False
    if len(line.split()) > 10:
        return False
    if _TITLE_NOISE.search(line.lower()):
        return False
    # Wrapped sentence fragments ("guidance by the supervisor.",
    # "Officers, and Community Mobilizers to build up") start
    # lowercase or carry commas -- real titles do neither.
    if line[0].islower() or "," in line:
        return False
    # Bullet items ("• Community Mobilization") are list entries
    # (training topics, duty areas), never job titles. Enumerated
    # entries ("1. Field Assistant (7.8yrs)") stay valid.
    if re.match(r"^[\u2022\u00b7\u25cf*\-\u2013\u2014>]", line):
        return False
    # Sub-section headings from reports/annexes
    # ("(b) Community Engagement and Trust Building -").
    if re.match(r"^\([a-z0-9]{1,2}\)\s", line) or line.endswith("-"):
        return False
    # Wrapped prose carrying mid-line subsection markers or sentence
    # breaks ("Guidelines. (d) Community Engagement to").
    if re.search(r"\([a-z0-9]{1,2}\)", line[3:]):
        return False
    if re.search(r"\.\s+[A-Z]", line[3:]):
        return False
    # Area-of-Expertise tag: "Community Mobilization (1.5 yr)" --
    # a decimal duration with a space marks a skill tag; real title
    # tenures are written without the space ("(7.8yrs)", "(0.4yrs)").
    after = cv_text[idx: idx + 60]
    if re.match(r"\s*\(\d+\.\d+\s+yr", after):
        return False
    return True


def _looks_like_job_entry(cv_text: str, idx: int) -> bool:
    """True when the line at *idx* carries an employment marker.

    A tenure in the title itself ("Field Facilitator (2.6yrs)")
    or a date range on the following line ("(14 Mar 2019 - 30 Apr 2023)").
    Extra-curricular / certification lists look identical but carry neither marker.
    """
    line_start = cv_text.rfind("\n", 0, idx) + 1
    line_end = cv_text.find("\n", idx)
    if line_end < 0:
        line_end = len(cv_text)
    line = cv_text[line_start:line_end]
    if re.search(r"\(\d+(?:\.\d+)?\s*yrs?\b", line, re.IGNORECASE):
        return True
    after = cv_text[line_end: line_end + 120]
    if re.search(r"\(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\s*[-–]", after):
        return True
    return False


def _find_current_role(cv_text: str, tiers: list) -> Tuple[str, str, str, float]:
    """Isolate the candidate's CURRENT (or most recent) job title and say
    which reporting-line tier it belongs to.

    Returns (display_title, tier_label, state, fraction) where state is
    one of 'current', 'listed', 'earlier' or '' when nothing matched.
    """
    lower = cv_text.lower()

    # 1. Start of the employment section (first job listed = most recent).
    # The header must be a short line of its own -- prose such as
    # "...professional experience, organizational skills, and ..." inside a
    # summary paragraph is NOT a section header.
    sec = None
    for m in _SECTION_RE.finditer(lower):
        ls = lower.rfind("\n", 0, m.start()) + 1
        le = lower.find("\n", m.start())
        if le < 0:
            le = len(lower)
        if len(" ".join(lower[ls:le].split())) <= 60:
            sec = m
            break
    start = sec.start() if sec else 0

    # 2. Window holding the current job: everything up to the first
    # "Present / till date" marker after that section header.
    cur = _CURRENT_RE.search(lower, start)
    if cur and 0 < cur.start() - start < 4000:
        window, origin, state = lower[start:cur.start()], start, "current"
    elif sec and len(lower[start:start + 500].strip()) > 40:
        window, origin, state = lower[start:start + 500], start, "listed"
    else:
        window, origin, state = lower[:500], 0, "listed"

    # 3. Highest tier present in the current window... The phrase must
    # sit on a TITLE-LIKE line; mentions buried in duty/skill prose
    # ("community engagement" inside a sentence) do not count.
    for label, frac, pairs in tiers:
        for phrase, rx in pairs:
            for m in rx.finditer(window):
                idx = origin + m.start()
                if "advertisement" in lower[max(0, idx - 200): idx + 200]:
                    continue  # bdjobs posting header, not a job
                if _is_title_line(cv_text, idx):
                    return (_title_line(cv_text, idx, phrase),
                            label, state, frac)

    # 4. ...otherwise the role may only appear as an earlier job.
    # Search only INSIDE the employment section: the bdjobs CV header
    # ("Job Title: Site Management Field Assistant (Re- advertisement)") sits
    # above it on every applicant's CV and is the posting one applied for --
    # not work experience.
    scan_from = start if sec else 0
    region = lower[scan_from:]
    region_offset = scan_from
    for label, frac, pairs in tiers:
        for phrase, rx in pairs:
            for m in rx.finditer(region):
                idx = region_offset + m.start()
                if "advertisement" in lower[max(0, idx - 200): idx + 200]:
                    continue  # bdjobs posting header, not a job
                if _is_title_line(cv_text, idx) and \
                        _looks_like_job_entry(cv_text, idx):
                    return (_title_line(cv_text, idx, phrase),
                            label, "earlier", frac)

    return "", "", "", 0.0


def _role_fit(cv_text: str, tiers: list) -> Tuple[float, str, str]:
    """Score the current / most-recent job title against the JD's own
    reporting line (tiers derived from the uploaded JD). Returns
    (points, strength_note, gap_note).
    """
    if not ROLE_WEIGHT or not tiers:
        return 0.0, "", ""

    title, label, state, frac = _find_current_role(cv_text, tiers)
    if not label:
        return 0.0, "", (
            "No current/most-recent job title could be mapped onto the "
            "JD's own reporting line (no role from this posting's tier "
            "ladder found in the CV)."
        )

    pts = ROLE_WEIGHT * frac
    if state == "earlier":
        pts *= HISTORICAL_FACTOR
    pts = round(min(pts, ROLE_WEIGHT), 1)

    how = {
        "current": "current title (marked 'present/till date')",
        "listed": "first-listed role in the employment section",
        "earlier": "only an earlier role -- current title is unrelated",
    }[state]

    if state == "earlier":
        note = (
            f"Role fit: '{title}' ({label}) appears only as an earlier role, "
            f"so it earns a reduced {pts:g}/{ROLE_WEIGHT:g} points."
        )
    elif frac >= 0.9:
        note = (
            f"Role fit: current title '{title}' is a {label} post -- exactly "
            f"what the JD is hiring ({pts:g}/{ROLE_WEIGHT:g} points; {how})."
        )
    elif frac >= 0.6:
        note = (
            f"Role fit: current title '{title}' {label} -- on the JD's own "
            f"reporting line ({pts:g}/{ROLE_WEIGHT:g} points; {how})."
        )
    else:
        note = (
            f"Role fit: current title '{title}' is a {label} position, "
            f"partially relevant ({pts:g}/{ROLE_WEIGHT:g} points; {how})."
        )
    return pts, note, ""


# ── Flexible experience / tenure parsing ───────────────────────────────
# Works on any CV layout, not just one exact date style:
#   "(13 Feb 2021 - Continuing)"   "13 Feb 2021 – Present"
#   "Feb 2021 - now"               "2021 - till date"
#   "Total year of experience: 6.4 yrs"   "6 years and 4 months"
# Month names are parsed manually (no locale dependence).
_MONTHS: Dict[str, int] = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_TOKEN = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
                r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
                r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_ONE = rf"(?:(?:\d{{1,2}}\s+)?{_MONTH_TOKEN}\.?\s+\d{{4}}|\d{{4}})"
_OPEN_END = (r"present|till\s+date|to\s+date|continuing|continues|"
             r"ongoing|running|now|till\s+now")
DATE_RANGE_RE = re.compile(
    rf"\(?\s*({_DATE_ONE})\s*(?:[-–—]|\bto\b)\s*({_DATE_ONE}|{_OPEN_END})\s*\)?",
    re.IGNORECASE)


def _parse_date_flexible(s: str) -> Optional[datetime]:
    """Parse '13 Feb 2021', 'Feb 2021', '13 February 2021', '2021' --
    locale-independent (manual month table, not strptime %b)."""
    s = re.sub(r"[.,]", " ", s or "").strip().lower()
    s = " ".join(s.split())
    if not s:
        return None
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        return datetime(y, 1, 1) if 1950 <= y <= 2100 else None
    m = re.fullmatch(r"(?:(\d{1,2})\s+)?([a-z]+)\.?\s*(\d{4})", s)
    if not m:
        return None
    day = int(m.group(1) or 1)
    mon = _MONTHS.get(m.group(2))
    year = int(m.group(3))
    if mon is None or not (1950 <= year <= 2100):
        return None
    try:
        return datetime(year, mon, day)
    except ValueError:
        return datetime(year, mon, 1)          # e.g. 31 Feb -> 1 Feb


def _span_years(start_s: str, end_s: str) -> Optional[float]:
    """Years between two flexible dates; open ends ('present', 'continuing')
    run to now. None when either side is unparseable."""
    s0 = _parse_date_flexible(start_s)
    if s0 is None:
        return None
    if re.fullmatch(_OPEN_END, (end_s or "").strip().lower()):
        s1 = datetime.now()
    else:
        s1 = _parse_date_flexible(end_s)
    if s1 is None:
        return None
    years = (s1 - s0).days / 365.25
    return years if 0.0 <= years <= 40.0 else None


def _declared_total_years(cv_text: str) -> float:
    """A CV's own declared experience total, in years.

    Catches 'Total year of experience: 6.4 yrs', 'experience: 6 years',
    '6 years of experience', '6 years and 4 months', anywhere in the CV.
    Returns 0.0 when the CV declares nothing.
    """
    low = cv_text.lower()
    best = 0.0
    patterns = (
        r"total\s+(?:years?|yrs?)\s*(?:of\s+)?experience[^0-9]{0,32}(\d+(?:\.\d+)?)",
        r"total\s+experience[^0-9]{0,32}(\d+(?:\.\d+)?)",
        r"experience\s*(?:of|:|\-)\s*(?:total\s+)?(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b",
        r"(\d+(?:\.\d+)?)\s*(?:\+|plus)?\s*(?:years?|yrs?)\s*(?:of\s+)?experience",
        r"experience\s+(?:of|:|\-)\s*(?:total\s+)?(\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b",
    )
    for pat in patterns:
        for m in re.finditer(pat, low):
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            if 0.0 < val <= 40.0:
                best = max(best, val)
    m = re.search(
        r"(\d+)\s*(?:years?|yrs?)\s*(?:and|&|,)\s*(\d+)\s*months?", low)
    if m:
        best = max(best, int(m.group(1)) + int(m.group(2)) / 12.0)
    return round(best, 2)


def _aggregate_span_years(cv_text: str) -> float:
    """Sum of every distinct date range in the CV (employment block),
    de-duplicated -- the fallback when no tenure can be tied to a title."""
    seen: Set[Tuple[str, str]] = set()
    total = 0.0
    for m in DATE_RANGE_RE.finditer(cv_text):
        key = (m.group(1).lower(), m.group(2).lower())
        if key in seen:
            continue
        seen.add(key)
        y = _span_years(m.group(1), m.group(2))
        if y:
            total += y
    return round(min(total, 40.0), 2)


def _sm_line_years(cv_text: str, tiers: list) -> Tuple[float, List[str]]:
    """Weighted YEARS of same/similar JD-line experience.

    Scans employment entries such as::

        8. Community Mobilizer (0.6yrs)
        (20 May 2018 - 20 Nov 2018)

    and keeps only those whose TITLE sits on the active JD's own
    reporting line (any role tier). Each tenure is weighted by its
    tier's fraction -- the post the JD hires at 1.0, JD-named
    supporting roles at 0.8, leadership at 0.47 -- so "experience"
    here means THIS posting's field experience, not total years worked.
    Skill-tag lines ("Community Mobilization (1.0 yr)") are excluded:
    an entry must be enumerated ("3. Title ...") or be a short title
    line followed by a date-range line.

    Returns (weighted_years, ["Title (0.6yrs", ...]).
    """
    if not tiers:
        return 0.0, []

    lines = cv_text.splitlines()
    tenure_end = re.compile(r"\((\d+(?:\.\d+)?)\s*yrs?\s*\)$", re.IGNORECASE)
    date_range = DATE_RANGE_RE
    enumerated = re.compile(r"^\d+\s*[.)]\s*")

    def _tier_hit(title_lower: str):
        for label, frac, pairs in tiers:
            for _phrase, rx in pairs:
                if rx.search(title_lower):
                    return label, frac
        return None

    total = 0.0
    roles: List[str] = []
    for i, raw in enumerate(lines):
        s = " ".join(raw.split())
        if not s or len(s) > 90:
            continue
        nxt = " ".join(lines[i + 1].split()) if i + 1 < len(lines) else ""
        nxt_is_date = bool(date_range.search(nxt))

        title = ""
        years: Optional[float] = None
        m = tenure_end.search(s)
        if m:
            title = s[:m.start()].strip(" |,-–—:·•")
            years = float(m.group(1))
            title_like = (
                bool(enumerated.match(s))
                or (nxt_is_date and len(s.split()) <= 10
                    and "," not in s)
            )
        elif (md := date_range.search(s)) is not None and (
                enumerated.match(s)
                or (2 <= len(s.split()) <= 14 and "," not in s)):
            # Title and range share ONE line:
            #   "1. Staff Nurse (Midwife) (13 Feb 2021 - Continuing)"
            title = (s[:md.start()] + " " + s[md.end():]).strip(" |,-–—:·•()[]")
            years = _span_years(md.group(1), md.group(2))
            title_like = bool(title)
        elif nxt_is_date and (enumerated.match(s)
                              or (2 <= len(s.split()) <= 10
                                  and "," not in s)):
            md = date_range.search(nxt)
            title = s.strip(" |,-–—:·•")
            years = _span_years(md.group(1), md.group(2))
            title_like = True
        else:
            title_like = False

        if not title_like or years is None or not title:
            continue
        # The bdjobs application header is an advertisement, not a job.
        pos = cv_text.find(raw, 0) if raw else -1
        if pos >= 0 and "advertisement" in cv_text[
                max(0, pos - 200): pos + 200].lower():
            continue
        hit = _tier_hit(title.lower())
        if not hit:
            continue
        label, frac = hit
        total += years * frac
        roles.append(f"{title} ({years:g}yrs, {label})")

    return round(total, 2), roles


# Which filenames are tried, in order, when the JD folder has no explicit
# --jd path. A posting can arrive as .txt, .docx or .pdf, so all three are
# accepted and the first one found wins.
JD_FILENAMES = ("current_jd.txt", "current_jd.docx", "current_jd.pdf",
                "jd.txt", "jd.docx", "jd.pdf")
JD_EXTS = (".txt", ".docx", ".pdf")

# Everything except tab, newline and carriage return.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def find_jd_file(folder: Path = JD_FOLDER, explicit: Optional[Path] = None) -> Optional[Path]:
    """Locate the active JD in any supported format.

    Order: an explicit --jd path, then the known filenames, then the newest
    .txt/.docx/.pdf sitting in the folder. Returns None when nothing is found.
    """
    if explicit is not None:
        return explicit if explicit.is_file() else None

    for name in JD_FILENAMES:
        candidate = folder / name
        if candidate.is_file():
            return candidate

    if folder.is_dir():
        loose = [p for p in folder.iterdir()
                 if p.is_file() and p.suffix.lower() in JD_EXTS]
        if loose:
            return max(loose, key=lambda p: p.stat().st_mtime)
    return None


def clean_control_chars(text: str) -> str:
    """Drop control characters (NULs are common in scraped postings).

    Real job-board exports carry stray NULs inside the Bengali "report this
    job" block. They break keyword matching, and a .docx cannot even be
    written from text containing them. Tabs, newlines and carriage returns are kept.
    """
    return _CONTROL_CHARS.sub("", text)


def extract_jd_text(jd_path: Path) -> str:
    """Read a job description from .txt, .docx or .pdf.

    Recruiters mostly share postings as Word or PDF, so the active JD is
    accepted in any of the three formats. DOCX/PDF support degrades
    gracefully: if the optional reader is missing the caller gets '' rather
    than a crash.
    """
    suffix = jd_path.suffix.lower()

    if suffix == ".pdf":
        # Extract text from PDF using PdfReader
        try:
            reader = PdfReader(str(jd_path))
            text_parts = []
            for page in reader.pages[:50]:  # max_pages
                try:
                    t = page.extract_text() or ""
                    if t.strip():
                        text_parts.append(t)
                except Exception:
                    continue
            pdf_text = "\n".join(text_parts)
            return clean_control_chars(pdf_text)
        except Exception as exc:
            log.warning("Could not read PDF %s: %s", jd_path.name, exc)
            return ""

    if suffix == ".docx":
        try:
            import docx as _docx  # python-docx (optional)
        except ImportError:
            log.warning("python-docx is not installed -- cannot read %s. "
                        "Install it with: pip install python-docx", jd_path.name)
            return ""
        try:
            document = _docx.Document(str(jd_path))
        except Exception as exc:
            log.warning("Could not read DOCX %s: %s", jd_path.name, exc)
            return ""
        parts: list = []
        for para in document.paragraphs:
            if para.text and para.text.strip():
                parts.append(para.text)
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells
                         if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return clean_control_chars("\n".join(parts))

    return clean_control_chars(
        jd_path.read_text(encoding="utf-8", errors="replace")
    )


def extract_jd_profile(txt_path: Path) -> JDProfile:
    """Read the active JD (.txt / .docx / .pdf) and build a JDProfile.

    EVERYTHING comes from the file -- nothing is hardcoded.
    """
    text = extract_jd_text(txt_path)
    prof = JDProfile()
    prof.raw_text = text
    prof.title = _extract_title(text)
    prof.min_experience_years, prof.max_experience_years = _extract_experience(text)
    prof.location = _extract_location(text)
    prof.education_levels, prof.education_fields = _extract_education(text)
    prof.industry_keywords = _extract_industry(text)
    prof.languages = _extract_languages(text)
    prof.hard_skills = _extract_hard_skills(text)
    prof.responsibilities = _extract_responsibilities(text)

    # ── Dynamic cross-industry focus ────────────────────────────────
    # Derive the scoring focus from THIS JD on every load: a new upload
    # (Medical, Sales, Engineering, IT, NGO, ...) re-anchors the engine
    # completely -- no cached focus, no leftover domain from a previous
    # posting. An explicit --focus-area / UI override always wins.
    global FOCUS_AREA
    if _FOCUS_AREA_OVERRIDE is not None:
        FOCUS_AREA = _FOCUS_AREA_OVERRIDE
    else:
        FOCUS_AREA = derive_focus_area(text, prof.title)
    prof.focus_cores, prof.focus_terms = _focus_vocabulary(text)
    prof.role_tiers = build_role_tiers(prof)

    # Soft skills are always relevant in any JD
    prof.soft_skills = [
        s for s in [  # common soft skills found in any professional JD
            "communication", "teamwork", "leadership", "problem-solving",
            "critical thinking", "adaptability", "time-management",
            "attention-to-detail", "organization"
        ]
        if s in prof.raw_text.lower()
    ]

    return prof


# ══════════════════════════════════════════════════════════════════════════
# SECTION 2 -- PER-CANDIDATE SCORING
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# MULTI-STEP DEEP EVALUATION PIPELINE
#   Step 1  mandatory credential & licence verification   (_credential_check)
#   Step 2  core domain & skill overlap (semantic)        (_skill_match)
#   Step 3  contextual experience & sector/location fit   (_industry/_location)
#   Step 4  custom recruiter instructions -> weights      (parse_custom_instructions)
# ══════════════════════════════════════════════════════════════════════════

# ---- Step 1: licences, registrations, certifications ----------------------
_LICENSE_NEED_RE = re.compile(
    r"\b(?:licen[cs]e|licen[cs]ed|licen[cs]ing|registration|registered|"
    r"reg\.?\s*no|council|bnmc|bmdc|gmc|pmdc|bar council|medical board)\b",
    re.IGNORECASE)

_COUNCIL_NAMES = (
    "nursing and midwifery council", "nursing council", "midwifery council",
    "medical council", "nursing association", "health directorate",
    "bnmc", "bmdc", "pmdc", "bar council",
)

_ACTIVE_WORDS = re.compile(
    r"\b(?:active|valid|current|renewed|upto|until|till)\b", re.IGNORECASE)

# Specialised trainings a JD may require -- matched against the JD first,
# so they only ever score when THIS posting asks for them.
_CERT_TOKENS = (
    "bemonc", "bemoc", "emonc", "safe delivery", "skilled birth attendant",
    "bls", "acls", "pals", "cpr", "itls", "als",
    "immunization", "immunisation", "family planning",
    "infection prevention", "infection control", "anesthesia", "anaesthesia",
    "ccna", "ccnp", "aws certified", "azure", "pmp", "itil", "cisa",
    "cissp", "autocad", "solidworks", "six sigma", "iso 9001", "iso 27001",
)


def _cert_requirements(jd_text: str) -> List[str]:
    """Certifications/trainings THIS JD asks for (bounded, JD-derived)."""
    jd_low = (jd_text or "").lower()
    found = [t for t in _CERT_TOKENS if t in jd_low]
    for m in re.finditer(
            r"certifi(?:ed|cation)[^.\n]{0,40}?(?:in|as)\s+"
            r"([a-z][a-z\s&/-]{2,40})", jd_low):
        phrase = m.group(1).strip(" .,;:-")
        if phrase and phrase not in found:
            found.append(phrase)
    return found[:8]


def _registration_evidence(cv_text: str) -> str:
    """Evidence of a professional registration/licence in the CV ('' if none)."""
    cv_low = (cv_text or "").lower()
    for name in _COUNCIL_NAMES:
        if name in cv_low:
            return name
    m = re.search(
        r"(?:registration|reg(?:d)?\.?\s*no\.?|licen[cs]e(?:\s+no\.?)?|"
        r"registration\s+no)\s*[:.#/-]*\s*(?:no\.?|number|#)?\s*[:.#/-]*"
        r"([a-z]{0,8}[-\s]?[a-z0-9]{3,20})", cv_low)
    if m:
        return m.group(0).strip(" .,;:-")
    return ""


def _token_stems(text: str) -> Set[str]:
    """Stemmed token index of a text, for semantic (non-literal) matching."""
    return {_stem_token(t)
            for t in re.findall(r"[a-z0-9][a-z0-9+#'.-]*", (text or "").lower())}


def _credential_check(cv_text: str, profile: JDProfile
                      ) -> Tuple[float, str, str, dict]:
    """STEP 1 -- Mandatory credential & licence verification.

    Awards baseline compliance points the moment the JD's required
    registration/licence or specialised training is found in the CV, and
    records a (never score-killing) gap when the JD demands one and the CV
    shows none. The returned facts feed the core-requirements audit flag.
    """
    jd_low = (profile.raw_text or "").lower()
    cv_low = (cv_text or "").lower()
    needs_lic = bool(_LICENSE_NEED_RE.search(jd_low))
    certs = _cert_requirements(jd_low)
    reg = _registration_evidence(cv_text) if needs_lic else ""

    active = False
    if reg:
        at = cv_low.find(reg)
        if at >= 0:
            active = bool(_ACTIVE_WORDS.search(
                cv_text[max(0, at - 90): at + len(reg) + 90]))

    facts = {
        "license_required": needs_lic,
        "license_ok": (not needs_lic) or bool(reg),
        "registration": reg,
        "registration_active": active,
        "cert_hits": [],
    }
    if not needs_lic and not certs:
        return 0.0, "", "", facts

    pts = 0.0
    notes: List[str] = []
    gaps: List[str] = []

    if needs_lic:
        if reg:
            pts += 4.0
            notes.append(
                "Credentials: "
                + ("active " if active else "")
                + f"professional registration/licence verified ({reg})")
        else:
            gaps.append(
                "The JD requires professional registration/licence -- "
                "none was found in the CV.")

    if certs:
        stems = _token_stems(cv_text)
        hits: List[str] = []
        for c in certs:
            if c in cv_low:
                hits.append(c)
                continue
            words = [w for w in re.findall(r"[a-z]+", c) if len(w) > 2]
            if words and all(_stem_token(w) in stems for w in words):
                hits.append(c)
        if hits:
            facts["cert_hits"] = hits
            pts += min(3.0, 1.5 * len(hits))
            notes.append(
                "Credentials: specialised training matched ("
                + ", ".join(hits[:4]) + ")")
        else:
            gaps.append(
                "Required certification/training not mentioned: "
                + ", ".join(certs[:4]) + ".")

    return (round(min(pts, 7.0), 1), " | ".join(notes), " | ".join(gaps), facts)


# ---- Step 2: semantic skill overlap (stemming + abbreviations) ------------
_EQUIV_GROUPS: Tuple[frozenset, ...] = (
    frozenset({"anc", "antenatal", "antenatal care"}),
    frozenset({"pnc", "postnatal", "post natal", "postnatal care"}),
    frozenset({"bemonc", "bemoc", "emergency obstetric"}),
    frozenset({"emonc", "obstetric emergency"}),
    frozenset({"sba", "skilled birth attendant"}),
    frozenset({"hiv", "aids"}),
    frozenset({"tb", "tuberculosis"}),
    frozenset({"opd", "outpatient", "out patient"}),
    frozenset({"icu", "intensive care"}),
    frozenset({"epi", "immunization", "immunisation"}),
    frozenset({"bp", "blood pressure"}),
    frozenset({"dm", "diabetes mellitus"}),
)
_EQUIV_BY_TOKEN: Dict[str, frozenset] = {
    w: grp for grp in _EQUIV_GROUPS for w in grp}


def _stem_token(t: str) -> str:
    """Light, consistent stem so JD phrasing and CV phrasing collapse to
    one key: counsel/counseling/counselling, manage/management,
    deliver/delivery/deliveries ..."""
    t = (t or "").lower().strip(".,;:()[]/'\"`*#|")
    if len(t) <= 3 or t.isdigit():
        return t
    for suf in ("ement", "ment", "ation", "ition", "ies", "ied",
                "ing", "ed", "es", "s", "e", "y"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[:-len(suf)]
            break
    if len(t) >= 4 and t[-1] == t[-2]:        # counselling -> counsel
        t = t[:-1]
    return t


def _variant_match(variant: str, stems: Set[str]) -> bool:
    """All significant words of *variant* present (stemmed) in *stems*."""
    parts = [p for p in re.findall(r"[a-z0-9]+", variant.lower()) if len(p) > 2]
    return bool(parts) and all(_stem_token(p) in stems for p in parts)


def _skill_present(skill: str, cv_low: str, cv_stems: Set[str]) -> bool:
    """Semantic overlap between a JD skill phrase and the CV.

    Literal substring first (fast path), then stemmed token matching with
    abbreviation equivalence: 'ANC' <-> 'antenatal', 'PNC' <-> 'postnatal',
    'BEmONC' <-> 'emergency obstetric', 'counselling' <-> 'counseling'.
    """
    if not skill:
        return False
    if skill in cv_low:
        return True
    words = [w for w in re.findall(r"[a-z0-9+#'.-]+", skill.lower())
             if len(w) > 2]
    if not words:
        return False
    for w in words:
        options = _EQUIV_BY_TOKEN.get(w)
        if options:
            if not any(_variant_match(o, cv_stems) for o in options):
                return False
        elif _stem_token(w) not in cv_stems:
            return False
    return True


# ---- Step 3: contextual location fit ---------------------------------------
def _location_match(cv_text: str, location: str) -> Tuple[float, str, str]:
    """Is the JD's location present anywhere in the CV (current address,
    employer sites, summary)? Only scores when the JD states a location."""
    if not location:
        return 0.0, "", ""
    low = (cv_text or "").lower()
    variants: Set[str] = set()
    for part in re.split(r"[,/()]+", location):
        p = " ".join(part.split()).strip().lower()
        if len(p) < 3 or p == "bangladesh":
            continue
        variants.add(p)
        variants.add(p.replace("'", "").replace("`", ""))
    variants.add(" ".join(location.split()).lower())
    hits = sorted({v for v in variants if v and v in low})
    if hits:
        return (5.0,
                f"Sector/location fit: CV mentions the JD location "
                f"({', '.join(hits[:2])})", "")
    return (0.0, "", f"JD location ({location}) is not mentioned anywhere in the CV.")


# ---- Step 4: custom recruiter instructions ---------------------------------
def parse_custom_instructions(text: str) -> Dict[str, float]:
    """Translate free-form recruiter instructions into per-bucket weight
    multipliers (Step 4 of the deep scan).

    Heuristic keyword rules; any bucket left out keeps its weight 1.0, so
    an empty instruction box never changes scoring. Examples that fire:
      'prefer NGO/sector experience rather than total years'
          -> experience x0.6, industry x1.5, role x1.25
      'must have BNMC registration'  -> credential x1.6
      'based in Cox's Bazar'         -> location x1.5
    """
    low = (text or "").lower().strip()
    if not low:
        return {}
    mult: Dict[str, float] = {}

    def bump(key: str, v: float) -> None:
        mult[key] = max(mult.get(key, 1.0), v)

    def cut(key: str, v: float) -> None:
        mult[key] = min(mult.get(key, 1.0), v)

    # Generic mentions first; the down-weighting rules run AFTER so they win.
    if re.search(r"experience|tenure|years?\s+of", low):
        bump("experience", 1.2)
    if re.search(r"sector|industry|humanitarian|\bngo\b|similar (?:job )?"
                 r"role|relevant (?:role|experience)|field experience|"
                 r"same (?:domain|field)|community", low):
        bump("industry", 1.6)
        bump("role", 1.25)
    if re.search(r"rather than (?:total |overall )?year|over (?:total |overall )?"
                 r"year|instead of (?:total )?year|not total year|"
                 r"total years?.{0,40}(?:matter|important|count)", low):
        cut("experience", 0.6)
        bump("industry", 1.5)
    if re.search(r"licen[cs]e|registration|council|certificat|certified", low):
        bump("credential", 1.6)
    if re.search(r"location|relocat|based in|cox|dhaka|chattogram|sylhet", low):
        bump("location", 1.5)
    if re.search(r"education|degree|qualification|academic", low):
        bump("education", 1.5)
    if re.search(r"\bskill|technical|hands[- ]on|tool|software", low):
        bump("skills", 1.35)
    if re.search(r"language|bangla|english|arabic|fluency", low):
        bump("language", 1.6)
    return mult


@dataclass
class ScoreResult:
    """Result of scoring one candidate CV against the active JD.

    core_met is the audit flag: True when the CV's qualification, required
    registration/licence AND direct role history all satisfy the JD -- the
    recruiter UI then never labels the candidate a 'Weak Fit'.
    """
    score: float = 0.0
    strengths: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    core_met: bool = False
    core_facts: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)


def score_candidate(text: str, profile: JDProfile,
                    instructions: str = "") -> ScoreResult:
    """Score one candidate CV against the dynamic JD profile --
    MULTI-STEP DEEP EVALUATION PIPELINE.

    Step 1  mandatory credential & licence verification
    Step 2  core domain & skill overlap (semantic)
    Step 3  contextual experience / sector / location fit
    Step 4  custom recruiter instructions -> dynamic bucket weights

    Buckets always sum to 100 points regardless of the instruction
    multipliers (they re-normalise each other), so the total stays on the
    0-100 scale. core_met records the audit flag used by the recruiter
    remark: qualification + required licence + direct role history met.
    """
    result = ScoreResult()
    cv_low = text.lower()
    cv_stems = _token_stems(text)

    # ── Step 1: credentials & licences ────────────────────────────────
    cred_pts, cred_note, cred_gap, cred_facts = _credential_check(text, profile)
    if cred_note:
        result.strengths.append(cred_note)
    if cred_gap:
        result.gaps.append(cred_gap)
    result.core_facts = dict(cred_facts)

    # ── Step 2: core domain & skill overlap (semantic) ────────────────
    skills_pts, skills_note, skills_gap = _skill_match(text, profile)
    if skills_note:
        result.strengths.append(skills_note)
    if skills_gap:
        result.gaps.append(skills_gap)

    # ── Step 3: contextual experience / industry / location ──────────
    exp_pts, exp_note, exp_gap = _experience_match(text, profile)
    if exp_note:
        result.strengths.append(exp_note)
    if exp_gap:
        result.gaps.append(exp_gap)

    focus_pts, focus_note, focus_gap = _focus_match(text, profile)
    if focus_note:
        result.strengths.append(focus_note)
    if focus_gap:
        result.gaps.append(focus_gap)

    role_pts, role_note, role_gap = _role_fit(text, profile.role_tiers)
    if role_note:
        result.strengths.append(role_note)
    if role_gap:
        result.gaps.append(role_gap)

    sm_years, _sm_roles = _sm_line_years(text, profile.role_tiers)
    declared_years = _declared_total_years(text)
    total_years = max(sm_years, declared_years,
                      _aggregate_span_years(text))

    ind_pts, ind_note, ind_gap = _industry_match(text, profile.industry_keywords)
    if ind_note:
        result.strengths.append(ind_note)
    if ind_gap:
        result.gaps.append(ind_gap)

    loc_pts, loc_note, loc_gap = _location_match(text, profile.location)
    if loc_note:
        result.strengths.append(loc_note)
    if loc_gap:
        result.gaps.append(loc_gap)

    lang_pts, lang_note, lang_gap = _language_match(text, profile.languages)
    if lang_note:
        result.strengths.append(lang_note)
    if lang_gap:
        result.gaps.append(lang_gap)

    edu_match, edu_levels = _education_match(text, profile.education_levels)
    hard_skill_count = sum(
        1 for p in (profile.hard_skills or {})
        if _skill_present(p, cv_low, cv_stems))
    cert_hits = cred_facts.get("cert_hits", [])

    # ── audit flag (drives the recruiter remark & Weak-Fit floor) ─────
    edu_ok = (profile.education_levels == [] or edu_match is not False)
    role_ok = bool(role_pts and role_pts >= 0.6 * ROLE_WEIGHT) or bool(
        profile.role_tiers and sm_years >= 1.0)
    core_met = bool(edu_ok and cred_facts["license_ok"] and role_ok)

    # ── Step 4: custom recruiter instructions -> bucket weights ───────
    # Instruction multipliers scale the SAME bucket formulas the engine
    # always used, so blank instructions reproduce the default scoring
    # exactly, and the final cap keeps the total on the 0-100 scale.
    mult = parse_custom_instructions(instructions)

    def _m(key: str) -> float:
        return mult.get(key, 1.0)

    skills_final = round(
        min(100.0, skills_pts * (B_SKILLS * _m("skills") / 100.0)), 1)
    exp_final = round(
        min(100.0, exp_pts * (B_EXP * _m("experience") / 100.0)), 1)
    focus_final = round(
        min(100.0, focus_pts * (FOCUS_WEIGHT * _m("focus") / 100.0)), 1)
    role_final = round(
        min(100.0, role_pts * (ROLE_WEIGHT * _m("role") / 100.0)), 1)
    edu_final = round(8.0 * _m("education"), 1) if edu_match is True else 0.0
    cred_final = round(min(7.0, cred_pts) * _m("credential"), 1)
    ind_final = round(min(5.0, ind_pts) * _m("industry"), 1)
    loc_final = round(min(5.0, loc_pts)
                      * max(_m("industry"), _m("location")), 1)
    lang_final = round(min(3.0, lang_pts * 0.6) * _m("language"), 1)

    # Direct on-role tenures + documented years stay as small bonuses.
    sm_bonus = round(min(5.0, sm_years * 0.5), 1) if sm_years else 0.0
    years_bonus = round(min(3.0, max(
        (declared_years or 0.0), _aggregate_span_years(text)) * 0.4), 1)
    hard_bonus = round(min(5.0, float(hard_skill_count)), 1)

    total = round(min(100.0, skills_final + exp_final + focus_final
                      + role_final + edu_final + cred_final + ind_final
                      + loc_final + lang_final + sm_bonus + years_bonus
                      + hard_bonus), 1)

    # ── Weak-Fit floor: a CV whose core qualification, required licence
    #    AND direct role history are all satisfied is never scored "weak".
    if core_met and total < 45.0:
        total = 45.0
        result.strengths.append(
            "Audit floor: core requirements (qualification, required "
            "registration and direct role history) all satisfied — score "
            "held at 45 so the candidate is never labelled WEAK FIT")

    result.score = total

    # ── audit reasoning: strengths first, weakest concerns last ───────
    all_strengths = [s for s in dict.fromkeys(result.strengths) if s]
    all_gaps = [g for g in dict.fromkeys(result.gaps) if g]
    if not all_strengths:
        all_strengths = ["No specific strengths extracted"]
    if not all_gaps:
        all_gaps = ["No significant gaps detected against the JD"]

    result.core_met = core_met
    result.facts = {
        "experience_years": round(total_years, 1),
        "on_role_years": round(sm_years, 1),
        "declared_years": declared_years,
        "education_levels": edu_levels,
        "education_ok": edu_ok,
        "hard_skills_matched": hard_skill_count,
        "certifications": cert_hits,
        "registration": cred_facts.get("registration", ""),
        "registration_active": cred_facts.get("registration_active", False),
        "weight_multipliers": {k: round(v, 2) for k, v in mult.items()},
    }
    return result


def _skill_match(cv_text: str, profile: JDProfile) -> Tuple[float, str, str]:
    """STEP 2 -- Core domain & skill overlap (semantic, not literal).

    Hard skills AND responsibilities are matched through stemmed tokens
    with abbreviation equivalence (ANC <-> antenatal, PNC <-> postnatal,
    counselling <-> counseling ...), so CV wording differences no longer
    register as missing skills. Returns (points, note, gap_note).
    """
    skills: Dict[str, float] = dict(profile.hard_skills or {})
    for resp in (profile.responsibilities or []):
        if resp and resp not in skills and 3 <= len(resp) <= 60:
            skills[resp] = 1.0          # supporting weight, capped later
    if not skills:
        return 0.0, "", ""

    lower = cv_text.lower()
    stems = _token_stems(cv_text)
    matched = [phrase for phrase in skills if _skill_present(phrase, lower, stems)]

    if not matched:
        return 0.0, "", (
            "No hard skills or responsibilities from the JD were detected "
            "in the CV. The CV may lack the technical keywords the JD "
            "requires."
        )

    # Score: each matched skill contributes, capped at B_SKILLS points
    n_matched = len(matched)
    # Weight by the skill's importance (already weighted during extraction)
    # Simple count-based scoring: n/50 * B_SKILLS, capped
    pts = round(min(B_SKILLS, n_matched * 1.5), 1)
    note = (
        f"Core skills: {n_matched} JD hard skill(s) matched "
        f"({', '.join(matched[:5])})"
    )
    gap = ""
    return pts, note, gap


def _experience_match(cv_text: str, profile: JDProfile) -> Tuple[float, str, str]:
    """Score years-of-experience match against the JD's stated range.

    Experience source priority (robust multi-step fallback):
      1. on-role tenure years tied to the JD's own role ladder
         (flexible date ranges + explicit '(x.yr)' tenures)
      2. the CV's declared total ("Total year of experience: 6.4 yrs")
      3. the sum of every distinct date range in the employment block
    A CV that clearly HAS experience can therefore never score the
    'no tenures identifiable' penalty just because its date layout is
    unusual.

    Returns (points, note, gap_note).
    """
    min_yrs = profile.min_experience_years
    max_yrs = profile.max_experience_years

    if min_yrs == 0.0 and max_yrs == 0.0:
        return 0.0, "", ""

    # Step A: experience on the JD's own role line.
    onrole_years, _ = _sm_line_years(cv_text, profile.role_tiers)

    # Step B/C: declared total, then aggregated employment spans.
    declared = _declared_total_years(cv_text)
    span_sum = _aggregate_span_years(cv_text)
    total_years = max(declared, span_sum)

    if onrole_years and onrole_years > 0:
        used, source = onrole_years, "on-role"
    elif total_years > 0:
        used, source = total_years, (
            "total" if declared else "employment-span")
    else:
        return 0.0, "", (
            "No experience years could be determined from the CV: neither "
            "role-line tenures, a declared total, nor any date range was "
            "identifiable."
        )

    def _src_label() -> str:
        if source == "on-role":
            return "on-role experience"
        if source == "total":
            return "total experience (declared on the CV)"
        return "total experience (calculated from employment dates)"

    # Extra context when on-role years fall short but total experience is real.
    context = ""
    if source != "on-role" and declared and declared >= min_yrs:
        context = f" (CV declares {declared:g} yrs total experience)"

    if min_yrs <= used <= max_yrs:
        pts = round(min(B_EXP, 45.0), 1)
        note = (
            f"Experience: {used:.1f} yrs {_src_label()} "
            f"fits the JD's required {min_yrs:.1f}-{max_yrs:.1f} yrs range"
            f"{context}"
        )
        gap = ""
    elif used < min_yrs:
        deficit = min_yrs - used
        pts = round(min(B_EXP, 45.0 * (used / min_yrs)), 1) if min_yrs > 0 else 0.0
        note = (
            f"Experience: {used:.1f} yrs {_src_label()}, "
            f"below the JD minimum of {min_yrs:.1f} yrs "
            f"(deficit: {deficit:.1f} yrs){context}"
        )
        gap = (
            f"CV shows {used:.1f} yrs {_src_label()} against the JD's "
            f"minimum of {min_yrs:.1f} years."
        )
    else:
        excess = used - max_yrs
        # Overqualification softens but never destroys the bucket: a long,
        # relevant track record keeps at least 75% of the experience points.
        pts = (round(min(B_EXP, 45.0 * max(max_yrs / used, 0.75)), 1)
               if used > 0 else 0.0)
        note = (
            f"Experience: {used:.1f} yrs {_src_label()}, "
            f"above the JD maximum of {max_yrs:.1f} yrs "
            f"(excess: {excess:.1f} yrs){context}"
        )
        gap = (
            f"CV exceeds the JD's maximum experience of {max_yrs:.1f} years. "
            f"Consider whether this indicates overqualification."
        )

    return pts, note, gap


def _hard_skill_match(cv_text: str, hard_skills: Dict[str, float]) -> float:
    """Count how many JD hard skills appear in the CV text.

    Returns the count of matched skills (0-50), used for small bonuses.
    """
    if not hard_skills:
        return 0.0

    lower = cv_text.lower()
    matched = [phrase for phrase in hard_skills if phrase in lower]
    return float(len(matched))


def _education_match(cv_text: str, accepted_levels: List[str]) -> Tuple[Optional[bool], List[str]]:
    """Check if the CV's highest education level meets the JD's accepted levels.

    Returns (meets_requirement, detected_levels).
    """
    if not accepted_levels:
        return None, []

    EDU_RANK = {"ssc/hsc": 1, "vocational": 1, "diploma": 2,
                "bachelor": 3, "masters": 4, "phd": 5}

    lower = cv_text.lower()
    detected: List[str] = []

    level_checks = [
        ("phd", ["ph.d", "phd", "doctorate"]),
        ("masters", ["masters", "m.sc", "mba", "m.eng", "master"]),
        ("bachelor", ["bachelor", "b.sc", "b.eng", "b.tech", "bss", "llb",
                      "ba ", "bs ", "b.a.", "b.s."]),
        ("diploma", ["diploma"]),
        ("ssc/hsc", ["ssc", "hsc", "h.s.c", "s.s.c", "secondary school"]),
        ("vocational", ["vocational", "technical training"]),
    ]
    for label, keywords in level_checks:
        if any(kw in lower for kw in keywords):
            detected.append(label)

    if not detected:
        return None, []

    best = max(EDU_RANK.get(lvl, 0) for lvl in detected)
    need = min(EDU_RANK.get(lvl, 3) for lvl in accepted_levels)

    return best >= need, detected


def _industry_match(cv_text: str, industry_keywords: List[str]) -> Tuple[float, Optional[str], Optional[str]]:
    """Check how many industry keywords from the JD appear in the CV.

    Returns (count, note, gap_note).
    """
    if not industry_keywords:
        return 0.0, "", ""

    lower = cv_text.lower()
    stems = _token_stems(cv_text)
    matched = [kw for kw in industry_keywords
               if _skill_present(kw.lower(), lower, stems)]

    if not matched:
        return 0.0, "", (
            "No industry keywords from the JD were detected in the CV. "
            "The CV may be from a different industry sector."
        )

    pts = min(5.0, len(matched) * 1.5)
    note = (
        f"Industry: {len(matched)} JD industry keyword(s) matched "
        f"({', '.join(matched[:3])})"
    )
    gap = ""
    return pts, note, gap


def _language_match(cv_text: str, languages: List[str]) -> Tuple[float, Optional[str], Optional[str]]:
    """Check how many language requirements from the JD appear in the CV.

    Returns (count, note, gap_note).
    """
    if not languages:
        return 0.0, "", ""

    lower = cv_text.lower()
    matched = [lang for lang in languages if lang.lower() in lower]

    if not matched:
        return 0.0, "", (
            "No language requirements from the JD were detected in the CV. "
            "The CV may not include the required languages."
        )

    pts = min(3.0, len(matched) * 1.0)
    note = (
        f"Languages: {len(matched)} JD language requirement(s) matched "
        f"({', '.join(matched[:3])})"
    )
    gap = ""
    return pts, note, gap


def build_reasoning(result: ScoreResult) -> str:
    """Build the structured engine reasoning string for the recruiter remark.

    This mirrors what the UI shows in the 'Full engine reasoning' expander.
    Leads with the audit verdict (core requirements) and the strongest
    evidence, then the concerns -- never the reverse.
    """
    parts = []
    score = result.score
    if result.core_met and score < 70:
        parts.append(
            f"CORE REQUIREMENTS MET — score {score:.1f}/100 "
            "(qualification, required registration and direct role "
            "history all satisfied — not a weak fit despite the score)")
    elif score >= 70:
        parts.append(f"STRONG FIT — score {score:.1f}/100")
    elif score >= 55:
        parts.append(f"POTENTIAL FIT — score {score:.1f}/100")
    elif score >= 45:
        parts.append(f"BORDERLINE — score {score:.1f}/100")
    else:
        parts.append(f"WEAK FIT — score {score:.1f}/100")

    # Strengths
    if result.strengths:
        parts.append("Strongest evidence: " + " ; ".join(result.strengths[:3]))
    else:
        parts.append("Strongest evidence: no clear strength could be extracted")

    # Gaps/concerns
    if result.gaps:
        parts.append("Main concern: " + result.gaps[0])
    else:
        parts.append("Main concern: no significant gap detected against the JD")

    facts = result.facts or {}
    if facts:
        bits = []
        if facts.get("on_role_years"):
            bits.append(f"on-role tenure {facts['on_role_years']:g} yrs")
        elif facts.get("experience_years"):
            bits.append(f"experience {facts['experience_years']:g} yrs")
        if facts.get("registration"):
            bits.append("registration " + facts["registration"])
        if facts.get("certifications"):
            bits.append("training " + ", ".join(facts["certifications"][:3]))
        if facts.get("weight_multipliers"):
            adj = ", ".join(f"{k} x{v:g}" for k, v in
                            facts["weight_multipliers"].items())
            bits.append("custom instruction weights applied: " + adj)
        if bits:
            parts.append("Audit trail: " + " | ".join(bits))

    return " | ".join(parts)


# ══════════════════════════════════════════════════════════════════════════
# SECTION 3 -- POSITION-BASED OUTPUT WORKFLOW
# ══════════════════════════════════════════════════════════════════════════

def create_output_folders(position_title: str) -> Tuple[Path, Path]:
    """Create the output directory structure based on the JD position title.

    Creates:
    - 3_Evaluation_Output/ (if not exists)
    - TOP_CV/ (or mode-specific folder) inside Evaluation_Output
    - Returns (evaluation_output_dir, shortlist_folder)
    """
    # Clean the position title for use as a directory name
    clean_title = re.sub(r"[^\w\-\s]", "", position_title).strip()
    clean_title = re.sub(r"\s+", "_", clean_title)

    # Create evaluation output directory
    eval_dir = OUTPUT_FOLDER
    eval_dir.mkdir(parents=True, exist_ok=True)

    # Create the position-specific shortlist folder
    shortlist_dir = TOP_CV_FOLDER / clean_title
    shortlist_dir.mkdir(parents=True, exist_ok=True)

    # Also ensure the mode-specific shortlist folder exists
    if PRIORITY_MODE == "experience":
        mode_dir = TOP_CV_FOLDER
    elif PRIORITY_MODE == "qualification":
        mode_dir = TOP_CV_FOLDER / "QUALIFICATION"
    else:
        mode_dir = TOP_CV_FOLDER / "BALANCED"
    mode_dir.mkdir(parents=True, exist_ok=True)

    return eval_dir, shortlist_dir


def copy_top_cv(cv_path: Path, rank: int, score: float, candidate_name: str,
                shortlist_dir: Path) -> Optional[Path]:
    """Copy a top-ranked candidate's PDF to the shortlist folder with clean naming.

    Naming convention: Rank_1_Score_88_CandidateName.pdf
    """
    if not cv_path or not cv_path.exists():
        return None

    # Clean the candidate name for use in a filename
    clean_name = re.sub(r"[^\w\s\-]", "", candidate_name)
    clean_name = re.sub(r"\s+", "_", clean_name).strip("_")

    # Generate the new filename
    new_name = f"Rank_{rank}_Score_{int(score):02d}_{clean_name}.pdf"
    dest_path = shortlist_dir / new_name

    try:
        shutil.copy2(str(cv_path), str(dest_path))
        return dest_path
    except Exception as exc:
        log.warning("Could not copy %s to %s: %s", cv_path, dest_path, exc)
        return None


def write_ranking_csv(rows: List[Dict], csv_path: Path) -> None:
    """Write the ranked candidates report to CSV.

    Each row dict should contain: Rank, Candidate_File_Name, Match_Score,
    Bonus_Points, Status, Strengths, Weaknesses, Recruiter_Remark,
    Summary_Reasoning
    """
    df = pd.DataFrame(rows)
    df.to_csv(str(csv_path), index=False, encoding="utf-8-sig")
    log.info("Wranking report written to %s (%d rows)", csv_path, len(rows))


# ═════════════════════════════════════════════════════════════════════════
# SECTION 4 -- CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════

def main_cli():
    """Command-line entry point for the CV-JD matcher.

    Usage:
        python evaluator.py                     # default: top 200
        python evaluator.py --top 30           # shortlist top 30
        python evaluator.py --threshold 60      # move all scoring >= 60
        python evaluator.py --jd posting.docx   # JD supplied as Word / PDF
        python evaluator.py --no-blacklist      # ignore blacklist.csv for this run
        python evaluator.py --blacklist-names exclude   # name hit = exclude too
    """
    # Declared first: --focus-area's default reads FOCUS_AREA below, and an
    # explicit pin must reach extract_jd_profile (which derives focus from
    # the JD only when no override was given).
    global FOCUS_AREA, _FOCUS_AREA_OVERRIDE

    parser = argparse.ArgumentParser(
        description="100% Dynamic CV Screening, Ranking & Shortlisting"
    )
    parser.add_argument("--top", type=int, default=TOP_N_CANDIDATES,
                        help=f"Shortlist top N candidates (default: {TOP_N_CANDIDATES})")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Move all candidates scoring >= this threshold")
    parser.add_argument("--jd", type=Path, default=None,
                        help="Path to job description file (.txt, .docx, .pdf)")
    parser.add_argument("--no-blacklist", action="store_true",
                        help="Ignore blacklist.csv for this run")
    parser.add_argument("--blacklist-names", type=str, choices=["flag", "exclude", "off"],
                        default="flag",
                        help="Name-match mode: flag(review), exclude(drop), off(ignore)")
    parser.add_argument("--focus-area", type=str, default=FOCUS_AREA,
                        help="Override the focus area (default: derived from the uploaded JD, e.g. 'medical')")
    parser.add_argument("--priority-mode", type=str, choices=["experience", "qualification", "balanced"],
                        default=PRIORITY_MODE,
                        help="Scoring priority mode")
    args = parser.parse_args()

    # Override settings from CLI arguments
    # (These override the module-level defaults set in SECTION 0 -- CONFIGURATION)
    if args.focus_area:
        _FOCUS_AREA_OVERRIDE = args.focus_area
        FOCUS_AREA = args.focus_area
    PRIORITY_MODE = args.priority_mode if args.priority_mode else PRIORITY_MODE

    # Re-apply mode-specific weights after priority mode change
    if PRIORITY_MODE == "experience":
        FOCUS_WEIGHT = 15.0
        ROLE_WEIGHT = 45.0
        HISTORICAL_FACTOR = 0.8
        B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 15.0, 45.0, 8.0, 5.0
        B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG = 10.0, 7.0, 5.0, 5.0
    elif PRIORITY_MODE == "qualification":
        FOCUS_WEIGHT = 15.0
        ROLE_WEIGHT = 15.0
        HISTORICAL_FACTOR = 0.33
        B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 25.0, 15.0, 22.0, 13.0
        B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG = 10.0, 5.0, 5.0, 5.0
    else:  # balanced
        FOCUS_WEIGHT = 12.0
        ROLE_WEIGHT = 25.0
        HISTORICAL_FACTOR = 0.33
        B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 35.0, 25.0, 15.0, 5.0
        B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG = 10.0, 5.0, 5.0, 5.0

    # Update mode-specific folder names
    if PRIORITY_MODE == "experience":
        SHORTLIST_FOLDER = Path("TOP_CV")
        OUTPUT_CSV = Path("3_Evaluation_Output") / "ranked_candidates_report.csv"
    elif PRIORITY_MODE == "qualification":
        SHORTLIST_FOLDER = Path("TOP_CV_QUALIFICATION")
        OUTPUT_CSV = Path("3_Evaluation_Output") / "ranked_candidates_qualification.csv"
    else:  # balanced
        SHORTLIST_FOLDER = Path("TOP_CV_BALANCED")
        OUTPUT_CSV = Path("3_Evaluation_Output") / "ranked_candidates_balanced.csv"
        B_SKILLS, B_EXP, B_EDU_LEVEL, B_EDU_FIELD = 35.0, 25.0, 15.0, 5.0
        B_INDUSTRY, B_LOCATION, B_SOFT, B_LANG = 10.0, 5.0, 5.0, 5.0

    SHORTLIST_FOLDER = Path("TOP_CV") if PRIORITY_MODE == "experience" else \
                       Path("TOP_CV_QUALIFICATION") if PRIORITY_MODE == "qualification" else \
                       Path("TOP_CV_BALANCED")
    OUTPUT_CSV = Path("3_Evaluation_Output") / f"ranked_candidates_{PRIORITY_MODE}.csv"

    # Locate the active JD
    jd_path = args.jd if args.jd else find_jd_file()
    if jd_path is None:
        print("ERROR: No job description found. Place current_jd.txt/.docx/.pdf in 1_Active_JD/")
        sys.exit(1)

    # Extract JD profile
    profile = extract_jd_profile(jd_path)
    profile.focus_cores, profile.focus_terms = _focus_vocabulary(profile.raw_text)

    # Build role tiers from the profile
    profile.role_tiers = build_role_tiers(profile)

    # Create output folders based on the JD title
    position_title = profile.title or "Unknown_Position"
    eval_dir, shortlist_dir = create_output_folders(position_title)

    # Load blacklist
    bl_mode = args.blacklist_names
    entries = load_blacklist()
    if not args.no_blacklist and entries:
        pass  # blacklist will be applied per-CV
    else:
        entries = []

    # Find all candidate CVs
    cv_folder = CV_FOLDER
    if not cv_folder.is_dir():
        print(f"ERROR: No candidate folder found at {cv_folder}")
        sys.exit(1)

    cv_files = sorted(cv_folder.glob("*.pdf")) + sorted(cv_folder.glob("*.docx"))
    if not cv_files:
        print(f"ERROR: No CV files found in {cv_folder}")
        sys.exit(1)

    log.info("Found %d candidate CVs", len(cv_files))
    log.info("JD: %s (%s)", jd_path.name, position_title)

    # Score each candidate
    rows: List[Dict] = []
    for i, cv_path in enumerate(cv_files, 1):
        log.info("Processing %d/%d: %s", i, len(cv_files), cv_path.name)

        try:
            # Read CV text
            from pypdf import PdfReader
            data = cv_path.read_bytes()
            if not data:
                rows.append({
                    "Rank": "", "Candidate_File_Name": cv_path.name,
                    "Match_Score": 0.0, "Bonus_Points": 0.0,
                    "Status": "EMPTY_FILE",
                    "Strengths": ["Empty file"], "Weaknesses": ["No content"],
                    "Recruiter_Remark": "Not evaluated: file is empty",
                    "Summary_Reasoning": "CV file was empty"
                })
                continue

            reader = PdfReader(io.BytesIO(data))
            text_parts = []
            for page in reader.pages[:25]:  # MAX_PAGES
                try:
                    t = page.extract_text() or ""
                    if t.strip():
                        text_parts.append(t)
                except Exception:
                    continue
            cv_text = "\n".join(text_parts)

            if len(cv_text.strip()) < 80:
                rows.append({
                    "Rank": "", "Candidate_File_Name": cv_path.name,
                    "Match_Score": 0.0, "Bonus_Points": 0.0,
                    "Status": "SCANNED_IMAGE",
                    "Strengths": ["Could not extract text"],
                    "Weaknesses": ["Scanned/image-only PDF"],
                    "Recruiter_Remark": "Not evaluated: scanned or image-only PDF",
                    "Summary_Reasoning": "Extracted very little text — likely a scanned image PDF"
                })
                continue

            # Apply blacklist check
            bv = blacklist_check(cv_text, cv_path.name, entries, bl_mode)
            if bv.excluded:
                rows.append({
                    "Rank": "", "Candidate_File_Name": cv_path.name,
                    "Match_Score": 0.0, "Bonus_Points": 0.0,
                    "Status": "EXCLUDED",
                    "Strengths": [],
                    "Weaknesses": [bv.excluded],
                    "Recruiter_Remark": f"EXCLUDED — {bv.excluded}",
                    "Summary_Reasoning": f"Blacklisted: {bv.excluded}"
                })
                # Copy to excluded folder if needed
                continue
            if bv.review:
                # Flagged for review — still score but mark
                pass

            # Score the candidate
            result = score_candidate(cv_text, profile)

            # Determine status
            if bv.excluded:
                status = "EXCLUDED"
            elif bv.review:
                status = "REVIEW"
            else:
                status = "RANKED"

            # Copy top CV to shortlist folder
            if status in ("RANKED", "REVIEW"):
                copy_top_cv(cv_path, len([r for r in rows if r.get("Status") in ("RANKED", "REVIEW")]) + 1,
                           result.score, cv_path.name.replace(".pdf", "").replace(".docx", ""), shortlist_dir)

            # Build row
            strengths = result.strengths if result.strengths else ["No specific strengths extracted"]
            gaps = result.gaps if result.gaps else ["No significant gaps detected against the JD"]

            rows.append({
                "Rank": "",  # Will be filled after sorting
                "Candidate_File_Name": cv_path.name,
                "Match_Score": result.score,
                "Bonus_Points": 0.0,
                "Status": status,
                "Strengths": " ; ".join(strengths),
                "Weaknesses": " ; ".join(gaps),
                "Recruiter_Remark": build_reasoning(result),
                "Summary_Reasoning": result.score  # placeholder
            })

        except Exception as exc:
            log.error("Error processing %s: %s", cv_path.name, exc)
            rows.append({
                "Rank": "", "Candidate_File_Name": cv_path.name,
                "Match_Score": 0.0, "Bonus_Points": 0.0,
                "Status": "ERROR",
                "Strengths": [f"Error: {str(exc)[:80]}"],
                "Weaknesses": ["Scoring error"],
                "Recruiter_Remark": f"Error during scoring: {str(exc)}",
                "Summary_Reasoning": f"Failed to process: {str(exc)}"
            })

    # Rank the scored rows (excluded/unreadable rows keep blank rank)
    scored = [r for r in rows if r["Status"] in ("RANKED", "REVIEW")]
    scored.sort(key=lambda r: r["Match_Score"], reverse=True)
    for i, r in enumerate(scored, 1):
        r["Rank"] = i

    # Fill in ranks for non-scored rows (keep their original order)
    ranked_set = {r["Candidate_File_Name"] for r in scored}
    for r in rows:
        if r["Candidate_File_Name"] not in ranked_set:
            # Find next available rank
            used_ranks = {r["Rank"] for r in rows if r["Rank"]}
            next_rank = 1
            while next_rank in used_ranks:
                next_rank += 1
            r["Rank"] = next_rank

    # Write the ranking CSV
    write_ranking_csv(rows, OUTPUT_CSV)

    # Print summary
    n_ranked = sum(1 for r in rows if r["Status"] in ("RANKED", "REVIEW"))
    n_excluded = sum(1 for r in rows if r["Status"] == "EXCLUDED")
    n_errors = sum(1 for r in rows if r["Status"] in ("EMPTY_FILE", "SCANNED_IMAGE", "ERROR"))

    print(f"\n=== Screening Complete ===")
    print(f"Total candidates: {len(rows)}")
    print(f"Ranked/Reviewed: {n_ranked}")
    print(f"Excluded (blacklist): {n_excluded}")
    print(f"Unreadable/Error: {n_errors}")
    print(f"Top shortlist written to: {SHORTLIST_FOLDER}")
    print(f"Ranking CSV: {OUTPUT_CSV}")
    print(f"\nJD position: {position_title}")
    print(f"Priority mode: {PRIORITY_MODE}")
    print(f"Focus area: {FOCUS_AREA}")


if __name__ == "__main__":
    main_cli()