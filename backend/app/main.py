from fastapi import FastAPI

from app.api import runs


def create_app() -> FastAPI:
    app = FastAPI(title="SignalNest")
    app.include_router(runs.router)
    return app


app = create_app()
