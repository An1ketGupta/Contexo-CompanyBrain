/**
 * Wire-format types shared between background, content scripts, and the
 * side-panel React app. Discriminated unions so chrome.runtime.onMessage
 * handlers narrow without `any`.
 */

export type ScrapePayload = {
  url: string
  title: string
  hostname: string
  content: string
}

export type RuntimeMessage =
  | { type: 'CB_SCRAPE_ACTIVE_TAB' }
  | { type: 'CB_GET_ACTIVE_TAB_INFO' }

export type ScrapeResult =
  | { ok: true; data: ScrapePayload }
  | { ok: false; error: string }

export type TabInfoResult =
  | { ok: true; data: { url: string; title: string; hostname: string; tabId: number } }
  | { ok: false; error: string }
