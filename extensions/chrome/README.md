# Company Brain — Chrome Extension

Side-panel client for the Company Brain knowledge base. Built with Manifest V3,
Chrome's native `sidePanel` API, React 18, and Vite + CRXJS.

## Architecture (1-minute overview)

```
┌──────────────────────────┐   chrome.sendMessage   ┌─────────────────────────┐
│  Side panel (React)      │ ─────────────────────► │  Background SW          │
│  - Auth (Supabase REST)  │ ◄───────────────────── │  - sidePanel open       │
│  - Chat SSE stream       │   ScrapeResult         │  - executeScript inject │
│  - Add-to-Brain UI       │                        │  - Readability parse    │
└──────────────────────────┘                        └─────────────────────────┘
        │                                                       │
        │  Bearer JWT                                           │  chrome.scripting
        ▼                                                       ▼
┌──────────────────────────┐                        ┌─────────────────────────┐
│  FastAPI                 │                        │  Active tab DOM         │
│  - /chat/stream (SSE)    │                        │  - serialize to HTML    │
│  - /documents/from-url   │                        │                         │
└──────────────────────────┘                        └─────────────────────────┘
```

Key design choices:

- **Native side panel, not an iframe.** No third-party cookie issues, no
  X-Frame-Options dance, no need to special-case an `?embedded=true` route in
  the Next.js app. The side panel is a first-class top-level document.
- **Bearer JWT, not session cookies.** The side panel speaks Supabase Auth
  REST directly (`/auth/v1/token?grant_type=password`) and stores tokens in
  `chrome.storage.local`. Refresh is automatic, with a fallback to the login
  screen on `SessionExpired`.
- **`activeTab`, not `<all_urls>`.** Scraping only runs when the user clicks
  "Add to Brain" — a user gesture that satisfies activeTab. No background
  reads of Gmail/Slack DOMs. Easier Web Store review.
- **Readability runs in the SW**, not the page. The page just serializes its
  DOM to HTML and ships it back; the SW reconstructs and parses. Avoids
  bundling Readability into the content-script entry.

## Setup

Install deps (pnpm or npm both fine):

```bash
cd extensions/chrome
pnpm install
```

Copy `.env.example` to `.env` and fill in:

```bash
VITE_API_URL=http://localhost:8000             # FastAPI dev URL
VITE_SUPABASE_URL=https://YOUR.supabase.co     # same project as Next.js app
VITE_SUPABASE_ANON_KEY=eyJ...                  # anon key (safe to ship)
```

## Develop

```bash
pnpm dev
```

Vite + CRXJS produces a hot-reloading `dist/` directory.

Load it into Chrome:

1. `chrome://extensions`
2. Toggle **Developer mode** (top right)
3. Click **Load unpacked**
4. Select `extensions/chrome/dist`

The toolbar icon now opens the side panel. Sign in with any account that
exists in your Supabase project. On any normal web page, you can:

- Toggle **Use as context** — the current tab's URL + title are prepended to
  the next prompt.
- Click **+ Add to Brain** — Readability extracts clean article text and
  POSTs to `/documents/from-url`. Inngest picks up the `doc/uploaded-text`
  event and runs chunk + embed.

## Build for publishing

```bash
pnpm build
pnpm zip       # produces company-brain-extension.zip
```

Upload the zip to the Chrome Web Store dev console (one-time $5 developer
fee). Note that any environment-derived host (FastAPI / Supabase URL) is
baked in at build time — you'll want a production `.env` before zipping.

## Icons

The `src/icons/` directory expects `icon-16.png`, `icon-48.png`, and
`icon-128.png`. Drop in your brand assets; the manifest references them
verbatim. CRXJS copies them to `dist/icons/` on build.

## Permissions, explained

| Permission   | What we use it for                                                  |
|--------------|---------------------------------------------------------------------|
| `sidePanel`  | The whole UI. Required to call `chrome.sidePanel.setPanelBehavior`. |
| `scripting`  | `chrome.scripting.executeScript` to serialize the page DOM.         |
| `activeTab`  | Gives us tab URL + scripting rights, scoped to the user-clicked tab.|
| `storage`    | `chrome.storage.local` for the auth session.                        |
| `tabs`       | `chrome.tabs.query({active: true})` to read the active tab metadata.|

No `host_permissions` are declared — `activeTab` covers scraping on the
user-clicked tab; FastAPI + Supabase calls go through the side panel's own
origin (`chrome-extension://<id>`) and rely on the API's CORS allow-list.

## Production checklist

- [ ] `.env` has prod `VITE_API_URL` (e.g. `https://api.companybrain.app`).
- [ ] FastAPI deployment's `CORSMiddleware` has `allow_origin_regex` for
      `^chrome-extension://[a-z0-9]+$` (already in `apps/api/app/main.py`).
- [ ] `supabase/migrations/023_v4_day3_webpage_source.sql` is applied
      (`supabase db push`).
- [ ] Icons present in `src/icons/`.
- [ ] Manifest version bumped for each Web Store upload.
