/**
 * Thin wrapper around Google's official Picker API.
 *
 * Loads `https://apis.google.com/js/api.js`, then `gapi.load('picker', …)` on
 * first use, and exposes a single `openDriveFolderPicker()` async function.
 *
 * The script tag is loaded at most once across the page lifetime; subsequent
 * picker invocations reuse the existing `gapi.picker` namespace.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type GoogleNS = any;

declare global {
  interface Window {
    gapi?: GoogleNS;
    google?: GoogleNS;
  }
}

const GAPI_SRC = "https://apis.google.com/js/api.js";

let gapiLoaderPromise: Promise<void> | null = null;
let pickerLoaderPromise: Promise<void> | null = null;

function loadGapi(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Picker can only run in the browser"));
  }
  if (window.gapi) return Promise.resolve();
  if (gapiLoaderPromise) return gapiLoaderPromise;

  gapiLoaderPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${GAPI_SRC}"]`,
    );
    const script = existing ?? document.createElement("script");
    if (!existing) {
      script.src = GAPI_SRC;
      script.async = true;
      script.defer = true;
      document.body.appendChild(script);
    }
    script.addEventListener("load", () => resolve());
    script.addEventListener("error", () =>
      reject(new Error("Failed to load Google API script")),
    );
    // If the script was already loaded by a prior caller, the load event has
    // fired and `gapi` is set — short-circuit.
    if (window.gapi) resolve();
  });
  return gapiLoaderPromise;
}

function loadPicker(): Promise<void> {
  if (pickerLoaderPromise) return pickerLoaderPromise;
  pickerLoaderPromise = loadGapi().then(
    () =>
      new Promise<void>((resolve) => {
        window.gapi.load("picker", { callback: () => resolve() });
      }),
  );
  return pickerLoaderPromise;
}

export interface PickedFolder {
  id: string;
  name: string;
}

export interface OpenDriveFolderPickerOptions {
  accessToken: string;
  developerKey: string;
  appId?: string;
  /** If set, pre-select these folder IDs in the picker UI (not natively
   * supported by Google Picker — we rely on the caller deduping). */
  alreadySelectedIds?: string[];
}

/**
 * Open Google's folder picker and resolve with the user's selection.
 *
 * Resolves with `[]` if the user cancels. Rejects if the SDK fails to load,
 * if Google reports an access-denied error (usually a token-scope problem),
 * or if the developer key is missing.
 */
export async function openDriveFolderPicker(
  opts: OpenDriveFolderPickerOptions,
): Promise<PickedFolder[]> {
  if (!opts.developerKey) {
    throw new Error(
      "NEXT_PUBLIC_GOOGLE_API_KEY is not set — required for Google Picker",
    );
  }
  await loadPicker();
  const google = window.google as GoogleNS;
  if (!google?.picker) {
    throw new Error("Google Picker did not finish loading");
  }

  return new Promise<PickedFolder[]>((resolve, reject) => {
    try {
      // Two views: My Drive folders + Shared drives. Both restricted to
      // folder-MIME so the user can only pick folders.
      const myDriveFolders = new google.picker.DocsView(
        google.picker.ViewId.FOLDERS,
      )
        .setIncludeFolders(true)
        .setSelectFolderEnabled(true)
        .setMimeTypes("application/vnd.google-apps.folder");

      const sharedDrives = new google.picker.DocsView(
        google.picker.ViewId.FOLDERS,
      )
        .setIncludeFolders(true)
        .setSelectFolderEnabled(true)
        .setEnableDrives(true)
        .setMimeTypes("application/vnd.google-apps.folder");

      const builder = new google.picker.PickerBuilder()
        .addView(myDriveFolders)
        .addView(sharedDrives)
        .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
        .enableFeature(google.picker.Feature.SUPPORT_DRIVES)
        .setOAuthToken(opts.accessToken)
        .setDeveloperKey(opts.developerKey)
        .setTitle("Select folders to sync");

      if (opts.appId) builder.setAppId(opts.appId);

      const picker = builder
        .setCallback((data: GoogleNS) => {
          const action = data[google.picker.Response.ACTION];
          if (action === google.picker.Action.PICKED) {
            const docs = (data[google.picker.Response.DOCUMENTS] ?? []) as GoogleNS[];
            const folders: PickedFolder[] = docs
              .filter(
                (d) =>
                  d[google.picker.Document.MIME_TYPE] ===
                  "application/vnd.google-apps.folder",
              )
              .map((d) => ({
                id: d[google.picker.Document.ID] as string,
                name:
                  (d[google.picker.Document.NAME] as string | undefined) ??
                  (d[google.picker.Document.ID] as string),
              }));
            resolve(folders);
          } else if (action === google.picker.Action.CANCEL) {
            resolve([]);
          }
        })
        .build();
      picker.setVisible(true);
    } catch (err) {
      reject(err);
    }
  });
}


// ── File picker (single-select, DOCX + Google Docs only) ───────────────────

const GOOGLE_DOC_MIME = "application/vnd.google-apps.document";
const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export interface PickedDriveFile {
  id: string;
  name: string;
  mimeType: string;
}

export interface OpenDriveFilePickerOptions {
  accessToken: string;
  developerKey: string;
  appId?: string;
  /** Override the picker title. */
  title?: string;
}

/**
 * Open Google's Picker filtered to Google Docs + .docx files so HR can
 * pick a single template file from Drive. Single-select; cancelling resolves
 * with `null`.
 *
 * Two views — My Drive and Shared drives — match the folder picker pattern
 * so workspaces that keep canonical templates in a Team Drive still work.
 */
export async function openDriveFilePicker(
  opts: OpenDriveFilePickerOptions,
): Promise<PickedDriveFile | null> {
  if (!opts.developerKey) {
    throw new Error(
      "NEXT_PUBLIC_GOOGLE_API_KEY is not set — required for Google Picker",
    );
  }
  await loadPicker();
  const google = window.google as GoogleNS;
  if (!google?.picker) {
    throw new Error("Google Picker did not finish loading");
  }

  return new Promise<PickedDriveFile | null>((resolve, reject) => {
    try {
      const mimes = `${GOOGLE_DOC_MIME},${DOCX_MIME}`;

      const myDrive = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setIncludeFolders(true)
        .setSelectFolderEnabled(false)
        .setMimeTypes(mimes);

      const sharedDrives = new google.picker.DocsView(google.picker.ViewId.DOCS)
        .setIncludeFolders(true)
        .setSelectFolderEnabled(false)
        .setEnableDrives(true)
        .setMimeTypes(mimes);

      const builder = new google.picker.PickerBuilder()
        .addView(myDrive)
        .addView(sharedDrives)
        .enableFeature(google.picker.Feature.SUPPORT_DRIVES)
        .setOAuthToken(opts.accessToken)
        .setDeveloperKey(opts.developerKey)
        .setTitle(opts.title || "Select a template (Google Doc or .docx)");

      if (opts.appId) builder.setAppId(opts.appId);

      const picker = builder
        .setCallback((data: GoogleNS) => {
          const action = data[google.picker.Response.ACTION];
          if (action === google.picker.Action.PICKED) {
            const docs = (data[google.picker.Response.DOCUMENTS] ?? []) as GoogleNS[];
            const first = docs[0];
            if (!first) {
              resolve(null);
              return;
            }
            const mime = first[google.picker.Document.MIME_TYPE] as string;
            if (mime !== GOOGLE_DOC_MIME && mime !== DOCX_MIME) {
              // Picker is restricted, but defend against future SDK changes.
              reject(
                new Error(
                  "Pick a Google Doc or .docx file — that file type isn't supported as a template.",
                ),
              );
              return;
            }
            resolve({
              id: first[google.picker.Document.ID] as string,
              name:
                (first[google.picker.Document.NAME] as string | undefined) ??
                (first[google.picker.Document.ID] as string),
              mimeType: mime,
            });
          } else if (action === google.picker.Action.CANCEL) {
            resolve(null);
          }
        })
        .build();
      picker.setVisible(true);
    } catch (err) {
      reject(err);
    }
  });
}
