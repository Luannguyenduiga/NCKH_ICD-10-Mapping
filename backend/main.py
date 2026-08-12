# -*- coding: utf-8 -*-
"""
SMIG Gateway - API cổng liên thông.

Ba nhóm endpoint:
    /api/standardize      chuẩn hóa chẩn đoán tiếng Việt -> mã ICD-10
    /api/fhir/condition   sinh HL7 FHIR Condition Resource
    /api/fhir/sync        đẩy/đọc/xóa hồ sơ trên EMR Cloud (HAPI FHIR)
"""

import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.fhir_helper import (
    build_fhir_condition_resource,
    build_fhir_patient_resource,
    is_valid_icd10,
    sanitize_icd10_code,
    to_fhir_id,
)
from nlp.clinical_rules import CONFIDENCE_POLICY, verification_status_for
from nlp.nlp_engine import NLPEngine

# --- Cấu hình (đọc từ biến môi trường, có giá trị mặc định cho môi trường demo) ---
FHIR_SERVER_URL = os.getenv("SMIG_FHIR_SERVER_URL", "http://127.0.0.1:8090/fhir")
FHIR_TIMEOUT = float(os.getenv("SMIG_FHIR_TIMEOUT", "8"))
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "SMIG_ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000,"
        "http://127.0.0.1:8085,http://localhost:8085",
    ).split(",") if o.strip()
]

nlp_engine: Optional[NLPEngine] = None
engine_error: Optional[str] = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Nạp mô hình NLP một lần khi khởi động (thay cho @app.on_event đã lỗi thời)."""
    global nlp_engine, engine_error
    try:
        nlp_engine = NLPEngine()
    except Exception as exc:  # noqa: BLE001 - giữ server sống để /health báo lỗi rõ ràng
        engine_error = str(exc)
        print(f"Error initializing NLP engine: {exc}")
        nlp_engine = None
    yield
    nlp_engine = None


app = FastAPI(
    title="Smart Medical Interoperability Gateway API",
    description="NLP-powered ICD-10 standardization and HL7 FHIR converter",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS giới hạn theo danh sách nguồn cụ thể. Dùng "*" kèm allow_credentials=True
# là cấu hình không hợp lệ theo đặc tả CORS và bị trình duyệt từ chối.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Mô hình dữ liệu
# ---------------------------------------------------------------------------
class StandardizeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(4, ge=1, le=10)


class EntityModel(BaseModel):
    text: str
    normalized: str
    code: str
    # Dạng liền không dấu chấm (A00.0 -> A000) cho HIS/báo cáo dùng mã rút gọn.
    # Mã để liên thông FHIR luôn là `code`.
    code_no_dot: str
    type: str


class PredictionModel(BaseModel):
    code: str
    code_no_dot: str
    raw_code: str
    name_vi: str
    name_en: str
    confidence: float
    confidence_band: str
    similarity_score: float
    rerank_score: float
    matched_by: str
    match_type: str
    explanation: List[str] = []
    suggested_verification_status: str
    requires_review: bool


class CombinationMemberModel(BaseModel):
    code: str
    code_no_dot: str
    raw_code: str
    name_vi: str
    confidence: float
    # "etiology" = mã bệnh nguyên (†), "manifestation" = mã biểu hiện (*).
    role: str


class CombinationModel(BaseModel):
    """Cặp mã dao găm/sao khi chẩn đoán mô tả cả bệnh nguyên lẫn biểu hiện."""
    display: str
    members: List[CombinationMemberModel]
    rationale: List[str]


class DiagnosisModel(BaseModel):
    """
    Một chẩn đoán độc lập tách từ dòng bệnh án.

    Câu một bệnh cho đúng một mục. Câu nhiều bệnh ("sỏi bàng quang, suy thận
    cấp") cho nhiều mục, mỗi mục giữ độ tin cậy riêng thay vì chia nhau.
    """
    fragment: str
    predictions: List[PredictionModel]


class StandardizeResponse(BaseModel):
    query: str
    normalized_query: str
    entities: List[EntityModel]
    predictions: List[PredictionModel]
    diagnoses: List[DiagnosisModel] = []
    # None khi chẩn đoán chỉ có một vế - phần lớn trường hợp.
    combination: Optional[CombinationModel] = None
    latency_ms: float


class FHIRConvertRequest(BaseModel):
    patient_id: str = Field(..., min_length=1)
    patient_name: str = Field(..., min_length=1)
    raw_clinical_note: str = Field(..., min_length=1)
    icd10_code: str = Field(..., min_length=1)
    icd10_display: str = Field(..., min_length=1)
    confidence_score: float = Field(..., ge=0, le=100)
    clinical_status: Optional[str] = "active"
    # Bỏ trống để Gateway tự suy ra từ độ tin cậy theo CONFIDENCE_POLICY.
    verification_status: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------
def _require_engine() -> NLPEngine:
    if nlp_engine is None:
        raise HTTPException(
            status_code=503,
            detail=f"NLP Engine chưa sẵn sàng. {engine_error or 'Đang nạp mô hình...'}",
        )
    return nlp_engine


def stable_condition_id(patient_id: str, icd10_code: str) -> str:
    """
    Sinh id ổn định cho Condition từ (bệnh nhân, mã bệnh).

    Nhờ id ổn định, thao tác đồng bộ dùng PUT nên chạy lại nhiều lần vẫn cho ra
    đúng một tài nguyên trên EMR. Bản trước dùng POST với UUID ngẫu nhiên: máy
    chủ HAPI tự cấp id mới, nên id lưu trong HIS không trỏ tới tài nguyên nào và
    mỗi lần bấm đồng bộ lại sinh thêm một bản trùng.
    """
    code_part = sanitize_icd10_code(icd10_code).replace(".", "-")
    return to_fhir_id(f"cond-{patient_id}-{code_part}")


def _fhir_headers(content: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/fhir+json"}
    if content:
        headers["Content-Type"] = "application/fhir+json"
    return headers


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    return {
        "status": "healthy" if nlp_engine is not None else "degraded",
        "model_loaded": nlp_engine is not None,
        "model_name": os.path.basename(nlp_engine.model_name) if nlp_engine else None,
        "icd10_entries": len(nlp_engine.db) if nlp_engine else 0,
        "engine_error": engine_error,
        "confidence_policy": CONFIDENCE_POLICY,
        "fhir_server": FHIR_SERVER_URL,
        "timestamp": time.time(),
    }


@app.post("/api/standardize", response_model=StandardizeResponse)
def standardize_diagnosis(request: StandardizeRequest):
    engine = _require_engine()
    start_time = time.time()

    try:
        normalized_query = engine.expand_query(request.query)
        entities = engine.extract_entities_regex(request.query)
        composite = engine.query_composite(request.query, top_k=request.top_k)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi khi xử lý NLP: {exc}") from exc

    predictions = composite["predictions"]
    diagnoses = composite.get("diagnoses") or []

    # Danh sách phẳng và các chẩn đoán dùng chung phần lớn bản ghi, nhưng ứng
    # viên hạng thấp của từng vế thì chỉ nằm trong `diagnoses`. Duyệt cả hai để
    # không bản ghi nào thiếu trường trạng thái - Pydantic bắt buộc có.
    for pred in [p for p in predictions] + [p for d in diagnoses for p in d["predictions"]]:
        status = verification_status_for(pred["confidence"])
        pred["suggested_verification_status"] = status
        pred["requires_review"] = status != "confirmed"

    return {
        "query": request.query,
        "normalized_query": normalized_query,
        "entities": entities,
        "predictions": predictions,
        "diagnoses": diagnoses,
        "combination": composite["combination"],
        "latency_ms": round((time.time() - start_time) * 1000, 2),
    }


@app.post("/api/fhir/condition")
def convert_to_fhir(request: FHIRConvertRequest):
    clean_code = sanitize_icd10_code(request.icd10_code)
    if not is_valid_icd10(clean_code):
        raise HTTPException(
            status_code=422,
            detail=f"Mã '{request.icd10_code}' không đúng định dạng ICD-10 (ví dụ hợp lệ: E11.9).",
        )

    if request.confidence_score < CONFIDENCE_POLICY["reject"]:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Độ tin cậy {request.confidence_score}% dưới ngưỡng tối thiểu "
                f"{CONFIDENCE_POLICY['reject']}%. Cần bác sĩ chọn lại mã ICD-10."
            ),
        )

    verification = request.verification_status or verification_status_for(request.confidence_score)
    try:
        return build_fhir_condition_resource(
            patient_id=request.patient_id,
            patient_name=request.patient_name,
            raw_clinical_note=request.raw_clinical_note,
            icd10_code=clean_code,
            icd10_display=request.icd10_display,
            confidence_score=request.confidence_score,
            clinical_status=request.clinical_status or "active",
            verification_status=verification,
            condition_id=stable_condition_id(request.patient_id, clean_code),
            engine_version=os.path.basename(nlp_engine.model_name) if nlp_engine else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi tạo tài nguyên FHIR: {exc}") from exc


@app.get("/api/fhir/sync", response_model=List[dict])
def get_synced_records(count: int = 10):
    """Đọc các Condition mới nhất trên EMR Cloud. Trả danh sách rỗng nếu EMR offline."""
    url = f"{FHIR_SERVER_URL}/Condition?_sort=-_lastUpdated&_count={max(1, min(count, 50))}"
    try:
        response = requests.get(url, headers=_fhir_headers(), timeout=FHIR_TIMEOUT)
        response.raise_for_status()
        bundle = response.json()
    except requests.RequestException:
        print("Warning: EMR Cloud offline, returning empty synced list.")
        return []

    return [entry["resource"] for entry in bundle.get("entry", []) if entry.get("resource")]


@app.post("/api/fhir/sync")
def sync_fhir_record(record: Dict[str, Any]):
    """
    Đẩy một Condition lên EMR Cloud, kèm Patient tương ứng.

    Chấp nhận hai dạng thân yêu cầu:
        {...Condition...}                      - chỉ có chẩn đoán
        {"condition": {...}, "patient": {...}} - kèm thông tin hành chính bệnh nhân

    Dùng PUT cho cả hai tài nguyên nên thao tác là idempotent: đồng bộ lại cùng
    một chẩn đoán sẽ cập nhật đúng bản ghi cũ thay vì tạo bản trùng.
    """
    payload = record.get("condition") if "condition" in record else record
    demographics = record.get("patient") or {}

    if not isinstance(payload, dict) or payload.get("resourceType") != "Condition":
        raise HTTPException(status_code=422, detail="Chỉ chấp nhận tài nguyên loại Condition.")

    condition_id = payload.get("id")
    if not condition_id:
        raise HTTPException(status_code=422, detail="Condition thiếu trường 'id'.")

    subject = payload.get("subject") or {}
    patient_ref = subject.get("reference", "")
    if not patient_ref.startswith("Patient/"):
        raise HTTPException(status_code=422, detail="Condition.subject.reference phải có dạng 'Patient/<id>'.")

    patient_id = patient_ref[len("Patient/"):]
    patient_resource = build_fhir_patient_resource(
        patient_id=demographics.get("id") or patient_id,
        patient_name=demographics.get("name") or subject.get("display") or patient_id,
        gender=demographics.get("gender"),
        birth_date=demographics.get("birth_date"),
    )

    try:
        r_patient = requests.put(
            f"{FHIR_SERVER_URL}/Patient/{to_fhir_id(patient_id)}",
            json=patient_resource, headers=_fhir_headers(content=True), timeout=FHIR_TIMEOUT,
        )
        r_patient.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=(f"Không thể kết nối EMR Cloud (HAPI FHIR) tại {FHIR_SERVER_URL}. "
                    f"Hãy chạy 'docker compose up -d'. Chi tiết: {exc}"),
        ) from exc

    try:
        r_cond = requests.put(
            f"{FHIR_SERVER_URL}/Condition/{to_fhir_id(condition_id)}",
            json=payload, headers=_fhir_headers(content=True), timeout=FHIR_TIMEOUT,
        )
        r_cond.raise_for_status()
    except requests.RequestException as exc:
        detail = getattr(exc.response, "text", "")[:300] if getattr(exc, "response", None) else str(exc)
        raise HTTPException(status_code=502, detail=f"EMR Cloud từ chối Condition: {detail}") from exc

    return {
        "status": "success",
        "message": "Đồng bộ thành công lên HAPI FHIR Server",
        "condition_id": to_fhir_id(condition_id),
        "patient_id": to_fhir_id(patient_id),
        "location": f"{FHIR_SERVER_URL}/Condition/{to_fhir_id(condition_id)}",
        "created": r_cond.status_code == 201,
    }


@app.get("/api/fhir/condition/{condition_id}")
def read_synced_condition(condition_id: str):
    """Đọc lại một Condition từ EMR Cloud - dùng để đối chiếu sau khi liên thông."""
    try:
        response = requests.get(
            f"{FHIR_SERVER_URL}/Condition/{to_fhir_id(condition_id)}",
            headers=_fhir_headers(), timeout=FHIR_TIMEOUT,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy Condition '{condition_id}' trên EMR Cloud: {exc}",
        ) from exc


@app.delete("/api/fhir/sync")
def delete_synced_records():
    """
    Xóa thật các Condition đã liên thông trên EMR Cloud.

    Bản trước chỉ trả về "success" mà không xóa gì, nên danh sách trên giao diện
    không hề thay đổi sau khi người dùng bấm nút.
    """
    records = get_synced_records(count=50)
    if not records:
        return {"status": "success", "deleted": 0, "message": "Không có bản ghi nào để xóa."}

    deleted, failed = 0, []
    for record in records:
        resource_id = record.get("id")
        if not resource_id:
            continue
        try:
            response = requests.delete(
                f"{FHIR_SERVER_URL}/Condition/{resource_id}",
                headers=_fhir_headers(), timeout=FHIR_TIMEOUT,
            )
            response.raise_for_status()
            deleted += 1
        except requests.RequestException as exc:
            failed.append(f"{resource_id}: {exc}")

    if failed and not deleted:
        raise HTTPException(status_code=502, detail=f"Không xóa được bản ghi nào. {failed[0]}")

    return {
        "status": "success",
        "deleted": deleted,
        "failed": failed,
        "message": f"Đã xóa {deleted} bản ghi khỏi EMR Cloud."
                   + (f" {len(failed)} bản ghi lỗi." if failed else ""),
    }


# Phục vụ giao diện tĩnh. Mount ở cuối để không che các route API phía trên.
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="static")
else:
    print(f"Warning: Frontend directory '{frontend_dir}' not found. Serving API only.")
