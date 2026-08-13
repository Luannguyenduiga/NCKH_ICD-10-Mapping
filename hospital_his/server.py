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
FALLBACK_AUTO_CONFIRM = 80.0


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


def init_db(force: bool = False):
    with db_cursor(commit=True) as cursor:
        if force:
            cursor.execute("DROP TABLE IF EXISTS patients")
            cursor.execute("DROP TABLE IF EXISTS patient_conditions")

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
        # Một dòng chẩn đoán có thể chứa nhiều bệnh ("sỏi bàng quang, suy thận
        # cấp"), mỗi bệnh là một FHIR Condition riêng. Các cột icd10_* trên bảng
        # patients chỉ giữ được một mã nên chúng được giữ lại làm chẩn đoán
        # chính (để tương thích với phần hiển thị cũ), còn danh sách đầy đủ nằm ở
        # bảng này.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patient_conditions (
                patient_id TEXT NOT NULL,
                icd10_code TEXT NOT NULL,
                icd10_display TEXT,
                fragment TEXT,
                confidence_score REAL,
                verification_status TEXT,
                fhir_condition_id TEXT,
                status TEXT,
                message TEXT,
                sync_time TEXT,
                PRIMARY KEY (patient_id, icd10_code)
            )
        """)

        # Nâng cấp lược đồ cho cơ sở dữ liệu đã tạo từ phiên bản trước.
        existing = {row[1] for row in cursor.execute("PRAGMA table_info(patients)")}
        if "verification_status" not in existing:
            cursor.execute("ALTER TABLE patients ADD COLUMN verification_status TEXT")

        cursor.execute("SELECT COUNT(*) FROM patients")
        if cursor.fetchone()[0] == 0:
            pass # Không có dữ liệu mẫu, vì danh mục ICD-10 đã được chuẩn hóa từ file Excel.


class PatientCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    gender: str = Field(..., min_length=1, max_length=20)
    birth_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    clinical_note: str = Field(..., min_length=1, max_length=2000)


@app.get("/api/patients")
def get_patients(search_id: str = None):
    try:
        # If search_id is specified, we query locally or pull from EMR Cloud
        if search_id:
            search_id = search_id.strip()
            # 1. Search locally
            with db_cursor() as cursor:
                cursor.execute("SELECT * FROM patients WHERE id = ?", (search_id,))
                row = cursor.fetchone()
                if row:
                    patient = dict(row)
                    cursor.execute(
                        "SELECT * FROM patient_conditions WHERE patient_id = ?", (search_id,))
                    patient["conditions"] = [dict(r) for r in cursor.fetchall()]
                    return [patient]
            
            # 2. If not found locally, query HAPI FHIR (EMR Cloud)
            try:
                resp = requests.get(f"http://127.0.0.1:8090/fhir/Patient/{search_id}", timeout=3)
                if resp.status_code == 200:
                    resource = resp.json()
                    p_id = resource.get("id")
                    
                    # Extract name
                    name_list = resource.get("name", [])
                    p_name = ""
                    if name_list:
                        p_name = name_list[0].get("text")
                        if not p_name:
                            given = name_list[0].get("given", [])
                            family = name_list[0].get("family", "")
                            p_name = (" ".join(given) + " " + family).strip()
                    if not p_name:
                        p_name = "Bệnh nhân EMR"
                    
                    # Extract gender
                    gender = resource.get("gender", "")
                    p_gender = "Nam" if gender == "male" else ("Nữ" if gender == "female" else "Khác")
                    
                    # Extract birth date
                    p_birth = resource.get("birthDate", "---")
                    
                    patient = {
                        "id": p_id,
                        "name": p_name,
                        "gender": p_gender,
                        "birth_date": p_birth,
                        "clinical_note": "Hồ sơ liên thông từ VNPT HIS",
                        "sync_status": "Synced",
                        "sync_time": "EMR Cloud",
                        "conditions": []
                    }
                    
                    # Fetch conditions for this patient from HAPI FHIR
                    try:
                        resp_cond = requests.get(f"http://127.0.0.1:8090/fhir/Condition?subject=Patient/{search_id}", timeout=3)
                        if resp_cond.status_code == 200:
                            bundle_cond = resp_cond.json()
                            for entry in bundle_cond.get("entry", []):
                                cond_res = entry.get("resource", {})
                                code_coding = cond_res.get("code", {}).get("coding", [])
                                icd_code = code_coding[0].get("code", "") if code_coding else ""
                                icd_display = code_coding[0].get("display", "") if code_coding else ""
                                
                                notes = cond_res.get("note", [])
                                clinical_note = notes[0].get("text") if notes else ""
                                
                                patient["conditions"].append({
                                    "patient_id": p_id,
                                    "icd10_code": icd_code,
                                    "icd10_display": icd_display,
                                    "fragment": clinical_note or "Chẩn đoán liên thông",
                                    "confidence_score": 100.0,
                                    "verification_status": "confirmed",
                                    "fhir_condition_id": cond_res.get("id"),
                                    "status": "Synced"
                                })
                                patient["icd10_code"] = icd_code
                                patient["icd10_display"] = icd_display
                                if clinical_note:
                                    patient["clinical_note"] = clinical_note
                    except Exception as e:
                        print("Failed to fetch conditions from FHIR:", e)
                        
                    return [patient]
            except Exception as e:
                print("Failed to fetch patient from FHIR:", e)
                
            return []

        # If search_id is NOT specified, return local SQLite patients ONLY
        with db_cursor() as cursor:
            cursor.execute("SELECT * FROM patients ORDER BY id")
            patients = [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT * FROM patient_conditions ORDER BY patient_id, rowid")
            by_patient = {}
            for row in cursor.fetchall():
                condition = dict(row)
                by_patient.setdefault(condition["patient_id"], []).append(condition)
        for patient in patients:
            patient["conditions"] = by_patient.get(patient["id"], [])
        return patients
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi cơ sở dữ liệu: {exc}") from exc
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


def _save_conditions(patient_id: str, conditions: list):
    """
    Ghi đè toàn bộ danh sách chẩn đoán của một hồ sơ.

    Xóa trước rồi chèn lại thay vì chèn thêm: đồng bộ lại một hồ sơ đã sửa chẩn
    đoán phải bỏ đi những mã không còn đúng, nếu chỉ chèn thêm thì mã cũ nằm lại
    vĩnh viễn trong bệnh án.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_cursor(commit=True) as cursor:
        cursor.execute("DELETE FROM patient_conditions WHERE patient_id = ?", (patient_id,))
        cursor.executemany(
            "INSERT INTO patient_conditions (patient_id, icd10_code, icd10_display,"
            " fragment, confidence_score, verification_status, fhir_condition_id,"
            " status, message, sync_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (patient_id, c["code"], c["name_vi"], c["fragment"], c["confidence"],
                 c["verification_status"], c.get("fhir_condition_id"), c["status"],
                 c.get("message"), now)
                for c in conditions
            ],
        )


def _fetch_diagnoses(patient: dict) -> list:
    """
    Gọi Gateway chuẩn hóa và quy về danh sách chẩn đoán, mỗi chẩn đoán một mã chính.

    Gateway trả `diagnoses` khi tách được nhiều bệnh trong một dòng. Bản Gateway
    cũ chưa có trường này nên vẫn đọc `predictions` làm phương án dự phòng, coi
    cả câu là một chẩn đoán.
    """
    try:
        std_resp = requests.post(
            f"{GATEWAY_URL}/api/standardize",
            json={"query": patient["clinical_note"]},
            timeout=REQUEST_TIMEOUT,
        )
        std_resp.raise_for_status()
        data = std_resp.json()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=(f"Không thể kết nối Gateway tại {GATEWAY_URL}. "
                    f"Hãy đảm bảo Gateway đang chạy. Chi tiết: {exc}"),
        ) from exc

    groups = data.get("diagnoses") or []
    if not groups and data.get("predictions"):
        groups = [{"fragment": patient["clinical_note"], "predictions": data["predictions"]}]

    diagnoses, seen = [], set()
    for group in groups:
        candidates = group.get("predictions") or []
        if not candidates:
            continue
        best = candidates[0]
        # Hai vế cho ra cùng một mã thì chỉ liên thông một lần: EMR không nên
        # nhận hai Condition trùng mã cho cùng một bệnh nhân.
        if best["code"] in seen:
            continue
        seen.add(best["code"])
        confidence = float(best["confidence"])
        diagnoses.append({
            "fragment": group.get("fragment") or patient["clinical_note"],
            "code": best["code"],
            "code_no_dot": best.get("code_no_dot", ""),
            "name_vi": best["name_vi"],
            "confidence": confidence,
            "verification_status": best.get("suggested_verification_status", "unconfirmed"),
            "requires_review": bool(
                best.get("requires_review", confidence < FALLBACK_AUTO_CONFIRM)),
            "candidates": [
                {"code": p["code"], "code_no_dot": p.get("code_no_dot", ""),
                 "name_vi": p["name_vi"], "confidence": p["confidence"]}
                for p in candidates
            ],
        })
    return diagnoses


def _push_condition(patient: dict, diagnosis: dict) -> str:
    """Sinh FHIR Condition cho một chẩn đoán rồi đẩy lên EMR Cloud, trả id đã xác nhận."""
    fhir_resp = requests.post(
        f"{GATEWAY_URL}/api/fhir/condition",
        json={
            "patient_id": patient["id"],
            "patient_name": patient["name"],
            "raw_clinical_note": patient["clinical_note"],
            "icd10_code": diagnosis["code"],
            "icd10_display": diagnosis["name_vi"],
            "confidence_score": diagnosis["confidence"],
            "clinical_status": "active",
            "gender": patient["gender"],
            "birth_date": patient["birth_date"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    fhir_resp.raise_for_status()
    fhir_resource = fhir_resp.json()

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
    return sync_resp.json().get("condition_id") or fhir_resource.get("id")


@app.post("/api/sync/{patient_id}")
def sync_patient(patient_id: str):
    """
    Liên thông một hồ sơ: chuẩn hóa -> sinh FHIR -> đẩy lên EMR Cloud.

    Một dòng chẩn đoán có thể chứa nhiều bệnh, và mỗi bệnh phải thành một FHIR
    Condition riêng. Bản trước chỉ lấy `predictions[0]` nên bệnh thứ hai trong
    câu biến mất khỏi hồ sơ liên thông.

    Chốt chặn an toàn lâm sàng được xét cho TỪNG chẩn đoán: mã dưới ngưỡng dừng
    lại chờ bác sĩ duyệt, nhưng không chặn những mã đã đủ tin cậy trong cùng câu.
    """
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM patients WHERE id = ?", (patient_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ bệnh án")
    patient = dict(row)

    # Bước 1: chuẩn hóa chẩn đoán qua mô hình NLP của Gateway.
    diagnoses = _fetch_diagnoses(patient)
    if not diagnoses:
        _mark_sync_result(patient_id, "Failed")
        _save_conditions(patient_id, [])
        return {
            "patient_id": patient_id, "status": "Failed",
            "icd10_code": None, "icd10_display": None, "fhir_condition_id": None,
            "confidence_score": 0.0, "conditions": [],
            "message": "Không tìm thấy mã ICD-10 phù hợp cho chẩn đoán lâm sàng này.",
        }

    # Bước 2 và 3: sinh Condition rồi đẩy lên EMR Cloud, làm riêng từng chẩn đoán.
    results, transport_errors = [], []
    for diagnosis in diagnoses:
        record = dict(diagnosis)
        if diagnosis["requires_review"]:
            record.update({
                "status": "Needs Review",
                "fhir_condition_id": None,
                "message": (
                    f"Độ tin cậy {diagnosis['confidence']}% chưa đạt ngưỡng tự động "
                    f"({FALLBACK_AUTO_CONFIRM}%), chờ bác sĩ xác nhận."
                ),
            })
            results.append(record)
            continue
        try:
            condition_id = _push_condition(patient, diagnosis)
        except requests.HTTPError as exc:
            detail = exc.response.text[:300] if exc.response is not None else str(exc)
            transport_errors.append(("http", f"Gateway trả về lỗi: {detail}"))
            record.update({"status": "Failed", "fhir_condition_id": None,
                           "message": f"Gateway trả về lỗi: {detail}"})
        except requests.RequestException as exc:
            transport_errors.append(("net", f"Mất kết nối trong quá trình liên thông: {exc}"))
            record.update({"status": "Failed", "fhir_condition_id": None,
                           "message": f"Mất kết nối: {exc}"})
        else:
            record.update({
                "status": "Synced",
                "fhir_condition_id": condition_id,
                "message": f"Đã tạo FHIR Condition {condition_id}.",
            })
        results.append(record)

    synced = [r for r in results if r["status"] == "Synced"]
    review = [r for r in results if r["status"] == "Needs Review"]
    failed = [r for r in results if r["status"] == "Failed"]

    # Không có mã nào đi được vì lỗi hạ tầng: giữ nguyên hành vi cũ là ném lỗi,
    # để giao diện phân biệt được "hệ thống hỏng" với "chờ bác sĩ duyệt".
    if failed and not synced and not review:
        _mark_sync_result(patient_id, "Failed")
        _save_conditions(patient_id, results)
        kind, detail = transport_errors[0]
        raise HTTPException(status_code=502 if kind == "http" else 503, detail=detail)

    status = "Failed" if failed else ("Needs Review" if review else "Synced")
    # Chẩn đoán đầu câu đóng vai chẩn đoán chính trên bảng bệnh án; danh sách đầy
    # đủ nằm ở patient_conditions.
    primary = results[0]
    _mark_sync_result(
        patient_id, status,
        icd10_code=primary["code"], icd10_display=primary["name_vi"],
        fhir_condition_id=primary.get("fhir_condition_id"),
        confidence_score=primary["confidence"],
        verification_status=primary["verification_status"],
    )
    _save_conditions(patient_id, results)

    if status == "Synced":
        ids = ", ".join(r["fhir_condition_id"] for r in synced)
        message = (f"Liên thông thành công {len(synced)} chẩn đoán! "
                   f"Tài nguyên FHIR Condition: {ids}.")
    elif status == "Needs Review":
        message = (
            f"{len(synced)}/{len(results)} chẩn đoán đã liên thông; "
            f"{len(review)} mã chưa đạt ngưỡng tự động ({FALLBACK_AUTO_CONFIRM}%), "
            f"chờ bác sĩ xác nhận: {', '.join(r['code'] for r in review)}."
        )
    else:
        message = (
            f"{len(synced)}/{len(results)} chẩn đoán đã liên thông; "
            f"{len(failed)} mã gặp lỗi: {', '.join(r['code'] for r in failed)}."
        )

    return {
        "patient_id": patient_id, "status": status,
        "icd10_code": primary["code"], "icd10_display": primary["name_vi"],
        # Dạng liền không dấu chấm cho phần mềm viện dùng mã rút gọn; mã đẩy lên
        # EMR vẫn là dạng có dấu chấm theo chuẩn FHIR.
        "icd10_code_no_dot": primary.get("code_no_dot", ""),
        "fhir_condition_id": primary.get("fhir_condition_id"),
        "confidence_score": primary["confidence"],
        "verification_status": primary["verification_status"],
        "conditions": [
            {k: v for k, v in r.items() if k != "candidates"} for r in results
        ],
        "candidates": primary["candidates"],
        "message": message,
    }


@app.delete("/api/patients/{patient_id}")
def delete_patient(patient_id: str):
    try:
        with db_cursor(commit=True) as cursor:
            cursor.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Không tìm thấy bệnh nhân")
            # SQLite không tự thi hành khóa ngoại nếu chưa bật PRAGMA, nên phải
            # dọn tay để bảng chẩn đoán không giữ lại hồ sơ đã xóa.
            cursor.execute("DELETE FROM patient_conditions WHERE patient_id = ?", (patient_id,))
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
