from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="FX Engine")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
