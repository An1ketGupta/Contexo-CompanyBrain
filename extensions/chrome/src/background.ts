/// <reference types="chrome" />
/**
 * MV3 service worker.
 *
 * Two responsibilities:
 *   1) Open the side panel when the toolbar action is clicked. We use
 *      `setPanelBehavior` instead of an `action.onClicked` handler because
 *      the latter doesn't satisfy Chrome's "user gesture" requirement for
 *      `chrome.sidePanel.open()` on every host.
 *   2) Bridge the side panel ↔ active tab. The side panel can't run
 *      content scripts directly; it asks the SW to inject the scraper and
 *      relay back the result. Programmatic injection (not a manifest
 *      `content_scripts` entry) keeps idle tabs untouched and the Web
 *      Store privacy review painless.
 */
import { Readability } from '@mozilla/readability'
import type { RuntimeMessage, ScrapeResult, TabInfoResult } from './lib/messages'

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.error('[CB] setPanelBehavior failed', err))
})

chrome.runtime.onMessage.addListener(
  (message: RuntimeMessage, _sender, sendResponse) => {
    if (message?.type === 'CB_GET_ACTIVE_TAB_INFO') {
      void resolveActiveTabInfo().then(sendResponse)
      return true
    }
    if (message?.type === 'CB_SCRAPE_ACTIVE_TAB') {
      void scrapeActiveTab().then(sendResponse)
      return true
    }
    return false
  },
)

async function resolveActiveTabInfo(): Promise<TabInfoResult> {
  try {
    const tab = await getActiveTab()
    if (!tab?.id || !tab.url) {
      return { ok: false, error: 'No active tab to read.' }
    }
    if (!isInjectable(tab.url)) {
      return { ok: false, error: 'Cannot read this page (browser-internal URL).' }
    }
    return {
      ok: true,
      data: {
        tabId: tab.id,
        url: tab.url,
        title: tab.title ?? '',
        hostname: safeHost(tab.url),
      },
    }
  } catch (err) {
    return { ok: false, error: stringifyErr(err) }
  }
}

async function scrapeActiveTab(): Promise<ScrapeResult> {
  try {
    const tab = await getActiveTab()
    if (!tab?.id || !tab.url) {
      return { ok: false, error: 'No active tab to scrape.' }
    }
    if (!isInjectable(tab.url)) {
      return {
        ok: false,
        error: 'Cannot scrape this page (chrome://, file://, or store-protected).',
      }
    }

    // executeScript with `func` runs the function in the page's isolated world.
    // We can't import Readability in there, so we serialize the DOM to a string
    // here, then construct the Readability instance back in the SW context
    // where the bundler resolved the module.
    const [{ result: serialized }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const clone = document.cloneNode(true) as Document
        return {
          html: '<!doctype html>' + (clone.documentElement?.outerHTML ?? ''),
          title: document.title,
          url: location.href,
        }
      },
    })

    if (!serialized?.html) {
      return { ok: false, error: 'Page returned no HTML to parse.' }
    }

    const parser = new DOMParser()
    const doc = parser.parseFromString(serialized.html, 'text/html')
    // Readability needs an absolute base; if missing, mutate the doc directly.
    if (!doc.querySelector('base')) {
      const base = doc.createElement('base')
      base.setAttribute('href', serialized.url)
      doc.head?.prepend(base)
    }

    const article = new Readability(doc).parse()
    // Fall back to raw text content when Readability declines (short pages,
    // SPAs without article semantics — Notion, Linear, etc.).
    const textContent =
      article?.textContent?.trim() ||
      (doc.body?.innerText ?? doc.body?.textContent ?? '').trim()

    if (!textContent) {
      return { ok: false, error: 'No readable text found on this page.' }
    }

    return {
      ok: true,
      data: {
        url: serialized.url,
        title: (article?.title || serialized.title || '').slice(0, 255),
        hostname: safeHost(serialized.url),
        // 200k char cap matches the backend's DocumentFromUrlRequest limit.
        // Anything longer is almost certainly nav junk we didn't strip.
        content: textContent.slice(0, 200_000),
      },
    }
  } catch (err) {
    return { ok: false, error: stringifyErr(err) }
  }
}

async function getActiveTab(): Promise<chrome.tabs.Tab | undefined> {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true })
  return tab
}

function isInjectable(url: string): boolean {
  if (!url) return false
  // chrome://, edge://, about:, view-source:, file://, and chromewebstore are
  // off-limits to programmatic injection. Quick prefix check beats catching
  // the executeScript error after the fact.
  const blocked = [
    'chrome://',
    'chrome-extension://',
    'about:',
    'view-source:',
    'edge://',
    'file://',
    'https://chrome.google.com/webstore',
    'https://chromewebstore.google.com',
  ]
  return !blocked.some((p) => url.startsWith(p))
}

function safeHost(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return ''
  }
}

function stringifyErr(err: unknown): string {
  if (err instanceof Error) return err.message
  try {
    return String(err)
  } catch {
    return 'unknown error'
  }
}
