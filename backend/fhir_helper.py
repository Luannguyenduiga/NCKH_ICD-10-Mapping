# -*- coding: utf-8 -*-
"""
Sinh tài nguyên HL7 FHIR R4 cho SMIG Gateway.

Hai nguyên tắc chi phối module này:

* Mã ICD-10 phải sạch. Danh mục của Bộ Y tế đính kèm ký hiệu dao găm (†) cho hệ
  thống dagger/asterisk; ký hiệu đó không thuộc mã và sẽ bị máy chủ FHIR từ chối.
* Trạng thái xác nhận phải phản ánh đúng độ tin cậy của mô hình. Một mã do máy
  gợi ý với độ tin cậy 45% không được phép mang nhãn "confirmed".
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

# Namespace do dự án sở hữu. KHÔNG dùng hl7.org cho extension tự định nghĩa:
# theo quy tắc đặt tên của HL7, URL phải trỏ về tổ chức định nghĩa extension đó.
SMIG_NAMESPACE = "https://smig.nckh.vn/fhir"
EXT_CONFIDENCE = f"{SMIG_NAMESPACE}/StructureDefinition/nlp-confidence-score"
EXT_ENGINE = f"{SMIG_NAMESPACE}/StructureDefinition/nlp-engine"
# Khóa nghiệp vụ của một Condition. Đây là thứ conditional update tìm theo, thay
# cho việc client tự đặt id tài nguyên.
SYSTEM_CONDITION_KEY = f"{SMIG_NAMESPACE}/condition-key"

# --- Định danh bệnh nhân ---------------------------------------------------
# Số CCCD và thẻ BHYT có giá trị trên toàn quốc nên dùng được làm khóa liên
# thông. Mã bệnh án thì KHÔNG: "BN001" của bệnh viện A và "BN001" của bệnh viện B
# là hai người khác nhau. Vì vậy system của MRN phải mang mã cơ sở khám chữa
# bệnh - theo đặc tả FHIR, `identifier.system` chính là nơi chỉ ra ai cấp định
# danh đó.
SYSTEM_CCCD = f"{SMIG_NAMESPACE}/identifier/cccd"
SYSTEM_BHYT = f"{SMIG_NAMESPACE}/identifier/bhyt"
SYSTEM_FACILITY = f"{SMIG_NAMESPACE}/identifier/co-so-kcb"


# --- Kiểm định dạng định danh toàn quốc ------------------------------------
# Mã ICD-10 đã được kiểm kỹ (`is_valid_icd10`), trong khi CCCD/BHYT trước đây
# nhận mọi chuỗi - kể cả một tên bệnh gõ nhầm ô. Hậu quả của hai lỗi không cân
# nhau: mã ICD sai thì bác sĩ nhìn ra ngay, còn định danh sai thì âm thầm tách
# hồ sơ của một người thành hai, và chỉ lộ ra đúng lúc cần bệnh sử nhất.
_CCCD_RE = re.compile(r"^\d{12}$")           # Căn cước công dân
_CMND_RE = re.compile(r"^\d{9}$")            # CMND đời cũ, vẫn còn lưu hành
_BHYT_RE = re.compile(r"^[A-Z]{2}\d{13}$")   # ví dụ GD4010120152431
# Người nhập liệu hay chấm/cách/gạch cho dễ đọc. Bỏ các ký tự này TRƯỚC khi so
# khớp, và lưu bản đã bỏ: giữ nguyên thì "079 095 010245" và "079095010245" là
# hai định danh khác nhau, tách hồ sơ đúng theo kiểu mà lớp kiểm này sinh ra để
# chặn.
_NGAN_CACH_RE = re.compile(r"[\s.\-]")


def normalize_citizen_id(value: Optional[str]) -> Optional[str]:
    """
    Chuẩn hóa và kiểm số CCCD. Trả None nếu bỏ trống, ném ValueError nếu sai.

    Bỏ trống là hợp lệ - không phải bệnh nhân nào cũng có giấy tờ lúc tiếp nhận,
    và luồng đã có phương án lùi về mã bệnh án. Cái không được phép là một giá
    trị CÓ mà sai định dạng: nó tạo ra một danh tính toàn quốc giả.
    """
    if value is None:
        return None
    cleaned = _NGAN_CACH_RE.sub("", value).strip()
    if not cleaned:
        return None
    if not (_CCCD_RE.match(cleaned) or _CMND_RE.match(cleaned)):
        raise ValueError(
            f"Số CCCD '{value}' không hợp lệ: phải là 12 chữ số "
            f"(hoặc 9 chữ số nếu là CMND cũ). Bỏ trống nếu chưa có giấy tờ.")
    return cleaned


def normalize_insurance_card(value: Optional[str]) -> Optional[str]:
    """Chuẩn hóa và kiểm số thẻ BHYT. Cùng quy ước với `normalize_citizen_id`."""
    if value is None:
        return None
    cleaned = _NGAN_CACH_RE.sub("", value).strip().upper()
    if not cleaned:
        return None
    if not _BHYT_RE.match(cleaned):
        raise ValueError(
            f"Số thẻ BHYT '{value}' không hợp lệ: phải là 2 chữ cái + 13 chữ số, "
            f"ví dụ GD4010120152431. Bỏ trống nếu chưa có thẻ.")
    return cleaned


def validate_identifier_value(system: str, value: str) -> None:
    """
    Kiểm một cặp (system, value) đọc từ tài nguyên do bên ngoài gửi tới.

    Cần thiết vì `/api/fhir/sync` chấp nhận Condition dựng sẵn: nếu chỉ kiểm ở
    `/api/fhir/condition` thì một client tự dựng tài nguyên vẫn đưa được định
    danh rác lên trục, và cửa kiểm coi như không tồn tại.
    """
    if system == SYSTEM_CCCD:
        normalize_citizen_id(value)
    elif system == SYSTEM_BHYT:
        normalize_insurance_card(value)


def mrn_system(facility_code: str) -> str:
    """
    System của mã bệnh án, gắn với cơ sở đã cấp mã đó.

    Thiếu mã cơ sở thì lùi về một nhãn cố định chứ KHÔNG để `to_fhir_id` sinh
    chuỗi ngẫu nhiên: system đổi mỗi lần gọi sẽ làm conditional update không bao
    giờ khớp lại bản ghi cũ, tức mỗi lần đồng bộ đẻ thêm một bệnh nhân mới.
    """
    code = (facility_code or "").strip()
    return f"{SMIG_NAMESPACE}/identifier/mrn/{to_fhir_id(code) if code else 'khong-ro-co-so'}"


# Cơ sở khám chữa bệnh đã ghi nhận chẩn đoán. R4 không cho `Condition.recorder`
# trỏ tới Organization nên dùng extension của dự án; giá trị là tham chiếu có
# `display` để đọc JSON là thấy ngay tên bệnh viện.
EXT_SOURCE_FACILITY = f"{SMIG_NAMESPACE}/StructureDefinition/source-facility"

ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10"

# Giá trị hợp lệ theo HL7 FHIR R4.
CLINICAL_STATUSES = {"active", "recurrence", "relapse", "inactive", "remission", "resolved"}
VERIFICATION_STATUSES = {
    "unconfirmed", "provisional", "differential",
    "confirmed", "refuted", "entered-in-error",
}
GENDER_MAP = {
    "nam": "male", "male": "male", "m": "male",
    "nữ": "female", "nu": "female", "female": "female", "f": "female",
    "khác": "other", "other": "other",
}

_STATUS_DISPLAY = {
    "active": "Active", "recurrence": "Recurrence", "relapse": "Relapse",
    "inactive": "Inactive", "remission": "Remission", "resolved": "Resolved",
    "unconfirmed": "Unconfirmed", "provisional": "Provisional",
    "differential": "Differential", "confirmed": "Confirmed",
    "refuted": "Refuted", "entered-in-error": "Entered in Error",
}


def sanitize_icd10_code(code: str) -> str:
    """Gỡ ký hiệu dao găm/sao khỏi mã ICD-10 trước khi đưa vào tài nguyên FHIR."""
    return re.sub(r"[†*‡]", "", code or "").strip()


def is_valid_icd10(code: str) -> bool:
    """Kiểm tra mã theo định dạng WHO: 1 chữ cái + 2 chữ số, tùy chọn .n[n]."""
    return bool(re.fullmatch(r"[A-Z]\d{2}(\.\d{1,2})?", sanitize_icd10_code(code).upper()))


def to_fhir_id(value: str) -> str:
    """
    Chuyển một định danh bất kỳ sang FHIR id hợp lệ.

    FHIR R4 chỉ cho phép [A-Za-z0-9.-] và tối đa 64 ký tự, nên mã bệnh án dạng
    "MRN-2026-001" thì giữ nguyên còn số CCCD có khoảng trắng sẽ được làm sạch.
    """
    cleaned = re.sub(r"[^A-Za-z0-9.\-]", "-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return (cleaned or f"unknown-{uuid.uuid4().hex[:8]}")[:64]


def utc_now_iso() -> str:
    """Dấu thời gian ISO 8601 UTC (datetime.utcnow() đã bị loại bỏ từ Python 3.12)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _codeable(system: str, code: str) -> dict:
    return {"coding": [{"system": system, "code": code,
                        "display": _STATUS_DISPLAY.get(code, code.capitalize())}]}


def build_fhir_condition_resource(
    patient_id: str,
    patient_name: str,
    raw_clinical_note: str,
    icd10_code: str,
    icd10_display: str,
    confidence_score: float,
    clinical_status: str = "active",
    verification_status: str = "confirmed",
    condition_key: Optional[str] = None,
    engine_version: Optional[str] = None,
    facility_code: Optional[str] = None,
    facility_name: Optional[str] = None,
    citizen_id: Optional[str] = None,
    insurance_card: Optional[str] = None,
) -> dict:
    """
    Dựng một FHIR Condition Resource (R4) hợp lệ.

    Tham số:
        patient_id: mã bệnh án nội viện của bệnh nhân.
        patient_name: họ tên bệnh nhân.
        raw_clinical_note: nguyên văn chẩn đoán bác sĩ nhập.
        icd10_code: mã ICD-10 (được tự động làm sạch ký hiệu †).
        icd10_display: tên chuẩn của mã.
        confidence_score: độ tin cậy của mô hình, thang 0-100.
        clinical_status: active | recurrence | relapse | inactive | remission | resolved.
        verification_status: unconfirmed | provisional | differential | confirmed | ...
        condition_key: khóa nghiệp vụ (bệnh nhân, mã bệnh). Được đưa vào
            `Condition.identifier` để lần đồng bộ sau CẬP NHẬT đúng bản ghi cũ
            qua conditional update, thay vì tạo bản trùng trên EMR.
        engine_version: định danh mô hình NLP, phục vụ truy vết.
        facility_code: mã cơ sở khám chữa bệnh đã ghi nhận chẩn đoán.
        facility_name: tên cơ sở đó, để đọc hồ sơ là biết ngay bệnh viện nào ghi.

    Trả về:
        dict: cấu trúc JSON của FHIR Condition Resource.
    """
    clean_code = sanitize_icd10_code(icd10_code)
    if not clean_code:
        raise ValueError("Mã ICD-10 rỗng sau khi chuẩn hóa.")

    if clinical_status not in CLINICAL_STATUSES:
        raise ValueError(f"clinical_status không hợp lệ: {clinical_status}")
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError(f"verification_status không hợp lệ: {verification_status}")

    confidence_decimal = round(max(0.0, min(100.0, confidence_score)) / 100.0, 4)

    extensions = [{"url": EXT_CONFIDENCE, "valueDecimal": confidence_decimal}]
    if engine_version:
        extensions.append({"url": EXT_ENGINE, "valueString": engine_version})
    if facility_code:
        # Tham chiếu LOGIC theo identifier: lúc dựng tài nguyên chưa biết id mà
        # EMR sẽ cấp cho Organization. Khâu đồng bộ thay bằng tham chiếu thật,
        # `display` giữ nguyên nên nhìn JSON là đọc được tên bệnh viện.
        extensions.append({
            "url": EXT_SOURCE_FACILITY,
            "valueReference": {
                "identifier": {"system": SYSTEM_FACILITY, "value": facility_code},
                "display": facility_name or facility_code,
            },
        })

    # KHÔNG đặt "id": id do máy chủ EMR cấp. Client tự đặt id cộng với PUT theo id
    # là một lỗ ghi đè - biết mã bệnh án và mã ICD-10 là dựng được id của người
    # khác. Định danh nghiệp vụ nằm ở `identifier`, và đồng bộ đi qua conditional
    # update nên vẫn idempotent.
    resource = {
        "resourceType": "Condition",
        "clinicalStatus": _codeable(
            "http://terminology.hl7.org/CodeSystem/condition-clinical", clinical_status),
        "verificationStatus": _codeable(
            "http://terminology.hl7.org/CodeSystem/condition-ver-status", verification_status),
        "category": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-category",
                "code": "encounter-diagnosis",
                "display": "Encounter Diagnosis",
            }]
        }],
        "code": {
            "coding": [{
                "system": ICD10_SYSTEM,
                "code": clean_code,
                "display": icd10_display,
            }],
            "text": raw_clinical_note,
        },
        # Tham chiếu LOGIC theo định danh bệnh nhân: lúc dựng tài nguyên chưa biết
        # id mà EMR sẽ cấp. Khâu đồng bộ bổ sung `reference` trỏ tới id thật và
        # giữ nguyên `identifier`, nhờ vậy chỉ cần đọc Condition là đủ biết nó nói
        # về ai - không phải tin vào một khối dữ liệu gửi kèm bên ngoài.
        "subject": {
            "identifier": dict(zip(
                ("system", "value"),
                patient_match_key(patient_id, facility_code or "", citizen_id, insurance_card),
            )),
            "display": patient_name,
        },
        "recordedDate": utc_now_iso(),
        "extension": extensions,
    }

    if condition_key:
        resource["identifier"] = [{"system": SYSTEM_CONDITION_KEY, "value": condition_key}]

    if facility_code:
        # Gắn thêm vào meta.tag để lọc được ngay trên API mà không cần đọc
        # extension: GET /Condition?_tag=<system>|<mã cơ sở>
        resource["meta"] = {"tag": [{
            "system": SYSTEM_FACILITY,
            "code": facility_code,
            "display": facility_name or facility_code,
        }]}

    return resource


def build_fhir_organization_resource(facility_code: str, facility_name: str) -> dict:
    """Dựng Organization cho một cơ sở khám chữa bệnh, khóa theo mã cơ sở."""
    return {
        "resourceType": "Organization",
        "identifier": [{"system": SYSTEM_FACILITY, "value": facility_code}],
        "active": True,
        "type": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                "code": "prov",
                "display": "Healthcare Provider",
            }]
        }],
        "name": facility_name or facility_code,
    }


def facility_of(condition: dict) -> Optional[dict]:
    """Đọc cơ sở đã ghi nhận chẩn đoán: {'code': ..., 'name': ...} hoặc None."""
    for tag in (condition.get("meta") or {}).get("tag") or []:
        if isinstance(tag, dict) and tag.get("system") == SYSTEM_FACILITY and tag.get("code"):
            return {"code": tag["code"], "name": tag.get("display") or tag["code"]}

    for ext in condition.get("extension") or []:
        if isinstance(ext, dict) and ext.get("url") == EXT_SOURCE_FACILITY:
            ref = ext.get("valueReference") or {}
            code = (ref.get("identifier") or {}).get("value")
            if code:
                return {"code": code, "name": ref.get("display") or code}
    return None


def subject_identifier_of(condition: dict) -> Optional[Tuple[str, str]]:
    """Đọc định danh bệnh nhân (system, value) từ `Condition.subject.identifier`."""
    ident = (condition.get("subject") or {}).get("identifier") or {}
    system, value = (ident.get("system") or "").strip(), (ident.get("value") or "").strip()
    return (system, value) if system and value else None


def condition_key_of(condition: dict) -> Optional[str]:
    """Đọc khóa nghiệp vụ ra khỏi một Condition, bỏ qua các identifier hệ khác."""
    for ident in condition.get("identifier") or []:
        if isinstance(ident, dict) and ident.get("system") == SYSTEM_CONDITION_KEY:
            value = (ident.get("value") or "").strip()
            if value:
                return value
    return None


def patient_match_key(
    patient_id: str,
    facility_code: str,
    citizen_id: Optional[str] = None,
    insurance_card: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Chọn khóa đồng nhất bệnh nhân, trả về (system, value).

    Thứ tự ưu tiên phản ánh phạm vi hiệu lực của từng loại định danh:

    1. Số CCCD - duy nhất toàn quốc, nên cùng một người khám ở hai bệnh viện vẫn
       quy về đúng một hồ sơ trên trục.
    2. Thẻ BHYT - cũng toàn quốc, dùng khi chưa có CCCD.
    3. Mã bệnh án + mã cơ sở - phương án cuối. Mã bệnh án chỉ có nghĩa trong
       phạm vi một bệnh viện, nên bắt buộc phải kèm mã cơ sở; thiếu nó thì
       "BN001" của hai bệnh viện khác nhau sẽ bị trộn thành một người.
    """
    cccd = (citizen_id or "").strip()
    if cccd:
        return SYSTEM_CCCD, cccd
    bhyt = (insurance_card or "").strip()
    if bhyt:
        return SYSTEM_BHYT, bhyt
    return mrn_system(facility_code), patient_id


def patient_local_key(patient_id: str, facility_code: str) -> Tuple[str, str]:
    """
    Định danh nội viện của bệnh nhân: mã bệnh án gắn với cơ sở đã cấp nó.

    Khác `patient_match_key` ở chỗ KHÔNG phụ thuộc vào việc HIS có gửi kèm CCCD
    hay BHYT. Mã bệnh án luôn có mặt (là trường bắt buộc) và không đổi giữa các
    lần khám tại cùng một viện, nên đây là thứ duy nhất dùng làm khóa nghiệp vụ
    ổn định được.

    `patient_match_key` vẫn là khóa ĐỒNG NHẤT giữa các viện - hai việc khác nhau:
        đồng nhất liên viện  -> CCCD/BHYT, phạm vi toàn quốc
        ổn định khóa nội viện -> mã bệnh án, phạm vi một viện
    Gộp hai việc vào một hàm là nguyên nhân khiến khóa đổi giữa chừng.
    """
    return mrn_system(facility_code), patient_id


def patient_identifier_candidates(
    patient_id: str,
    facility_code: str,
    citizen_id: Optional[str] = None,
    insurance_card: Optional[str] = None,
) -> list:
    """
    Mọi định danh mà bệnh nhân này CÓ THỂ đã được ghi trên trục ở lần trước.

    Cần thiết vì hồ sơ đầy dần theo thời gian: lần đầu chỉ có mã bệnh án, lần sau
    mới có CCCD. Tra theo đúng một định danh thì lần sau không tìm thấy bản cũ và
    trục đẻ thêm một bệnh nhân nữa cho cùng một người.

    Trả về danh sách (system, value) đã khử trùng lặp, giữ thứ tự ưu tiên.
    """
    ung_vien = []
    for system, value in (
        (SYSTEM_CCCD, (citizen_id or "").strip()),
        (SYSTEM_BHYT, (insurance_card or "").strip()),
        (mrn_system(facility_code), (patient_id or "").strip()),
    ):
        if value and (system, value) not in ung_vien:
            ung_vien.append((system, value))
    return ung_vien


def build_fhir_patient_resource(
    patient_id: str,
    patient_name: str,
    gender: Optional[str] = None,
    birth_date: Optional[str] = None,
    facility_code: str = "",
    citizen_id: Optional[str] = None,
    insurance_card: Optional[str] = None,
) -> dict:
    """
    Dựng FHIR Patient Resource (R4).

    Giới tính và ngày sinh được đưa vào đầy đủ: bản trước bỏ qua hai trường này
    dù HIS đã có, khiến hồ sơ liên thông lên EMR bị mất dữ liệu hành chính.

    Bệnh nhân mang nhiều định danh cùng lúc: CCCD và BHYT có giá trị toàn quốc,
    còn mã bệnh án đi kèm mã cơ sở đã cấp nó.
    """
    identifiers = [{
        "system": mrn_system(facility_code),
        "value": patient_id,
        "assigner": {"display": facility_code} if facility_code else None,
    }]
    identifiers[0] = {k: v for k, v in identifiers[0].items() if v is not None}

    if (citizen_id or "").strip():
        identifiers.insert(0, {"system": SYSTEM_CCCD, "value": citizen_id.strip()})
    if (insurance_card or "").strip():
        identifiers.append({"system": SYSTEM_BHYT, "value": insurance_card.strip()})

    # Cũng không đặt "id" - xem lý do ở build_fhir_condition_resource. Định danh
    # nằm ở `identifier` và đó là khóa để conditional update tìm lại bản ghi.
    resource = {
        "resourceType": "Patient",
        "identifier": identifiers,
        "name": [{"text": patient_name, "use": "official"}],
    }

    mapped_gender = GENDER_MAP.get((gender or "").strip().lower())
    if mapped_gender:
        resource["gender"] = mapped_gender
    if birth_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth_date.strip()):
        resource["birthDate"] = birth_date.strip()

    return resource
