import uvicorn

# Fixed local dev port — apps/api already owns 8000/8001 in this environment,
# and the Documenso webhook (services/documenso/README.md) is configured to
# call back on 8002.
ESIGN_DEV_PORT = 8002

if __name__ == "__main__":
    uvicorn.run("app.main:app", reload=True, port=ESIGN_DEV_PORT)
