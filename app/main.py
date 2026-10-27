import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.config import CORS_ORIGINS
from app.routes.claims import router as claims_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Healthcare Claims Processing API", description="Claim intake API with PostgreSQL persistence and private S3 uploads.", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(claims_router)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(_request: Request, error: SQLAlchemyError) -> JSONResponse:
    logger.error(
        "RDS database operation failed; no local database fallback is configured",
        exc_info=(type(error), error, error.__traceback__),
    )
    return JSONResponse(status_code=503, content={"detail": "RDS database is unavailable"})


@app.get("/", tags=["Health"], summary="API health check")
def read_root() -> dict[str, str]:
    return {"message": "Healthcare Claims Processing API"}
