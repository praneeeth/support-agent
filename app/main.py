from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.admin.routes import router as admin_router
from app.agent.routes import router as agent_router
from app.channels.email import router as email_router
from app.channels.webchat import router as webchat_router
from app.channels.whatsapp import router as whatsapp_router
from app.handoff.routes import router as staff_router
from app.verticals.config import get_vertical


def create_app() -> FastAPI:
    # Loading the vertical here makes a broken config a startup error.
    app = FastAPI(title=f"{get_vertical().business.name} Support Agent")
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
    app.include_router(staff_router)
    app.include_router(admin_router)
    app.include_router(agent_router)
    app.include_router(webchat_router)
    app.include_router(whatsapp_router)
    app.include_router(email_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
