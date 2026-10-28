#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  publish-github.sh — clean GitHub publish for the CV ↔ JD Matcher
#
#  Verifies the project, re-checks it for PII, then builds a PII-free,
#  single-commit history from the CURRENT (scrubbed) working tree.
#  Any older local history is archived, never pushed, so the pre-cleanup
#  snapshot cannot leak into the repository.
#
#  Usage (Git Bash on Windows, any bash elsewhere):
#      bash scripts/publish-github.sh
#          ...verify + PII-gate + fresh clean local commit (no push)
#
#      bash scripts/publish-github.sh https://github.com/YOUR_USER/cv-jd-matcher.git
#          ...same, then push the result (create the empty repo first:
#          https://github.com/new)
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_URL="${1:-}"
PYTHON="${PYTHON:-python}"
STAMP="$(date +%Y%m%d-%H%M%S)"

# Files that will be committed (tracked + new, minus everything ignored).
scan_files() { git ls-files -z -c -o --exclude-standard 2>/dev/null || true; }
# Allowed phone series: the sequential synthetic demo numbers 01700000xxx.
PHONE_RE='01[3-9][0-9]{8}'
EMAIL_RE='[A-Za-z0-9._%+-]+@(gmail|yahoo|hotmail|outlook)\.[A-Za-z]{2,}'

# ── 1. Verification gate: the code must compile and pass its tests ─────
echo "[1/5] Verification gate (py_compile + smoke tests) ..."
"$PYTHON" -m py_compile app.py evaluator.py
if ! "$PYTHON" tests/smoke_app.py >"smoke-${STAMP}.log" 2>&1; then
    echo "  FAILED — smoke tests did not pass. See smoke-${STAMP}.log"
    exit 1
fi
rm -f "smoke-${STAMP}.log"
echo "  OK — all checks passed."

# ── 2. PII gate: nothing that looks like real personal data may ship ───
# -I skips binaries (PDFs, fonts); only the 01700000xxx demo series passes.
echo "[2/5] PII gate (phones + personal emails) ..."
BAD_PHONES="$(scan_files | xargs -0 grep -IhoE "$PHONE_RE" 2>/dev/null \
    | grep -v '^01700000' | sort -u || true)"
BAD_EMAILS="$(scan_files | xargs -0 grep -IhoE "$EMAIL_RE" 2>/dev/null \
    | sort -u || true)"
if [ -n "$BAD_PHONES$BAD_EMAILS" ]; then
    echo "  FAILED — possible real PII would be committed:"
    [ -n "$BAD_PHONES" ] && echo "$BAD_PHONES"
    [ -n "$BAD_EMAILS" ] && echo "$BAD_EMAILS"
    echo "  Replace it with synthetic data (demo phone series: 01700000xxx)."
    exit 1
fi
echo "  OK — no real-looking phone numbers or personal emails."

# ── 3. Archive any pre-existing history instead of pushing it ──────────
echo "[3/5] Archiving previous history (if any) ..."
if git rev-parse --git-dir >/dev/null 2>&1; then
    HAS_HISTORY="$(git rev-list --count --all 2>/dev/null || echo 0)"
    if [ "$HAS_HISTORY" -gt 0 ]; then
        mv .git ".git-pre-scrub-backup-${STAMP}"
        echo "  old history archived at .git-pre-scrub-backup-${STAMP}/ (local only, gitignored)"
    fi
fi

# ── 4. Fresh, clean repository with a single initial commit ────────────
echo "[4/5] Initializing clean repository ..."
git init -b main >/dev/null 2>&1 || git init >/dev/null
git add -A
# Re-gate the exact index that is about to be committed.
if git grep --cached -I -h -oE "$PHONE_RE" 2>/dev/null | grep -qv '^01700000'; then
    echo "  FAILED — index still contains a non-demo phone number."; exit 1
fi
git commit -q -m "Initial commit: CV ↔ JD Matcher — enterprise ATS

Weighted priority scoring engine with JD-derived, industry-agnostic focus
and role tiers; PDF/DOCX JD ingestion; position-based TOP_CV output;
blacklist screening; Brown & Executive Blue Streamlit UI; synthetic demo
data only (PII gate enforced by scripts/publish-github.sh)."
echo "  OK — single clean commit on 'main'."

# ── 5. Push (only when a repository URL was given) ─────────────────────
if [ -z "$REPO_URL" ]; then
    echo "[5/5] Push skipped (no repository URL given)."
    echo
    echo "Local clean history is ready. To push:"
    echo "  1. create an empty repo at https://github.com/new"
    echo "  2. bash scripts/publish-github.sh https://github.com/YOUR_USER/cv-jd-matcher.git"
    exit 0
fi

echo "[5/5] Pushing to ${REPO_URL} ..."
if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$REPO_URL"
else
    git remote add origin "$REPO_URL"
fi
git push -u origin main

echo
echo "Done — clean history published to main."
echo "Next: deploy it  →  README.md → 📦 Deployment (Streamlit Cloud / Vercel)"
