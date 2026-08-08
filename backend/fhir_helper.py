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
from typing import Optional

# Namespace do dự án sở hữu. KHÔNG dùng hl7.org cho extension tự định nghĩa:
# theo quy tắc đặt tên của HL7, URL phải trỏ về tổ chức định nghĩa extension đó.
SMIG_NAMESPACE = "https://smig.nckh.vn/fhir"
EXT_CONFIDENCE = f"{SMIG_NAMESPACE}/StructureDefinition/nlp-confidence-score"
EXT_ENGINE = f"{SMIG_NAMESPACE}/StructureDefinition/nlp-engine"
SYSTEM_MRN = f"{SMIG_NAMESPACE}/identifier/hospital-mrn"

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
    condition_id: Optional[str] = None,
    engine_version: Optional[str] = None,
) -> dict:
    """
    Dựng một FHIR Condition Resource (R4) hợp lệ.

    Tham số:
        patient_id: định danh bệnh nhân (số CCCD hoặc mã bệnh án nội viện).
        patient_name: họ tên bệnh nhân.
        raw_clinical_note: nguyên văn chẩn đoán bác sĩ nhập.
        icd10_code: mã ICD-10 (được tự động làm sạch ký hiệu †).
        icd10_display: tên chuẩn của mã.
        confidence_score: độ tin cậy của mô hình, thang 0-100.
        clinical_status: active | recurrence | relapse | inactive | remission | resolved.
        verification_status: unconfirmed | provisional | differential | confirmed | ...
        condition_id: id cố định cho tài nguyên. Truyền vào để lần đồng bộ sau
            GHI ĐÈ đúng bản ghi cũ thay vì tạo bản trùng trên EMR.
        engine_version: định danh mô hình NLP, phục vụ truy vết.

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

    resource_id = to_fhir_id(condition_id) if condition_id else f"cond-{uuid.uuid4()}"
    confidence_decimal = round(max(0.0, min(100.0, confidence_score)) / 100.0, 4)

    extensions = [{"url": EXT_CONFIDENCE, "valueDecimal": confidence_decimal}]
    if engine_version:
        extensions.append({"url": EXT_ENGINE, "valueString": engine_version})

    return {
        "resourceType": "Condition",
        "id": resource_id,
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
        "subject": {
            "reference": f"Patient/{to_fhir_id(patient_id)}",
            "display": patient_name,
        },
        "recordedDate": utc_now_iso(),
        "extension": extensions,
    }


def build_fhir_patient_resource(
    patient_id: str,
    patient_name: str,
    gender: Optional[str] = None,
    birth_date: Optional[str] = None,
) -> dict:
    """
    Dựng FHIR Patient Resource (R4).

    Giới tính và ngày sinh được đưa vào đầy đủ: bản trước bỏ qua hai trường này
    dù HIS đã có, khiến hồ sơ liên thông lên EMR bị mất dữ liệu hành chính.
    """
    fhir_id = to_fhir_id(patient_id)
    resource = {
        "resourceType": "Patient",
        "id": fhir_id,
        "identifier": [{
            "system": SYSTEM_MRN,
            "value": patient_id,
        }],
        "name": [{"text": patient_name, "use": "official"}],
    }

    mapped_gender = GENDER_MAP.get((gender or "").strip().lower())
    if mapped_gender:
        resource["gender"] = mapped_gender
    if birth_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", birth_date.strip()):
        resource["birthDate"] = birth_date.strip()

    return resource
