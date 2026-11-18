from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings
from app.routers import health, risk_score, turns
from app.services.virtual_queue import QueueError, VirtualQueue

MAX_BODY_BYTES = 4096


def _invalid():
    return JSONResponse({"message": "Solicitud inválida."}, status_code=422)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def create_app(settings: Settings, queue: VirtualQueue) -> FastAPI:
    app = FastAPI(title="AlpaTeck ticketing", lifespan=lifespan, docs_url="/docs", redoc_url=None)
    app.state.settings = settings
    app.state.queue = queue

    @app.middleware("http")
    async def body_size_limit(request: Request, call_next):
        if request.method == "POST":
            try:
                size = int(request.headers.get("Content-Length", "0"))
            except ValueError:
                return _invalid()
            if not 0 < size <= MAX_BODY_BYTES:
                return _invalid()
        return await call_next(request)

    @app.exception_handler(QueueError)
    async def queue_error_handler(request: Request, exc: QueueError):
        return JSONResponse({"message": str(exc)}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return _invalid()

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(exc.detail, status_code=exc.status_code)
        if exc.status_code == 404:
            return JSONResponse({"message": "Ruta no encontrada."}, status_code=404)
        return JSONResponse({"message": str(exc.detail)}, status_code=exc.status_code)

    app.include_router(health.router)
    app.include_router(turns.router)
    app.include_router(risk_score.router)
    return app
