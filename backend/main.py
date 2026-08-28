# -*- coding: utf-8 -*-
"""
SMIG Gateway - API cổng liên thông.

Ba nhóm endpoint:
    /api/standardize      chuẩn hóa chẩn đoán tiếng Việt -> mã ICD-10
    /api/fhir/condition   sinh HL7 FHIR Condition Resource
    /api/fhir/sync        đẩy/đọc/xóa hồ sơ trên EMR Cloud (HAPI FHIR)
"""

import os
import re
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.fhir_helper import (
    EXT_SOURCE_FACILITY,
    SYSTEM_CONDITION_KEY,
    SYSTEM_FACILITY,
    build_fhir_condition_resource,
    build_fhir_organization_resource,
    build_fhir_patient_resource,
    condition_key_of,
    facility_of,
    is_valid_icd10,
    utc_now_iso,
    normalize_citizen_id,
    normalize_insurance_card,
    patient_identifier_candidates,
    patient_local_key,
    patient_match_key,
    sanitize_icd10_code,
    validate_identifier_value,
    subject_identifier_of,
    to_fhir_id,
)
from nlp.clinical_rules import CONFIDENCE_POLICY, confidence_band, verification_status_for
from nlp.nlp_engine import NLPEngine

# --- Cấu hình (đọc từ biến môi trường, có giá trị mặc định cho môi trường demo) ---
FHIR_SERVER_URL = os.getenv("SMIG_FHIR_SERVER_URL", "http://127.0.0.1:8090/fhir")
FHIR_TIMEOUT = float(os.getenv("SMIG_FHIR_TIMEOUT", "8"))

# Cơ sở khám chữa bệnh mà bản Gateway này phục vụ. Khi triển khai thật, MỖI bệnh
# viện chạy một bản Gateway riêng với mã khác nhau, nên hai giá trị này quyết
# định: (a) mã bệnh án được quy về đúng bệnh viện đã cấp nó, (b) mỗi chẩn đoán
# trên trục mang tên nơi đã ghi. Để mặc định giống nhau ở hai bệnh viện là quay
# lại đúng lỗi trộn hồ sơ mà cấu hình này sinh ra để chặn.
FACILITY_CODE = os.getenv("SMIG_FACILITY_CODE", "BV-DEMO-01")
FACILITY_NAME = os.getenv("SMIG_FACILITY_NAME", "Bệnh viện Demo SMIG")

# Cho phép HIS TỰ KHAI mã cơ sở trong từng yêu cầu, để MỘT bản Gateway phục vụ
# nhiều bệnh viện. Mặc định TẮT, và đó là mặc định đúng cho triển khai thật: mã
# cơ sở là danh tính của bên ghi hồ sơ, phải do phía máy chủ xác lập (ở đây là
# biến môi trường, thực tế là chứng thư/khóa API), chứ để bên gọi tự khai thì
# bệnh viện B khai mình là bệnh viện A được ngay.
#
# Bật lên chỉ dành cho môi trường thử nghiệm cục bộ: mô hình NLP chiếm vài GB
# RAM nên chạy hai bản Gateway trên một máy là quá nặng, trong khi vẫn cần hai
# bệnh viện phân biệt được nhau để trình diễn kịch bản liên thông.
ALLOW_CLIENT_FACILITY = os.getenv("SMIG_ALLOW_CLIENT_FACILITY", "0").strip().lower() in {
    "1", "true", "yes", "on",
}
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
    # Bỏ trống thì Gateway tự tra tên trong danh mục ICD-10. Bắt buộc bên gọi
    # phải có tên là ép mỗi HIS tự mang theo một bản danh mục, và bản nào cũng
    # nghèo hơn bản 12.137 mã ở đây - hậu quả thấy được là chẩn đoán lên trục
    # mang tên chỗ điền tạm thay vì tên bệnh, bác sĩ tuyến sau đọc không ra bệnh.
    icd10_display: Optional[str] = None
    confidence_score: float = Field(..., ge=0, le=100)
    clinical_status: Optional[str] = "active"
    # Bỏ trống để Gateway tự suy ra từ độ tin cậy theo CONFIDENCE_POLICY.
    verification_status: Optional[str] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None
    # Định danh toàn quốc. Có thì hồ sơ của cùng một người ở nhiều bệnh viện quy
    # về một Patient trên trục; không có thì lùi về mã bệnh án + mã cơ sở.
    citizen_id: Optional[str] = None
    insurance_card: Optional[str] = None
    # Ngày khám (YYYY-MM-DD). Bỏ trống thì lấy ngày hiện tại. Nằm trong khóa
    # nghiệp vụ nên cùng bệnh nhân mắc lại cùng bệnh ở lần khám sau sẽ là một
    # Condition mới, thay vì đè lên chẩn đoán cũ.
    encounter_date: Optional[str] = None
    # Cơ sở khám chữa bệnh đang ghi hồ sơ. Bỏ trống thì lấy cấu hình của Gateway
    # (đường đi mặc định, và là đường đi duy nhất khi mỗi bệnh viện chạy một bản
    # Gateway riêng). Khai mã khác chỉ được chấp nhận khi Gateway bật
    # SMIG_ALLOW_CLIENT_FACILITY - xem `resolve_facility`.
    facility_code: Optional[str] = None
    facility_name: Optional[str] = None

    # Kiểm ngay tại tầng nhận yêu cầu, nên sai định dạng là 422 trước khi bất kỳ
    # tài nguyên nào được dựng. Giá trị trả về đã chuẩn hóa (bỏ dấu cách/chấm/
    # gạch) và chính bản chuẩn hóa đó đi tiếp vào `identifier`.
    @field_validator("citizen_id")
    @classmethod
    def _kiem_cccd(cls, value: Optional[str]) -> Optional[str]:
        return normalize_citizen_id(value)

    @field_validator("insurance_card")
    @classmethod
    def _kiem_bhyt(cls, value: Optional[str]) -> Optional[str]:
        return normalize_insurance_card(value)


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


def resolve_facility(
    claimed_code: Optional[str] = None,
    claimed_name: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Chốt mã cơ sở dùng cho một yêu cầu: bên gọi khai, hay cấu hình của Gateway.

    Bỏ trống thì lấy cấu hình của Gateway - đây là đường đi của mọi bản HIS cũ,
    nên hành vi không đổi. Khai đúng mã của chính Gateway cũng luôn hợp lệ.

    Khai một mã KHÁC chỉ được chấp nhận khi `SMIG_ALLOW_CLIENT_FACILITY` bật.
    Không có chốt này thì bất kỳ ai gọi được Gateway đều tự nhận mình là bệnh
    viện khác và ghi hồ sơ dưới danh nghĩa nơi đó. Từ chối thẳng chứ không âm
    thầm lấy mã của Gateway: sai cấu hình mà vẫn chạy thì hồ sơ của bệnh viện B
    nằm trên trục dưới tên bệnh viện A, và không ai phát hiện ra.
    """
    code = (claimed_code or "").strip()
    if not code or code == FACILITY_CODE:
        return FACILITY_CODE, FACILITY_NAME
    if not ALLOW_CLIENT_FACILITY:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Yêu cầu khai mã cơ sở '{code}' nhưng Gateway này phục vụ "
                f"'{FACILITY_CODE}'. Triển khai thật thì mỗi bệnh viện chạy một bản "
                f"Gateway riêng. Nếu đang thử nghiệm cục bộ và muốn một bản Gateway "
                f"phục vụ nhiều bệnh viện, hãy đặt SMIG_ALLOW_CLIENT_FACILITY=1."
            ),
        )
    return code, (claimed_name or "").strip() or code


def stable_condition_key(
    patient_key: str,
    icd10_code: str,
    encounter_date: Optional[str] = None,
    facility_code: Optional[str] = None,
) -> str:
    """
    Sinh khóa nghiệp vụ ổn định cho Condition.

    Khóa gồm bốn thành phần, mỗi thành phần chặn một kiểu trộn dữ liệu:

        {mã cơ sở}-{mã bệnh án}-{mã ICD}-{ngày khám}

    * **mã cơ sở** - thiếu nó thì chẩn đoán của bệnh viện B ghi đè lên chẩn đoán
      của bệnh viện A khi hai nơi tình cờ dùng trùng mã bệnh án. Truyền
      `facility_code` khi một bản Gateway phục vụ nhiều bệnh viện; bỏ trống thì
      lấy mã của chính Gateway.
    * **mã bệnh án** - KHÔNG dùng CCCD/BHYT ở đây, dù chúng "toàn quốc" hơn. Hồ
      sơ đầy dần theo thời gian: lần khám đầu HIS chỉ có mã bệnh án, lần sau mới
      bổ sung CCCD. Khóa bám theo định danh ưu tiên cao nhất hiện có sẽ ĐỔI giữa
      hai lần, và cùng một chẩn đoán thành hai bản ghi trên trục. Mã bệnh án là
      thứ duy nhất chắc chắn có mặt và không đổi - xem `patient_local_key`.
      Việc đồng nhất một người giữa nhiều viện do `Patient.identifier` đảm nhiệm,
      không phải khóa này (mã cơ sở đã nằm trong khóa nên chẩn đoán của hai viện
      vốn dĩ là hai bản ghi riêng).
    * **ngày khám** - thiếu nó thì cùng người mắc lại cùng bệnh ở lần khám sau sẽ
      đè lên chẩn đoán lần trước, làm mất lịch sử điều trị.

    Khóa đi vào `Condition.identifier` và là thứ conditional update tìm theo, nên
    đồng bộ lại cùng một chẩn đoán trong cùng ngày vẫn cho đúng một tài nguyên.
    """
    code_part = sanitize_icd10_code(icd10_code).replace(".", "-")
    day = (encounter_date or "").strip() or utc_now_iso()[:10]
    facility = (facility_code or "").strip() or FACILITY_CODE
    return to_fhir_id(f"{facility}-{patient_key}-{code_part}-{day}")


def _fhir_headers(content: bool = False) -> Dict[str, str]:
    headers = {"Accept": "application/fhir+json"}
    if content:
        headers["Content-Type"] = "application/fhir+json"
    return headers


def _conditional_url(resource_type: str, system: str, value: str) -> str:
    """Dựng URL conditional update: PUT /{Type}?identifier={system}|{value}."""
    token = quote(f"{system}|{value}", safe="")
    return f"{FHIR_SERVER_URL}/{resource_type}?identifier={token}"


def _select_patient_identifier(
    candidates: List[Tuple[str, str]],
    fallback: Tuple[str, str],
) -> Tuple[str, str]:
    """
    Chọn định danh dùng làm điều kiện cho conditional update của Patient.

    Hồ sơ bệnh nhân đầy dần theo thời gian, nên định danh "tốt nhất" của lần này
    có thể chưa tồn tại ở lần trước. Tra thẳng theo nó sẽ không thấy bản cũ và
    máy chủ tạo thêm một Patient nữa cho cùng một người; tệ hơn, hai bản đó cùng
    mang mã bệnh án nên lần sau conditional update khớp cả hai và bị trả 412.

    Vì vậy hỏi trục trước bằng TẤT CẢ định danh đã biết (dấu phẩy trong tham số
    `identifier` là phép HOẶC theo đặc tả FHIR), rồi cập nhật đúng bản tìm được.

    Tìm thấy nhiều bệnh nhân khác nhau thì dừng lại: đó là hai hồ sơ cần hợp nhất
    bằng `Patient.link`, và chọn bừa một bản sẽ làm mất bệnh sử của bản kia.
    """
    if not candidates:
        return fallback

    try:
        response = requests.get(
            f"{FHIR_SERVER_URL}/Patient",
            params={"identifier": ",".join(f"{s}|{v}" for s, v in candidates)},
            headers=_fhir_headers(), timeout=FHIR_TIMEOUT,
        )
        response.raise_for_status()
        found = [e["resource"] for e in (response.json() or {}).get("entry", [])
                 if e.get("resource")]
    except (requests.RequestException, ValueError):
        # Không tra được thì giữ nguyên hành vi cũ. Bước này để gộp hồ sơ tốt hơn,
        # không được biến sự cố mạng thành lỗi chặn cả luồng liên thông.
        return fallback

    if len(found) > 1:
        raise HTTPException(
            status_code=409,
            detail=(f"Trên EMR Cloud đang có {len(found)} hồ sơ khớp cùng một bệnh "
                    f"nhân ({', '.join(f'{s}|{v}' for s, v in candidates)}). Cần hợp "
                    f"nhất bằng Patient.link trước khi đồng bộ tiếp."),
        )
    if not found:
        return fallback

    # Cập nhật theo một định danh mà bản tìm được ĐANG mang, nếu không thì điều
    # kiện lại không khớp và ta quay về đúng chỗ cũ - tạo thêm một bản trùng.
    dang_co = {(i.get("system"), i.get("value"))
               for i in found[0].get("identifier") or [] if isinstance(i, dict)}
    for cap in candidates:
        if cap in dang_co:
            return cap
    return fallback


def _resource_id_from(response: requests.Response, resource_type: str) -> Optional[str]:
    """
    Lấy id do máy chủ cấp sau một conditional update.

    Ưu tiên thân phản hồi (HAPI trả tài nguyên đầy đủ), dự phòng bằng header
    Location vì máy chủ được phép trả thân rỗng.
    """
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("id"):
            return str(body["id"])
    except ValueError:
        pass

    location = response.headers.get("Location") or response.headers.get("Content-Location") or ""
    match = re.search(rf"/{resource_type}/([^/?]+)", location)
    return match.group(1) if match else None


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
        "facility_code": FACILITY_CODE,
        "facility_name": FACILITY_NAME,
        # Bật nghĩa là một bản Gateway đang phục vụ nhiều bệnh viện, và mã cơ sở
        # do bên gọi khai. Phơi ra đây để nhìn là biết Gateway đang ở chế độ thử
        # nghiệm, không nhầm với cấu hình triển khai thật.
        "allow_client_facility": ALLOW_CLIENT_FACILITY,
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
        pred["suggested_verification_status"] = verification_status_for(pred["confidence"])
        # Suy từ mức tin cậy, KHÔNG suy từ trạng thái FHIR. Hai việc này vốn khác
        # nhau: `requires_review` quyết định có được tự động đẩy lên trục hay
        # không, còn `verificationStatus` là bản ghi tự khai độ chắc chắn của nó.
        # Từ khi máy thôi tự gán "confirmed", nếu vẫn so với chuỗi đó thì mọi ca
        # đều thành cần duyệt và luồng tự động liên thông chết hẳn.
        pred["requires_review"] = confidence_band(pred["confidence"]) != "high"

    return {
        "query": request.query,
        "normalized_query": normalized_query,
        "entities": entities,
        "predictions": predictions,
        "diagnoses": diagnoses,
        "combination": composite["combination"],
        "latency_ms": round((time.time() - start_time) * 1000, 2),
    }


# Chỗ điền tạm mà HIS hay gửi lên thay cho tên bệnh. Chúng KHÔNG phải tên bệnh:
# "Chẩn đoán kèm theo" nói rằng đây là bệnh phụ, không nói đó là bệnh gì, nên bác
# sĩ tuyến sau đọc bệnh án về vẫn không biết phải điều trị gì. Gặp mấy chuỗi này
# thì bỏ qua và tra tên thật trong danh mục.
NHAN_KHONG_PHAI_TEN_BENH = {
    "chẩn đoán kèm theo", "chan doan kem theo",
    "chẩn đoán phụ", "chan doan phu",
    "chẩn đoán liên thông", "chan doan lien thong",
    "không rõ", "khong ro", "n/a", "-", "--",
}


def ten_benh_theo_ma(clean_code: str, ten_ben_goi: Optional[str] = None) -> Optional[str]:
    """
    Chốt tên bệnh cho một mã: danh mục trước, tên bên gọi gửi lên sau.

    Ưu tiên danh mục vì đây là bản chính thức của Bộ Y tế và Gateway là nơi duy
    nhất chắc chắn có đủ nó. HIS thường chỉ mang theo một bảng nhỏ, hoặc điền một
    nhãn chung khi không tra ra - và nhãn ấy đi thẳng lên trục thành tên bệnh.

    Tên bên gọi chỉ dùng khi mã không có trong danh mục, tức phần mở rộng mà bản
    danh mục hiện tại còn thiếu; lúc đó có tên còn hơn không.
    """
    if nlp_engine is not None and hasattr(nlp_engine, "db_index"):
        muc = tim_muc_danh_muc(nlp_engine, clean_code)
        if muc and muc.get("name_vi"):
            return muc["name_vi"]

    ten = (ten_ben_goi or "").strip()
    if ten and ten.lower() not in NHAN_KHONG_PHAI_TEN_BENH:
        return ten
    return None


def tim_muc_danh_muc(engine, code: str) -> Optional[dict]:
    """
    Tra một mục trong danh mục, chấp nhận cả mã CÓ và KHÔNG có dấu chấm.

    Danh mục khóa theo mã có chấm ("I21.9"), nhưng HIS thường cầm dạng viết liền
    ("I219") vì đó là dạng nhiều hệ thống cũ lưu - `code_no_dot` là trường hạng
    nhất trong danh mục chính vì vậy. Chỉ nhận một dạng thì nửa số lần tra trượt
    và HIS lại quay về điền nhãn chung, tức đúng lỗi này quay lại.
    """
    clean = sanitize_icd10_code(code)
    muc = engine.db_index.get(clean)
    if muc:
        return muc
    # "I219" -> "I21.9": dấu chấm trong ICD-10 luôn đứng sau ký tự thứ ba.
    if "." not in clean and len(clean) > 3:
        return engine.db_index.get(f"{clean[:3]}.{clean[3:]}")
    return None


@app.get("/api/icd10/{code}")
def tra_ten_icd10(code: str):
    """
    Tra tên bệnh theo mã ICD-10.

    Để HIS hiện đúng tên bệnh trên màn hình của chính nó mà không phải ôm theo
    một bản danh mục riêng. Cùng một nguồn với phần dựng Condition, nên tên bác
    sĩ thấy lúc chọn mã và tên nằm trên trục là một.
    """
    engine = _require_engine()
    muc = tim_muc_danh_muc(engine, code)
    if not muc:
        raise HTTPException(
            status_code=404,
            detail=f"Mã '{code}' không có trong danh mục ICD-10 ({len(engine.db)} mã).",
        )
    return {
        "code": muc["code"],
        "code_no_dot": muc.get("code_no_dot") or clean.replace(".", ""),
        "name_vi": muc.get("name_vi"),
        "name_en": muc.get("name_en"),
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

    ten_benh = ten_benh_theo_ma(clean_code, request.icd10_display)
    if not ten_benh:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Mã '{clean_code}' không có trong danh mục ICD-10 và bên gọi cũng "
                f"không gửi kèm tên bệnh. Không dựng Condition thiếu tên: chẩn đoán "
                f"không có tên bệnh thì bác sĩ tuyến sau đọc về không biết bệnh gì."
            ),
        )

    verification = request.verification_status or verification_status_for(request.confidence_score)
    facility_code, facility_name = resolve_facility(
        request.facility_code, request.facility_name)
    # Khóa Condition bám theo mã bệnh án nội viện, KHÔNG theo CCCD/BHYT: đó là
    # định danh duy nhất không đổi giữa các lần khám tại cùng một viện. Bám theo
    # CCCD thì lần khám sau - lúc hồ sơ đã bổ sung giấy tờ - sinh ra khóa khác và
    # cùng một chẩn đoán thành hai bản ghi trên trục.
    _, patient_key = patient_local_key(request.patient_id, facility_code)
    try:
        return build_fhir_condition_resource(
            patient_id=request.patient_id,
            patient_name=request.patient_name,
            raw_clinical_note=request.raw_clinical_note,
            icd10_code=clean_code,
            icd10_display=ten_benh,
            confidence_score=request.confidence_score,
            clinical_status=request.clinical_status or "active",
            verification_status=verification,
            condition_key=stable_condition_key(
                patient_key, clean_code, request.encounter_date, facility_code),
            engine_version=os.path.basename(nlp_engine.model_name) if nlp_engine else None,
            facility_code=facility_code,
            facility_name=facility_name,
            citizen_id=request.citizen_id,
            insurance_card=request.insurance_card,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Lỗi tạo tài nguyên FHIR: {exc}") from exc


def _fetch_conditions(count: int, facility_code: Optional[str] = None) -> List[dict]:
    """
    Đọc Condition mới nhất trên EMR Cloud, tùy chọn lọc theo cơ sở đã ghi nhận.

    Bộ lọc dựa trên `meta.tag` mà `build_fhir_condition_resource` gắn sẵn, nên
    không phải tải cả trục về rồi lọc ở phía Gateway.
    """
    url = f"{FHIR_SERVER_URL}/Condition?_sort=-_lastUpdated&_count={max(1, min(count, 50))}"
    if facility_code:
        url += f"&_tag={quote(f'{SYSTEM_FACILITY}|{facility_code}', safe='')}"
    try:
        response = requests.get(url, headers=_fhir_headers(), timeout=FHIR_TIMEOUT)
        response.raise_for_status()
        bundle = response.json()
    except requests.RequestException:
        print("Warning: EMR Cloud offline, returning empty synced list.")
        return []

    return [entry["resource"] for entry in bundle.get("entry", []) if entry.get("resource")]


@app.get("/api/fhir/sync", response_model=List[dict])
def get_synced_records(count: int = 10):
    """
    Đọc các Condition mới nhất trên EMR Cloud. Trả danh sách rỗng nếu EMR offline.

    KHÔNG lọc theo cơ sở: đây là khung nhìn "những gì đang có trên trục", và thấy
    được chẩn đoán do nơi khác lập chính là điều đang cần trình bày.
    """
    return _fetch_conditions(count)


@app.post("/api/fhir/sync")
def sync_fhir_record(record: Dict[str, Any]):
    """
    Đẩy một Condition lên EMR Cloud, kèm Patient tương ứng.

    Chấp nhận hai dạng thân yêu cầu:
        {...Condition...}                      - chỉ có chẩn đoán
        {"condition": {...}, "patient": {...}} - kèm thông tin hành chính bệnh nhân

    Dùng conditional update (`PUT /{Type}?identifier=...`) cho cả hai tài nguyên
    nên thao tác là idempotent mà không cần client tự đặt id: không khớp bản nào
    thì máy chủ tạo mới và tự cấp id, khớp đúng một bản thì cập nhật bản đó, khớp
    nhiều bản thì máy chủ trả 412 và không sửa gì.
    """
    payload = record.get("condition") if "condition" in record else record
    demographics = record.get("patient") or {}

    if not isinstance(payload, dict) or payload.get("resourceType") != "Condition":
        raise HTTPException(status_code=422, detail="Chỉ chấp nhận tài nguyên loại Condition.")

    cond_key = condition_key_of(payload)
    if not cond_key:
        raise HTTPException(
            status_code=422,
            detail=(f"Condition thiếu identifier hệ '{SYSTEM_CONDITION_KEY}'. "
                    f"Hãy sinh tài nguyên qua POST /api/fhir/condition."),
        )

    subject = payload.get("subject") or {}
    subject_ident = subject_identifier_of(payload)
    if not subject_ident:
        raise HTTPException(
            status_code=422,
            detail="Condition.subject.identifier thiếu định danh bệnh nhân (system + value).",
        )
    patient_system, patient_key = subject_ident

    # Tài nguyên có thể do client tự dựng chứ không qua /api/fhir/condition, nên
    # cửa kiểm định dạng phải đứng ở CẢ hai đường vào. Thiếu chỗ này thì một
    # Condition mang `cccd|tên bệnh` vẫn lên được trục và tách hồ sơ bệnh nhân.
    try:
        validate_identifier_value(patient_system, patient_key)
        demo_cccd = normalize_citizen_id(demographics.get("citizen_id"))
        demo_bhyt = normalize_insurance_card(demographics.get("insurance_card"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Cơ sở đã ghi chẩn đoán đọc từ chính tài nguyên (meta.tag / extension), vì
    # một bản Gateway có thể phục vụ nhiều bệnh viện. `resolve_facility` chặn
    # trường hợp tài nguyên mang mã lạ mà Gateway chưa bật chế độ nhiều cơ sở.
    ghi_nhan = facility_of(payload) or {"code": FACILITY_CODE, "name": FACILITY_NAME}
    facility_code, facility_name = resolve_facility(ghi_nhan.get("code"), ghi_nhan.get("name"))
    facility = {"code": facility_code, "name": facility_name}

    # Một nguồn duy nhất cho định danh bệnh nhân: `Condition.subject.identifier`.
    # Nếu thân yêu cầu kèm `patient` mâu thuẫn thì từ chối thẳng thay vì âm thầm
    # chọn một trong hai - chọn nhầm sẽ tạo Patient mới và để Condition mồ côi.
    # Mã cơ sở lấy từ tài nguyên chứ không từ cấu hình Gateway: dùng nhầm mã thì
    # `mrn_system` lệch và mọi hồ sơ không có CCCD đều bị coi là mâu thuẫn.
    # So khớp bằng bản ĐÃ chuẩn hóa. Dùng bản thô thì "079 095 010245" trong khối
    # 'patient' không khớp "079095010245" trong Condition do chính Gateway sinh,
    # và yêu cầu hợp lệ bị trả 422 oan.
    claimed_system, claimed_key = patient_match_key(
        (demographics.get("id") or "").strip() or patient_key,
        facility_code, demo_cccd, demo_bhyt,
    )
    if demographics and (claimed_system, claimed_key) != (patient_system, patient_key):
        raise HTTPException(
            status_code=422,
            detail=(f"Định danh bệnh nhân mâu thuẫn: khối 'patient' cho "
                    f"'{claimed_system}|{claimed_key}' nhưng Condition.subject.identifier "
                    f"mang '{patient_system}|{patient_key}'."),
        )

    # Thứ tự bắt buộc: Organization -> Patient -> Condition. Toàn vẹn tham chiếu
    # đã bật nên tài nguyên được trỏ tới phải tồn tại trước.
    try:
        r_org = requests.put(
            _conditional_url("Organization", SYSTEM_FACILITY, facility["code"]),
            json=build_fhir_organization_resource(facility["code"], facility["name"]),
            headers=_fhir_headers(content=True), timeout=FHIR_TIMEOUT,
        )
        r_org.raise_for_status()

        # Định danh dùng để TÌM bản cũ, có thể khác định danh "tốt nhất" của lần
        # này - xem `_select_patient_identifier`. Thân tài nguyên vẫn mang đầy đủ
        # định danh, nên sau lần đồng bộ này bản trên trục tra được bằng cả hai.
        #
        # Không có khối `patient` thì `Condition.subject.identifier` là tất cả
        # những gì ta biết: KHÔNG suy ra mã bệnh án từ nó, vì giá trị nằm đó có
        # thể là số CCCD và sẽ thành một mã bệnh án bịa.
        ung_vien = [(patient_system, patient_key)]
        if demographics:
            for cap in patient_identifier_candidates(
                (demographics.get("id") or "").strip(),
                facility_code, demo_cccd, demo_bhyt,
            ):
                if cap not in ung_vien:
                    ung_vien.append(cap)

        put_system, put_key = _select_patient_identifier(
            ung_vien, (patient_system, patient_key))

        r_patient = requests.put(
            _conditional_url("Patient", put_system, put_key),
            json=build_fhir_patient_resource(
                patient_id=(demographics.get("id") or "").strip() or patient_key,
                patient_name=demographics.get("name") or subject.get("display") or patient_key,
                gender=demographics.get("gender"),
                birth_date=demographics.get("birth_date"),
                facility_code=facility["code"],
                citizen_id=demo_cccd,
                insurance_card=demo_bhyt,
            ),
            headers=_fhir_headers(content=True), timeout=FHIR_TIMEOUT,
        )
        r_patient.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=(f"Không thể kết nối EMR Cloud (HAPI FHIR) tại {FHIR_SERVER_URL}. "
                    f"Hãy chạy 'docker compose up -d'. Chi tiết: {exc}"),
        ) from exc

    server_org_id = _resource_id_from(r_org, "Organization")
    server_patient_id = _resource_id_from(r_patient, "Patient")
    if not server_patient_id:
        raise HTTPException(
            status_code=502,
            detail="EMR Cloud không trả về id của Patient sau khi đồng bộ.",
        )

    # Trỏ Condition vào id THẬT vừa nhận, giữ nguyên phần `identifier` để tài
    # nguyên tự mô tả được nó nói về ai và do đâu ghi. Bỏ qua bước này thì tham
    # chiếu chỉ có identifier, và khi bật enforce_referential_integrity_on_write
    # máy chủ sẽ từ chối vì không có tham chiếu thật để kiểm.
    payload = dict(payload)
    payload.pop("id", None)
    payload["subject"] = {**subject, "reference": f"Patient/{server_patient_id}"}
    if server_org_id:
        payload["extension"] = [
            {**ext, "valueReference": {**ext["valueReference"],
                                       "reference": f"Organization/{server_org_id}"}}
            if ext.get("url") == EXT_SOURCE_FACILITY and ext.get("valueReference") else ext
            for ext in payload.get("extension") or []
        ]

    try:
        r_cond = requests.put(
            _conditional_url("Condition", SYSTEM_CONDITION_KEY, cond_key),
            json=payload, headers=_fhir_headers(content=True), timeout=FHIR_TIMEOUT,
        )
        r_cond.raise_for_status()
    except requests.RequestException as exc:
        detail = getattr(exc.response, "text", "")[:300] if getattr(exc, "response", None) else str(exc)
        raise HTTPException(status_code=502, detail=f"EMR Cloud từ chối Condition: {detail}") from exc

    server_condition_id = _resource_id_from(r_cond, "Condition")
    if not server_condition_id:
        raise HTTPException(
            status_code=502,
            detail="EMR Cloud không trả về id của Condition sau khi đồng bộ.",
        )

    return {
        "status": "success",
        "message": f"Đồng bộ thành công lên HAPI FHIR Server ({facility['name']})",
        "condition_id": server_condition_id,
        "condition_key": cond_key,
        "patient_id": server_patient_id,
        "patient_identifier": f"{patient_system}|{patient_key}",
        "facility_code": facility["code"],
        "facility_name": facility["name"],
        "location": f"{FHIR_SERVER_URL}/Condition/{server_condition_id}",
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


@app.delete("/api/fhir/condition/{condition_id}")
def delete_synced_condition(condition_id: str, facility: Optional[str] = None):
    """
    Gỡ MỘT Condition khỏi EMR Cloud, chỉ khi nó do chính cơ sở đang gọi lập ra.

    Dùng cho luồng bác sĩ sửa lại chẩn đoán đã liên thông. Mã ICD-10 nằm trong
    khóa nghiệp vụ, nên sửa mã sẽ sinh ra một tài nguyên khác chứ không đè lên
    bản cũ; không gỡ bản mang mã sai thì hồ sơ trên trục mang cả mã đúng lẫn mã
    sai và bên nhận không biết tin cái nào.

    Khác `DELETE /api/fhir/sync` ở chỗ đó xóa hàng loạt bản ghi mới nhất, không
    dùng được để sửa đúng một chẩn đoán.

    **Chốt cơ sở.** Bản trước nhận thẳng `condition_id` rồi xóa, không hỏi bản
    ghi đó của ai. Ghép với `GET /api/fhir/sync` - vốn cố ý trả chẩn đoán của
    MỌI cơ sở kèm `id`, vì thấy được hồ sơ nơi khác lập chính là điều cần trình
    bày - thì thành một đường xóa chéo: đọc danh sách, lấy id của bệnh viện B,
    gọi xóa. Đúng kiểu trộn dữ liệu mà mã cơ sở trong khóa nghiệp vụ sinh ra để
    chặn ở đường GHI, chỉ khác là nó nằm ở đường XÓA.

    Nay đường xóa một bản ghi theo đúng một chính sách với đường xóa hàng loạt:
    `facility` đi qua `resolve_facility`, nên khai mã khác chỉ được chấp nhận khi
    Gateway bật cờ nhiều cơ sở, không thì 403.
    """
    facility_code, _ = resolve_facility(facility)
    resource_id = to_fhir_id(condition_id)
    url = f"{FHIR_SERVER_URL}/Condition/{resource_id}"

    # Đọc trước khi xóa để biết bản ghi thuộc về ai. Tốn thêm một vòng gọi, nhưng
    # không có cách nào rẻ hơn: FHIR không có "xóa kèm điều kiện theo thẻ".
    try:
        read = requests.get(url, headers=_fhir_headers(), timeout=FHIR_TIMEOUT)
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"EMR Cloud không đọc được Condition '{condition_id}': {exc}",
        ) from exc

    # Bản ghi đã không còn trên trục thì mục tiêu coi như đã đạt: thao tác gỡ phải
    # lặp lại được mà không báo lỗi, vì HIS có thể gọi lại sau khi mất mạng.
    if read.status_code in (404, 410):
        return {
            "status": "success",
            "condition_id": condition_id,
            "facility_code": facility_code,
            "message": f"Condition {condition_id} vốn đã không còn trên EMR Cloud.",
        }

    try:
        read.raise_for_status()
        condition = read.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"EMR Cloud không đọc được Condition '{condition_id}': {exc}",
        ) from exc

    chu_so_huu = facility_of(condition)
    if chu_so_huu and chu_so_huu["code"] != facility_code:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Condition '{condition_id}' do cơ sở '{chu_so_huu['code']}' "
                f"({chu_so_huu['name']}) lập, cơ sở '{facility_code}' không được gỡ. "
                f"Mỗi cơ sở chỉ sửa hoặc gỡ chẩn đoán của chính mình."
            ),
        )

    # Bản ghi không mang thẻ cơ sở là dữ liệu có từ trước khi Gateway gắn thẻ.
    # Cho xóa, vì không quy được về cơ sở nào thì cũng không có ai để bảo vệ, và
    # từ chối thì những bản ghi này kẹt lại trên trục vĩnh viễn không dọn được.
    # Đổi lại, chúng KHÔNG được chốt cơ sở - đây là lý do nên dọn sạch dữ liệu cũ
    # thay vì để lẫn với dữ liệu đã có thẻ.

    try:
        response = requests.delete(url, headers=_fhir_headers(), timeout=FHIR_TIMEOUT)
        if response.status_code not in (404, 410):
            response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"EMR Cloud không gỡ được Condition '{condition_id}': {exc}",
        ) from exc

    return {
        "status": "success",
        "condition_id": condition_id,
        "facility_code": facility_code,
        "message": f"Đã gỡ Condition {condition_id} khỏi EMR Cloud.",
    }


@app.delete("/api/fhir/sync")
def delete_synced_records(facility: Optional[str] = None):
    """
    Xóa thật các Condition đã liên thông trên EMR Cloud.

    CHỈ xóa bản ghi của cơ sở đang gọi. Bản trước lấy 50 Condition mới nhất trên
    toàn trục rồi xóa sạch, nên bệnh viện A bấm "dọn dữ liệu demo" là xóa luôn
    chẩn đoán của bệnh viện B - đúng kiểu hỏng mà cả thiết kế này sinh ra để
    chặn, chỉ khác là nó nằm ở đường xóa chứ không phải đường ghi.

    Tham số `facility` đi qua `resolve_facility`, nên vẫn theo đúng một chính
    sách với đường ghi: khai mã khác chỉ được chấp nhận khi Gateway bật cờ nhiều
    cơ sở, không thì 403.
    """
    facility_code, _ = resolve_facility(facility)
    records = _fetch_conditions(50, facility_code)
    if not records:
        # Giữ nguyên hình dạng phản hồi với nhánh có xóa: phía gọi đọc `remaining`
        # để biết còn phải bấm tiếp hay không, thiếu trường là nó phải đoán.
        return {"status": "success", "deleted": 0, "failed": [], "remaining": 0,
                "facility_code": facility_code,
                "message": f"Không có bản ghi nào của {facility_code} để xóa."}

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

    # Mỗi lượt chỉ lấy được 50 bản mới nhất. Không nói ra thì người dùng thấy
    # "đã xóa 50" mà danh sách vẫn còn và tưởng chức năng hỏng.
    con_lai = len(_fetch_conditions(50, facility_code))

    return {
        "status": "success",
        "deleted": deleted,
        "failed": failed,
        "remaining": con_lai,
        "facility_code": facility_code,
        "message": f"Đã xóa {deleted} bản ghi của {facility_code} khỏi EMR Cloud."
                   + (f" {len(failed)} bản ghi lỗi." if failed else "")
                   + (f" Còn {con_lai} bản ghi, bấm lại để xóa tiếp." if con_lai else ""),
    }


# Phục vụ giao diện tĩnh. Mount ở cuối để không che các route API phía trên.
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="static")
else:
    print(f"Warning: Frontend directory '{frontend_dir}' not found. Serving API only.")
