/**
 * blacklist.ts — browser port of the evaluator's blacklist gate.
 *
 * Same matching rules as `evaluator.py`, so a candidate excluded by the CLI is
 * excluded on the website too:
 *   * phone numbers are compared on their last 10 digits (+880 / dashes /
 *     spaces are equivalent);
 *   * a name matches only as a FULL alias — a lone surname is never enough;
 *   * a hit on any alias or any phone blocks the candidate.
 *
 * The list is parsed from pasted text, so a spreadsheet column copied straight
 * out of Excel (tabs) or a CSV both work.
 */

export interface BlacklistEntry {
  label: string;
  names: string[];
  phones: string[];
}

const HONORIFICS = new Set([
  "md", "mohammad", "mohammed", "mohd", "muhammad",
  "mr", "mrs", "ms", "mst", "ska", "sk", "sheikh",
]);

const DIGIT_RUN = /[0-9][0-9\s\-().+]{7,}[0-9]/g;

export function normName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ");
}

export function normPhone(value: string): string {
  const digits = value.replace(/\D/g, "");
  return digits.length > 10 ? digits.slice(-10) : digits;
}

function nameVariants(alias: string): string[] {
  const norm = normName(alias);
  if (!norm) return [];
  const tokens = norm.split(" ");
  const out = [norm];
  const trimmed = tokens.filter((t) => !HONORIFICS.has(t));
  if (trimmed.length && trimmed.join(" ") !== norm) out.push(trimmed.join(" "));
  // A lone word is too weak a signal ("khan" matches half a camp).
  return [...new Set(out)].filter((v) => v.split(" ").length >= 2);
}

export function phonesIn(text: string): Set<string> {
  const found = new Set<string>();
  for (const raw of text.match(DIGIT_RUN) ?? []) {
    const digits = raw.replace(/\D/g, "");
    if (digits.length >= 10) found.add(digits.slice(-10));
  }
  return found;
}

/**
 * Parse pasted blacklist text.
 *
 * Accepts one candidate per line: `Name, phone, phone` or a tab-separated
 * spreadsheet row. Tokens that are pure numbers (row indexes from a pasted
 * table) are ignored, as is a header row.
 */
export function parseBlacklist(text: string): BlacklistEntry[] {
  const entries: BlacklistEntry[] = [];

  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;

    const cells = line.split(/\t|;|,(?![^"]*"[^"]*$)/).map((c) => c.trim().replace(/^"|"$/g, ""));
    const names: string[] = [];
    const phones: string[] = [];

    for (const cell of cells) {
      const digits = cell.replace(/\D/g, "");
      if (digits.length >= 10 && cell.replace(/[\d\s\-().+]/g, "").length <= 3) {
        const fp = normPhone(cell);
        if (fp.length >= 10) phones.push(fp);
        continue;
      }
      for (const alias of cell.split("|")) {
        const trimmed = alias.trim();
        if (!trimmed) continue;
        if (/^\d+$/.test(trimmed)) continue; // pasted row index
        names.push(...nameVariants(trimmed));
      }
    }

    if (names.some((n) => n === normName("candidate name"))) continue; // header
    if (!names.length && !phones.length) continue;
    entries.push({
      label: cells[0] || phones[0] || "blacklist entry",
      names: [...new Set(names)],
      phones: [...new Set(phones)],
    });
  }

  return entries;
}

export interface BlacklistHit {
  label: string;
  reason: string;
}

/** Return a hit when the CV text or file name matches any entry. */
export function blacklistCheck(
  text: string,
  fileName: string,
  entries: BlacklistEntry[],
): BlacklistHit | null {
  if (!entries.length) return null;

  const stem = fileName.replace(/\.[^.]+$/, "");
  const haystack = ` ${normName(stem)} ${normName(text)} `;
  const phones = phonesIn(text);

  for (const entry of entries) {
    for (const phone of entry.phones) {
      if (phones.has(phone)) {
        return {
          label: entry.label,
          reason: `blacklisted phone number (…${phone.slice(-4)}, ${entry.label})`,
        };
      }
    }
    for (const variant of entry.names) {
      if (haystack.includes(` ${variant} `)) {
        return { label: entry.label, reason: `blacklisted name '${variant}'` };
      }
    }
  }
  return null;
}
