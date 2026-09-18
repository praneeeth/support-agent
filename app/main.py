from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Northwind Goods Support Agent")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
