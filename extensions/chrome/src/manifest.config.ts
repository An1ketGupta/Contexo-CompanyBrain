import { defineManifest } from '@crxjs/vite-plugin'
import pkg from '../package.json'

// MV3 manifest. Two non-obvious choices:
//
//  • `activeTab` instead of `<all_urls>` host permissions. The Web Store treats
//    blanket host access as a privacy concern; activeTab grants the same
//    scraping capability but ONLY after a user-initiated gesture (clicking the
//    extension icon or invoking a command). Side-panel UX qualifies.
//
//  • No `content_scripts` block. We inject the scraper programmatically from
//    the side panel via chrome.scripting.executeScript when the user clicks
//    "Add to Brain". No content script runs on idle tabs → no perf cost and
//    no surprise reads of Gmail/Slack DOMs.
export default defineManifest({
  manifest_version: 3,
  name: 'Nirnaya IQ',
  version: pkg.version,
  description: pkg.description,

  action: {
    default_title: 'Open Nirnaya IQ',
    default_icon: {
      '16': 'icons/icon-16.png',
      '48': 'icons/icon-48.png',
      '128': 'icons/icon-128.png',
    },
  },
  icons: {
    '16': 'icons/icon-16.png',
    '48': 'icons/icon-48.png',
    '128': 'icons/icon-128.png',
  },

  side_panel: {
    default_path: 'src/sidepanel/index.html',
  },

  background: {
    service_worker: 'src/background.ts',
    type: 'module',
  },

  permissions: ['sidePanel', 'scripting', 'activeTab', 'storage', 'tabs'],

  // Empty host_permissions — activeTab covers the scraping case at the
  // moment the user clicks "Add to Brain", and CORS handles the fetch calls
  // from the side-panel iframe context.
  host_permissions: [],
})
