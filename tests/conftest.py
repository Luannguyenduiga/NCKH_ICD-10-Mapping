# -*- coding: utf-8 -*-
"""
Đồ dùng chung cho bộ kiểm thử liên thông.

Bộ này KHÔNG nạp mô hình NLP và KHÔNG cần Docker: nó kiểm phần sinh HL7 FHIR,
chốt chặn mã cơ sở và luồng bệnh án của HIS. Chất lượng nhận diện mã ICD-10 là
việc của `nlp/test_nlp.py`.
"""
import os
import sys
import tempfile
from urllib.parse import parse_qsl, unquote, urlparse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class FakeResponse:
    def __init__(self, body, status=200, location=None):
        self._body = body
        self.status_code = status
        self.text = str(body)
        self.headers = {"Location": location} if location else {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeEmr:
    """
    EMR Cloud giả lập trong bộ nhớ.

    Bắt chước đúng thứ mà đường liên thông dựa vào: conditional update
    (`PUT /{Type}?identifier=system|value`) - khớp một bản thì cập nhật bản đó,
    không khớp bản nào thì tạo mới và tự cấp id. Chính hành vi này quyết định
    hai bệnh viện có gộp hồ sơ hay không, nên giả lập sai chỗ này là bộ test mất
    hết ý nghĩa.
    """

    def __init__(self):
        self.store = {"Patient": {}, "Condition": {}, "Organization": {}}
        self._seq = 0

    @staticmethod
    def _identifiers(resource):
        return {(i.get("system"), i.get("value"))
                for i in resource.get("identifier", []) if isinstance(i, dict)}

    @staticmethod
    def _tags(resource):
        return {(t.get("system"), t.get("code"))
                for t in (resource.get("meta") or {}).get("tag") or []
                if isinstance(t, dict)}

    @staticmethod
    def _truy_van(url, params):
        """
        Gộp tham số trên URL và tham số truyền rời thành một bảng tra.

        `parse_qsl` đã tự giải mã phần trăm, nên KHÔNG unquote trước đó: làm vậy
        là giải mã hai lần và một giá trị chứa '&' hay '=' sẽ bị cắt sai chỗ.
        """
        query = dict(parse_qsl(urlparse(url).query))
        query.update(params or {})
        return query

    @staticmethod
    def _cap_dinh_danh(bieu_thuc):
        """Tách `sys|val,sys2|val2` - dấu phẩy là phép HOẶC theo đặc tả FHIR."""
        return {tuple(m.split("|", 1)) for m in unquote(bieu_thuc).split(",") if "|" in m}

    def put(self, url, json=None, headers=None, timeout=None):
        rtype = url.split("/fhir/")[-1].split("?")[0]
        can_tim = self._cap_dinh_danh(url.split("identifier=")[-1])

        khop = [rid for rid, existing in self.store[rtype].items()
                if can_tim & self._identifiers(existing)]

        # Conditional update khớp NHIỀU bản thì máy chủ không được đoán bản nào.
        # HAPI trả 412 và không sửa gì; giả lập phải làm đúng vậy, nếu không thì
        # lỗi tách hồ sơ bệnh nhân sẽ đi qua bộ test mà không ai thấy.
        if len(khop) > 1:
            return FakeResponse(
                {"resourceType": "OperationOutcome",
                 "issue": [{"severity": "error",
                            "diagnostics": f"Khớp {len(khop)} bản {rtype}"}]},
                412)

        if khop:
            rid = khop[0]
            self.store[rtype][rid] = {**json, "id": rid}
            return FakeResponse(self.store[rtype][rid],
                                location=f"http://emr/fhir/{rtype}/{rid}")

        self._seq += 1
        rid = f"{rtype.lower()}-{self._seq}"
        self.store[rtype][rid] = {**json, "id": rid}
        return FakeResponse(self.store[rtype][rid], 201, f"http://emr/fhir/{rtype}/{rid}")

    def get(self, url, params=None, headers=None, timeout=None):
        query = self._truy_van(url, params)
        if "/Patient" in url:
            can_tim = self._cap_dinh_danh(query["identifier"])
            hits = [r for r in self.store["Patient"].values()
                    if can_tim & self._identifiers(r)]
            return FakeResponse({"entry": [{"resource": r} for r in hits]})
        if "/Condition" in url:
            if "subject" in query:
                hits = [c for c in self.store["Condition"].values()
                        if (c.get("subject") or {}).get("reference") == query["subject"]]
                return FakeResponse({"entry": [{"resource": c} for c in hits]})
            # Danh sách trên trục: mới nhất trước, lọc theo cơ sở nếu có _tag.
            hits = list(self.store["Condition"].values())[::-1]
            if query.get("_tag"):
                can_tim = self._cap_dinh_danh(query["_tag"])
                hits = [c for c in hits if can_tim & self._tags(c)]
            return FakeResponse(
                {"entry": [{"resource": c} for c in hits[:int(query.get("_count", 10))]]})
        return FakeResponse({}, 404)

    def delete(self, url, headers=None, timeout=None):
        rtype, _, rid = url.split("/fhir/")[-1].partition("/")
        if rid in self.store.get(rtype, {}):
            del self.store[rtype][rid]
            return FakeResponse({"resourceType": "OperationOutcome"})
        return FakeResponse({"resourceType": "OperationOutcome"}, 404)

    # --- Tiện ích đọc kết quả ---
    def codes(self):
        return sorted(c["code"]["coding"][0]["code"] for c in self.store["Condition"].values())

    def facilities(self):
        return sorted(c["meta"]["tag"][0]["code"] for c in self.store["Condition"].values())


@pytest.fixture
def emr():
    return FakeEmr()


@pytest.fixture
def gateway(emr, monkeypatch):
    """
    Gateway đã trỏ vào EMR giả lập.

    Đặt thẳng biến của module thay vì biến môi trường: `backend.main` chỉ được
    nạp một lần cho cả phiên pytest, nên đổi biến môi trường ở bài test thứ hai
    sẽ không có tác dụng và bài đó lặng lẽ kiểm nhầm cấu hình của bài trước.
    """
    from backend import main

    monkeypatch.setattr(main, "FACILITY_CODE", "BV-A-001")
    monkeypatch.setattr(main, "FACILITY_NAME", "Bệnh viện Đa khoa A")
    monkeypatch.setattr(main, "ALLOW_CLIENT_FACILITY", False)
    monkeypatch.setattr(main.requests, "put", emr.put)
    monkeypatch.setattr(main.requests, "get", emr.get)
    monkeypatch.setattr(main.requests, "delete", emr.delete)
    return main


@pytest.fixture
def make_his(monkeypatch):
    """
    Dựng một bản HIS với mã cơ sở và bệnh án cục bộ RIÊNG.

    Trả về hàm dựng để một bài test mô phỏng được hai bệnh viện: mỗi lần gọi là
    cấu hình lại module `hospital_his.server` sang bệnh viện tương ứng.
    """
    from hospital_his import server

    def _dung(facility_code="BV-A-001", facility_name="Bệnh viện Đa khoa A",
              emr_client=None, gateway_post=None):
        monkeypatch.setattr(server, "FACILITY_CODE", facility_code)
        monkeypatch.setattr(server, "FACILITY_NAME", facility_name)
        monkeypatch.setattr(
            server, "FHIR_MRN_SYSTEM",
            f"https://smig.nckh.vn/fhir/identifier/mrn/{server._to_fhir_id(facility_code)}")
        monkeypatch.setattr(
            server, "DB_PATH",
            os.path.join(tempfile.mkdtemp(), f"his_{facility_code}.sqlite"))
        server.init_db(force=True)

        if emr_client is not None:
            monkeypatch.setattr(server.requests, "get", emr_client.get)
        if gateway_post is not None:
            monkeypatch.setattr(server.requests, "post", gateway_post)
        return server

    return _dung
