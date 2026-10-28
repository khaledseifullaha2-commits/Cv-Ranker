/**
 * ingest.ts — turn uploaded files into plain text, entirely in the browser.
 *
 * Security-relevant choices (see SECURITY.md):
 *   * nothing is ever uploaded — the file bytes are read with the File API and
 *     parsed locally, so candidate PII never leaves the device;
 *   * `isEvalSupported: false` keeps pdf.js from using `new Function`, which
 *     lets the site run under a CSP without 'unsafe-eval';
 *   * hard limits on file size, file count, page count and extracted
 *     characters bound the memory an unfriendly PDF can consume.
 */

import "./typed-array-compat.ts";
import * as pdfjsLib from "pdfjs-dist";
// Our own wrapper around the pdf.js worker: it installs the typed-array shim
// inside the worker realm first (see pdf-worker.ts).
import workerUrl from "./pdf-worker.ts?worker&url";
import { normalizeNewlines } from "./engine.ts";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

export const MAX_FILE_BYTES = 15 * 1024 * 1024; // 15 MiB per file
export const MAX_FILES = 300;
export const MAX_PAGES = 25;
export const MAX_CHARS = 400_000;

export interface IngestedDoc {
  name: string;
  size: number;
  text: string;
  /** Set when the file could not be read; the row is still shown. */
  error?: string;
}

export interface IngestReport {
  docs: IngestedDoc[];
  /** Files rejected before parsing (too large / wrong type / too many). */
  skipped: Array<{ name: string; reason: string }>;
}

interface Fragment {
  str: string;
  x: number;
  y: number;
  width: number;
}

/**
 * Rebuild visual text LINES from positioned glyph runs.
 *
 * The scoring engine (like pypdf) is line-based: job entries, tenures and
 * date ranges are recognised per line. pdf.js hands back positioned text
 * items with no line structure, so join items into lines by their y position
 * and top-to-bottom order — otherwise every CV would collapse into one
 * useless line and no tenure could ever be detected.
 */
function itemsToLines(items: Array<unknown>): string[] {
  const frags: Fragment[] = [];
  for (const item of items) {
    const anyItem = item as { str?: string; transform?: number[]; width?: number };
    if (typeof anyItem.str !== "string" || !anyItem.str || !anyItem.transform) continue;
    frags.push({
      str: anyItem.str,
      x: anyItem.transform[4],
      y: anyItem.transform[5],
      width: anyItem.width ?? 0,
    });
  }

  frags.sort((a, b) => (Math.abs(a.y - b.y) > 2.5 ? b.y - a.y : a.x - b.x));

  const lines: string[] = [];
  let current: Fragment[] = [];
  let currentY = Number.NaN;

  const flush = () => {
    if (!current.length) return;
    let text = "";
    let prevEnd = Number.NEGATIVE_INFINITY;
    for (const frag of current) {
      if (text && frag.x - prevEnd > 0.5 && !text.endsWith(" ")) text += " ";
      text += frag.str;
      prevEnd = frag.x + frag.width;
    }
    const line = text.replace(/\s+/g, " ").trim();
    if (line) lines.push(line);
    current = [];
  };

  for (const frag of frags) {
    if (Number.isNaN(currentY) || Math.abs(frag.y - currentY) <= 2.5) {
      current.push(frag);
      currentY = Number.isNaN(currentY) ? frag.y : currentY;
    } else {
      flush();
      current.push(frag);
      currentY = frag.y;
    }
  }
  flush();
  return lines;
}

async function readPdfText(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  // pdf.js ships no eval / new Function anywhere, so this works under a CSP
  // without 'unsafe-eval' (verified against the shipped bundles, see
  // SECURITY.md). Fonts are never needed: we only extract text.
  const task = pdfjsLib.getDocument({
    data: bytes,
    disableFontFace: true,
    disableAutoFetch: true,
    disableStream: true,
    // Standard-font metrics live in public/pdfjs/standard_fonts (images of
    // the pdf.js release, Apache-2.0). Without them pdf.js warns and some
    // text extraction loses character spacing fidelity.
    standardFontDataUrl: new URL("pdfjs/standard_fonts/", document.baseURI).toString(),
  });

  try {
    const doc = await task.promise;
    const parts: string[] = [];
    const pages = Math.min(doc.numPages, MAX_PAGES);
    for (let i = 1; i <= pages; i++) {
      const page = await doc.getPage(i);
      const content = await page.getTextContent();
      const text = itemsToLines(content.items).join("\n");
      if (text.trim()) parts.push(text);
      page.cleanup();
    }
    // pypdf joins page texts with a newline; mirror that so the same CV
    // scores the same here and in the CLI.
    return parts.join("\n");
  } finally {
    // Terminates the pdf.js worker for this document, bounding memory use.
    await task.destroy();
  }
}

const looksTextual = (file: File) =>
  file.type.startsWith("text/") ||
  /\.(txt|csv|md|text)$/i.test(file.name) ||
  file.type === "" ;

/** Read one file to text. Throws on unsupported / oversized input. */
export async function ingestFile(file: File): Promise<IngestedDoc> {
  if (file.size > MAX_FILE_BYTES) {
    throw new Error(
      `File is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is ` +
        `${MAX_FILE_BYTES / 1024 / 1024} MB.`,
    );
  }

  let text: string;
  if (file.type === "application/pdf" || /\.pdf$/i.test(file.name)) {
    text = await readPdfText(file);
  } else if (looksTextual(file)) {
    text = await file.text();
  } else {
    throw new Error("Only PDF and plain-text CVs are supported.");
  }

  text = normalizeNewlines(text);
  const truncated = text.length > MAX_CHARS;
  if (truncated) text = text.slice(0, MAX_CHARS);

  return {
    name: file.name,
    size: file.size,
    text,
    error: truncated ? `Only the first ${MAX_CHARS.toLocaleString()} characters were read.` : undefined,
  };
}

/** Ingest a batch, reporting per-file failures instead of stopping. */
export async function ingestFiles(
  files: File[],
  onProgress?: (done: number, total: number, label: string) => void,
): Promise<IngestReport> {
  const docs: IngestedDoc[] = [];
  const skipped: Array<{ name: string; reason: string }> = [];
  const list = files.slice(0, MAX_FILES);

  if (files.length > MAX_FILES) {
    for (const extra of files.slice(MAX_FILES)) {
      skipped.push({ name: extra.name, reason: `Only ${MAX_FILES} files are read per run.` });
    }
  }

  for (let i = 0; i < list.length; i++) {
    const file = list[i];
    onProgress?.(i, list.length, file.name);
    try {
      docs.push(await ingestFile(file));
    } catch (err) {
      skipped.push({ name: file.name, reason: err instanceof Error ? err.message : String(err) });
    }
    // Yield so the UI can paint progress on large batches.
    if (i % 4 === 3) await new Promise((resolve) => setTimeout(resolve, 0));
  }

  onProgress?.(list.length, list.length, "");
  return { docs, skipped };
}

/** Ingest a document fetched from the site itself (used by the demo). */
export async function ingestUrl(url: string, name: string): Promise<IngestedDoc> {
  const response = await fetch(url, { credentials: "omit", cache: "no-store" });
  if (!response.ok) throw new Error(`Could not load ${name} (HTTP ${response.status}).`);
  const blob = await response.blob();
  return ingestFile(new File([blob], name, { type: blob.type }));
}
