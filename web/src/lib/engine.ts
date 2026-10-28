/**
 * engine.ts — TypeScript port of `evaluator.py` (sections 0–3).
 *
 * The whole screening model lives here: dynamic JD extraction, the nine
 * scoring buckets and the site-management-line tenure detector.
 *
 * Deliberately pure: no DOM, no network, no Node APIs. The same module runs
 * in the browser, inside the Worker and under `node tools/verify-engine.ts`
 * (used to check the port against the Python original).
 */

// ══════════════════════════════════════════════════════════════════════════
// Types
// ══════════════════════════════════════════════════════════════════════════

export type PriorityMode = "experience" | "balanced" | "qualification";

export interface RoleTier {
  label: string;
  frac: number;
  pairs: Array<[string, RegExp]>;
}

export interface JdProfile {
  rawText: string;
  title: string;
  minYears: number;
  maxYears: number;
  location: string;
  educationLevels: string[];
  educationFields: string[];
  industryKeywords: string[];
  hardSkills: Map<string, number>;
  softSkills: string[];
  responsibilities: string[];
  languages: string[];
  focusCores: string[];
  focusTerms: string[];
  roleTiers: RoleTier[];
}

export interface ScoreResult {
  score: number;
  strengths: string[];
  gaps: string[];
  /** Points contributed by each bucket — drives the score-bar UI. */
  breakdown: Record<string, number>;
  /** Weighted site-management-line years (experience mode only). */
  smYears: number;
  /** Per-role SM-line tenure breakdown, e.g. "Field Officer (2yrs, ...)". */
  smRoles: string[];
}

export interface ModeConfig {
  focusWeight: number;
  roleWeight: number;
  historicalFactor: number;
  skills: number;
  exp: number;
  eduLevel: number;
  eduField: number;
  industry: number;
  location: number;
  soft: number;
  lang: number;
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 0 — Configuration (mirrors evaluator.py)
// ══════════════════════════════════════════════════════════════════════════

export const MODES: Record<PriorityMode, ModeConfig> = {
  // Same/similar site-management-line experience dominates — NOT total years.
  experience: {
    focusWeight: 18, roleWeight: 30, historicalFactor: 0.8,
    skills: 15, exp: 45, eduLevel: 8, eduField: 5,
    industry: 10, location: 7, soft: 5, lang: 5,
  },
  // Education level + field match dominate.
  qualification: {
    focusWeight: 10, roleWeight: 8, historicalFactor: 0.33,
    skills: 25, exp: 15, eduLevel: 22, eduField: 13,
    industry: 10, location: 5, soft: 5, lang: 5,
  },
  // The original balanced weights.
  balanced: {
    focusWeight: 20, roleWeight: 15, historicalFactor: 0.33,
    skills: 35, exp: 25, eduLevel: 15, eduField: 5,
    industry: 10, location: 5, soft: 5, lang: 5,
  },
};

/** Focus competency mined from the JD. Comma-separate several areas. */
export const DEFAULT_FOCUS_AREA = "site management";

/** Every other bucket shrinks so the total is still 100 points. */
export function scaleFor(cfg: ModeConfig): number {
  return Math.max(0, 100 - cfg.focusWeight - cfg.roleWeight) / 100;
}

/** The Site Management reporting line, strongest tier first. */
export const ROLE_TIERS: Array<[string, number, string[]]> = [
  ["site-management title", 1.0, [
    "site management", "site management officer",
    "site management assistant", "site management coordinator",
    "site manager", "camp management officer", "smo",
  ]],
  ["works under Site Management", 0.8, [
    "facilitator", "field officer", "community mobilizer",
    "community mobiliser", "mobilizer", "mobiliser",
    "community mobilization", "community mobilisation",
    "community volunteer", "community worker",
    "community outreach", "outreach worker", "outreach officer",
    "community engagement", "camp facilitator", "site facilitator",
    "community facilitator", "community development officer",
    "data collector", "enumerator",
  ]],
  ["field leading", 0.47, [
    "team leader", "field leader", "coordinator", "supervisor",
    "program manager", "programme manager", "project manager",
    "officer in charge", "oic", "head of", "chief", "manager",
  ]],
];

const STOP_WORDS = new Set([
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
  "read", "before", "encourage", "submit", "cv",
  "complete", "including", "contact", "number", "recent",
  "passport", "size", "photograph", "application", "accepted",
  "based", "early", "short-listed", "candidates",
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
  "would", "like", "know", "about", "limited",
]);

// ══════════════════════════════════════════════════════════════════════════
// Helpers
// ══════════════════════════════════════════════════════════════════════════

/** Python's str.title() — capitalises after any non-letter. */
export function pyTitle(value: string): string {
  return value.replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/** Python's round(x, 1) (half-up is close enough for display values). */
export function round1(x: number): number {
  return Math.round(x * 10) / 10;
}

/** Python's "%g" formatting: 6 significant digits, trailing zeros trimmed. */
export function g(x: number): string {
  return String(Number(x.toPrecision(6)));
}

/**
 * Python's `str.islower()` for a single character: true only when the
 * character is cased AND uppercase-free. A leading digit or punctuation
 * returns false, which is what keeps enumerated job titles such as
 * "1. Site Management Field Mobilizer (1yrs)" valid.
 */
function isLowerChar(ch: string): boolean {
  const cased = ch.toLowerCase() !== ch.toUpperCase();
  return cased && ch === ch.toLowerCase();
}

export function round2(x: number): number {
  return Math.round(x * 100) / 100;
}

/** Python's re.escape for the characters we care about. */
export function escapeRe(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const collapse = (value: string): string => value.split(/\s+/).filter(Boolean).join(" ");

/** Mirror Python's universal-newline text reads. */
export function normalizeNewlines(value: string): string {
  return value.replace(/\r\n|\r/g, "\n");
}

function allRegex(text: string, pattern: RegExp): RegExpExecArray[] {
  const re = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : pattern.flags + "g");
  const out: RegExpExecArray[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    out.push(m);
    if (m.index === re.lastIndex) re.lastIndex += 1; // guard against zero-width loops
  }
  return out;
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 1 — Dynamic JD extraction (zero hardcoding)
// ══════════════════════════════════════════════════════════════════════════

export function extractTitle(text: string): string {
  for (const raw of text.trim().split("\n").slice(0, 5)) {
    const line = raw.trim();
    if (line.length > 5 && !line.startsWith("Vacancy")) return line;
  }
  return "Unknown Title";
}

export function extractExperience(text: string): [number, number] {
  const lower = text.toLowerCase();

  let m = /(\d{1,2})\s*(?:to|-)\s*(\d{1,2})\s*(?:years?|yrs?)/.exec(lower);
  if (m) return [Number(m[1]), Number(m[2])];

  m = /(?:at\s+least|minimum|min\.?)\s+(\d{1,2})\s*(?:years?|yrs?)/.exec(lower);
  if (m) return [Number(m[1]), Number(m[1])];

  m = /(\d{1,2})\s*(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp)/.exec(lower);
  if (m) return [Number(m[1]), Number(m[1])];

  return [0, 0];
}

export function extractLocation(text: string): string {
  const cities = [
    "cox's bazar", "cox bazar", "cox`s bazar", "dhaka",
    "chittagong", "chattogram", "sylhet", "rajshahi",
    "khulna", "barishal", "rangpur", "mymensingh",
    "ukhia", "ukhiya", "tekhnaf", "ramu",
  ];
  const lower = text.toLowerCase();
  for (const city of cities) {
    if (lower.includes(city)) return pyTitle(city);
  }
  return "";
}

const EDU_LEVELS: Array<[string, string[]]> = [
  ["phd", ["ph.d", "phd", "doctorate"]],
  ["masters", ["masters", "m.sc", "mba", "m.eng", "master"]],
  ["bachelor", ["bachelor", "b.sc", "b.eng", "b.tech", "bss", "llb", "ba ", "bs ", "b.a.", "b.s."]],
  ["diploma", ["diploma"]],
  ["ssc/hsc", ["ssc", "hsc", "h.s.c", "s.s.c", "secondary school"]],
  ["vocational", ["vocational", "technical training"]],
];

const EDU_FIELDS = [
  "social science", "law", "legal", "communication", "humanities",
  "mechanical", "electrical", "civil", "computer science",
  "business", "management", "public administration",
  "international relations", "development studies",
  "environmental", "agriculture", "public health",
  "education", "psychology", "sociology", "anthropology",
  "political science", "economics", "finance",
];

export function extractEducation(text: string): [string[], string[]] {
  const lower = text.toLowerCase();
  const levels = EDU_LEVELS.filter(([, kws]) => kws.some((kw) => lower.includes(kw)))
    .map(([label]) => label);
  const fields = EDU_FIELDS.filter((f) => lower.includes(f));
  return [levels, fields];
}

const INDUSTRY_TERMS = [
  "ngo", "consulting", "manpower", "recruitment", "humanitarian",
  "development", "aid", "refugee", "camp", "unhcr", "iom",
  "red cross", "government", "public sector", "private sector",
  "health", "education", "environment", "finance", "banking",
  "telecom", "ict", "energy", "manufacturing", "construction",
  "agriculture", "garment", "rmg", "textile",
];

export function extractIndustry(text: string): string[] {
  const lower = text.toLowerCase();
  return INDUSTRY_TERMS.filter((t) => lower.includes(t));
}

const LANGUAGES = [
  "bangla", "bengali", "english", "hindi", "urdu", "arabic",
  "chittagonian", "rohingya", "rakhine", "french", "spanish",
  "mandarin", "chinese", "japanese", "korean",
];

export function extractLanguages(text: string): string[] {
  const lower = text.toLowerCase();
  return LANGUAGES.filter((l) => lower.includes(l)).map(pyTitle);
}

/**
 * Domain-specific hard skills mined from the JD's own Skills / Additional
 * Requirements / Responsibilities sections, weighted by where they appear
 * (skills 1.5×, additional requirements 1.3×, duties 1.0×) and by length
 * (multi-word phrases are more specific). Capped at the top 50.
 */
export function extractHardSkills(text: string): Map<string, number> {
  const lower = text.toLowerCase();

  const skillsSection =
    /skills?\s*&?\s*expertise[:\s]*(.*?)(?:compensation|other\s+benefit|employment|$)/s
      .exec(lower)?.[1] ?? "";
  const addlSection =
    /additional\s+requirements?[:\s]*(.*?)(?:competencies|languages|responsibilities|$)/s
      .exec(lower)?.[1] ?? "";
  const respSection =
    /job\s+responsibilit(?:y|ies)[:\s]*(.*?)(?:skills|compensation|application|deadline|$)/s
      .exec(lower)?.[1] ?? "";

  const combined = `${skillsSection} ${addlSection} ${respSection}`;
  const rawPhrases = allRegex(combined, /\b([a-z]{3,}(?:\s+[a-z]{2,}){0,2})\b/);

  const scores = new Map<string, number>();
  const bump = (phrase: string, weight: number) => {
    const prev = scores.get(phrase);
    if (prev === undefined) scores.set(phrase, weight);
    else scores.set(phrase, Math.max(prev, weight));
  };

  for (const m of rawPhrases) {
    const phrase = m[1].trim();
    // Python's str.split() splits on ANY whitespace run, not just spaces.
    const words = phrase.split(/\s+/).filter(Boolean);
    if (words.every((w) => STOP_WORDS.has(w))) continue;
    if (words.length === 1 && STOP_WORDS.has(words[0])) continue;

    let weight = 1.0;
    if (skillsSection.includes(phrase)) weight = 1.5;
    else if (addlSection.includes(phrase)) weight = 1.3;
    if (words.length >= 2) weight *= 1.4;
    bump(phrase, weight);
  }

  // Capitalised phrases anywhere in the JD may be explicit skill names.
  for (const m of allRegex(text, /\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b/)) {
    const phrase = m[1].toLowerCase().trim();
    const words = phrase.split(/\s+/).filter(Boolean);
    if (words.every((w) => STOP_WORDS.has(w))) continue;
    if (words.length >= 2 && !scores.has(phrase)) scores.set(phrase, 1.0);
  }

  const sorted = [...scores.entries()].sort((a, b) => b[1] - a[1]).slice(0, 50);
  return new Map(sorted);
}

export function extractResponsibilities(text: string): string[] {
  const m = /job\s+responsibilit(?:y|ies)[:\s]*(.*?)(?:skills|compensation|application|deadline|$)/is
    .exec(text);
  if (!m) return [];
  return m[1]
    .split(/(?:\r?\n|(?<=\.)\s+)/)
    .map((item) => item.trim())
    .filter((item) => item.length > 15);
}

/** Build the focus vocabulary from the focus area + the active JD text. */
export function focusVocabulary(
  jdText: string,
  focusArea: string,
  focusWeight: number,
): [string[], string[]] {
  const cores = focusArea.split(",").map((c) => c.trim().toLowerCase()).filter(Boolean);
  if (cores.length === 0 || !focusWeight) return [[], []];

  const focusTerms = new Set<string>();
  for (const core of cores) {
    for (const w of core.match(/[a-z']+/g) ?? []) focusTerms.add(w);
  }

  const words = jdText.toLowerCase().match(/[a-z'`]+/g) ?? [];
  const related: string[] = [];
  const seen = new Set<string>();
  for (const width of [2, 3, 4, 5]) {
    for (let i = 0; i + width <= words.length; i++) {
      const window = words.slice(i, i + width);
      if (!window.some((w) => focusTerms.has(w))) continue;
      const phrase = window.join(" ");
      if (seen.has(phrase) || cores.includes(phrase)) continue;
      seen.add(phrase);
      related.push(phrase);
    }
  }

  related.sort((a, b) => {
    const d = a.split(" ").length - b.split(" ").length;
    return d !== 0 ? d : a.length - b.length;
  });
  return [cores, related.slice(0, 40)];
}

function phraseRegex(phrase: string): RegExp {
  return new RegExp(`(?<![a-z])${escapeRe(phrase.toLowerCase())}s?(?![a-z])`);
}

/** Role phrases the JD itself asks for (posting title + duty-line titles). */
export function jdRolePhrases(jdTitle: string, jdText: string): string[] {
  const out: string[] = [];
  const title = collapse(jdTitle).toLowerCase();
  if (title.length > 3 && title.length < 70 && !title.startsWith("unknown")) out.push(title);

  const pat = /\b((?:site management|field|community|camp|protection)\s+(?:officer|assistant|coordinator|facilitator|manager|specialist))\b/gi;
  for (const m of allRegex(jdText, pat)) {
    const phrase = m[1].toLowerCase();
    if (!out.includes(phrase)) out.push(phrase);
  }
  if (/\bSMO\b/.test(jdText) && !out.includes("smo")) out.push("smo");
  return out.slice(0, 12);
}

export function buildRoleTiers(title: string, jdText: string): RoleTier[] {
  const tiers: RoleTier[] = [];
  ROLE_TIERS.forEach(([label, frac, phrases], idx) => {
    const phraseList = [...phrases];
    if (idx === 0) {
      for (const ph of jdRolePhrases(title, jdText)) {
        if (!phraseList.includes(ph)) phraseList.push(ph);
      }
    }
    const pairs: Array<[string, RegExp]> = [];
    for (const raw of phraseList) {
      const ph = raw.trim().toLowerCase();
      if (ph && !pairs.some(([p]) => p === ph)) pairs.push([ph, phraseRegex(ph)]);
    }
    if (pairs.length) tiers.push({ label, frac, pairs });
  });
  return tiers;
}

export function extractJdProfile(
  jdTextRaw: string,
  focusArea: string = DEFAULT_FOCUS_AREA,
  mode: PriorityMode = "experience",
): JdProfile {
  // Python's Path.read_text() decodes with universal newlines, so CRLF/CR
  // arrive as LF. Normalise identically, otherwise a phrase mined across a
  // line break ("...bdjobs.com\nhttps://...") would keep a stray \r and never
  // match a CV.
  const jdText = normalizeNewlines(jdTextRaw);
  const cfg = MODES[mode];
  const [minYears, maxYears] = extractExperience(jdText);
  const [educationLevels, educationFields] = extractEducation(jdText);
  const [focusCores, focusTerms] = focusVocabulary(jdText, focusArea, cfg.focusWeight);
  const title = extractTitle(jdText);

  return {
    rawText: jdText,
    title,
    minYears,
    maxYears,
    location: extractLocation(jdText),
    educationLevels,
    educationFields,
    industryKeywords: extractIndustry(jdText),
    hardSkills: extractHardSkills(jdText),
    responsibilities: extractResponsibilities(jdText),
    languages: extractLanguages(jdText),
    focusCores,
    focusTerms,
    roleTiers: buildRoleTiers(title, jdText),
    softSkills: [
      "communication", "teamwork", "leadership", "management",
      "problem solving", "analytical", "organizational",
      "interpersonal", "negotiation", "coordination",
    ],
  };
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 2 — Current-role detection
// ══════════════════════════════════════════════════════════════════════════

const SECTION_RE = new RegExp(
  [
    "(?:work|professional|employment|job|career)\\s+experience",
    "(?:experience)\\s+(?:profile|summary|details)",
    "employment\\s+(?:history|record|profile)",
    "work\\s+history|career\\s+(?:history|summary|profile)",
  ].join("|"),
  "gi",
);

const CURRENT_RE =
  /\b(?:present|till\s+date|to\s+date|continuing|ongoing|currently\s+working|upto\s+date)\b/i;

const TITLE_NOISE =
  /\b(experience|ability|strong|-skilled|skills?|years?|worked|responsible|summary|qualification|pressure|stakeholders?)\b/;

function lineBounds(text: string, idx: number): [number, number] {
  const start = text.lastIndexOf("\n", idx - 1) + 1;
  let end = text.indexOf("\n", idx);
  if (end < 0) end = text.length;
  return [start, end];
}

function titleLine(cvText: string, idx: number, fallback: string): string {
  const [start, end] = lineBounds(cvText, idx);
  const line = collapse(cvText.slice(start, end)).replace(/^[\s:|,;\-–—•.]+/, "");
  // (strip is trailing-only above; the Python original also trims the tail)
  if (line.length < 2 || line.length > 70) return fallback;
  return line;
}

function isTitleLine(cvText: string, idx: number): boolean {
  const [start, end] = lineBounds(cvText, idx);
  const line = collapse(cvText.slice(start, end));
  if (line.length < 2 || line.length > 70) return false;
  if (line.split(" ").length > 10) return false;
  if (TITLE_NOISE.test(line.toLowerCase())) return false;
  if (isLowerChar(line[0]) || line.includes(",")) return false;
  if (/^[\u2022\u00b7\u25cf*\-–—>]/.test(line)) return false;
  if (/^\([a-z0-9]{1,2}\)\s/.test(line) || line.endsWith("-")) return false;
  if (/\([a-z0-9]{1,2}\)/.test(line.slice(3))) return false;
  if (/\.\s+[A-Z]/.test(line.slice(3))) return false;
  const after = cvText.slice(idx, idx + 60);
  if (/^\s*\(\d+\.\d+\s+yr/.test(after)) return false;
  return true;
}

function looksLikeJobEntry(cvText: string, idx: number): boolean {
  const [start, end] = lineBounds(cvText, idx);
  const line = cvText.slice(start, end);
  if (/\(\d+(?:\.\d+)?\s*yrs?\b/i.test(line)) return true;
  const after = cvText.slice(end, end + 120);
  if (/\(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\s*[-–]/.test(after)) return true;
  return false;
}

export interface CurrentRole {
  title: string;
  label: string;
  state: "" | "current" | "listed" | "earlier";
  frac: number;
}

/** Isolate the candidate's current / most recent title and its tier. */
export function findCurrentRole(cvText: string, tiers: RoleTier[]): CurrentRole {
  const lower = cvText.toLowerCase();

  let secIndex = -1;
  for (const m of allRegex(lower, SECTION_RE)) {
    const [ls, le] = lineBounds(lower, m.index);
    if (collapse(lower.slice(ls, le)).length <= 60) {
      secIndex = m.index;
      break;
    }
  }
  const start = secIndex >= 0 ? secIndex : 0;

  const cur = CURRENT_RE.exec(lower.slice(start));
  let window: string;
  let origin: number;
  let state: "" | "current" | "listed";
  const curIdx = cur ? start + cur.index : -1;
  if (cur && curIdx - start > 0 && curIdx - start < 4000) {
    window = lower.slice(start, curIdx);
    origin = start;
    state = "current";
  } else if (secIndex >= 0 && lower.slice(start, start + 500).trim().length > 40) {
    window = lower.slice(start, start + 500);
    origin = start;
    state = "listed";
  } else {
    window = lower.slice(0, 500);
    origin = 0;
    state = "listed";
  }

  const advertisementNear = (idx: number) =>
    lower.slice(Math.max(0, idx - 200), idx + 200).includes("advertisement");

  for (const tier of tiers) {
    for (const [phrase, rx] of tier.pairs) {
      for (const m of allRegex(window, rx)) {
        const idx = origin + m.index;
        if (advertisementNear(idx)) continue;
        if (isTitleLine(cvText, idx)) {
          return { title: titleLine(cvText, idx, phrase), label: tier.label, state, frac: tier.frac };
        }
      }
    }
  }

  const scanFrom = secIndex >= 0 ? start : 0;
  const region = lower.slice(scanFrom);
  for (const tier of tiers) {
    for (const [phrase, rx] of tier.pairs) {
      for (const m of allRegex(region, rx)) {
        const idx = scanFrom + m.index;
        if (advertisementNear(idx)) continue;
        if (isTitleLine(cvText, idx) && looksLikeJobEntry(cvText, idx)) {
          return {
            title: titleLine(cvText, idx, phrase),
            label: tier.label,
            state: "earlier",
            frac: tier.frac,
          };
        }
      }
    }
  }

  return { title: "", label: "", state: "", frac: 0 };
}

export function roleFit(cvText: string, tiers: RoleTier[], cfg: ModeConfig) {
  if (!cfg.roleWeight || tiers.length === 0) {
    return { pts: 0, strength: "", gap: "", breakdownKey: "role" };
  }
  const found = findCurrentRole(cvText, tiers);
  if (!found.label) {
    return {
      pts: 0,
      strength: "",
      gap:
        "No current/most-recent job title could be mapped onto the Site Management " +
        "field line (no site-management, facilitator/field-support or field-leading " +
        "title found in the CV).",
      breakdownKey: "role",
    };
  }

  let pts = cfg.roleWeight * found.frac;
  if (found.state === "earlier") pts *= cfg.historicalFactor;
  pts = round1(Math.min(pts, cfg.roleWeight));

  const how = {
    current: "current title (marked 'present/till date')",
    listed: "first-listed role in the employment section",
    earlier: "only an earlier role -- current title is unrelated",
  }[found.state as "current" | "listed" | "earlier"];

  let note: string;
  if (found.state === "earlier") {
    note = `Role fit: '${found.title}' (${found.label}) appears only as an earlier role, ` +
      `so it earns a reduced ${g(pts)}/${g(cfg.roleWeight)} points.`;
  } else if (found.frac >= 0.9) {
    note = `Role fit: current title '${found.title}' is a ${found.label} post -- exactly ` +
      `what the JD is hiring (${g(pts)}/${g(cfg.roleWeight)} points; ${how}).`;
  } else if (found.frac >= 0.6) {
    note = `Role fit: current title '${found.title}' ${found.label} -- e.g. a Facilitator ` +
      `reports into the SMO, so it is on the JD's own reporting line ` +
      `(${g(pts)}/${g(cfg.roleWeight)} points; ${how}).`;
  } else {
    note = `Role fit: current title '${found.title}' is a ${found.label} position, ` +
      `partially relevant (${g(pts)}/${g(cfg.roleWeight)} points; ${how}).`;
  }
  return { pts, strength: note, gap: "", breakdownKey: "role" };
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 3a — Site-management-line tenure
// ══════════════════════════════════════════════════════════════════════════

const MONTHS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];

function parseDate(value: string): Date | null {
  const parts = collapse(value).split(" ");
  if (parts.length !== 3) return null;
  const day = Number(parts[0]);
  const monthName = parts[1].toLowerCase();
  const year = Number(parts[2]);
  if (!Number.isFinite(day) || !Number.isFinite(year)) return null;
  const month = MONTHS.findIndex((m) => m.startsWith(monthName.slice(0, 3)));
  if (month < 0) return null;
  const d = new Date(Date.UTC(year, month, day));
  return Number.isNaN(d.getTime()) ? null : d;
}

const DATE_RANGE_RE = new RegExp(
  "\\(\\s*(\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4})\\s*[-\\u2013\\u2014]\\s*" +
  "(\\d{1,2}\\s+[A-Za-z]{3,9}\\s+\\d{4}|present|till\\s+date|to\\s+date|now)\\s*\\)",
  "i",
);

const TENURE_END_RE = /\((\d+(?:\.\d+)?)\s*yrs?\s*\)$/i;
const ENUMERATED_RE = /^\d+\s*[.)]\s*/;

/**
 * Weighted YEARS of same/similar site-management-line experience.
 *
 * Scans enumerated employment entries ("8. Community Mobilizer (0.6yrs)") and
 * title lines followed by a date range, keeps only titles on the SM reporting
 * line and weights each tenure by its tier. Skill tags ("Community
 * Mobilization (1.0 yr)") are excluded.
 */
export function smLineYears(
  cvText: string,
  tiers: RoleTier[],
  now: Date = new Date(),
): { years: number; roles: string[] } {
  if (!tiers.length) return { years: 0, roles: [] };

  const lines = cvText.split(/\r\n|\r|\n/);

  const spanYears = (startS: string, endS: string): number | null => {
    const start = parseDate(startS);
    if (!start) return null;
    let end = parseDate(endS);
    if (!end) {
      if (/^(present|till|to\s+date|now)/i.test(endS)) end = now;
      else return null;
    }
    const years = (end.getTime() - start.getTime()) / 86_400_000 / 365.25;
    return years >= 0 && years <= 40 ? years : null;
  };

  const tierHit = (titleLower: string): { label: string; frac: number } | null => {
    for (const tier of tiers) {
      for (const [, rx] of tier.pairs) {
        if (rx.test(titleLower)) return { label: tier.label, frac: tier.frac };
      }
    }
    return null;
  };

  let total = 0;
  const roles: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const s = collapse(raw);
    if (!s || s.length > 90) continue;
    const nxt = i + 1 < lines.length ? collapse(lines[i + 1]) : "";
    const nxtIsDate = DATE_RANGE_RE.test(nxt);

    let title = "";
    let years: number | null = null;

    const m = TENURE_END_RE.exec(s);
    if (m) {
      title = s.slice(0, m.index).replace(/^[ |,\-–—:·•]+|[ |,\-–—:·•]+$/g, "");
      years = Number(m[1]);
      const titleLike =
        ENUMERATED_RE.test(s) || (nxtIsDate && s.split(" ").length <= 10 && !s.includes(","));
      if (!titleLike) continue;
    } else if (nxtIsDate && (ENUMERATED_RE.test(s) || (s.split(" ").length >= 2 && s.split(" ").length <= 10 && !s.includes(",")))) {
      const md = DATE_RANGE_RE.exec(nxt);
      title = s.replace(/^[ |,\-–—:·•]+|[ |,\-–—:·•]+$/g, "");
      years = md ? spanYears(md[1], md[2]) : null;
    } else {
      continue;
    }

    if (years === null || !title) continue;

    // The bdjobs application header is an advertisement, not a job.
    const pos = raw ? cvText.indexOf(raw) : -1;
    if (pos >= 0 && cvText.slice(Math.max(0, pos - 200), pos + 200).toLowerCase().includes("advertisement")) {
      continue;
    }

    const hit = tierHit(title.toLowerCase());
    if (!hit) continue;
    total += years * hit.frac;
    roles.push(`${title} (${g(years)}yrs, ${hit.label})`);
  }

  return { years: round2(total), roles };
}

// ══════════════════════════════════════════════════════════════════════════
// SECTION 3b — Candidate scoring
// ══════════════════════════════════════════════════════════════════════════

export function findExperienceYears(text: string): number | null {
  const patterns = [
    /(\d{1,2}(?:\.\d)?)\s*(?:\+?\s*)?(?:years?|yrs?)\s*(?:of\s+)?(?:experience|exp(?:erience)?)/gi,
    /(?:experience|exp)\s*[:\-]?\s*(?:at\s+least\s+)?(\d{1,2}(?:\.\d)?)\s*(?:years?|yrs?)/gi,
    /total\s+experience\s*[:\-]?\s*(\d{1,2}(?:\.\d)?)/gi,
    /(\d{1,2}(?:\.\d)?)\s*(?:years?|yrs?)\s*(?:of\s+)?(?:relevant|work|professional|field)/gi,
  ];
  const matches: number[] = [];
  for (const pat of patterns) {
    for (const m of allRegex(text, pat)) {
      const val = Number(m[1]);
      if (Number.isFinite(val) && val > 0 && val <= 50) matches.push(val);
    }
  }
  return matches.length ? Math.max(...matches) : null;
}

const CV_EDU_LEVELS: Array<[string, string[]]> = [
  ["phd", ["phd", "ph.d", "doctorate"]],
  ["masters", ["masters", "m.sc", "mba", "m.eng", "master"]],
  ["bachelor", ["bachelor", "b.sc", "b.eng", "b.tech", "bss", "llb", "ba ", "bs ", "b.a.", "b.s.", "graduate"]],
  ["diploma", ["diploma"]],
  ["ssc/hsc", ["ssc", "hsc", "h.s.c", "s.s.c", "secondary school"]],
  ["vocational", ["vocational", "technical training"]],
];

export function findEducation(text: string): [string[], string[]] {
  const lower = text.toLowerCase();
  const levels = CV_EDU_LEVELS.filter(([, kws]) => kws.some((kw) => lower.includes(kw)))
    .map(([label]) => label);
  const fields = [
    "social science", "law", "legal", "communication", "humanities",
    "mechanical", "electrical", "civil", "computer science",
    "business", "management", "public administration",
    "international relations", "development studies",
    "environmental", "agriculture", "public health",
    "education", "psychology", "sociology", "anthropology",
    "political science", "economics", "finance", "ngo",
  ].filter((f) => lower.includes(f));
  return [levels, fields];
}

export function softSkillMatch(cvText: string, softSkills: string[]): [number, string[]] {
  const lower = cvText.toLowerCase();
  const hits = softSkills.filter((s) => lower.includes(s));
  return [Math.min(hits.length * 1.5, 5.0), hits];
}

export function languageMatch(cvText: string, required: string[]): [number, string[]] {
  const lower = cvText.toLowerCase();
  const hits = required.filter((l) => lower.includes(l.toLowerCase())).map((l) => l.toLowerCase());
  const pts = required.length ? Math.min((hits.length / required.length) * 5.0, 5.0) : 2.5;
  return [pts, hits];
}

export function focusMatch(cvText: string, profile: JdProfile, focusArea: string, focusWeight: number) {
  const cores = focusArea.split(",").map((c) => c.trim().toLowerCase()).filter(Boolean);
  if (!focusWeight || cores.length === 0 || profile.focusCores.length === 0) {
    return { pts: 0, strength: "", gap: "" };
  }

  const lower = cvText.toLowerCase();
  const focusTerms = new Set<string>();
  for (const core of cores) for (const w of core.match(/[a-z']+/g) ?? []) focusTerms.add(w);
  const anchor = [...focusTerms].sort((a, b) => a.length - b.length)[0] ?? "";

  const strong = profile.focusCores.filter((c) => lower.includes(c));
  const tierA: string[] = [];
  const tierB: string[] = [];
  for (const term of profile.focusTerms) {
    if (!lower.includes(term)) continue;
    if (term.split(" ").includes(anchor)) tierA.push(term);
    else tierB.push(term);
  }

  let contextual = false;
  for (const term of [...strong, ...tierA, ...tierB].slice(0, 8)) {
    for (const m of allRegex(lower, new RegExp(escapeRe(term), "g"))) {
      const window = lower.slice(Math.max(0, m.index - 160), m.index + term.length + 160);
      if (/experience|worked|\bwork\b|years|responsib|duties?|role|assigned|managed|handled|involved|served|charge/.test(window)) {
        contextual = true;
        break;
      }
    }
    if (contextual) break;
  }

  let pts = strong.length ? 8.0 : 0.0;
  pts += Math.min(tierA.length * 2.0, 6.0);
  pts += Math.min(tierB.length * 1.0, 4.0);
  pts += contextual ? 4.0 : 0.0;
  pts = round1(Math.min(pts, focusWeight));

  if (pts <= 0) {
    return {
      pts: 0,
      strength: "",
      gap: `No ${focusArea} experience detected in the CV -- this is the primary focus of the active JD.`,
    };
  }

  const sample = [...strong, ...tierA, ...tierB].slice(0, 5);
  const note = `Focus '${focusArea}': matched ${strong.length} core + ${tierA.length} ` +
    `domain-related + ${tierB.length} general term(s) (${sample.join(", ")})` +
    (contextual ? " with clear experience context." : " without explicit experience context.");
  return { pts, strength: note, gap: "" };
}

const EMPTY_BREAKDOWN = (): Record<string, number> => ({
  skills: 0, experience: 0, education: 0, industry: 0,
  location: 0, soft: 0, language: 0, focus: 0, role: 0,
});

export interface ScoreOptions {
  mode?: PriorityMode;
  focusArea?: string;
}

export function scoreCandidate(
  text: string,
  profile: JdProfile,
  options: ScoreOptions = {},
): ScoreResult {
  const mode = options.mode ?? "experience";
  const focusArea = options.focusArea ?? DEFAULT_FOCUS_AREA;
  const cfg = MODES[mode];
  const SCALE = scaleFor(cfg);

  const result: ScoreResult = {
    score: 0,
    strengths: [],
    gaps: [],
    breakdown: EMPTY_BREAKDOWN(),
    smYears: 0,
    smRoles: [],
  };

  if (text.trim().length < 80) {
    result.gaps.push(
      "CV text is too short or unreadable (likely a scanned/image PDF). Manual review is required.",
    );
    return result;
  }

  const textLower = text.toLowerCase();

  // ── A. Hard skills (max cfg.skills) ────────────────────────────────────
  let skillHits = 0;
  const totalSkills = profile.hardSkills.size || 1;
  const matchedSkills: string[] = [];
  for (const [name, weight] of profile.hardSkills) {
    if (textLower.includes(name.toLowerCase())) {
      skillHits += weight;
      matchedSkills.push(name);
    }
  }
  let maxPossible = 0;
  for (const w of profile.hardSkills.values()) maxPossible += w;
  if (maxPossible === 0) maxPossible = 1;
  const skillPts = Math.min((skillHits / maxPossible) * cfg.skills, cfg.skills) * SCALE;
  result.breakdown.skills = round1(skillPts);

  if (matchedSkills.length) {
    const top = [...matchedSkills]
      .sort((a, b) => (profile.hardSkills.get(b) ?? 0) - (profile.hardSkills.get(a) ?? 0))
      .slice(0, 6);
    result.strengths.push(
      `Matches ${matchedSkills.length}/${totalSkills} JD skills: ${top.join(", ")}.`,
    );
  } else {
    result.gaps.push("No explicit match found for JD-specific hard skills.");
  }

  // ── B. Experience (max cfg.exp) ────────────────────────────────────────
  const expYears = findExperienceYears(text);
  const minReq = profile.minYears;
  const maxReq = profile.maxYears;
  const rangeStr = maxReq ? `${g(minReq)}-${g(maxReq)}` : `${g(minReq)}`;
  const fullAt = Math.max(maxReq, minReq, 1.0);
  let expPts = 0;

  if (mode === "experience") {
    const { years: smYears, roles: smRoles } = smLineYears(text, profile.roleTiers);
    result.smYears = smYears;
    result.smRoles = smRoles;
    if (smYears > 0) {
      expPts = Math.min(smYears / fullAt, 1.0) * cfg.exp;
      result.strengths.push(
        `Site-management-line experience: ${g(smYears)} weighted yrs in same/similar ` +
        `role(s): ${smRoles.slice(0, 3).join("; ")} (JD requires ${rangeStr} yrs).`,
      );
      if (expYears !== null && expYears > smYears + 1) {
        result.strengths.push(
          `Total career claimed ${g(expYears)} yrs, but only ${g(smYears)} yrs sit on the ` +
          `site-management line (general experience is not prioritised).`,
        );
      }
    } else {
      const cur = findCurrentRole(text, profile.roleTiers);
      if (cur.label && expYears) {
        const counted = Math.min(expYears, minReq || fullAt);
        expPts = (counted * cur.frac) / fullAt * cfg.exp;
        result.strengths.push(
          `SM-line role '${cur.title}' (${cur.label}) has no dated tenure; counting ` +
          `${g(counted)} of ${g(expYears)} claimed yrs, capped at the JD minimum (${rangeStr} yrs).`,
        );
      } else if (cur.label) {
        expPts = cfg.exp * 0.15;
        result.strengths.push(
          `SM-line role '${cur.title}' (${cur.label}) found, but no tenure or ` +
          `total-years data to quantify it.`,
        );
      } else {
        expPts = 0;
        result.gaps.push(
          "No dated site-management-line experience: no same/similar SM role with a " +
          "tenure or date range found in the CV.",
        );
      }
    }
  } else if (expYears !== null) {
    const cap = 3.0;
    if (minReq > 0 && maxReq > 0) {
      if (expYears >= minReq) {
        expPts = Math.min((Math.min(expYears / minReq, cap) * cfg.exp) / cap, cfg.exp);
      } else {
        expPts = Math.max((expYears / minReq) * (cfg.exp * 0.6), cfg.exp * 0.12);
      }
    } else if (minReq > 0) {
      expPts = Math.min((Math.min(expYears / minReq, cap) * cfg.exp) / cap, cfg.exp);
    } else {
      expPts = Math.min(expYears * (cfg.exp / 12.5), cfg.exp);
    }
    result.strengths.push(
      `Candidate claims ${g(expYears)} years experience (JD requires ${rangeStr} years).`,
    );
  } else {
    expPts = 3.0;
    result.gaps.push("Could not extract specific years of experience.");
  }

  expPts = Math.min(expPts, cfg.exp) * SCALE;
  result.breakdown.experience = round1(expPts);

  // ── C. Education level (max cfg.eduLevel / + eduField) ─────────────────
  const [cvEduLevels, cvEduFields] = findEducation(text);
  const eduRank: Record<string, number> = {
    "ssc/hsc": 1, vocational: 1, diploma: 2, bachelor: 3, masters: 4, phd: 5,
  };
  let eduPts = 0;

  if (cvEduLevels.length) {
    if (mode === "qualification" && profile.educationLevels.length) {
      const best = Math.max(...cvEduLevels.map((e) => eduRank[e] ?? 0));
      const reqs = profile.educationLevels.map((e) => eduRank[e] ?? 3);
      const req = reqs.length ? Math.min(...reqs) : 3;
      if (best >= req) eduPts = cfg.eduLevel;
      else if (best === req - 1) eduPts = cfg.eduLevel * 0.5;
      else eduPts = cfg.eduLevel * 0.2;
      result.strengths.push(
        `Education level(s): ${cvEduLevels.join(", ")} ` +
        `(${best >= req ? "meets" : "below"} JD requirement ${profile.educationLevels.join(", ")}).`,
      );
    } else {
      const eduOrder: Record<string, number> = {
        phd: 15, masters: 14, bachelor: 12, diploma: 10, "ssc/hsc": 7, vocational: 8,
      };
      eduPts = cfg.eduLevel * (Math.max(...cvEduLevels.map((e) => eduOrder[e] ?? 5)) / 15.0);
      result.strengths.push(`Education level(s): ${cvEduLevels.join(", ")}.`);
    }
  } else {
    eduPts = cfg.eduLevel * (2.0 / 15.0);
    result.gaps.push("No clear education level detected in the CV.");
  }

  // ── C2. Education field match ──────────────────────────────────────────
  let fieldBonus = 0;
  if (profile.educationFields.length && cvEduFields.length) {
    const overlap = cvEduFields.filter((f) => profile.educationFields.includes(f));
    if (overlap.length) {
      fieldBonus = Math.min(overlap.length * (cfg.eduField / 2.5), cfg.eduField);
      result.strengths.push(`Education field aligns with JD: ${overlap.join(", ")}.`);
    } else {
      result.gaps.push(
        `CV fields (${cvEduFields.slice(0, 3).join(", ")}) may not align with JD ` +
        `preference (${profile.educationFields.slice(0, 3).join(", ")}).`,
      );
    }
  }
  eduPts = Math.min(eduPts + fieldBonus, cfg.eduLevel + cfg.eduField) * SCALE;
  result.breakdown.education = round1(eduPts);

  // ── D. Industry / domain ───────────────────────────────────────────────
  let indPts = 0;
  if (profile.industryKeywords.length) {
    const hits = profile.industryKeywords.filter((k) => textLower.includes(k));
    if (hits.length) {
      indPts = Math.min(hits.length * (cfg.industry / 4), cfg.industry) * SCALE;
      result.strengths.push(`Industry background: ${hits.join(", ")}.`);
    } else {
      result.gaps.push(
        `No explicit match for JD industry keywords (${profile.industryKeywords.slice(0, 4).join(", ")}).`,
      );
    }
  }
  result.breakdown.industry = round1(indPts);

  // ── E. Location ────────────────────────────────────────────────────────
  let locPts = 0;
  if (profile.location && textLower.includes(profile.location.toLowerCase())) {
    locPts = cfg.location * SCALE;
    result.strengths.push(`Candidate mentions ${profile.location} (JD location).`);
  }
  result.breakdown.location = round1(locPts);

  // ── F. Soft skills ─────────────────────────────────────────────────────
  const [softRaw, softHits] = softSkillMatch(text, profile.softSkills);
  const softPts = (softRaw / 5.0) * cfg.soft * SCALE;
  if (softHits.length) {
    result.strengths.push(`Soft skills present: ${softHits.slice(0, 5).join(", ")}.`);
  }
  result.breakdown.soft = round1(softPts);

  // ── G. Languages ───────────────────────────────────────────────────────
  const [langRaw, langHits] = languageMatch(text, profile.languages);
  const langPts = (langRaw / 5.0) * cfg.lang * SCALE;
  if (profile.languages.length) {
    if (langHits.length) {
      result.strengths.push(
        `Languages: ${langHits.join(", ")} (JD requires: ${profile.languages.join(", ")}).`,
      );
    } else {
      result.gaps.push(
        `JD requires ${profile.languages.join(", ")} -- not explicitly mentioned in CV.`,
      );
    }
  }
  result.breakdown.language = round1(langPts);

  // ── H. Focus area ──────────────────────────────────────────────────────
  const focus = focusMatch(text, profile, focusArea, cfg.focusWeight);
  if (focus.strength) result.strengths.push(focus.strength);
  if (focus.gap) result.gaps.push(focus.gap);
  result.breakdown.focus = round1(focus.pts);

  // ── I. Current-role fit ────────────────────────────────────────────────
  const role = roleFit(text, profile.roleTiers, cfg);
  if (role.strength) result.strengths.push(role.strength);
  if (role.gap) result.gaps.push(role.gap);
  result.breakdown.role = round1(role.pts);

  const raw = skillPts + expPts + eduPts + indPts + locPts + softPts + langPts +
    focus.pts + role.pts;
  result.score = round1(Math.min(Math.max(raw, 0), 100));

  if (!result.gaps.length) {
    result.gaps.push("No major gaps detected relative to the JD requirements.");
  }
  return result;
}

export function buildReasoning(result: ScoreResult): string {
  const parts: string[] = [];
  if (result.strengths.length) parts.push(`Strengths: ${result.strengths.join(" ")}`);
  if (result.gaps.length) parts.push(`Gaps: ${result.gaps.join(" ")}`);

  // Python renders a float score as "37.0", so keep one decimal always.
  const s = result.score.toFixed(1);
  if (result.score >= 70) {
    parts.push(`Overall, this candidate is a strong match (score ${s}/100) and warrants an interview.`);
  } else if (result.score >= 45) {
    parts.push(`Overall, this candidate is a moderate match (score ${s}/100) -- review manually before shortlisting.`);
  } else {
    parts.push(`Overall, this candidate is a weak match (score ${s}/100) and likely does not meet the core requirements.`);
  }
  return parts.join(" ");
}
