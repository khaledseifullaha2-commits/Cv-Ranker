/**
 * Static asset Worker for the CV ↔ JD Matcher.
 *
 * It serves the Vite build from the ASSETS binding and attaches the security
 * headers a public site should send. The policy is deliberately strict:
 *
 *   * `script-src 'self'` with NO 'unsafe-eval' — possible because the app
 *     never uses eval/new Function and pdf.js runs with isEvalSupported:false;
 *   * `style-src 'self'` with NO 'unsafe-inline' — the app uses a stylesheet
 *     and never sets inline style attributes;
 *   * `connect-src 'self'` and `form-action 'none'` — the page has no reason
 *     to send candidate data anywhere;
 *   * `frame-ancestors 'none'` + `X-Frame-Options: DENY` — no clickjacking.
 *
 * Read-only: only GET/HEAD reach the assets, so the static build cannot be
 * used as an upload endpoint.
 */

const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "worker-src 'self' blob:",
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
  "upgrade-insecure-requests",
].join("; ");

const SECURITY_HEADERS = {
  "Content-Security-Policy": CSP,
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Permissions-Policy":
    "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Resource-Policy": "same-origin",
  "X-Frame-Options": "DENY",
  "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
  "X-Robots-Tag": "index, follow",
};

const IMMUTABLE_ASSET = /^\/assets\/.+\.[0-9a-f]{8,}\./i;

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD", "Content-Type": "text/plain; charset=utf-8" },
      });
    }

    const response = await env.ASSETS.fetch(request);
    const headers = new Headers(response.headers);

    for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
      headers.set(name, value);
    }

    // Hashed build assets are safe to cache forever; HTML must not be.
    const { pathname } = new URL(request.url);
    if (IMMUTABLE_ASSET.test(pathname)) {
      headers.set("Cache-Control", "public, max-age=31536000, immutable");
    } else if (pathname === "/" || pathname.endsWith(".html")) {
      headers.set("Cache-Control", "no-cache");
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  },
};
