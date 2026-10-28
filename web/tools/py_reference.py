#!/usr/bin/env python3
"""
py_reference.py — score the `.verify/` fixture CVs with the PYTHON engine and
write `.verify/expected.json`.

`web/tools/verify-engine.ts` then re-scores the same CV text with the
TypeScript port and compares the two, so a port regression is caught
mechanically instead of by eyeballing the shortlist.

Usage (from the project root, inside the venv):
    python web/tools/py_reference.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY = ROOT / ".verify"


def load_evaluator():
    spec = importlib.util.spec_from_file_location("evaluator", ROOT / "evaluator.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("Could not load evaluator.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluator"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not (VERIFY / "cvs.json").exists():
        raise SystemExit(
            f"{VERIFY / 'cvs.json'} is missing — generate the fixture first "
            "(see web/tools/README.md)."
        )

    ev = load_evaluator()
    profile = ev.extract_jd_profile(VERIFY / "jd.txt")
    cvs = json.loads((VERIFY / "cvs.json").read_text(encoding="utf-8"))

    rows = []
    for cv in cvs:
        result = ev.score_candidate(cv["text"], profile)
        rows.append({
            "name": cv["name"],
            "score": result.score,
            "reasoning": ev.build_reasoning(result),
        })

    payload = {
        "mode": ev.PRIORITY_MODE,
        "focusArea": ev.FOCUS_AREA,
        "profile": {
            "title": profile.title,
            "minYears": profile.min_experience_years,
            "maxYears": profile.max_experience_years,
            "location": profile.location,
            "educationLevels": profile.education_levels,
            "educationFields": profile.education_fields,
            "industryKeywords": profile.industry_keywords,
            "languages": profile.languages,
            "hardSkills": len(profile.hard_skills),
            "focusCores": len(profile.focus_cores),
            "focusTerms": len(profile.focus_terms),
            "roleTiers": [[label, frac, len(pairs)] for label, frac, pairs in profile.role_tiers],
        },
        "candidates": rows,
    }
    (VERIFY / "expected.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"wrote {VERIFY / 'expected.json'}  ({len(rows)} candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
