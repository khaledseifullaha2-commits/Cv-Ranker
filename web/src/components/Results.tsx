import { useMemo, useState } from "react";
import { MODES, scaleFor, type JdProfile, type PriorityMode } from "../lib/engine.ts";
import { copyText, download, toCsv } from "../lib/export.ts";

export interface RankedRow {
  name: string;
  score: number;
  smYears: number;
  smRoles: string[];
  blocked: string | null;
  strengths: string[];
  gaps: string[];
  reasoning: string;
  breakdown: Record<string, number>;
  chars: number;
  error?: string;
}

interface Props {
  rows: RankedRow[];
  profile: JdProfile;
  shortlistSize: number;
  minScore: number;
  mode: PriorityMode;
}

function scoreClass(score: number): string {
  if (score >= 70) return "high";
  if (score >= 45) return "mid";
  return "low";
}

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export default function Results({ rows, profile, shortlistSize, minScore, mode }: Props) {
  const [filter, setFilter] = useState("");
  const [hideBlocked, setHideBlocked] = useState(false);
  const [onlyShortlist, setOnlyShortlist] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);
  const [copied, setCopied] = useState(false);

  const scored = rows.filter((r) => !r.blocked);
  const blocked = rows.filter((r) => r.blocked);
  // Shortlist = the best candidates that also clear the score floor, so a weak
  // field does not get "shortlisted" just for being the only CVs submitted.
  const shortlist = scored.filter((r) => r.score >= minScore).slice(0, shortlistSize);
  const shortlistNames = new Set(shortlist.map((r) => r.name));

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return rows.filter((row) => {
      if (hideBlocked && row.blocked) return false;
      if (onlyShortlist && !shortlistNames.has(row.name)) return false;
      if (needle && !row.name.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [rows, filter, hideBlocked, onlyShortlist, shortlistNames]);

  const current = rows.find((r) => r.name === selected) ?? null;

  const cfg = MODES[mode];
  const scale = scaleFor(cfg);
  const buckets: Array<{ key: string; label: string; max: number }> = [
    { key: "role", label: "Current role fit", max: cfg.roleWeight },
    { key: "focus", label: "Focus area", max: cfg.focusWeight },
    { key: "experience", label: "SM-line experience", max: cfg.exp * scale },
    { key: "skills", label: "JD skills", max: cfg.skills * scale },
    { key: "education", label: "Education", max: (cfg.eduLevel + cfg.eduField) * scale },
    { key: "industry", label: "Industry", max: cfg.industry * scale },
    { key: "location", label: "Location", max: cfg.location * scale },
    { key: "soft", label: "Soft skills", max: cfg.soft * scale },
    { key: "language", label: "Languages", max: cfg.lang * scale },
  ];

  const exportRows = visible.map((r) => ({
    name: r.name,
    score: r.score,
    smYears: r.smYears,
    blocked: r.blocked,
    reasoning: r.reasoning,
  }));

  const smYears = scored.filter((r) => r.smYears > 0).map((r) => r.smYears);

  const copyNames = async () => {
    const ok = await copyText(shortlist.map((r) => r.name).join("\n"));
    setCopied(ok);
    if (ok) setTimeout(() => setCopied(false), 2500);
  };

  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="label">CVs screened</div>
          <div className="value">{rows.length}</div>
        </div>
        <div className="card">
          <div className="label">Shortlist</div>
          <div className="value">{shortlist.length}</div>
        </div>
        <div className="card">
          <div className="label">Blacklisted</div>
          <div className="value">{blocked.length}</div>
        </div>
        <div className="card">
          <div className="label">Median SM-line yrs</div>
          <div className="value">{median(smYears).toFixed(1)}</div>
        </div>
        <div className="card">
          <div className="label">Strong (≥ 70)</div>
          <div className="value">{scored.filter((r) => r.score >= 70).length}</div>
        </div>
        <div className="card">
          <div className="label">Score floor</div>
          <div className="value">{minScore}</div>
        </div>
      </div>

      <div className="panel-body tight">
        <div className="chips">
          <span className="chip"><b>JD</b> {profile.title}</span>
          <span className="chip">
            <b>Experience</b> {profile.maxYears ? `${profile.minYears}-${profile.maxYears}` : profile.minYears} yrs
          </span>
          {profile.location ? <span className="chip"><b>Location</b> {profile.location}</span> : null}
          <span className="chip"><b>Skills mined</b> {profile.hardSkills.size}</span>
          <span className="chip"><b>Focus terms</b> {profile.focusTerms.length}</span>
          <span className="chip">
            <b>Role tiers</b> {profile.roleTiers.map((t) => `${t.label} (${t.pairs.length})`).join(", ")}
          </span>
          {profile.educationLevels.length ? (
            <span className="chip"><b>Education</b> {profile.educationLevels.join(", ")}</span>
          ) : null}
          {profile.languages.length ? (
            <span className="chip"><b>Languages</b> {profile.languages.join(", ")}</span>
          ) : null}
        </div>

        <div className="toolbar">
          <input
            className="grow"
            type="text"
            placeholder="Filter by file name…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            aria-label="Filter candidates by file name"
          />
          <label className="checkline">
            <input
              type="checkbox"
              checked={onlyShortlist}
              onChange={(event) => setOnlyShortlist(event.target.checked)}
            />
            Shortlist only
          </label>
          <label className="checkline">
            <input
              type="checkbox"
              checked={hideBlocked}
              onChange={(event) => setHideBlocked(event.target.checked)}
            />
            Hide blacklisted
          </label>
          <button
            type="button"
            className="btn small"
            onClick={() => download("cv_screening_results.csv", toCsv(exportRows), "text/csv")}
          >
            ⬇ CSV
          </button>
          <button
            type="button"
            className="btn small"
            onClick={() =>
              download(
                "cv_screening_results.json",
                JSON.stringify({ job: profile.title, rows: exportRows }, null, 2),
                "application/json",
              )
            }
          >
            ⬇ JSON
          </button>
          <button type="button" className="btn small" onClick={copyNames}>
            {copied ? "✓ Copied" : "⧉ Copy shortlist names"}
          </button>
        </div>
      </div>

      <div className="divider" />

      <div className="tablewrap">
        <table className="results">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Candidate file</th>
              <th scope="col">SM-line yrs</th>
              <th scope="col">Score</th>
              <th scope="col">Match</th>
              <th scope="col">Flags</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr
                key={row.name}
                className={`${row.blocked ? "blocked " : ""}${row.name === selected ? "selected" : ""}`}
                onClick={() => {
                  setSelected(row.name === selected ? null : row.name);
                  setShowRaw(false);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setSelected(row.name === selected ? null : row.name);
                  }
                }}
                tabIndex={0}
              >
                <td className="rank">{rows.indexOf(row) + 1}</td>
                <td className="cellname">{row.name}</td>
                <td className="cellnum">{row.smYears ? row.smYears.toFixed(1) : "—"}</td>
                <td className={`score ${scoreClass(row.score)}`}>{row.score.toFixed(1)}</td>
                <td>
                  <progress className="scorebar" value={row.blocked ? 0 : row.score} max={100} />
                </td>
                <td>
                  {row.blocked ? (
                    <span className="badge block">BLACKLISTED</span>
                  ) : shortlistNames.has(row.name) ? (
                    <span className="badge shortlist">SHORTLIST</span>
                  ) : null}
                  {row.error ? <span className="badge warn"> partial</span> : null}
                </td>
              </tr>
            ))}
            {!visible.length ? (
              <tr>
                <td colSpan={6} className="muted">
                  No candidate matches the current filter.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {current ? (
        <div className="detail">
          <div className="toolbar">
            <h3>{current.name}</h3>
            <span className={`badge ${scoreClass(current.score) === "high" ? "shortlist" : ""}`}>
              {current.score.toFixed(1)} / 100
            </span>
            {current.blocked ? <span className="badge block">BLACKLISTED</span> : null}
            <span className="muted mono">{current.chars.toLocaleString()} chars read</span>
            <button
              type="button"
              className="btn small"
              onClick={() => setSelected(null)}
            >
              Close
            </button>
          </div>

          {current.blocked ? (
            <p className="muted">
              Excluded before scoring: matched {current.blocked}. Remove the entry from the
              blacklist to screen this candidate normally.
            </p>
          ) : (
            <div className="breakdown">
              {buckets.map((bucket) => (
                <div className="breakrow" key={bucket.key}>
                  <span className="bl">{bucket.label}</span>
                  <progress
                    className="scorebar"
                    value={current.breakdown[bucket.key] ?? 0}
                    max={Math.max(bucket.max, 1)}
                  />
                  <span className="bv">
                    {(current.breakdown[bucket.key] ?? 0).toFixed(1)}/{bucket.max.toFixed(0)}
                  </span>
                </div>
              ))}
            </div>
          )}

          {current.smRoles.length ? (
            <div className="notes">
              <h4 className="muted">Site-management-line tenure counted</h4>
              <ul>
                {current.smRoles.map((role) => (
                  <li key={role}>{role}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {current.strengths.length ? (
            <div className="notes good">
              <h4>Strengths</h4>
              <ul>
                {current.strengths.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {current.gaps.length ? (
            <div className="notes bad">
              <h4>Gaps</h4>
              <ul>
                {current.gaps.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div>
            <button type="button" className="btn small" onClick={() => setShowRaw(!showRaw)}>
              {showRaw ? "Hide" : "Show"} report line
            </button>
            {showRaw ? <pre className="raw">{current.reasoning}</pre> : null}
          </div>
        </div>
      ) : null}
    </>
  );
}
