import uvicorn

# Fixed local dev port — apps/api already owns 8000/8001 in this environment,
# and the Documenso webhook (services/documenso/README.md) is configured to
# call back on 8002.
ESIGN_DEV_PORT = 8002

if __name__ == "__main__":
    # Watch only the app package. Left to its default the reloader watches the
    # whole cwd — including .venv — and silently stops noticing edits.
    uvicorn.run("app.main:app", reload=True, reload_dirs=["app"], port=ESIGN_DEV_PORT)
