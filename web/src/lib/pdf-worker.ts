/**
 * pdf-worker.ts — the pdf.js worker, with the compatibility shim in place.
 *
 * The worker is its own realm, so the typed-array shim has to run *inside* it
 * before any pdf.js code executes. Importing the shim first and the pdf.js
 * worker module second guarantees that order (ES module evaluation order).
 *
 * Vite bundles this file as a real Worker; `?worker&url` in `ingest.ts` gives
 * us its URL for `GlobalWorkerOptions.workerSrc`.
 */

import "./typed-array-compat.ts";
import "pdfjs-dist/build/pdf.worker.min.mjs";
