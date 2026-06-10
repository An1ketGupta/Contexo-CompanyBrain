"""Day-14 integration services (Drive, Notion, Email Forward).

Each submodule owns:
    * OAuth / inbound webhook flow
    * Polling logic invoked by Inngest crons
    * Upsert into the documents table via `text_ingest.ingest_text_document`

This package depends on environment-gated third-party SDKs. Importing it
without credentials configured is safe — the polling crons skip cleanly
and the routers return a "not configured" error to the UI.
"""
