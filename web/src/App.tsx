import { useMemo, useRef, useState } from "react";
import Results, { type RankedRow } from "./components/Results.tsx";
import {
  DEFAULT_FOCUS_AREA,
  buildReasoning,
  extractJdProfile,
  scoreCandidate,
  type JdProfile,
  type PriorityMode,
} from "./lib/engine.ts";
import { blacklistCheck, parseBlacklist } from "./lib/blacklist.ts";
import { MAX_FILES, ingestFiles, ingestUrl, type IngestedDoc } from "./lib/ingest.ts";
import { DEMO_BLACKLIST, DEMO_CVS, DEMO_JD_URL } from "./lib/demo.ts";

interface Notice {
  kind: "warn" | "info";
  text: string;
}

const MODE_LABELS: Record<PriorityMode, string> = {
  experience: "Experience first — site-management-line tenure (default)",
  balanced: "Balanced — the original weights",
  qualification: "Qualification first — education level & field",
};

const formatSize = (bytes: number) =>
  bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

export default function App() {
  const [jdText, setJdText] = useState("");
  const [docs, setDocs] = useState<IngestedDoc[]>([]);
  const [skipped, setSkipped] = useState<Array<{ name: string; reason: string }>>([]);
  const [blacklistText, setBlacklistText] = useState("");
  const [mode, setMode] = useState<PriorityMode>("experience");
  const [focusArea, setFocusArea] = useState(DEFAULT_FOCUS_AREA);
  const [topN, setTopN] = useState(20);
  const [minScore, setMinScore] = useState(45);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [rows, setRows] = useState<RankedRow[] | null>(null);
  const [usedProfile, setUsedProfile] = useState<JdProfile | null>(null);
  const [dragging, setDragging] = useState(false);

  const fileInput = useRef<HTMLInputElement>(null);

  const blacklistCount = useMemo(
    () => parseBlacklist(blacklistText).length,
    [blacklistText],
  );

  const addFiles = async (files: File[]) => {
    if (!files.length) return;
    setBusy(`Reading ${files.length} file(s)…`);
    setNotice(null);
    try {
      const report = await ingestFiles(files, (done, total, label) => {
        if (done < total) setBusy(`Reading ${done + 1}/${total} — ${label}`);
      });
      setDocs((prev) => [...prev, ...report.docs]);
      setSkipped(report.skipped);
      if (report.skipped.length) {
        setNotice({
          kind: "warn",
          text: `${report.skipped.length} file(s) skipped: ` +
            report.skipped.map((s) => `${s.name} (${s.reason})`).join("; "),
        });
      }
    } finally {
      setBusy(null);
    }
  };

  const loadDemo = async () => {
    setBusy("Loading sample data…");
    setNotice(null);
    setRows(null);
    try {
      const response = await fetch(new URL(DEMO_JD_URL, document.baseURI), {
        credentials: "omit",
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setJdText(await response.text());

      const loaded: IngestedDoc[] = [];
      const failed: Array<{ name: string; reason: string }> = [];
      for (const sample of DEMO_CVS) {
        try {
          loaded.push(await ingestUrl(new URL(sample.url, document.baseURI).toString(), sample.name));
        } catch (err) {
          failed.push({
            name: sample.name,
            reason: err instanceof Error ? err.message : String(err),
          });
        }
      }
      setDocs(loaded);
      setSkipped(failed);
      setBlacklistText(DEMO_BLACKLIST);
      setNotice({
        kind: "info",
        text:
          "Sample data loaded — a real public job posting plus three synthetic CVs. " +
          "The blacklist row blocks sample CV 2 on purpose so you can see the gate work.",
      });
    } catch (err) {
      setNotice({
        kind: "warn",
        text: `Could not load the sample data: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setBusy(null);
    }
  };

  const clearAll = () => {
    setDocs([]);
    setSkipped([]);
    setRows(null);
    setUsedProfile(null);
    setNotice(null);
  };

  const run = async () => {
    if (!jdText.trim()) {
      setNotice({ kind: "warn", text: "Paste the job description first (or load the sample data)." });
      return;
    }
    if (!docs.length) {
      setNotice({ kind: "warn", text: "Add at least one CV — PDF or plain text." });
      return;
    }

    setNotice(null);
    setRows(null);
    setBusy("Reading the job description…");
    // Let the browser paint before the scoring loop starts.
    await new Promise((resolve) => setTimeout(resolve, 30));

    const profile = extractJdProfile(jdText, focusArea.trim() || DEFAULT_FOCUS_AREA, mode);
    const blacklist = parseBlacklist(blacklistText);
    const output: RankedRow[] = [];

    for (let i = 0; i < docs.length; i++) {
      const doc = docs[i];
      const hit = blacklistCheck(doc.text, doc.name, blacklist);

      if (hit) {
        output.push({
          name: doc.name,
          score: 0,
          smYears: 0,
          smRoles: [],
          blocked: hit.reason,
          strengths: [],
          gaps: [],
          breakdown: {},
          chars: doc.text.length,
          error: doc.error,
          reasoning:
            `EXCLUDED -- blacklisted candidate: matched ${hit.reason}. Scoring skipped; ` +
            "this CV is refused by the shortlister. Remove the row from the blacklist to re-include.",
        });
      } else {
        const result = scoreCandidate(doc.text, profile, { mode, focusArea });
        output.push({
          name: doc.name,
          score: result.score,
          smYears: result.smYears,
          smRoles: result.smRoles,
          blocked: null,
          strengths: result.strengths,
          gaps: result.gaps,
          breakdown: result.breakdown,
          chars: doc.text.length,
          error: doc.error,
          reasoning: buildReasoning(result),
        });
      }

      if (i % 20 === 19) {
        setBusy(`Scoring… ${i + 1}/${docs.length}`);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    }

    // Stable sort: equal scores keep their original order, like the CLI report.
    output.sort((a, b) => b.score - a.score);
    setRows(output);
    setUsedProfile(profile);
    setBusy(null);
  };

  const emptyDocs = docs.filter((d) => !d.text.trim()).length;

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="mark">CV</span>
          <div>
            <h1>CV ↔ JD Matcher</h1>
            <p className="subtitle">
              Rule-based CV screening for site-management roles — transparent scores, no black box.
            </p>
          </div>
        </div>
        <span className="pill">🔒 Runs in your browser — no CV is uploaded</span>
      </header>

      <main className="layout">
        <section className="panel">
          <div className="panel-head">
            <h2>1 · Job description</h2>
            <button type="button" className="btn small" onClick={loadDemo} disabled={!!busy}>
              ✨ Load sample data
            </button>
          </div>
          <div className="panel-body">
            <div className="field">
              <label htmlFor="jd">Paste the full posting</label>
              <textarea
                id="jd"
                value={jdText}
                onChange={(event) => setJdText(event.target.value)}
                placeholder="Paste the job advertisement here — requirements, responsibilities, skills, languages…"
                spellCheck={false}
              />
              <p className="hint">
                Every requirement is read from this text: nothing is hardcoded. Education, skills,
                languages, location and the site-management reporting line are all mined from it.
              </p>
            </div>

            <div className="field">
              <span className="fieldlabel">2 · Candidate CVs</span>
              <div
                className={`dropzone${dragging ? " over" : ""}`}
                onClick={() => fileInput.current?.click()}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(event) => {
                  event.preventDefault();
                  setDragging(false);
                  void addFiles(Array.from(event.dataTransfer.files));
                }}
              >
                <strong>Drop CVs here, or click to choose</strong>
                <span>PDF or TXT · up to {MAX_FILES} files · 15 MB each</span>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  accept=".pdf,.txt,.csv,application/pdf,text/plain"
                  onChange={(event) => {
                    void addFiles(Array.from(event.target.files ?? []));
                    event.target.value = "";
                  }}
                />
              </div>

              {docs.length ? (
                <>
                  <div className="toolbar">
                    <span className="hint">
                      {docs.length} CV(s) loaded
                      {emptyDocs ? ` · ${emptyDocs} with no readable text (scanned PDFs?)` : ""}
                    </span>
                    <button type="button" className="btn small" onClick={clearAll}>
                      Clear
                    </button>
                  </div>
                  <div className="filelist">
                    {docs.map((doc) => (
                      <div className={`fileitem${doc.text.trim() ? "" : " err"}`} key={doc.name}>
                        <span className="name">{doc.name}</span>
                        <span className="filemeta">
                          {formatSize(doc.size)} ·{" "}
                          {doc.text.trim() ? `${doc.text.length.toLocaleString()} chars` : "no text"}
                        </span>
                      </div>
                    ))}
                  </div>
                </>
              ) : null}
            </div>

            <details className="disclosure">
              <summary>Screening options</summary>
              <div className="panel-body">
                <div className="options-grid">
                  <div className="field span-2">
                    <label htmlFor="mode">Ranking priority</label>
                    <select
                      id="mode"
                      value={mode}
                      onChange={(event) => setMode(event.target.value as PriorityMode)}
                    >
                      {(Object.keys(MODE_LABELS) as PriorityMode[]).map((key) => (
                        <option key={key} value={key}>
                          {MODE_LABELS[key]}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label htmlFor="focus">Focus area</label>
                    <input
                      id="focus"
                      type="text"
                      value={focusArea}
                      onChange={(event) => setFocusArea(event.target.value)}
                      placeholder="site management"
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="topn">Shortlist size</label>
                    <input
                      id="topn"
                      type="number"
                      min={1}
                      max={MAX_FILES}
                      value={topN}
                      onChange={(event) => setTopN(Math.max(1, Number(event.target.value) || 1))}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="minscore">Minimum score</label>
                    <input
                      id="minscore"
                      type="number"
                      min={0}
                      max={100}
                      value={minScore}
                      onChange={(event) =>
                        setMinScore(Math.min(100, Math.max(0, Number(event.target.value) || 0)))
                      }
                    />
                  </div>
                </div>

                <div className="field">
                  <label htmlFor="blacklist">Blacklist ({blacklistCount} entries)</label>
                  <textarea
                    id="blacklist"
                    className="mono"
                    value={blacklistText}
                    onChange={(event) => setBlacklistText(event.target.value)}
                    placeholder={"Name, phone\nMd. Karim, 01711-000001"}
                    spellCheck={false}
                  />
                  <p className="hint">
                    One candidate per line: name(s) and/or phone numbers. Phones are compared on
                    their last 10 digits; a name matches only in full. Matched CVs are refused
                    before scoring and can never enter the shortlist.
                  </p>
                </div>
              </div>
            </details>

            <button type="button" className="btn primary wide" onClick={run} disabled={!!busy}>
              {busy ? busy : `▶ Screen ${docs.length || ""} candidate${docs.length === 1 ? "" : "s"}`}
            </button>

            <details className="disclosure">
              <summary>How the score is built</summary>
              <ol className="hint">
                <li>
                  In <b>experience mode</b> the 45-point experience bucket counts only dated
                  <b> site-management-line tenure</b> — site-management titles (full weight),
                  roles that report into them such as facilitator / field officer / mobilizer
                  (80%) and field-leading titles (47%). General career years never outrank it.
                </li>
                <li>
                  <b>Current-role fit</b> (30 points) grades the most recent job title against the
                  JD's own reporting line; a title held only earlier is discounted.
                </li>
                <li>
                  <b>Focus area</b> (18 points) is mined from the JD (e.g. "site level",
                  "collective sites") and needs experience context near the mention.
                </li>
                <li>
                  The remaining points cover JD skills, education level and field, industry,
                  location, soft skills and languages — each scaled so the total is 100.
                </li>
              </ol>
            </details>
          </div>
        </section>

        <section className="panel">
          <div className="panel-head">
            <h2>3 · Ranking</h2>
            {rows ? <span className="hint">Sorted by score, highest first</span> : null}
          </div>

          {notice ? <p className={`notice${notice.kind === "info" ? " info" : ""}`}>{notice.text}</p> : null}

          {rows && usedProfile ? (
            <Results
              rows={rows}
              profile={usedProfile}
              shortlistSize={topN}
              minScore={minScore}
              mode={mode}
            />
          ) : (
            <div className="empty">
              <h3>Nothing screened yet</h3>
              <p>
                Paste a job description, add candidate CVs, then press <b>Screen</b>. Everything —
                PDF text extraction, blacklist matching and scoring — happens on this device.
              </p>
              <p className="hint">
                No account, no upload, no server-side storage of candidate data.
              </p>
            </div>
          )}
        </section>
      </main>

      <footer className="footer">
        <p>
          Scores are a triage aid, not a hiring decision. Always review the CVs yourself before
          shortlisting. Exports stay on your device.
        </p>
        {skipped.length ? (
          <p className="muted">
            Skipped on load: {skipped.map((s) => `${s.name} (${s.reason})`).join("; ")}
          </p>
        ) : null}
      </footer>
    </>
  );
}
