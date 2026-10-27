import os

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-south-1"))
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME", "")
_database_url = os.getenv("DATABASE_URL", "").strip()
DATABASE_HOST = os.getenv("DATABASE_HOST", "").strip()
DATABASE_NAME = os.getenv("DATABASE_NAME", "").strip()
DATABASE_URL = (
    make_url(_database_url)
    .set(
        host=DATABASE_HOST or make_url(_database_url).host,
        database=DATABASE_NAME or make_url(_database_url).database,
    )
    .render_as_string(hide_password=False)
    if _database_url and (DATABASE_HOST or DATABASE_NAME)
    else _database_url
)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]
EHR_API_BASE_URL = os.getenv("EHR_API_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
PAYER_API_BASE_URL = os.getenv("PAYER_API_BASE_URL", "http://127.0.0.1:8002").rstrip("/")
CODING_API_BASE_URL = os.getenv("CODING_API_BASE_URL", "http://127.0.0.1:8003").rstrip("/")
MOCK_API_CONNECT_TIMEOUT = float(os.getenv("MOCK_API_CONNECT_TIMEOUT", "2.0"))
MOCK_API_RESPONSE_TIMEOUT = float(os.getenv("MOCK_API_RESPONSE_TIMEOUT", "5.0"))
BEDROCK_KNOWLEDGE_BASE_ID = os.getenv("BEDROCK_KNOWLEDGE_BASE_ID", "").strip()
BEDROCK_POLICY_MODEL_ID = os.getenv("BEDROCK_POLICY_MODEL_ID", "").strip()
BEDROCK_POLICY_RETRIEVAL_RESULTS = int(os.getenv("BEDROCK_POLICY_RETRIEVAL_RESULTS", "10"))


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


TESSERACT_CMD = os.getenv("TESSERACT_CMD", "").strip()
TESSERACT_LANGUAGE = os.getenv("TESSERACT_LANGUAGE", "eng").strip() or "eng"
OCR_RENDER_DPI = _env_int("OCR_RENDER_DPI", 250, minimum=200, maximum=300)
OCR_AUTO_VALIDATION_CONFIDENCE = _env_int(
    "OCR_AUTO_VALIDATION_CONFIDENCE", 95, minimum=0, maximum=100
)
OCR_VERIFICATION_CONFIDENCE = _env_int(
    "OCR_VERIFICATION_CONFIDENCE", 80, minimum=0, maximum=100
)
if OCR_VERIFICATION_CONFIDENCE > OCR_AUTO_VALIDATION_CONFIDENCE:
    raise RuntimeError(
        "OCR_VERIFICATION_CONFIDENCE cannot exceed OCR_AUTO_VALIDATION_CONFIDENCE"
    )

_NORMALIZABLE_FIELDS = {
    "patient_id",
    "patient_name",
    "provider_id",
    "payer_id",
    "policy_id",
    "diagnosis_code",
    "procedure_code",
    "amount",
    "date_of_service",
}
NORMALIZATION_REQUIRED_FIELDS = tuple(
    field.strip()
    for field in os.getenv(
        "NORMALIZATION_REQUIRED_FIELDS",
        "patient_id,provider_id,diagnosis_code,amount",
    ).split(",")
    if field.strip()
)
unknown_required_fields = set(NORMALIZATION_REQUIRED_FIELDS) - _NORMALIZABLE_FIELDS
if unknown_required_fields:
    raise RuntimeError(
        "NORMALIZATION_REQUIRED_FIELDS contains unsupported fields: "
        + ", ".join(sorted(unknown_required_fields))
    )
