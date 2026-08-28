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
from typing import List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Response
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
FHIR_NS = "https://smig.nckh.vn/fhir"
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
def get_patients(search_id: str = None, response: Response = None):
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

        # Không có search_id: danh sách bệnh án của viện này, NHƯNG mỗi hồ sơ
        # được bồi thêm chẩn đoán mà tuyến khác đã ghi cho cùng người đó.
        #
        # Trước đây nhánh này cố ý chỉ trả bệnh án cục bộ, và đó là chỗ hỏng: một
        # hệ thống liên thông mà màn hình chính vẫn chỉ thấy phần của mình thì
        # phần liên thông chỉ tồn tại trong các màn phụ. Bác sĩ mở bệnh án ra phải
        # thấy ngay bệnh nhân đã khám gì ở đâu, kèm tên nơi khám - không thấy thì
        # không dùng được, mà thấy nhưng không rõ ai ghi thì càng nguy hiểm hơn.
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

        theo_dinh_danh, chan_doan_theo_ho_so, truc_online = _ban_do_tren_truc()
        if response is not None:
            # Danh sách trả về là mảng nên không nhét được cờ trạng thái vào thân.
            # Đưa qua header để giao diện nói được "đang thiếu phần tuyến khác",
            # thay vì hiện một bệnh án trông có vẻ đầy đủ mà thật ra không phải.
            response.headers["X-EMR-Online"] = "true" if truc_online else "false"
        if truc_online:
            for patient in patients:
                _boi_chan_doan_tuyen_khac(patient, theo_dinh_danh, chan_doan_theo_ho_so)
        return patients
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi cơ sở dữ liệu: {exc}") from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi cơ sở dữ liệu: {exc}") from exc


# --- Ghep du lieu tuyen khac vao benh an cuc bo ----------------------------

def _ban_do_tren_truc():
    """
    Đọc toàn bộ trục trong ĐÚNG HAI lần gọi, bất kể có bao nhiêu bệnh nhân.

    Hỏi trục một lần cho mỗi bệnh nhân thì màn hình danh sách phát sinh N+1 lượt
    gọi mạng và chậm dần theo số hồ sơ - kiểu chậm chỉ lộ ra khi dữ liệu đã nhiều,
    tức đúng lúc không sửa được nữa.

    Trả về ``(theo_dinh_danh, chan_doan_theo_ho_so, online)``:

    * ``theo_dinh_danh`` - ``{(hệ, giá trị): id hồ sơ trên EMR}``
    * ``chan_doan_theo_ho_so`` - ``{id hồ sơ trên EMR: [chẩn đoán]}``
    * ``online`` - đọc được trục hay không. Không đọc được thì phía gọi giữ
      nguyên bệnh án cục bộ chứ không báo lỗi: trục hỏng không được làm hỏng
      luôn màn hình bệnh án của chính viện mình.
    """
    theo_dinh_danh, chan_doan_theo_ho_so = {}, {}
    try:
        r_bn = requests.get(f"{FHIR_SERVER_URL}/Patient",
                            params={"_count": 200}, timeout=REQUEST_TIMEOUT)
        r_cd = requests.get(f"{FHIR_SERVER_URL}/Condition",
                            params={"_count": 500}, timeout=REQUEST_TIMEOUT)
        r_bn.raise_for_status()
        r_cd.raise_for_status()
    except requests.RequestException:
        return theo_dinh_danh, chan_doan_theo_ho_so, False

    for entry in r_bn.json().get("entry", []):
        res = entry.get("resource") or {}
        ho_so_id = res.get("id")
        for ident in res.get("identifier", []) or []:
            he, gt = ident.get("system"), ident.get("value")
            if he and gt and ho_so_id:
                theo_dinh_danh[(he, gt)] = ho_so_id

    for entry in r_cd.json().get("entry", []):
        res = entry.get("resource") or {}
        subject = res.get("subject") or {}
        # Ưu tiên `reference`: cùng một người có thể được ghi dưới hai định danh
        # khác nhau ở hai lần khám (lần đầu chỉ có mã bệnh án, lần sau mới có
        # CCCD). Gom theo `identifier` sẽ tách người đó thành hai hồ sơ.
        ref = (subject.get("reference") or "").rsplit("/", 1)[-1]
        if not ref:
            ident = subject.get("identifier") or {}
            ref = theo_dinh_danh.get((ident.get("system"), ident.get("value")))
        if ref:
            chan_doan_theo_ho_so.setdefault(ref, []).append(res)

    return theo_dinh_danh, chan_doan_theo_ho_so, True


def _boi_chan_doan_tuyen_khac(patient: dict, theo_dinh_danh: dict,
                              chan_doan_theo_ho_so: dict) -> None:
    """
    Bồi vào một hồ sơ những chẩn đoán mà tuyến khác đã ghi cho cùng người đó.

    Sửa `patient` tại chỗ. Mọi chẩn đoán - kể cả của chính viện này - đều được
    gắn `facility_code`/`facility_name`/`la_ngoai_vien`, để giao diện không bao
    giờ phải đoán ai ghi bản nào. Nói ra nguồn là phần bắt buộc của liên thông:
    một chẩn đoán không rõ nơi lập thì bác sĩ không đánh giá được độ tin cậy.
    """
    ho_so_id = None
    for he, gia_tri in ((FHIR_CCCD_SYSTEM, patient.get("citizen_id")),
                        (FHIR_BHYT_SYSTEM, patient.get("insurance_card")),
                        (FHIR_MRN_SYSTEM, patient.get("id"))):
        if gia_tri and (he, gia_tri) in theo_dinh_danh:
            ho_so_id = theo_dinh_danh[(he, gia_tri)]
            break

    # Chẩn đoán của chính viện này: đánh dấu nguồn để hiển thị đồng nhất với
    # phần tuyến khác, và cờ `chua_lien_thong` cho bản chưa đẩy lên trục.
    for c in patient["conditions"]:
        c.setdefault("facility_code", FACILITY_CODE)
        c.setdefault("facility_name", FACILITY_NAME)
        c.setdefault("la_ngoai_vien", False)
        c["chua_lien_thong"] = not c.get("fhir_condition_id")

    if not ho_so_id:
        # Không quy được về hồ sơ nào trên trục. Với bệnh nhân chưa có CCCD/BHYT
        # thì đây là chuyện bình thường, không phải lỗi.
        patient["truc_quy_chieu"] = None
        patient["so_chan_doan_ngoai_vien"] = 0
        return

    patient["truc_quy_chieu"] = ho_so_id
    da_co = {c.get("fhir_condition_id") for c in patient["conditions"]
             if c.get("fhir_condition_id")}
    # Mã bệnh mà viện này đã có bản cục bộ. Với chẩn đoán do CHÍNH viện này lập,
    # bệnh án cục bộ là bản gốc còn trục chỉ là bản sao - nên bản trục bị bỏ qua,
    # nếu không thì cùng một bệnh hiện hai dòng: một "chưa liên thông" và một
    # "chỉ đọc", hai điều không thể cùng đúng. Chỉ áp dụng trong phạm vi viện
    # mình; chẩn đoán của tuyến khác thì luôn hiện, vì ta không có bản gốc nào.
    ma_cuc_bo = {c.get("icd10_code") for c in patient["conditions"]}

    ngoai = 0
    for res in chan_doan_theo_ho_so.get(ho_so_id, []):
        if res.get("id") in da_co:
            continue  # bản này đã nằm trong bệnh án cục bộ, không đếm hai lần
        coding = ((res.get("code") or {}).get("coding") or [{}])
        notes = res.get("note") or []
        nguon = next((t for t in (res.get("meta") or {}).get("tag", [])
                      if t.get("system") == FHIR_FACILITY_SYSTEM), {})
        ma_cs = nguon.get("code")
        la_ngoai = bool(ma_cs) and ma_cs != FACILITY_CODE
        if not la_ngoai and coding[0].get("code") in ma_cuc_bo:
            continue
        lam_sang = ((res.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code")
        if la_ngoai:
            ngoai += 1
        patient["conditions"].append({
            "patient_id": patient["id"],
            "icd10_code": coding[0].get("code", ""),
            "icd10_display": coding[0].get("display", ""),
            "fragment": (notes[0].get("text") if notes else "") or "Chẩn đoán liên thông",
            # Bản trên trục KHÔNG mang điểm của mô hình: điểm ấy là của lần chẩn
            # đoán tại nơi lập, không phải thứ viện này chấm. Để trống còn hơn
            # bịa ra một con số trông như đã kiểm chứng.
            "confidence_score": None,
            "verification_status": None,
            "fhir_condition_id": res.get("id"),
            "status": "Synced",
            "clinical_status": lam_sang or "active",
            "recorded_date": res.get("recordedDate") or res.get("onsetDateTime"),
            "facility_code": ma_cs,
            "facility_name": nguon.get("display") or ma_cs or "Không rõ cơ sở",
            "la_ngoai_vien": la_ngoai,
            "chua_lien_thong": False,
            # Không có bản cục bộ nên không sửa được. Giao diện dựa vào cờ này để
            # khóa nút Sửa/Gỡ thay vì để bác sĩ bấm rồi nhận 404.
            "chi_doc": True,
        })
    patient["so_chan_doan_ngoai_vien"] = ngoai


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


@app.get("/api/patients/{patient_id}/history")
def get_patient_history(patient_id: str):
    """
    Bệnh sử ĐẦY ĐỦ của một bệnh nhân: nội viện cộng với mọi tuyến khác trên trục.

    Vì sao cần riêng một đường này: `GET /api/patients` cố ý chỉ trả bệnh án cục
    bộ, nên bác sĩ nhìn hồ sơ một bệnh nhân đã tiếp nhận tại đây thì **chỉ thấy
    phần viện mình ghi**. Chẩn đoán do tuyến dưới hay tuyến trên lập vẫn nằm trên
    trục nhưng không hiện ra ở đâu cả - đúng thứ mà cả hệ thống liên thông sinh ra
    để cho xem.

    **Không đòi hỏi hồ sơ cục bộ.** `patient_id` nhận cả mã bệnh án nội viện lẫn
    CCCD, thẻ BHYT, hay mã bệnh án của viện khác. Bắt buộc phải có bệnh án tại đây
    thì đúng ca cần nhất - bệnh nhân vừa chuyển tuyến tới, chưa kịp tiếp nhận - lại
    là ca duy nhất không xem được bệnh sử. Người bệnh chưa từng khám ở đây thì viện
    này không có mã bệnh án cho họ, nên thứ bác sĩ cầm trong tay chỉ có giấy tờ
    tùy thân.

    Cách tìm trên trục: thử lần lượt **CCCD → BHYT → mã bệnh án**. Ngược thứ tự
    với `get_patients` là có chủ ý - ở đó chuỗi tra là thứ người dùng gõ nên phải
    thử mã bệnh án trước, còn ở đây ta cần định danh quét rộng nhất. Mã bệnh án
    chỉ quy được hồ sơ trong phạm vi một viện, dùng nó trước là tự giới hạn kết
    quả vào đúng phần vốn đã thấy.

    EMR Cloud không chạy thì trả phần cục bộ kèm `emr_online: false`, không ném
    lỗi: xem bệnh sử là thao tác đọc, hỏng phần liên thông không được làm hỏng
    luôn phần bệnh án của chính viện mình.
    """
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM patients WHERE id = ?", (patient_id,))
        row = cursor.fetchone()
        benh_nhan = dict(row) if row else None
        cuc_bo = []
        if benh_nhan:
            cursor.execute(
                "SELECT * FROM patient_conditions WHERE patient_id = ? ORDER BY rowid",
                (patient_id,))
            cuc_bo = [dict(r) for r in cursor.fetchall()]

    ket_qua = {
        "facility_code": FACILITY_CODE,
        "facility_name": FACILITY_NAME,
        # Có bệnh án tại viện này hay không. Giao diện dựa vào cờ này để biết bệnh
        # sử đang xem là của người đã tiếp nhận, hay của người mới chỉ tra trên trục.
        "co_ho_so_cuc_bo": benh_nhan is not None,
        "emr_online": False,
        "tra_cuu_bang": None,
        "theo_co_so": [],
        "chua_lien_thong": [],
        "canh_bao": None,
    }

    # Ứng viên định danh để hỏi trục. Có bệnh án thì lấy từ hồ sơ; không có thì
    # chính chuỗi người dùng đưa vào là định danh - chưa biết nó thuộc loại nào
    # nên thử cả ba hệ.
    if benh_nhan:
        ung_vien = [
            ("cccd", FHIR_CCCD_SYSTEM, (benh_nhan.get("citizen_id") or "").strip()),
            ("bhyt", FHIR_BHYT_SYSTEM, (benh_nhan.get("insurance_card") or "").strip()),
            ("mrn", FHIR_MRN_SYSTEM, (benh_nhan.get("id") or "").strip()),
        ]
        if not any(gt for _, _, gt in ung_vien[:2]):
            ket_qua["canh_bao"] = (
                "Hồ sơ chưa có CCCD hoặc thẻ BHYT nên chỉ tra được trong phạm vi bệnh "
                "viện này. Bổ sung định danh toàn quốc để thấy bệnh sử ở tuyến khác.")
    else:
        q = patient_id.strip()
        ung_vien = [("cccd", FHIR_CCCD_SYSTEM, q),
                    ("bhyt", FHIR_BHYT_SYSTEM, q),
                    ("mrn", FHIR_MRN_SYSTEM, q)]

    ho_so_truc = _tim_ho_so_tren_truc(ung_vien, ket_qua)
    tren_truc = _doc_chan_doan_tren_truc(ho_so_truc, ket_qua) if ho_so_truc else []

    # Không có ở đâu cả mới là 404. Chỉ cần một trong hai nơi có là còn thứ để đọc.
    if benh_nhan is None and ho_so_truc is None:
        raise HTTPException(
            status_code=404,
            detail=(f"Không tìm thấy '{patient_id}' ở bệnh viện này lẫn trên EMR Cloud. "
                    f"Thử tra bằng số CCCD hoặc thẻ BHYT."))

    ket_qua["patient"] = _thong_tin_benh_nhan(benh_nhan, ho_so_truc, patient_id)

    # Ghép hai nguồn theo id tài nguyên trên EMR. Không khử trùng thì mỗi chẩn
    # đoán đã liên thông hiện hai lần - một từ bệnh án cục bộ, một từ trục - và
    # bác sĩ đọc thành bệnh nhân mắc bệnh đó hai lần.
    id_tren_truc = {c["fhir_condition_id"] for c in tren_truc if c.get("fhir_condition_id")}
    theo_co_so = {}
    for c in tren_truc:
        theo_co_so.setdefault(c.get("facility_code") or "(không rõ)", []).append(c)

    for c in cuc_bo:
        # Chẩn đoán đã có trên trục thì bản của trục đã tính rồi. Chỉ bổ sung cờ
        # cho biết bản ghi này CÒN nằm trong bệnh án cục bộ, nhờ đó giao diện biết
        # chẩn đoán nào sửa được.
        if c.get("fhir_condition_id") and c["fhir_condition_id"] in id_tren_truc:
            for muc in theo_co_so.get(FACILITY_CODE, []):
                if muc.get("fhir_condition_id") == c["fhir_condition_id"]:
                    muc["co_ban_cuc_bo"] = True
                    muc["fragment"] = muc.get("fragment") or c.get("fragment")
                    muc["confidence_score"] = c.get("confidence_score")
                    muc["clinical_status"] = c.get("clinical_status") or muc.get("clinical_status")
            continue
        # Chưa lên trục: tách riêng chứ không xếp vào cơ sở nào. Xếp lẫn vào phần
        # của viện mình là nói rằng tuyến khác đọc được, trong khi thực tế chưa.
        ket_qua["chua_lien_thong"].append({
            "icd10_code": c.get("icd10_code"),
            "icd10_display": c.get("icd10_display"),
            "fragment": c.get("fragment"),
            "confidence_score": c.get("confidence_score"),
            "clinical_status": c.get("clinical_status") or "active",
            "status": c.get("status"),
            "sync_time": c.get("sync_time"),
            "co_ban_cuc_bo": True,
        })

    # Viện mình lên trước, các tuyến khác xếp sau theo mã cơ sở cho ổn định thứ tự.
    for ma_cs in sorted(theo_co_so, key=lambda m: (m != FACILITY_CODE, m)):
        muc = theo_co_so[ma_cs]
        ket_qua["theo_co_so"].append({
            "facility_code": ma_cs,
            "facility_name": muc[0].get("facility_name") or ma_cs,
            "la_ngoai_vien": ma_cs != FACILITY_CODE,
            "so_chan_doan": len(muc),
            "chan_doan": sorted(
                muc, key=lambda c: c.get("recorded_date") or "", reverse=True),
        })

    ket_qua["tong_tren_truc"] = sum(len(v) for v in theo_co_so.values())
    ket_qua["so_co_so"] = len(theo_co_so)
    ket_qua["so_co_so_ngoai_vien"] = sum(1 for m in theo_co_so if m != FACILITY_CODE)
    return ket_qua


def _thong_tin_benh_nhan(benh_nhan, ho_so_truc, patient_id: str) -> dict:
    """
    Khối thông tin hành chính, ưu tiên bệnh án cục bộ rồi mới tới trục.

    Bệnh án của chính viện mình là bản người tiếp đón vừa nhập và chịu trách nhiệm,
    nên nó đúng hơn bản trên trục vốn có thể do nơi khác ghi từ lâu.
    """
    if benh_nhan:
        return {
            "id": benh_nhan["id"],
            "name": benh_nhan["name"],
            "gender": benh_nhan["gender"],
            "birth_date": benh_nhan["birth_date"],
            "citizen_id": benh_nhan.get("citizen_id"),
            "insurance_card": benh_nhan.get("insurance_card"),
        }

    idents = {i.get("system"): i.get("value")
              for i in (ho_so_truc or {}).get("identifier", []) if isinstance(i, dict)}
    ten = ""
    for muc in (ho_so_truc or {}).get("name", []):
        ten = muc.get("text") or (" ".join(muc.get("given", []))
                                  + " " + muc.get("family", "")).strip()
        if ten:
            break
    gioi = (ho_so_truc or {}).get("gender")
    return {
        # Không có mã bệnh án tại viện này là chuyện bình thường với người chưa
        # từng khám ở đây; lùi về chính chuỗi đã tra để giao diện còn thứ hiển thị.
        "id": idents.get(FHIR_MRN_SYSTEM) or patient_id,
        "name": ten or "Bệnh nhân trên trục",
        "gender": "Nam" if gioi == "male" else ("Nữ" if gioi == "female" else "Khác"),
        "birth_date": (ho_so_truc or {}).get("birthDate", "---"),
        "citizen_id": idents.get(FHIR_CCCD_SYSTEM),
        "insurance_card": idents.get(FHIR_BHYT_SYSTEM),
        # Mã bệnh án tại các viện khác, LUÔN kèm tên cơ sở đã cấp. Hiện trần mã
        # không thì vô nghĩa và còn nguy hiểm: "BN0002" của viện này và của viện
        # kia là hai người khác nhau. Kèm cơ sở thì nó thành thông tin dùng được -
        # bác sĩ gọi sang nơi đó hỏi bệnh án gốc theo đúng mã họ đang giữ.
        "ma_benh_an_noi_khac": [
            {"facility_code": he.rsplit("/", 1)[-1], "value": gt}
            for he, gt in idents.items()
            if he.startswith(f"{FHIR_NS}/identifier/mrn/") and he != FHIR_MRN_SYSTEM
        ],
    }


def _tim_ho_so_tren_truc(ung_vien: List[tuple], ket_qua: dict) -> Optional[dict]:
    """
    Tìm tài nguyên Patient trên EMR Cloud theo danh sách định danh ứng viên.

    Ghi thẳng trạng thái tra cứu vào `ket_qua` (`emr_online`, `tra_cuu_bang`,
    `canh_bao`) để giao diện nói được vì sao danh sách rỗng: trục không chạy, hay
    bệnh nhân chưa có định danh toàn quốc, hay đúng là chưa ai ghi gì. Ba lý do
    này dẫn tới ba hành động khác nhau của bác sĩ, gộp thành "không có dữ liệu" là
    bỏ mất thông tin cần nhất.
    """
    for ten, he, gia_tri in ung_vien:
        if not gia_tri:
            continue
        try:
            resp = requests.get(
                f"{FHIR_SERVER_URL}/Patient",
                params={"identifier": f"{he}|{gia_tri}"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException:
            ket_qua["canh_bao"] = (
                "Không kết nối được EMR Cloud nên chỉ hiển thị phần bệnh án của "
                "bệnh viện này. Bệnh sử ở tuyến khác có thể còn thiếu.")
            return None
        ket_qua["emr_online"] = True
        if resp.status_code != 200:
            continue
        entries = resp.json().get("entry", [])
        if entries:
            ket_qua["tra_cuu_bang"] = ten
            return entries[0].get("resource") or {}
    return None


def _doc_chan_doan_tren_truc(ho_so_truc: dict, ket_qua: dict) -> List[dict]:
    """Đọc mọi Condition gắn với một hồ sơ trên trục, kèm cơ sở đã lập ra chúng."""
    emr_id = (ho_so_truc or {}).get("id")
    if not emr_id:
        return []
    try:
        resp = requests.get(
            f"{FHIR_SERVER_URL}/Condition",
            params={"subject": f"Patient/{emr_id}"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        ket_qua["canh_bao"] = "Đọc được hồ sơ trên trục nhưng không lấy được danh sách chẩn đoán."
        return []

    ds = []
    for entry in resp.json().get("entry", []):
        res = entry.get("resource") or {}
        coding = ((res.get("code") or {}).get("coding") or [{}])
        notes = res.get("note") or []
        nguon = next(
            (t for t in (res.get("meta") or {}).get("tag", [])
             if t.get("system") == FHIR_FACILITY_SYSTEM), {})
        lam_sang = ((res.get("clinicalStatus") or {}).get("coding") or [{}])[0].get("code")
        ds.append({
            "fhir_condition_id": res.get("id"),
            "icd10_code": coding[0].get("code", ""),
            "icd10_display": coding[0].get("display", ""),
            "fragment": notes[0].get("text") if notes else "",
            "clinical_status": lam_sang or "active",
            "recorded_date": res.get("recordedDate") or res.get("onsetDateTime"),
            "facility_code": nguon.get("code"),
            "facility_name": nguon.get("display") or nguon.get("code"),
            "co_ban_cuc_bo": False,
        })
    return ds


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
