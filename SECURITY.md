# Security & data-protection notes

This project handles **candidate CVs** — names, phone numbers, addresses, employment
history, sometimes ethnicity or disability status. That is personal data, and for a
refugee-context hiring pipeline it can be sensitive personal data. This document says
what is protected today, what will bite you as the project grows, and how to verify it.

---

## 1. Data-handling principles

* **The static website (`web/`) never receives a CV.** PDF text extraction, JD parsing,
  blacklist matching, scoring and export all run in the browser tab. There is no upload
  endpoint, no database, no analytics, no cookies, no third-party fonts or CDNs. The only
  network requests the app makes are for its own files.
* **The CLI never leaves your machine** either; it reads and writes local files only.
* **The Streamlit app (`app.py`) is the one component that *does* see a CV — and only in
  memory.** Uploads are validated, parsed and scored inside the request; nothing is
  written to disk, no database is used, and results live in the page session until you
  reload. That is the price of DOCX support and a shareable link, and it is the only
  place where a third party (the host) sits between you and the candidate's data.
* **Nothing is written to `localStorage`/`sessionStorage`.** Reload the tab and the
  session is gone.
* **The public demo uses synthetic data.** `web/public/demo/` contains three invented
  CVs and a public job posting — no real applicant is published.

Consequence: a compromise of the static site cannot leak a candidate, because the site
never holds one. The risk sits in the *repository* and in the *files on your disk*, which
is where section 4 focuses.

---

## 2. Controls implemented (with the evidence)

| Control | Where | Why it matters |
|---|---|---|
| Content-Security-Policy, `script-src 'self'`, **no `unsafe-eval`, no `unsafe-inline`** | `web/worker/index.js` header + `<meta>` fallback in `web/index.html` | Injected script cannot execute even if a CV or JD contains `<script>`; also blocks inline-handler XSS |
| Zero `eval` / `new Function` in the shipped bundles | verified: `grep -c -E '\bnew Function\(' web/dist/assets/*.js` → `0`, `grep -o 'eval(' → 0` | The strict policy is enforceable rather than aspirational |
| No inline `style` attributes or `<style>` tags in app code | `grep -rn "style=" web/src` → none; all styling is `styles.css` | Allows `style-src 'self'` with no `unsafe-inline` |
| `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (camera/mic/geo/payment off), `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, `X-Frame-Options: DENY`, HSTS | `web/worker/index.js` | MIME sniffing, referrer leakage, device APIs, clickjacking, cross-origin pulls |
| Reads-only Worker: any non-`GET`/`HEAD` → `405` | `web/worker/index.js` (verified: `curl -X POST` → 405) | The static deployment cannot be turned into an upload endpoint |
| **`assets.run_worker_first: true`** | `web/wrangler.jsonc` | Without it the asset layer answers matching requests itself and *no header above is ever sent* — a silent loss of the whole policy. If you self-host, keep this flag |
| Input limits: 15 MB/file, 300 files, 25 pages, 400 k characters | `web/src/lib/ingest.ts` | Bounds memory/CPU against a hostile or broken PDF instead of hanging the tab |
| Output escaping by construction | React rendering + no `dangerouslySetInnerHTML` anywhere (`grep` → none) | CV file names and text are data, never markup |
| CSV **formula-injection guard** | `web/src/lib/export.ts` | A CV named `=cmd\|'/c calc'!A0` cannot execute when the export is opened in Excel |
| Source maps disabled in the public build | `web/vite.config.ts` (`sourcemap: false`) | Does not publish your sources with the bundle |
| `.gitignore` for candidate data, blacklist, outputs and fixtures | `.gitignore` | Prevents the classic "real CVs pushed to a public repo" incident |
| No secrets anywhere | reviewed: the app has no API keys, no tokens, no server secrets | Nothing to rotate, nothing to leak |
| Pinned dependency tree | `web/package-lock.json` | Reproducible installs; a compromised newer version cannot slip in unnoticed |
| **Uploads validated by content, never by name or MIME type** (`%PDF-` inside the first 1024 bytes; a DOCX must be a ZIP containing `word/document.xml` that decompresses cleanly) | `app.py: validate_uploads` — verified by `tests/smoke_app.py`: HTML bytes named `x.pdf` and PDF bytes named `x.docx` are both rejected | A renamed executable, HTML or SVG cannot ride in as a “PDF” |
| **File names sanitised** (percent-decoded, then path/drive/traversal/NUL stripped and the character set allow-listed) — **and nothing is ever written to disk** | `app.py: safe_filename` — verified by `tests/smoke_app.py`: `../../etc/passwd.pdf` → `passwd.pdf`, `..%2F..%2Fetc%2Fshadow.pdf` → `shadow.pdf` | Path traversal has no target even if the sanitiser were somehow bypassed |
| Budgets on every request: 10 MB/file, 300 files, 150 MB/batch, 120 k JD chars, 25 PDF pages | `app.py` constants + `maxUploadSize` in `.streamlit/config.toml` | Bounds memory and CPU against a hostile or broken upload |
| **Per-file failure isolation** | `app.py` run loop — every file is read inside its own `try/except` | A corrupted file becomes a status row; the rest of the batch still finishes |
| Encrypted PDFs detected before parsing (`is_encrypted` + empty-password attempt) | `app.py: read_pdf_bytes` — verified by `tests/smoke_app.py` against a real encrypted PDF | A password-protected CV is reported as “ask for an unlocked copy”, never silently scored 0 |
| No `unsafe_allow_html` anywhere | `grep -n unsafe_allow_html app.py` → no matches | File names, JD text and CV text are rendered as data, never as markup |
| Telemetry off, XSRF protection on, file watcher scoped | `.streamlit/config.toml` | No CV data leaves the process; no cross-site request forgery against a session |
| A missing engine module or a redirected stdout cannot abort the app | `app.py` import guard; `evaluator.py` stdout shim made defensive | `evaluator` used to raise `AttributeError` at import time under any host that wraps stdout — taking the whole app down with it |

Verify the headers on any deployment yourself:

```bash
curl -s -D - -o /dev/null https://<your-site>/ | grep -iE "content-security-policy|x-content-type|referrer-policy|permissions-policy|x-frame|strict-transport"
```

If `content-security-policy` is missing, the Worker did not run — see the
`run_worker_first` note above, or the hosting platform's asset short-circuit behaviour.

---

## 3. Deliberate trade-offs

* **`upgrade-insecure-requests`** is set; the app is served over HTTPS only. Local
  `file://` use of `dist/index.html` is still possible but then the `<meta>` CSP (not the
  header) applies.
* **`connect-src 'self'`** means you cannot add an analytics or error-reporting SDK
  without widening the policy. Keep it that way — telemetry is how candidate PII leaks.
* **Blacklist data is stored in plain text on your disk.** It is personal data *even
  though* those people are blacklisted; treat the file like the CVs.
* **The demo blacklist** (one synthetic name) is published on purpose; nothing else is.
* **The hosting platform injects its own analytics script** (`static.cloudflareinsights.com`)
  into the served HTML. The CSP blocks it, which is the intended outcome and is visible
  as one blocked-script message in the console. Do not widen `script-src` to silence it:
  "one small analytics beacon" is exactly how page contents start leaving the browser.
* **Name-only blacklist matches are flagged, not enforced** (`--blacklist-names flag`, the
  default). A name is not proof of identity, and auto-excluding namesakes is a fairness
  problem, not a safety win; phones are conclusive. Switch to `exclude` only if you
  accept dropping namesakes.
* **The Streamlit app runs on a server, not in the browser.** The process that reads a CV
  is the process that serves the page. CVs are held only in RAM for the length of a
  request, but the host can see them. For the most sensitive pipelines, use the CLI.
* **`app.py` ships with no authentication of its own.** It inherits whatever the host
  puts in front of it. That is fine on localhost and on a private network; on the public
  internet it means anyone with the URL can screen CVs against your posting — turn on the
  host's auth (Streamlit Cloud → *Restrictions → Require authentication*).
* **The bonus criteria are recruiter input, not the engine.** They are added on top of
  the 0–100 score and reported in their own column so the engine's own number stays
  auditable. Do not tune the bonus cap until the ranking looks the way you want — that is
  p-hiring a shortlist.

---

## 4. Risks you will face next — and what to do

### 4.1 Publishing real CVs with the code (highest-probability incident)

`git add -A` on this project is one command away from publishing 1,600 CVs.

* Keep `.gitignore` as shipped; verify before every push:
  `git status --short` (nothing under `2_Active_CVs/`, `TOP_CV*/`, `3_Evaluation_Output/`)
  and `git check-ignore -v 2_Active_CVs/*.pdf blacklist.csv`.
* If it happens: `git rm --cached`, rotate nothing (no keys), but rewrite history
  (`git filter-repo`) *and* ask the host to purge caches/forks — deleting the commit is
  not enough. Then notify affected people; in most regimes that clock starts when you
  find out, not when you fix it.
* The web folder is what belongs on GitHub; the data folders are yours. Consider making
  the repo public but the data a separate private folder/repo.

### 4.2 Sharing the link

Preview and production URLs of the site are **public**. That is fine for the client-side
app (no data in it). Never "temporarily" add a JSON dump of screened candidates to
`web/public/` to show someone — that is the leak, and CDN caches keep it around.

### 4.3 Untrusted documents

CVs are attacker-controlled input. Today they are parsed by pdf.js in the browser, which
is why the file limits and the no-eval CSP matter: a malformed PDF degrades to "no text",
it cannot run code in your page. If you ever move extraction server-side, the same PDF
becomes a server-side parser target — sandbox it, cap it, and never pass a CV-supplied
path to a filesystem call (the CLI already sanitises filenames via `sanitize_filename`).

### 4.4 Export files and spreadsheets

Exports carry names and phone numbers. The CSV guard neutralises formulas, but the file
still lands in Downloads and mail attachments. Decide a retention rule ("delete exports
after the shortlist closes") and follow it.

### 4.5 Bias, fairness and auditability

Automated screening is a high-risk activity under GDPR-style regimes. Mitigations built
in: every score is explained rule by rule, nothing is a neural black box, the ranking can
be reproduced exactly, and the app is a triage aid with a "review manually" band. Keep a
human decision in the loop, keep the report CSV for the vacancy so a rejected applicant
can be answered, and do not add demographics to the scoring inputs.

### 4.6 If you later add a server (accounts, saved shortlists, a database)

Everything above changes. Non-negotiables at that point:

* authenticate with a real identity provider (never roll your own session cookies);
* scope every query by the signed-in user id — never by an id the browser sends;
* random unguessable object keys for stored files, no public buckets;
* secrets in the platform's secret store, never in the repo or the bundle;
* TLS everywhere, HSTS, the same CSP, and CSRF protection plus same-origin checks on
  mutations;
* redact candidate data from logs and error trackers;
* write down a retention period and delete on schedule.

### 4.7 Supply chain

`web/` pulls pdf.js, React and Vite. Keep `package-lock.json` committed, review
`npm audit` output before bumping, and re-check the "no eval" grep after any pdf.js
upgrade (that check is one command, and it is the CSP's foundation). Note that
`public/pdfjs/standard_fonts/` is third-party data — keep its LICENSE files intact.

`requirements.txt` pins exact versions for the same reason. Bump them deliberately, one
at a time, and re-run the committed test — it is the check that would catch a behaviour
change in `pypdf`, `python-docx` or `streamlit` itself:

```bash
python tests/smoke_app.py      # 46–47 assertions; exits non-zero on any failure
```

It is self-sufficient: run it on a fresh clone with no CV folder, no JD and no
blacklist and it still exercises every control above using generated fixtures (verified
in both states — 47 and 46 passing checks respectively).

### 4.8 A public link in front of real candidate data

The fastest way to undo every control in section 2 is to deploy `app.py` publicly and
then paste 1,600 real CVs into it. Before you share that link:

* **Enable authentication on the host**, or keep the public instance for synthetic demo
data and run real shortlists through the CLI.
* **The job description is confidential too.** It names the client, the salary, the
  location and often the security context of the site.
* **Turn off “Always rerun” in production.** A background tab that keeps re-running the
  script keeps re-processing CVs you thought you had closed.
* **Purge the deployment and its logs when the engagement ends.** On Streamlit Cloud
  that is *Delete app*; it is manual, and the logs outlive the app if you do nothing.
* **Watch the host's own telemetry.** `gatherUsageStats = false` in the committed config
  is what keeps Streamlit from phoning home; do not delete that file to “clean up”.

---

## 5. Reporting / incident checklist

1. **Contain** — unpublish the site (`sites_set_published(false)` or the hosting panel),
   or make the repo private. Retained deployments and logs are not deleted by this step.
2. **Identify** — what was exposed (CVs? blacklist? both?) and for how long.
3. **Remove** — purge the artifact from the host *and* from git history/caches/forks.
4. **Notify** — affected candidates and, where required, your data-protection contact;
   document what happened.
5. **Fix the cause** — a `.gitignore` rule, a header, a review step in the workflow — and
   add the check that would have caught it.

For the platform side (hosting, TLS, CDN) report through your hosting provider; for
vulnerabilities in this project's code, open a private security advisory on the
repository rather than a public issue.
