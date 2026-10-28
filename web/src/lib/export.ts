/**
 * export.ts — save the ranking to a file, or copy the shortlist names.
 *
 * The CSV writer neutralises spreadsheet formula injection: a CV file name or
 * reasoning string that starts with = + - @ (or a control character) is
 * prefixed with an apostrophe, so opening the export in Excel cannot execute
 * content that came out of an uploaded file. See SECURITY.md.
 */

export interface ExportRow {
  name: string;
  score: number;
  smYears: number;
  blocked: string | null;
  reasoning: string;
}

const RISKY_PREFIX = /^[=+\-@\t\r]/;

function csvCell(value: unknown): string {
  let text = value === null || value === undefined ? "" : String(value);
  if (RISKY_PREFIX.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

export function toCsv(rows: ExportRow[]): string {
  const header = [
    "Rank", "Match_Score", "Candidate_File_Name",
    "SM_Line_Years", "Status", "Summary_Reasoning",
  ];
  const lines = [header.map(csvCell).join(",")];
  rows.forEach((row, index) => {
    lines.push([
      index + 1,
      row.score.toFixed(1),
      row.name,
      row.smYears ? row.smYears.toFixed(2) : "",
      row.blocked ? "BLACKLISTED" : "screened",
      row.reasoning,
    ].map(csvCell).join(","));
  });
  // BOM so Excel reads the Bangla names and the ` as UTF-8.
  return `\uFEFF${lines.join("\r\n")}\r\n`;
}

export function download(filename: string, contents: string, mime: string): void {
  const blob = new Blob([contents], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Release the object URL so the blob is not pinned in memory.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}
