from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.audit_log import router as audit_log_router
from backend.app.api.auth import router as auth_router
from backend.app.api.document_memory import router as document_memory_router
from backend.app.api.llm_settings import router as llm_settings_router
from backend.app.api.transformations import router as transformations_router
from backend.app.core.config import settings
from backend.app.core.errors import AppError
from backend.app.db.database import initialize_database
from backend.app.services.planner import LLMPlanner


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Audit Data Transformer API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.web_origin.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, error: AppError):
    return JSONResponse(status_code=error.status_code, content={"error": {"code": error.code, "message": error.message}})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, error: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "Некорректные данные", "details": error.errors()}},
    )


@app.get("/health")
def health():
    planner = LLMPlanner()
    return {"status": "ok", "llm": planner.health()}


app.include_router(auth_router, prefix="/api")
app.include_router(transformations_router, prefix="/api")
app.include_router(audit_log_router, prefix="/api")
app.include_router(document_memory_router, prefix="/api")
app.include_router(llm_settings_router, prefix="/api")
