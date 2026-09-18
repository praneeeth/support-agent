from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.handoff.routes import router as staff_router


def create_app() -> FastAPI:
    app = FastAPI(title="Northwind Goods Support Agent")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(staff_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
