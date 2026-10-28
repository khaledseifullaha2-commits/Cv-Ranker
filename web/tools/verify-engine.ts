/**
 * verify-engine.ts — check the TypeScript port against the Python original.
 *
 * Reads the fixtures produced by `py_reference.py` and re-scores every CV with
 * `src/lib/engine.ts`, then compares the JD profile, the score and the
 * reasoning text.
 *
 * Usage (from the project root):
 *     node web/tools/verify-engine.ts            # default fixture path .verify
 *     node web/tools/verify-engine.ts --tol 0.3  # loosen the score tolerance
 *
 * Exits non-zero when a score drifts past the tolerance or the JD profile
 * extraction disagrees, so it also works as a CI gate.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  buildReasoning,
  extractJdProfile,
  scoreCandidate,
  type JdProfile,
  type PriorityMode,
} from "../src/lib/engine.ts";

interface Expected {
  mode: PriorityMode;
  focusArea: string;
  profile: {
    title: string;
    minYears: number;
    maxYears: number;
    location: string;
    educationLevels: string[];
    educationFields: string[];
    industryKeywords: string[];
    languages: string[];
    hardSkills: number;
    focusCores: number;
    focusTerms: number;
    roleTiers: Array<[string, number, number]>;
  };
  candidates: Array<{ name: string; score: number; reasoning: string }>;
}

const args = process.argv.slice(2);
const tolArg = args.indexOf("--tol");
const TOLERANCE = tolArg >= 0 ? Number(args[tolArg + 1]) : 0.25;
const fixtureDir = join(import.meta.dirname ?? ".", "..", "..", ".verify");

const read = (file: string) => readFileSync(join(fixtureDir, file), "utf8");
const expected: Expected = JSON.parse(read("expected.json"));
const jdText = read("jd.txt");
const cvs: Array<{ name: string; text: string }> = JSON.parse(read("cvs.json"));

let failures = 0;
const fail = (msg: string) => {
  failures += 1;
  console.log(`  FAIL  ${msg}`);
};
const check = (label: string, mine: unknown, theirs: unknown) => {
  const a = JSON.stringify(mine);
  const b = JSON.stringify(theirs);
  if (a === b) console.log(`  ok    ${label}: ${a}`);
  else fail(`${label}\n        py=${b}\n        ts=${a}`);
};

// ── 1. JD profile extraction ───────────────────────────────────────────────
const profile: JdProfile = extractJdProfile(jdText, expected.focusArea, expected.mode);
const p = expected.profile;

console.log(`\n== JD profile (mode=${expected.mode}, focus='${expected.focusArea}') ==`);
check("title", profile.title, p.title);
check("years", [profile.minYears, profile.maxYears], [p.minYears, p.maxYears]);
check("location", profile.location, p.location);
check("educationLevels", profile.educationLevels, p.educationLevels);
check("educationFields", profile.educationFields, p.educationFields);
check("industryKeywords", profile.industryKeywords, p.industryKeywords);
check("languages", profile.languages, p.languages);
check("hardSkills count", profile.hardSkills.size, p.hardSkills);
check("focusCores", profile.focusCores.length, p.focusCores);
check("focusTerms count", profile.focusTerms.length, p.focusTerms);
check(
  "roleTiers",
  profile.roleTiers.map((t) => [t.label, t.frac, t.pairs.length]),
  p.roleTiers,
);

// ── 2. Per-candidate scores ────────────────────────────────────────────────
console.log(`\n== Candidate scores (tolerance ±${TOLERANCE}) ==`);
let maxDelta = 0;
let reasoningMismatches = 0;

for (const cv of cvs) {
  const want = expected.candidates.find((c) => c.name === cv.name);
  if (!want) {
    fail(`${cv.name} is in cvs.json but not in expected.json`);
    continue;
  }
  const result = scoreCandidate(cv.text, profile, {
    mode: expected.mode,
    focusArea: expected.focusArea,
  });
  const delta = Math.abs(result.score - want.score);
  maxDelta = Math.max(maxDelta, delta);
  const flag = delta > TOLERANCE ? "FAIL" : "ok  ";
  if (delta > TOLERANCE) failures += 1;
  const reasoning = buildReasoning(result);
  const same = reasoning === want.reasoning;
  if (!same) reasoningMismatches += 1;
  console.log(
    `  ${flag}  py=${want.score.toFixed(1).padStart(5)}  ts=${result.score
      .toFixed(1)
      .padStart(5)}  Δ=${delta.toFixed(2).padStart(4)}  reasoning=${same ? "identical" : "differs"}  ${cv.name}`,
  );
}

console.log(
  `\n${cvs.length} candidates | max score delta ${maxDelta.toFixed(2)} | ` +
    `${reasoningMismatches} reasoning string(s) differ | ${failures} failure(s)`,
);
if (failures === 0) console.log("PORT VERIFIED\n");
process.exit(failures === 0 ? 0 : 1);
