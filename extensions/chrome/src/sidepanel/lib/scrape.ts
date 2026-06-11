/**
 * Side-panel side of the bridge: sends a message to the background SW which
 * runs the actual `chrome.scripting.executeScript` + Readability pass.
 * Wrapped in a Promise because chrome.runtime.sendMessage's MV3 signature is
 * callback-style.
 */
import type { ScrapeResult, TabInfoResult } from '../../lib/messages'

export async function getActiveTabInfo(): Promise<TabInfoResult> {
  return chrome.runtime.sendMessage({ type: 'CB_GET_ACTIVE_TAB_INFO' })
}

export async function scrapeActiveTab(): Promise<ScrapeResult> {
  return chrome.runtime.sendMessage({ type: 'CB_SCRAPE_ACTIVE_TAB' })
}
