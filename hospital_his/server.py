# -*- coding: utf-8 -*-
"""
Hệ thống thông tin bệnh viện (HIS) mô phỏng.

Đóng vai bên gửi trong kịch bản liên thông: lưu bệnh án cục bộ bằng SQLite rồi
gọi SMIG Gateway để chuẩn hóa chẩn đoán, sinh HL7 FHIR và đẩy lên EMR Cloud.
"""

import os
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from typing import Optional

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
FHIR_SERVER_URL = os.getenv("SMIG_FHIR_SERVER_URL", "http://127.0.0.1:8090/fhir")
# Bệnh án cục bộ của RIÊNG bệnh viện này. Chạy hai bản HIS trên cùng một máy để
# demo liên thông thì mỗi bản phải có tệp riêng, nếu không hai "bệnh viện" cùng
# đọc ghi một cơ sở dữ liệu và nhìn thấy y nguyên danh sách bệnh nhân của nhau -
# hỏng hẳn kịch bản mà bản demo muốn trình bày.
DB_PATH = os.getenv("SMIG_HIS_DB") or os.path.join(
    os.path.dirname(__file__), "his_db.sqlite")
REQUEST_TIMEOUT = float(os.getenv("SMIG_HIS_TIMEOUT", "60"))

# Mã cơ sở khám chữa bệnh của HIS này. Mã bệnh án chỉ có nghĩa trong phạm vi một
# bệnh viện nên system của identifier mang luôn mã cơ sở.
#
# HIS gửi mã này kèm mỗi yêu cầu sinh Condition. Khi mỗi bệnh viện chạy một bản
# Gateway riêng (triển khai thật) thì mã phải TRÙNG với SMIG_FACILITY_CODE của
# Gateway đó, nếu không Gateway trả 403. Khi thử nghiệm cục bộ mà một bản Gateway
# phục vụ cả hai bệnh viện (Gateway bật SMIG_ALLOW_CLIENT_FACILITY=1), chính mã
# này là thứ giữ cho hồ sơ hai nơi không lẫn vào nhau.
FACILITY_CODE = os.getenv("SMIG_FACILITY_CODE", "BV-DEMO-01")
FACILITY_NAME = os.getenv("SMIG_FACILITY_NAME", "Bệnh viện Demo SMIG")

# Mã bệnh án nằm ở Patient.identifier chứ không còn là id tài nguyên: từ khi EMR
# tự cấp id, tra cứu phải đi qua identifier mới tìm ra hồ sơ.
def _to_fhir_id(value: str) -> str:
    """
    Chuẩn hóa mã cơ sở đúng như Gateway làm (`fhir_helper.to_fhir_id`).

    Gateway lọc mã cơ sở qua hàm này trước khi dựng `identifier.system`. HIS ghép
    thẳng mã thô thì với mã sạch như "BV-A-001" hai bên vẫn trùng, nhưng mã có
    dấu gạch dưới hay khoảng trắng sẽ cho hai chuỗi khác nhau - và tra cứu bệnh
    nhân trên EMR im lặng trả về rỗng chứ không báo lỗi gì.
    """
    cleaned = re.sub(r"[^A-Za-z0-9.\-]", "-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return (cleaned or "khong-ro-co-so")[:64]


FHIR_MRN_SYSTEM = f"https://smig.nckh.vn/fhir/identifier/mrn/{_to_fhir_id(FACILITY_CODE)}"
FHIR_CCCD_SYSTEM = "https://smig.nckh.vn/fhir/identifier/cccd"
FHIR_BHYT_SYSTEM = "https://smig.nckh.vn/fhir/identifier/bhyt"
FHIR_FACILITY_SYSTEM = "https://smig.nckh.vn/fhir/identifier/co-so-kcb"

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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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
                clinical_status TEXT DEFAULT 'active',
                PRIMARY KEY (patient_id, icd10_code)
            )
        """)

        # Nâng cấp lược đồ cho cơ sở dữ liệu đã tạo từ phiên bản trước.
        cond_columns = {row[1] for row in cursor.execute("PRAGMA table_info(patient_conditions)")}
        # Trạng thái lâm sàng theo FHIR. Không lưu thì màn hình sửa chẩn đoán
        # không biết bệnh đang ở trạng thái nào, mỗi lần mở lại hiện "Đang mắc"
        # và bác sĩ vừa đánh dấu "Đã khỏi" xong sẽ tưởng thao tác không ăn.
        if "clinical_status" not in cond_columns:
            cursor.execute(
                "ALTER TABLE patient_conditions ADD COLUMN clinical_status TEXT DEFAULT 'active'")

        existing = {row[1] for row in cursor.execute("PRAGMA table_info(patients)")}
        if "verification_status" not in existing:
            cursor.execute("ALTER TABLE patients ADD COLUMN verification_status TEXT")
        # Định danh cấp quốc gia. Để trống vẫn liên thông được (khi đó khóa là mã
        # bệnh án kèm mã cơ sở), nhưng có thì trục mới nhận ra cùng một người
        # khám ở hai bệnh viện khác nhau.
        if "citizen_id" not in existing:
            cursor.execute("ALTER TABLE patients ADD COLUMN citizen_id TEXT")
        if "insurance_card" not in existing:
            cursor.execute("ALTER TABLE patients ADD COLUMN insurance_card TEXT")

        cursor.execute("SELECT COUNT(*) FROM patients")
        if cursor.fetchone()[0] == 0:
            pass # Không có dữ liệu mẫu, vì danh mục ICD-10 đã được chuẩn hóa từ file Excel.


class PatientCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=200)
    gender: str = Field(..., min_length=1, max_length=20)
    birth_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    clinical_note: str = Field(..., min_length=1, max_length=2000)
    # Định danh toàn quốc, không bắt buộc. Có thì trục nhận ra cùng một người
    # khám ở nhiều bệnh viện; không có thì hồ sơ chỉ quy được trong nội viện.
    citizen_id: Optional[str] = Field(None, max_length=20)
    insurance_card: Optional[str] = Field(None, max_length=20)


class DiagnosisCreate(BaseModel):
    """Một dòng bệnh án mới cho bệnh nhân đã có hồ sơ."""
    clinical_note: str = Field(..., min_length=1, max_length=2000)


# Trạng thái lâm sàng hợp lệ theo FHIR R4 (condition-clinical). Bác sĩ sửa hồ sơ
# thường không đổi mã bệnh mà đổi trạng thái: bệnh cũ đã khỏi thì phải chuyển
# sang `resolved` chứ không phải xóa khỏi bệnh án - lịch sử điều trị vẫn cần.
CLINICAL_STATUSES = {
    "active", "recurrence", "relapse", "inactive", "remission", "resolved",
}


class ConditionUpdate(BaseModel):
    """
    Bác sĩ sửa lại một chẩn đoán đã ghi nhận.

    Mã ICD-10 có thể đổi (mô hình NLP suy sai, bác sĩ chọn lại mã đúng), tên bệnh
    đi theo mã, và trạng thái lâm sàng đổi độc lập với mã.
    """
    icd10_code: str = Field(..., min_length=2, max_length=16)
    icd10_display: str = Field(..., min_length=1, max_length=300)
    clinical_status: str = Field("active", min_length=1, max_length=20)
    # Nguyên văn chẩn đoán của riêng bệnh này. Bỏ trống thì giữ nguyên đoạn đã lưu.
    fragment: Optional[str] = Field(None, max_length=2000)


@app.get("/api/config")
def get_config():
    """
    Cấu hình cho giao diện.

    Trình duyệt không đọc được biến môi trường, nên trước đây app.js ghi cứng
    địa chỉ Gateway - chạy hai bệnh viện trên hai cổng là hỏng ngay. Giao diện
    lấy cấu hình từ đây thay vì tự đoán.
    """
    return {
        "gateway_url": GATEWAY_URL,
        "facility_code": FACILITY_CODE,
        "facility_name": FACILITY_NAME,
        "fhir_server": FHIR_SERVER_URL,
    }


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
                    # Hồ sơ nội viện: bác sĩ được sửa chẩn đoán. Xem chú thích ở
                    # nhánh EMR Cloud bên dưới.
                    patient["is_local"] = True
                    return [patient]
            
            # 2. If not found locally, query HAPI FHIR (EMR Cloud)
            try:
                # Thử lần lượt ba loại định danh. Mã bệnh án chỉ có nghĩa trong
                # NỘI BỘ viện này, nên với bệnh nhân chuyển từ nơi khác tới thì nó
                # luôn trượt - bác sĩ chỉ có CCCD hoặc thẻ BHYT trong tay. Hỏi mỗi
                # namespace mã bệnh án là ca chuyển tuyến không bao giờ tra ra
                # bệnh sử, đúng vào lúc cần bệnh sử nhất.
                entries = []
                for he in (FHIR_MRN_SYSTEM, FHIR_CCCD_SYSTEM, FHIR_BHYT_SYSTEM):
                    resp = requests.get(
                        f"{FHIR_SERVER_URL}/Patient",
                        params={"identifier": f"{he}|{search_id}"},
                        timeout=3,
                    )
                    entries = resp.json().get("entry", []) if resp.status_code == 200 else []
                    if entries:
                        break
                if entries:
                    resource = entries[0].get("resource", {})
                    # Id thật do EMR cấp - dùng để truy các Condition của hồ sơ này.
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
                    
                    # Định danh toàn quốc lấy ngược từ EMR: nhờ nó bệnh viện này
                    # tiếp nhận lại bệnh nhân của nơi khác mà vẫn quy đúng hồ sơ.
                    idents = {i.get("system"): i.get("value")
                              for i in resource.get("identifier", []) if isinstance(i, dict)}

                    patient = {
                        # Giao diện và các thao tác sau vẫn thao tác theo mã bệnh
                        # án, không theo id nội bộ của EMR. Tìm bằng CCCD thì
                        # `search_id` là số CCCD chứ không phải mã bệnh án, nên ưu
                        # tiên mã bệnh án đọc từ hồ sơ - lấy số CCCD làm "mã bệnh
                        # án" sẽ hiển thị sai và không tra lại được.
                        "id": idents.get(FHIR_MRN_SYSTEM) or search_id,
                        "citizen_id": idents.get(FHIR_CCCD_SYSTEM),
                        "insurance_card": idents.get(FHIR_BHYT_SYSTEM),
                        "name": p_name,
                        "gender": p_gender,
                        "birth_date": p_birth,
                        "clinical_note": "Hồ sơ liên thông từ trục dữ liệu y tế",
                        "sync_status": "Synced",
                        "sync_time": "EMR Cloud",
                        # Hồ sơ chỉ đang ĐỌC từ trục, chưa tiếp nhận vào viện này
                        # nên không có bản ghi cục bộ để sửa. Giao diện dựa vào cờ
                        # này để hiện dạng chỉ đọc thay vì gọi API sửa rồi nhận 404.
                        "is_local": False,
                        "conditions": []
                    }
                    
                    # Fetch conditions for this patient from HAPI FHIR
                    try:
                        resp_cond = requests.get(
                            f"{FHIR_SERVER_URL}/Condition",
                            params={"subject": f"Patient/{p_id}"},
                            timeout=3,
                        )
                        if resp_cond.status_code == 200:
                            bundle_cond = resp_cond.json()
                            for entry in bundle_cond.get("entry", []):
                                cond_res = entry.get("resource", {})
                                code_coding = cond_res.get("code", {}).get("coding", [])
                                icd_code = code_coding[0].get("code", "") if code_coding else ""
                                icd_display = code_coding[0].get("display", "") if code_coding else ""
                                
                                notes = cond_res.get("note", [])
                                clinical_note = notes[0].get("text") if notes else ""

                                # Cơ sở đã ghi nhận chẩn đoán, để phân biệt bản
                                # ghi của bệnh viện mình với bản ghi liên thông
                                # từ bệnh viện khác.
                                nguon = next(
                                    (t for t in (cond_res.get("meta") or {}).get("tag", [])
                                     if t.get("system") == FHIR_FACILITY_SYSTEM), {})

                                patient["conditions"].append({
                                    "patient_id": patient["id"],
                                    "facility_code": nguon.get("code"),
                                    "facility_name": nguon.get("display") or nguon.get("code"),
                                    "la_ngoai_vien": bool(
                                        nguon.get("code") and nguon["code"] != FACILITY_CODE),
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
            patient["is_local"] = True
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
                "INSERT INTO patients (id, name, gender, birth_date, clinical_note,"
                " citizen_id, insurance_card, sync_status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'Unsynced')",
                (patient.id, patient.name, patient.gender, patient.birth_date,
                 patient.clinical_note,
                 (patient.citizen_id or "").strip() or None,
                 (patient.insurance_card or "").strip() or None),
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


def _save_conditions(patient_id: str, conditions: list, replace: bool = True):
    """
    Lưu danh sách chẩn đoán của một hồ sơ.

    `replace=True` (mặc định, dùng cho nút Đồng bộ): xóa trước rồi chèn lại. Đồng
    bộ lại một hồ sơ đã sửa chẩn đoán phải bỏ đi những mã không còn đúng, nếu chỉ
    chèn thêm thì mã cũ nằm lại vĩnh viễn trong bệnh án.

    `replace=False` (dùng cho "Thêm chẩn đoán"): giữ nguyên các chẩn đoán cũ và
    chèn thêm mã mới. Bệnh nhân tái khám phát hiện thêm bệnh thì bệnh cũ vẫn còn.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_cursor(commit=True) as cursor:
        if replace:
            cursor.execute("DELETE FROM patient_conditions WHERE patient_id = ?", (patient_id,))
        cursor.executemany(
            # OR REPLACE vì khóa chính là (patient_id, icd10_code): chẩn đoán lại
            # đúng mã cũ thì cập nhật dòng đó thay vì báo lỗi trùng khóa.
            "INSERT OR REPLACE INTO patient_conditions (patient_id, icd10_code, icd10_display,"
            " fragment, confidence_score, verification_status, fhir_condition_id,"
            " status, message, sync_time, clinical_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (patient_id, c["code"], c["name_vi"], c["fragment"], c["confidence"],
                 c["verification_status"], c.get("fhir_condition_id"), c["status"],
                 c.get("message"), now, c.get("clinical_status") or "active")
                for c in conditions
            ],
        )


def _retire_on_emr(condition_id: str) -> Optional[str]:
    """
    Gỡ một Condition đã liên thông khỏi EMR Cloud.

    Trả về None nếu gỡ được, hoặc chuỗi mô tả lỗi nếu không. Cố ý KHÔNG ném ngoại
    lệ: bản ghi mới đã lên trục rồi mới tới lượt gỡ bản cũ, hỏng ở bước này mà
    dựng ngược cả thao tác thì bệnh án cục bộ và trục lại lệch nhau thêm. Thay
    vào đó lỗi được trả ngược lên giao diện để bác sĩ biết còn bản ghi thừa.
    """
    try:
        # Khai mã cơ sở để Gateway biết ai đang gỡ. Gateway chỉ cho gỡ chẩn đoán
        # do chính cơ sở này lập; thiếu tham số thì nó lấy mã cấu hình của chính
        # nó, và ở chế độ một Gateway phục vụ nhiều bệnh viện, HIS sẽ bị 403 khi
        # gỡ đúng bản ghi của mình.
        resp = requests.delete(
            f"{GATEWAY_URL}/api/fhir/condition/{condition_id}",
            params={"facility": FACILITY_CODE}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return None
    except requests.RequestException as exc:
        return (f"Bệnh án cục bộ đã cập nhật, nhưng KHÔNG gỡ được bản ghi cũ "
                f"{condition_id} trên EMR Cloud: {exc}. Hãy kiểm tra lại trên trục.")


def _refresh_primary_diagnosis(patient_id: str):
    """
    Tính lại chẩn đoán chính và trạng thái liên thông của hồ sơ từ bảng chẩn đoán.

    Bảng `patients` chỉ giữ được một mã ICD-10 làm chẩn đoán chính. Sửa hoặc xóa
    một chẩn đoán mà không tính lại thì bệnh án còn trỏ vào mã vừa bị bỏ đi, và
    trạng thái liên thông vẫn báo "Đã liên thông" cho một hồ sơ không còn mã nào.
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "SELECT * FROM patient_conditions WHERE patient_id = ? ORDER BY rowid",
            (patient_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        if not rows:
            cursor.execute(
                "UPDATE patients SET icd10_code = NULL, icd10_display = NULL,"
                " fhir_condition_id = NULL, confidence_score = NULL,"
                " verification_status = NULL, sync_status = 'Unsynced', sync_time = ?"
                " WHERE id = ?",
                (now, patient_id),
            )
            return

        statuses = {r.get("status") for r in rows}
        # Một mã hỏng là cả hồ sơ chưa liên thông trọn vẹn, nên trạng thái xấu
        # nhất thắng - báo "Đã liên thông" khi còn mã chờ duyệt là che mất việc
        # bác sĩ phải làm.
        sync_status = ("Failed" if "Failed" in statuses
                       else "Needs Review" if "Needs Review" in statuses
                       else "Synced")
        primary = rows[0]
        cursor.execute(
            "UPDATE patients SET icd10_code = ?, icd10_display = ?, fhir_condition_id = ?,"
            " confidence_score = ?, verification_status = ?, sync_status = ?, sync_time = ?"
            " WHERE id = ?",
            (primary["icd10_code"], primary["icd10_display"], primary["fhir_condition_id"],
             primary["confidence_score"], primary["verification_status"],
             sync_status, now, patient_id),
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
    """
    Sinh FHIR Condition cho một chẩn đoán rồi đẩy lên EMR Cloud, trả id đã xác nhận.

    `diagnosis` nhận thêm ba khóa tùy chọn cho luồng bác sĩ sửa hồ sơ: `note`
    (nguyên văn riêng của chẩn đoán này), `clinical_status` và
    `verification_status_override`. Không có khóa nào thì payload gửi Gateway
    giống hệt trước - luồng NLP tự động không đổi một chút nào.
    """
    fhir_resp = requests.post(
        f"{GATEWAY_URL}/api/fhir/condition",
        json={
            "patient_id": patient["id"],
            "patient_name": patient["name"],
            # Bác sĩ sửa một chẩn đoán thì nguyên văn cần lưu là mô tả của riêng
            # bệnh đó, không phải cả dòng bệnh án gộp nhiều bệnh.
            "raw_clinical_note": diagnosis.get("note") or patient["clinical_note"],
            "icd10_code": diagnosis["code"],
            "icd10_display": diagnosis["name_vi"],
            "confidence_score": diagnosis["confidence"],
            # CHỈ đặt khi bác sĩ tự quyết. Luồng tự động để trống để Gateway suy
            # ra từ độ tin cậy đúng như trước: gửi lại giá trị HIS đang giữ nghe
            # thì tương đương, nhưng nếu Gateway là bản cũ chưa trả
            # `suggested_verification_status`, HIS sẽ gửi "unconfirmed" và ghi đè
            # mất kết luận của chính Gateway.
            "verification_status": diagnosis.get("verification_status_override"),
            "clinical_status": diagnosis.get("clinical_status") or "active",
            "gender": patient["gender"],
            "birth_date": patient["birth_date"],
            # Định danh toàn quốc, có thì gửi. Nhờ nó cùng một người khám ở nhiều
            # bệnh viện vẫn quy về một hồ sơ trên trục thay vì mỗi nơi một bản.
            "citizen_id": patient.get("citizen_id"),
            "insurance_card": patient.get("insurance_card"),
            # Bệnh viện đang ghi hồ sơ. Cần thiết khi một bản Gateway phục vụ
            # nhiều bệnh viện: thiếu nó thì mọi chẩn đoán trên trục đều mang tên
            # cơ sở cấu hình trong Gateway, và không còn phân biệt được nơi ghi.
            "facility_code": FACILITY_CODE,
            "facility_name": FACILITY_NAME,
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
                "citizen_id": patient.get("citizen_id"),
                "insurance_card": patient.get("insurance_card"),
            },
        },
        timeout=REQUEST_TIMEOUT,
    )
    sync_resp.raise_for_status()
    # Id do EMR Cloud xác nhận, không phải id tự đoán -> truy vết được thật.
    return sync_resp.json().get("condition_id") or fhir_resource.get("id")


def _sync_patient_record(patient_id: str, clinical_note: Optional[str] = None,
                         replace_existing: bool = True) -> dict:
    """
    Liên thông một hồ sơ: chuẩn hóa -> sinh FHIR -> đẩy lên EMR Cloud.

    Một dòng chẩn đoán có thể chứa nhiều bệnh, và mỗi bệnh phải thành một FHIR
    Condition riêng. Bản trước chỉ lấy `predictions[0]` nên bệnh thứ hai trong
    câu biến mất khỏi hồ sơ liên thông.

    Chốt chặn an toàn lâm sàng được xét cho TỪNG chẩn đoán: mã dưới ngưỡng dừng
    lại chờ bác sĩ duyệt, nhưng không chặn những mã đã đủ tin cậy trong cùng câu.

    `clinical_note` khác None để chẩn đoán một dòng bệnh án mới mà không đụng tới
    dòng đã lưu; `replace_existing=False` để giữ lại các chẩn đoán trước đó.
    """
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM patients WHERE id = ?", (patient_id,))
        row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ bệnh án")
    patient = dict(row)
    if clinical_note:
        patient["clinical_note"] = clinical_note

    # Bước 1: chuẩn hóa chẩn đoán qua mô hình NLP của Gateway.
    diagnoses = _fetch_diagnoses(patient)
    if not diagnoses:
        _mark_sync_result(patient_id, "Failed")
        _save_conditions(patient_id, [], replace=replace_existing)
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
        _save_conditions(patient_id, results, replace=replace_existing)
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
    _save_conditions(patient_id, results, replace=replace_existing)

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


@app.post("/api/sync/{patient_id}")
def sync_patient(patient_id: str):
    """Liên thông lại toàn bộ hồ sơ theo dòng bệnh án đang lưu."""
    return _sync_patient_record(patient_id)


@app.post("/api/patients/{patient_id}/diagnosis")
def add_diagnosis(patient_id: str, body: DiagnosisCreate):
    """
    Chẩn đoán thêm cho một bệnh nhân đã có hồ sơ, rồi liên thông ngay.

    Khác nút Đồng bộ ở chỗ **giữ lại** các chẩn đoán cũ. Đây là luồng tái khám
    hoặc phát hiện thêm bệnh: bệnh nhân đã có E11.9 từ lần trước, lần này thêm
    I10 thì hồ sơ phải mang cả hai chứ không phải thay thế.

    Trên EMR Cloud, mỗi chẩn đoán là một Condition riêng với khóa nghiệp vụ
    riêng, nên chẩn đoán mới không đè lên chẩn đoán cũ.
    """
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "UPDATE patients SET clinical_note = ? WHERE id = ?",
            (body.clinical_note, patient_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ bệnh án")

    return _sync_patient_record(patient_id, body.clinical_note, replace_existing=False)


def _load_condition(patient_id: str, icd10_code: str) -> tuple:
    """Đọc hồ sơ và một chẩn đoán của hồ sơ đó, hoặc 404 nếu không có."""
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM patients WHERE id = ?", (patient_id,))
        patient_row = cursor.fetchone()
        # Lấy kèm rowid: danh sách chẩn đoán xếp theo rowid, nên sửa một mã mà
        # chèn lại ở cuối sẽ làm nó nhảy xuống dưới cùng ngay dưới tay bác sĩ.
        cursor.execute(
            "SELECT rowid AS row_id, * FROM patient_conditions"
            " WHERE patient_id = ? AND icd10_code = ?",
            (patient_id, icd10_code),
        )
        condition_row = cursor.fetchone()

    if not patient_row:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ bệnh án")
    if not condition_row:
        raise HTTPException(
            status_code=404,
            detail=f"Hồ sơ {patient_id} không có chẩn đoán mang mã '{icd10_code}'.",
        )
    return dict(patient_row), dict(condition_row)


@app.put("/api/patients/{patient_id}/conditions/{icd10_code}")
def update_condition(patient_id: str, icd10_code: str, body: ConditionUpdate):
    """
    Sửa một chẩn đoán ĐÃ ghi nhận, kể cả chẩn đoán của lần khám trước.

    Trước đây hồ sơ chỉ thêm được chẩn đoán: mô hình NLP suy sai mã thì mã sai
    nằm lại vĩnh viễn trong bệnh án và trên trục, bác sĩ chỉ còn cách xóa cả hồ
    sơ rồi nhập lại từ đầu.

    Mã ICD-10 nằm trong khóa nghiệp vụ của Condition, nên đổi mã KHÔNG cập nhật
    được bản ghi cũ trên EMR mà sinh ra tài nguyên mới. Vì vậy thứ tự là: đẩy bản
    ghi đúng lên trục trước, rồi mới gỡ bản mang mã sai. Ngược lại thì có lúc hồ
    sơ trên trục không còn chẩn đoán nào.
    """
    if body.clinical_status not in CLINICAL_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=(f"Trạng thái lâm sàng '{body.clinical_status}' không hợp lệ. "
                    f"Chọn một trong: {', '.join(sorted(CLINICAL_STATUSES))}."),
        )

    patient, old = _load_condition(patient_id, icd10_code)
    new_code = body.icd10_code.strip().upper()
    note = (body.fragment or "").strip() or old.get("fragment") or patient["clinical_note"]

    diagnosis = {
        "code": new_code,
        "name_vi": body.icd10_display.strip(),
        "fragment": note,
        "note": note,
        # Bác sĩ tự chọn mã thì con số này không còn là điểm tin cậy của mô hình
        # nữa mà là quyết định chuyên môn - ghi 100% và "confirmed" để phần sau
        # không đem nó ra so với ngưỡng tự động rồi bắt duyệt lại chính người vừa
        # duyệt. Đây cũng là cách bác sĩ giải phóng một mã đang "Chờ duyệt".
        "confidence": 100.0,
        "verification_status": "confirmed",
        "verification_status_override": "confirmed",
        "clinical_status": body.clinical_status,
    }

    try:
        new_condition_id = _push_condition(patient, diagnosis)
    except requests.HTTPError as exc:
        detail = exc.response.text[:300] if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=f"Gateway trả về lỗi: {detail}") from exc
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể kết nối Gateway tại {GATEWAY_URL}: {exc}",
        ) from exc

    # Chỉ gỡ bản cũ khi EMR cấp id KHÁC. Sửa mà giữ nguyên mã trong cùng ngày
    # khám thì khóa nghiệp vụ không đổi, conditional update trả lại đúng tài
    # nguyên vừa cập nhật - gỡ nó đi là xóa mất bản ghi mới.
    old_condition_id = old.get("fhir_condition_id")
    warning = (_retire_on_emr(old_condition_id)
               if old_condition_id and old_condition_id != new_condition_id else None)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_cursor(commit=True) as cursor:
        # Xóa trước rồi chèn: khóa chính là (patient_id, icd10_code) nên đổi mã là
        # đổi khóa, cập nhật tại chỗ sẽ để lại dòng mang mã cũ.
        cursor.execute(
            "DELETE FROM patient_conditions WHERE patient_id = ? AND icd10_code = ?",
            (patient_id, icd10_code),
        )
        cursor.execute(
            # OR REPLACE cho trường hợp bác sĩ sửa mã này thành mã đã có sẵn trong
            # hồ sơ: hai chẩn đoán nhập lệch nay nhập một, thay vì lỗi trùng khóa.
            # Giữ nguyên rowid vừa xóa để chẩn đoán ở lại đúng chỗ trong danh sách.
            "INSERT OR REPLACE INTO patient_conditions (rowid, patient_id, icd10_code,"
            " icd10_display, fragment, confidence_score, verification_status,"
            " fhir_condition_id, status, message, sync_time, clinical_status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (old["row_id"],
             patient_id, new_code, diagnosis["name_vi"], note, diagnosis["confidence"],
             diagnosis["verification_status"], new_condition_id, "Synced",
             f"Bác sĩ sửa lại chẩn đoán, đã liên thông FHIR Condition {new_condition_id}.",
             now, body.clinical_status),
        )
    _refresh_primary_diagnosis(patient_id)

    return {
        "status": "success",
        "patient_id": patient_id,
        "old_icd10_code": icd10_code,
        "icd10_code": new_code,
        "icd10_display": diagnosis["name_vi"],
        "clinical_status": body.clinical_status,
        "fhir_condition_id": new_condition_id,
        "retired_condition_id": old_condition_id if warning is None else None,
        "warning": warning,
        "message": (
            f"Đã sửa chẩn đoán {icd10_code} thành {new_code} và liên thông lại "
            f"(FHIR Condition {new_condition_id})."
            if new_code != icd10_code else
            f"Đã cập nhật chẩn đoán {new_code} và liên thông lại "
            f"(FHIR Condition {new_condition_id})."
        ),
    }


@app.delete("/api/patients/{patient_id}/conditions/{icd10_code}")
def remove_condition(patient_id: str, icd10_code: str):
    """
    Gỡ một chẩn đoán khỏi bệnh án và khỏi EMR Cloud.

    Dùng khi chẩn đoán bị ghi nhầm hẳn. Bệnh đã điều trị xong thì nên sửa trạng
    thái lâm sàng sang `resolved` qua PUT chứ đừng xóa: xóa là mất lịch sử điều
    trị, còn `resolved` giữ lại bệnh sử mà vẫn báo đúng là bệnh không còn hoạt động.
    """
    _, condition = _load_condition(patient_id, icd10_code)

    warning = (_retire_on_emr(condition["fhir_condition_id"])
               if condition.get("fhir_condition_id") else None)

    with db_cursor(commit=True) as cursor:
        cursor.execute(
            "DELETE FROM patient_conditions WHERE patient_id = ? AND icd10_code = ?",
            (patient_id, icd10_code),
        )
    _refresh_primary_diagnosis(patient_id)

    return {
        "status": "success",
        "patient_id": patient_id,
        "icd10_code": icd10_code,
        "warning": warning,
        "message": f"Đã gỡ chẩn đoán {icd10_code} khỏi hồ sơ {patient_id}.",
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
