/**
 * demo.ts — the "load sample data" wiring.
 *
 * Everything the demo uses is synthetic and lives in `public/demo/`, so the
 * published site never contains a real candidate's CV or contact details.
 * The demo blacklist row matches the second sample CV on purpose: it shows
 * the exclusion gate working alongside the ranking.
 */

export const DEMO_JD_URL = "demo/jd.txt";

export const DEMO_CVS: Array<{ url: string; name: string }> = [
  { url: "demo/cv-1-site-management.pdf", name: "DEMO 1 - Site Management Field Assistant.pdf" },
  { url: "demo/cv-2-community-facilitator.txt", name: "DEMO 2 - Community Facilitator.txt" },
  { url: "demo/cv-3-finance-officer.txt", name: "DEMO 3 - Finance Officer.txt" },
];

export const DEMO_BLACKLIST = "Md. Kamrul Islam, 01711-000001\n";
