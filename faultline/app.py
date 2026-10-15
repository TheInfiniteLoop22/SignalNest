"""Faultline: fault-injection target. v0 (M1) only has /ok; the rest arrives in M2."""

from fastapi import FastAPI

app = FastAPI(title="Faultline")


@app.get("/ok")
async def ok() -> dict[str, str]:
    return {"status": "ok"}
