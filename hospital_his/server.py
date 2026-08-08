# -*- coding: utf-8 -*-
"""
Hệ thống thông tin bệnh viện (HIS) mô phỏng.

Đóng vai bên gửi trong kịch bản liên thông: lưu bệnh án cục bộ bằng SQLite rồi
gọi SMIG Gateway để chuẩn hóa chẩn đoán, sinh HL7 FHIR và đẩy lên EMR Cloud.
"""

import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime

import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Simulated Hospital Information System (HIS)",
    description="Mock HIS system to test integration with SMIG Interoperability Gateway",
    version="2.0.0",
    lifespan=lifespan,
)

GATEWAY_URL = os.getenv("SMIG_GATEWAY_URL", "http://127.0.0.1:8000")
DB_PATH = os.path.join(os.path.dirname(__file__), "his_db.sqlite")
REQUEST_TIMEOUT = float(os.getenv("SMIG_HIS_TIMEOUT", "60"))

# Dưới ngưỡng này, hồ sơ KHÔNG được tự động liên thông mà phải chờ bác sĩ duyệt.
# Giá trị thực tế do Gateway quyết định (nlp/clinical_rules.py) và được đọc về
# lúc đồng bộ; đây chỉ là giá trị dự phòng khi Gateway không trả về chính sách.
FALLBACK_AUTO_CONFIRM = 85.0


ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "SMIG_HIS_ALLOWED_ORIGINS",
        "http://127.0.0.1:8085,http://localhost:8085",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@contextmanager
def db_cursor(commit: bool = False):
    """
    Mở kết nối SQLite và luôn đóng lại, kể cả khi xảy ra lỗi.

    Bản trước gọi conn.close() thủ công ở từng nhánh, nên khi một HTTPException
    được ném ra giữa chừng thì kết nối bị bỏ ngỏ.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn.cursor()
        if commit:
            conn.commit()
    finally:
        conn.close()


DEFAULT_PATIENTS = [
    ("MRN-2026-001", "Nguyễn Văn An", "Nam", "1980-05-14",
     "Bệnh nhân bị ĐTĐ typ 2 không biến chứng"),
    ("MRN-2026-002", "Trần Thị Bình", "Nữ", "1975-10-22",
     "Bệnh nhân có tiền sử THA vô căn kèm đau ngực nhẹ"),
    ("MRN-2026-003", "Phạm Minh Đức", "Nam", "1992-03-08",
     "Chẩn đoán lâm sàng: Hen phế quản cấp (HPQ cấp)"),
    ("MRN-2026-004", "Lê Thị Hồng", "Nữ", "1968-12-01",
     "Theo dõi trào ngược dạ dày thực quản (GERD) mức độ nhẹ"),
    ("MRN-2026-005", "Vũ Hoàng Long", "Nam", "1985-07-30",
     "Chẩn đoán: Suy thận mạn giai đoạn 3 (CKD)"),
    ("MRN-2026-006", "Hoàng Anh Thư", "Nữ", "2000-09-15",
     "Cơn đau dạ dày cấp nghi do loét dạ dày tá tràng"),
]


def init_db(force: bool = False):
    with db_cursor(commit=True) as cursor:
        if force:
            cursor.execute("DROP TABLE IF EXISTS patients")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                gender TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                clinical_note TEXT NOT NULL,
                icd10_code TEXT,
                icd10_display TEXT,
                fhir_condition_id TEXT,
                confidence_score REAL,
                verification_status TEXT,
                sync_status TEXT DEFAULT 'Unsynced',
                sync_time TEXT
            )
        """)
        # Nâng cấp lược đồ cho cơ sở dữ liệu đã tạo từ phiên bản trước.
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(patients)")}
        if "verification_status" not in existing:
            cursor.execute("ALTER TABLE patients ADD COLUMN verification_status TEXT")

        cursor.execute("SELECT COUNT(*) FROM patients")
        if cursor.fetchone()[0] == 0:
            cursor.executemany(
                "INSERT INTO patients (id, name, gender, birth_date, clinical_note)"
                " VALUES (?, ?, ?, ?, ?)",
                DEFAULT_PATIENTS,
            )


class PatientCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    gender: str = Field(..., min_length=1, max_length=20)
    birth_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    clinical_note: str = Field(..., min_length=1, max_length=2000)


@app.get("/api/patients")
def get_patients():
    try:
        with db_cursor() as cursor:
            cursor.execute("SELECT * FROM patients ORDER BY id")
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi cơ sở dữ liệu: {exc}") from exc


@app.post("/api/patients")
def create_patient(patient: PatientCreate):
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute(
                "INSERT INTO patients (id, name, gender, birth_date, clinical_note, sync_status)"
                " VALUES (?, ?, ?, ?, ?, 'Unsynced')",
                (patient.id, patient.name, patient.gender, patient.birth_date, patient.clinical_note),
            )
        return {"status": "success", "message": "Đã tạo hồ sơ bệnh án thành công."}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=400, detail=f"Mã bệnh án '{patient.id}' đã tồn tại.") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi cơ sở dữ liệu: {exc}") from exc


def _mark_sync_result(patient_id: str, status: str, **fields):
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values())
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            f"UPDATE patients SET sync_status = ?, sync_time = ?"
            + (f", {columns}" if columns else "")
            + " WHERE id = ?",
            [status, datetime.now().strftime("%Y-%m-%d %H:%M:%S")] + values + [patient_id],
        )


@app.post("/api/sync/{patient_id}")
def sync_patient(patient_id: str):
    """
    Liên thông một hồ sơ: chuẩn hóa -> sinh FHIR -> đẩy lên EMR Cloud.

    Điểm khác biệt quan trọng so với bản trước: kết quả có độ tin cậy thấp KHÔNG
    còn được tự động gắn nhãn "confirmed" và đẩy thẳng lên EMR. Mã dưới ngưỡng
    tự động sẽ dừng lại ở trạng thái chờ bác sĩ duyệt.
    """
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM patients WHERE id = ?", (patient_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ bệnh án")
    patient = dict(row)

    try:
        # Bước 1: chuẩn hóa chẩn đoán qua mô hình NLP của Gateway.
        std_resp = requests.post(
            f"{GATEWAY_URL}/api/standardize",
            json={"query": patient["clinical_note"]},
            timeout=REQUEST_TIMEOUT,
        )
        std_resp.raise_for_status()
        predictions = std_resp.json().get("predictions", [])
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=(f"Không thể kết nối Gateway tại {GATEWAY_URL}. "
                    f"Hãy đảm bảo Gateway đang chạy. Chi tiết: {exc}"),
        ) from exc

    if not predictions:
        _mark_sync_result(patient_id, "Failed")
        return {
            "patient_id": patient_id, "status": "Failed",
            "icd10_code": None, "icd10_display": None, "fhir_condition_id": None,
            "confidence_score": 0.0,
            "message": "Không tìm thấy mã ICD-10 phù hợp cho chẩn đoán lâm sàng này.",
        }

    best = predictions[0]
    confidence = float(best["confidence"])
    verification = best.get("suggested_verification_status", "unconfirmed")

    # Chốt chặn an toàn lâm sàng: dưới ngưỡng thì dừng, không liên thông tự động.
    if best.get("requires_review", confidence < FALLBACK_AUTO_CONFIRM):
        _mark_sync_result(
            patient_id, "Needs Review",
            icd10_code=best["code"], icd10_display=best["name_vi"],
            confidence_score=confidence, verification_status=verification,
        )
        return {
            "patient_id": patient_id, "status": "Needs Review",
            "icd10_code": best["code"], "icd10_display": best["name_vi"],
            "fhir_condition_id": None, "confidence_score": confidence,
            "verification_status": verification,
            "candidates": [
                {"code": p["code"], "name_vi": p["name_vi"], "confidence": p["confidence"]}
                for p in predictions
            ],
            "message": (
                f"Độ tin cậy {confidence}% chưa đạt ngưỡng tự động ({FALLBACK_AUTO_CONFIRM}%). "
                f"Hồ sơ được giữ lại chờ bác sĩ xác nhận mã ICD-10 trước khi liên thông."
            ),
        }

    try:
        # Bước 2: sinh HL7 FHIR Condition Resource.
        fhir_resp = requests.post(
            f"{GATEWAY_URL}/api/fhir/condition",
            json={
                "patient_id": patient["id"],
                "patient_name": patient["name"],
                "raw_clinical_note": patient["clinical_note"],
                "icd10_code": best["code"],
                "icd10_display": best["name_vi"],
                "confidence_score": confidence,
                "clinical_status": "active",
                "gender": patient["gender"],
                "birth_date": patient["birth_date"],
            },
            timeout=REQUEST_TIMEOUT,
        )
        fhir_resp.raise_for_status()
        fhir_resource = fhir_resp.json()

        # Bước 3: đẩy lên EMR Cloud, kèm thông tin hành chính bệnh nhân.
        sync_resp = requests.post(
            f"{GATEWAY_URL}/api/fhir/sync",
            json={
                "condition": fhir_resource,
                "patient": {
                    "id": patient["id"], "name": patient["name"],
                    "gender": patient["gender"], "birth_date": patient["birth_date"],
                },
            },
            timeout=REQUEST_TIMEOUT,
        )
        sync_resp.raise_for_status()
        # Id do EMR Cloud xác nhận, không phải id tự đoán -> truy vết được thật.
        condition_id = sync_resp.json().get("condition_id") or fhir_resource.get("id")
    except requests.HTTPError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        _mark_sync_result(patient_id, "Failed")
        raise HTTPException(status_code=502, detail=f"Gateway trả về lỗi: {detail}") from exc
    except requests.RequestException as exc:
        _mark_sync_result(patient_id, "Failed")
        raise HTTPException(status_code=503, detail=f"Mất kết nối trong quá trình liên thông: {exc}") from exc

    _mark_sync_result(
        patient_id, "Synced",
        icd10_code=best["code"], icd10_display=best["name_vi"],
        fhir_condition_id=condition_id, confidence_score=confidence,
        verification_status=verification,
    )
    return {
        "patient_id": patient_id, "status": "Synced",
        "icd10_code": best["code"], "icd10_display": best["name_vi"],
        "fhir_condition_id": condition_id, "confidence_score": confidence,
        "verification_status": verification,
        "message": f"Liên thông thành công! Tài nguyên FHIR Condition: {condition_id}.",
    }


@app.delete("/api/patients/{patient_id}")
def delete_patient(patient_id: str):
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Không tìm thấy bệnh nhân")
        return {"status": "success", "message": f"Đã xóa hồ sơ bệnh nhân {patient_id}."}
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi xóa bệnh nhân: {exc}") from exc


@app.post("/api/reset")
def reset_database():
    try:
        init_db(force=True)
        return {"status": "success", "message": "Cơ sở dữ liệu HIS đã được cài đặt lại về mặc định."}
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi reset database: {exc}") from exc


frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="static")
else:
    print(f"Warning: Frontend directory '{frontend_dir}' not found at startup.")

if __name__ == "__main__":
    uvicorn.run("server:app", host="127.0.0.1", port=8085, reload=True)
