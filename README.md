# CV ↔ JD Matcher — Enterprise Applicant Tracking System

A professional, dynamic CV-to-Job Description screening platform with weighted priority scoring, position-based output workflow, and enterprise-grade UI/UX.

---

## 📋 Overview

The **CV ↔ JD Matcher** is an enterprise-grade Applicant Tracking System (ATS) that automates candidate screening and ranking against job descriptions. Built with Python, Streamlit, and PyPDF, it features:

- **Dynamic JD extraction** — All requirements are mined from the active JD file, zero hardcoded keywords
- **Weighted priority scoring engine** — Three pillars (Core Skills 40%, Experience 30%, Domain Knowledge 30%)
- **Position-based output workflow** — Auto-create folders named after the job title, copy top candidates
- **Enterprise Brown & Blue theme** — Professional ATS UI, not a generic template
- **Batch processing** — Seamlessly handle 1,000+ CVs with background chunking
- **Blacklist screening** — Exclude or flag blacklisted candidates
- **Multiple priority modes** — experience, qualification, or balanced scoring

---

## 🛠️ Installation

### Prerequisites

- Python 3.9+
- pip (Python package manager)

### Setup

```bash
# 1. Clone the repository
git clone <repository-url>
cd CV_Matcher

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Requirements

```
streamlit==1.66.1        # web UI + built-in file uploader / progress bars
pandas==3.0.5            # ranked report table, CSV + Excel assembly
pypdf==6.16.1            # PDF text extraction, encrypted-PDF detection
python-docx==1.2.0       # DOCX text + table extraction
openpyxl==3.1.5          # .xlsx export engine
```

---

## ▶️ Running the App

```bash
streamlit run app.py
```

The app will open at `http://localhost:8501` in your default browser.

---

## 📁 Project Structure

```
CV_Matcher/
├── app.py                  # Main Streamlit web application
├── evaluator.py            # Core scoring engine + JD extraction
├── requirements.txt        # Python dependencies
├── data/                   # ✓ Committed, changeable demo data
│   ├── jd.txt              # Fictional example JD (never auto-loaded)
│   └── candidates.csv      # Synthetic blacklist for testing
│
├── tests/
│   └── smoke_app.py        # Headless smoke tests (see 🧪 Testing)
├── scripts/
│   └── publish-github.sh   # Verified, PII-gated clean GitHub publish
│
├── 1_Active_JD/            # ← Gitignored: client's real job posting
│   └── current_jd.txt      # Real JD (replace or upload via UI)
│
├── 2_Active_CVs/           # ← Gitignored: real candidate CVs (PII — never commit)
│   └── *.pdf, *.docx
│
├── blacklist.csv           # ← Gitignored: real blacklist entries
│
├── .verify/                # ← Gitignored: verification fixtures
│   └── *.py
├── 3_Evaluation_Output/    # ✓ Generated output folders
│   └── ranked_candidates_report.csv
│   └── TOP_CV/             # ✓ Top candidates auto-copied here
│       └── Rank_1_Score_88_CandidateName.pdf
├── TOP_CV/                 # ✓ Mode-specific shortlist folders
│   └── TOP_CV_QUALIFICATION/
│   └── TOP_CV_BALANCED/
│
├── .gitignore              # git-ignored paths (real PII never commits)
├── SECURITY.md             # security best practices
└── README.md               # this file
```

### Key Folders

| Folder | Description |
|--------|-------------|
| `data/` | ✓ **Committed** changeable demo data — fictional example JD and synthetic blacklist. Nothing loads it automatically; supply your own JD. |
| `1_Active_JD/` | **Gitignored** — contains the client's real job posting. Replace or upload via the UI. |
| `2_Active_CVs/` | **Gitignored** — real candidate CVs containing PII. Never commit. Use for local testing only. |
| `3_Evaluation_Output/` | **Generated** — output reports and shortlist folders created by the app. |
| `TOP_CV/` | **Generated** — mode-specific shortlist folders (TOP_CV, TOP_CV_QUALIFICATION, TOP_CV_BALANCED). |

---

## 🎯 How It Works

### 1. Job Description Loading

The app never falls back to a committed job description — you always decide which posting is active:

1. **Upload via UI** — Drag & drop a `.txt`, `.docx`, or `.pdf` file. The app extracts text and builds a dynamic profile from scratch on every run.
2. **Paste directly** — Type or paste raw JD text into the text area.
3. **`1_Active_JD/` folder** — Drop `current_jd.txt`, `current_jd.docx`, or `current_jd.pdf` into the gitignored folder.

`data/jd.txt` is only a **fictional example** file for demos/tests — nothing loads it automatically. A fresh clone boots with an empty JD until you supply one.

### 2. Dynamic JD Profile Extraction

**Everything is extracted from the active JD file — zero hardcoding.** The app mines:

- **Job Title** — From the first line or a "Job Title:" line
- **Experience Range** — Min/max years specified in the JD
- **Location** — City/locality mentioned in the posting
- **Education Levels** — Required degrees (Bachelor's, Master's, etc.)
- **Education Fields** — Relevant academic disciplines
- **Hard Skills** — Technical keywords and competencies
- **Soft Skills** — Communication, leadership, teamwork, etc.
- **Languages** — Required or preferred languages
- **Industry Keywords** — NGO, consulting, health, telecom, etc.
- **Responsibilities** — List of job duties
- **Focus Area** — Derived automatically from this JD's own title/text (e.g. `medical` for a Medical Officer posting, `site management field` for a field-assistant posting). Override with `--focus-area` if needed.
- **Role Tiers** — A role ladder mined from this JD's title and duty lines, plus generic leadership titles; nothing carries over from a previous upload

### 3. Candidate Scoring

The weighted priority scoring engine evaluates each candidate across **three pillars**:

#### Pillar 1: Core Skills & Technical Fit (40% max)
- Matches CV text against the JD's hard skills list
- Reports count and names of matched skills
- Notes gaps where JD skills are absent from the CV

#### Pillar 2: Years of Experience & Educational Criteria (30% max)
- **Experience matching** — Compares candidate's on-role years (tenures on the JD's own role line) against the JD's required range
  - Perfect fit within range: full points
  - Below minimum: proportional reduction
  - Above maximum: proportional reduction
- **Education level matching** — Checks if candidate's highest degree meets JD requirements
  - Best degree >= required minimum = full points
  - Below requirement = no education points

#### Pillar 3: Domain Knowledge / Responsibilities / Bonus Criteria (30% max)
- **Focus area match** — How strongly the CV demonstrates experience in the focus competency derived from the active JD (e.g., "medical" for a Medical Officer posting)
  - Core phrase match: +8 points
  - Domain-related anchor phrases: up to +6 points
  - General focus-word phrases: up to +4 points
  - Experience context (within 160 chars of experience wording): +4 points
  - Capped at FOCUS_WEIGHT points
- **Role fit** — Current/most-recent job title mapped against the active JD's own reporting line
  - Tier 1 (the post the JD is hiring for): full ROLE_WEIGHT points
  - Tier 2 (supporting role named in the JD): 80% of ROLE_WEIGHT
  - Tier 3 (leadership): 47% of ROLE_WEIGHT
  - Earlier roles: reduced by HISTORICAL_FACTOR
- **Hard skills bonus** — Additional +5 points max for matched JD hard skills
- **Industry keyword match** — +5 points max for industry keywords from JD
- **Language match** — +3 points max for required languages

**Total score always sums to 100 across all pillars.**

**Score bands:**
- **70+** — STRONG FIT
- **55–69** — POTENTIAL FIT
- **45–54** — BORDERLINE
- **Below 45** — WEAK FIT

### 4. Blacklist Screening

- Candidates can be checked against `blacklist.csv`
- Three modes: **flag** (review, still score), **exclude** (drop like a phone match), **off** (ignore names)
- Phone number matching: fingerprints last 10 digits, handles +880/880 prefixes
- Name matching: normalizes honorifics (MD, MR, MRS, etc.), requires 2+ word matches
- Blacklist verdicts are final — excluded candidates score 0 and cannot be shortlisted

### 5. Position-Based Output Workflow

**Automatically creates directory structure based on the JD position title:**

1. **Parse the JD** — Extract the exact Job/Position Title (e.g., "Site Management Field Assistant")
2. **Create output directory** — `3_Evaluation_Output/` (if not exists)
3. **Create position folder** — Named after the position title (e.g., `3_Evaluation_Output/Site_Management_Field_Assistant/`)
4. **Create TOP_CV/ subdirectory** — Inside the position folder
5. **Auto-copy top candidates** — Top-ranked resumes are copied and renamed:
   - Naming: `Rank_1_Score_88_CandidateName.pdf`
   - Example: `Rank_1_Score_85_HasanAlam.pdf`

The folder structure is **mode-aware**:
- `PRIORITY_MODE = "experience"`: `TOP_CV/`
- `PRIORITY_MODE = "qualification"`: `TOP_CV_QUALIFICATION/`
- `PRIORITY_MODE = "balanced"`: `TOP_CV_BALANCED/`

---

## 🚀 Usage Guide

### First-Time Use (Fresh Clone)

1. `streamlit run app.py`
2. Upload a JD file (.txt / .docx / .pdf) or paste the posting text — the app starts with an empty JD by design
3. Review the JD summary in the "Job Description Setup" expander
4. Upload candidate CVs via the "Single Candidate Score" or "Batch Screening" tab
5. View results, rankings, and download the CSV report

### Real Workflow (With Your Own JD)

1. Place your JD in `1_Active_JD/` as `current_jd.txt`, `current_jd.docx`, or `current_jd.pdf`
2. Or upload via the UI using "Upload new JD file"
3. The app extracts the dynamic profile and displays a summary
4. Upload candidate CVs from `2_Active_CVs/` or via file uploader
5. Screening runs with full weighted scoring
6. View ranked results, copy top candidates to shortlist folders
7. Download the CSV report for your ATS records

### Batch Screening (1,000+ CVs)

1. Enable "Batch Mode" in the candidate upload section
2. Upload multiple CVs (.pdf or .docx)
3. Click "Start Batch Screening"
3. Progress updates every 50 candidates for smooth UI performance
4. Results appear with full scoring and ranking
5. Download the ranking CSV report
6. Top candidates are auto-copied to the mode-specific shortlist folder

---

## ⚙️ Configuration

### Priority Modes

Edit `evaluator.py` to change the `PRIORITY_MODE` global variable:

```python
PRIORITY_MODE = "experience"   # default
# or "qualification"
# or "balanced"
```

Each mode assigns different weights to the three scoring pillars so the total always sums to 100:

| Mode | Core Skills | Experience/Edu | Domain Knowledge | Focus | Role |
|------|-------------|----------------|------------------|-------|------|
| **experience** | 15% | 45% | 40% | 0% | — |
| **qualification** | 25% | 15% | 30% + 15% focus+edu | 15% | — |
| **balanced** | 35% | 25% | 20% + 12% focus + 8% edu | — | — |

### Focus Area

`FOCUS_AREA` is **derived automatically from the active JD** every time it is loaded — its title minus posting boilerplate and generic role heads (a Medical Officer JD yields `medical`, a Site Management JD yields `site management field`). Leave the module default as `""` to keep auto-derivation, or pin one explicitly:

```python
FOCUS_AREA = ""   # default: derive from the uploaded JD
# or pin explicitly via CLI:  python evaluator.py --focus-area "community mobilization"
```

Related focus phrases are mined from the active JD text — nothing is hardcoded, and nothing carries over from a previously loaded posting.

### Role Tiers

The `ROLE_TIERS` list in `evaluator.py` controls the generic fallback phrases of how candidate job titles map to the active JD's own reporting line. Each tier has:

- A **label** (descriptive name)
- A **fraction** of ROLE_WEIGHT (1.0 = full weight, 0.8 = 80%, 0.47 = 47%)
- A **list of phrases** that trigger that tier

On every JD load the ladder is rebuilt: the posting's own title opens tier 1, other role titles the JD text names fill tier 2, generic leadership titles fill tier 3. Roles from other postings' JDs are never used.

### Blacklist

Place blacklist entries in `blacklist.csv` with this format:

```
Candidate Name,Phone Numbers,Status,Notes
"Rafiul Karim","01700000001",blacklisted,"Synthetic demo entry"
```

The blacklist is gitignored — never commit real PII. Use the synthetic `data/candidates.csv` (fictional names, sequential demo phone numbers) for testing.

---

## 📊 Output Files

### Ranking CSV Report

Generated at `3_Evaluation_Output/ranked_candidates_report.csv` with columns:

| Column | Description |
|--------|-------------|
| **Rank** | Ranking position (1 = highest score) |
| **Candidate_File_Name** | Original filename |
| **Match_Score** | Score out of 100 |
| **Bonus_Points** | Additional bonus points |
| **Status** | RANKED / REVIEW / EXCLUDED / ERROR etc. |
| **Strengths** | Key strengths extracted from scoring |
| **Weaknesses** | Areas of concern/gaps |
| **Recruiter_Remark** | Full engine reasoning string |
| **Summary_Reasoning** | One-line summary |

### Shortlist Folders

Top-ranked candidates are automatically copied to mode-specific folders:

- `TOP_CV/Rank_1_Score_88_CandidateName.pdf` (experience mode)
- `TOP_CV_QUALIFICATION/Rank_1_Score_88_CandidateName.pdf` (qualification mode)
- `TOP_CV_BALANCED/Rank_1_Score_88_CandidateName.pdf` (balanced mode)

The folder is created under `3_Evaluation_Output/` with the mode name.

### Evaluation Output Summary

For each screened candidate, the engine produces:

- A **total score** (0-100)
- **Three pillar scores** (Skills, Experience, Domain)
- **Strengths list** — What the candidate has that matches the JD
- **Gaps list** — What the candidate is missing
- **Engine reasoning** — Human-readable explanation of the score
- **Blacklist verdict** — If applicable (excluded or review flag)

---

## 🔒 Security & Data Privacy

### Critical: Never Commit Real PII

- `1_Active_JD/` — **Gitignored** — contains the client's real job posting with salary, location, and company name
- `2_Active_CVs/` — **Gitignored** — real candidate CVs with PII (names, phone numbers, addresses)
- `blacklist.csv` — **Gitignored** — real blacklist entries with phone numbers

### Safe to Commit

- `data/jd.txt` — ✓ **Fictional example JD** (organization, salary and details invented for the demo) — safe for repository
- `data/candidates.csv` — ✓ **Synthetic blacklist** with fictional names and sequential demo phone numbers — safe for repository
- `3_Evaluation_Output/` — ✓ **Generated** output — appears in `.gitignore`
- `app.py`, `evaluator.py` — ✓ **Source code** — safe to commit

Raw posting files dropped into `data/` (`.pdf`, `.docx`, `.xlsx`) are gitignored by extension so a real JD or spreadsheet can never be committed by accident.

### Best Practices

1. **Keep real data gitignored** — All client PII, CVs, and blacklist entries must remain in gitignored folders
2. **Use synthetic data for commits** — The `data/` folder contains synthetic, redacted data safe for version control
3. **Upload real data via UI** — Load job descriptions and candidate CVs through the Streamlit interface, never edit gitignored files directly in the repo
4. **Regular cleanup** — Periodically clean `3_Evaluation_Output/` to remove generated reports from previous runs
5. **Backup before bulk operations** — Before screening 1,000+ candidates, backup any important data in `3_Evaluation_Output/`

---

## 🧪 Testing

### Smoke Tests

Run the headless smoke test to verify the scoring pipeline:

```bash
python tests/smoke_app.py
```

This tests:
- Upload hardening (validates file types and sizes)
- Scoring pipeline (read → blacklist → filter → score → remark → export)
- Blacklist integration
- CSV export format

### CLI Usage

```bash
# Default: score top 200 candidates
python evaluator.py

# Shortlist top 50
python evaluator.py --top 50

# Use a specific JD file
python evaluator.py --jd my_posting.docx

# Ignore blacklist for this run
python evaluator.py --no-blacklist

# Use qualification priority mode
python evaluator.py --priority-mode qualification --focus-area "community mobilization"
```

### CLI Options

| Option | Description |
|--------|-------------|
| `--top N` | Shortlist top N candidates (default: 200) |
| `--threshold X` | Move all candidates scoring >= X |
| `--jd path` | Path to job description file |
| `--no-blacklist` | Ignore blacklist.csv for this run |
| `--blacklist-names flag|exclude|off` | Name-match mode |
| `--focus-area text` | Override focus area |
| `--priority-mode experience|qualification|balanced` | Scoring priority mode |

---

## 🎨 Theme & Customization

### Brown & Blue Theme

The app features a professional Brown & Executive Blue theme:

**Color Palette:**
- **Primary/Headers/Active Tabs:** Deep Executive Blue `#1E3A8A` / `#0F172A`
- **Secondary/Accents/Highlights:** Rich Warm Brown `#B45309` / `#92400E` / `#D97706`
- **Background/Containers:** Clean Off-White `#FAFAF9` with crisp white cards `#FFFFFF`
- **Borders:** Subtle slate `#E7E5E4`
- **Status Badges:** Blue (matching), Brown (highlights/requirements), Red (blacklisted/dropped)

**Customization:**

To modify the theme, edit the `THEME_CSS` constant in `app.py`. The CSS is injected via `st.markdown()` and overrides Streamlit's default styling.

### Priority Mode & Focus Area

Configure via `evaluator.py` globals:

```python
PRIORITY_MODE = "experience"
FOCUS_AREA = "site management"
```

These settings affect:
- Weight distribution across the three scoring pillars
- Which phrases are mined from the JD for role tiers
- How focus-area matching is weighted in scores
- The shortlist folder name (TOP_CV, TOP_CV_QUALIFICATION, TOP_CV_BALANCED)

### UI Text & Labels

All user-facing text is in `app.py`. Edit the `jd_upload_section()`, `candidates_upload_section()`, and other helper functions to customize labels, help text, and instructional messages.

---

## 📦 Deployment

### Step 1 — Publish a clean history to GitHub

One command verifies the project, re-checks it for PII, builds a **fresh
single-commit history from the current (cleaned) tree** and pushes it:

```bash
# Git Bash on Windows / any bash elsewhere
bash scripts/publish-github.sh https://github.com/YOUR_USER/cv-jd-matcher.git
```

Create the empty repository first at <https://github.com/new>. The script:

1. Runs `py_compile` + `tests/smoke_app.py` — refuses to publish broken code
2. Scans every file to be committed for real-looking phone numbers / personal
   emails (the synthetic demo series `01700000xxx` is the only allowed format)
3. Archives any previous local history to `.git-pre-scrub-backup-*/`
   (local only, gitignored) so a pre-cleanup snapshot is never pushed
4. Commits the clean tree as a single initial commit and pushes `main`

Manual equivalent, if you prefer:

```bash
git init -b main
git add .
git commit -m "Initial commit: CV ↔ JD Matcher — enterprise ATS"
git remote add origin https://github.com/YOUR_USER/cv-jd-matcher.git
git push -u origin main
```

### Step 2 — Host the app

**Streamlit Community Cloud (recommended — native `app.py` hosting):**

1. Open <https://share.streamlit.io> → **Create app** → connect GitHub
2. Pick the repository, set **Main file path** to `app.py`
3. Deploy — dependencies install from the pinned `requirements.txt`
4. Every push to `main` redeploys automatically

> **Vercel note:** Vercel does not run Python/Streamlit apps, so `app.py`
> cannot be deployed there. Vercel *is* the right host for the optional
> static web build under `web/`, and the GitHub remote you created above is
> exactly what it connects to:
>
> 1. `cd web && npm install && npm run build` (outputs `web/dist/`)
> 2. In Vercel → **Add New Project** → import the GitHub repository
> 3. Set **Framework preset** to *Vite*, **Root Directory** to `web`
> 4. Build command `npm run build`, output directory `dist` → Deploy
>
> For a Python URL on a custom domain, use Streamlit Cloud (or any
> Python-capable host) and point the domain at it.

### Production Considerations

- Keep `1_Active_JD/` and `2_Active_CVs/` gitignored in production
- Use the UI to upload real JDs and CVs — never edit gitignored files directly
- Set up regular backups of `3_Evaluation_Output/` reports
- For large deployments, tune `BATCH_SIZE` in `evaluator.py`
- Monitor Streamlit logs for any scoring errors or memory issues

---

## 🐛 Known Limitations

1. **Scanned/image-only PDFs** — If a CV is a scanned image, text extraction will fail. The app flags these and gives a score of 0.
2. **Complex PDF formatting** — Tables, charts, and multi-column layouts may not extract cleanly. Simple, text-focused CVs work best.
3. **Very long CVs** — Only the first 25 pages are examined for text extraction. Extremely long CVs with critical info on later pages may be missed.
4. **DOCX formatting** — python-docx extracts paragraph text and table cells. Complex formatting (headers, footers, text boxes) may be lost.
5. **Font encoding** — Some PDFs use custom font encodings that PyPDF cannot decode. These will appear as empty or garbled text.
6. **Blacklist false positives** — Name matching normalizes honorifics but may still match unintended candidates if names share common components.
7. **Focus area coverage** — The focus area mining extracts phrases from the JD, but very niche or technical focus areas may have limited matching phrases.

---

## 📞 Support

- **Issues:** GitHub Issues
- **Questions:** Open an discussion on the repository
- **Custom requests:** Contact the development team

For production deployments, configuration questions, or integration with other systems, please refer to the full documentation or contact support.

---

## 🙏 Acknowledgments

- Streamlit for the excellent web app framework
- PyPDF and python-docx for PDF/DOCX text extraction
- pandas for data manipulation and CSV reporting
- The open-source community for inspiring tools and libraries

---

*CV ↔ JD Matcher — Enterprise ATS for modern recruitment*