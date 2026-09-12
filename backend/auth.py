# -*- coding: utf-8 -*-
"""
Xác thực bên gọi bằng khóa API gắn với cơ sở khám chữa bệnh (T1.4a).

Trước đây mã cơ sở là thứ HIS TỰ KHAI trong thân yêu cầu, và Gateway chỉ có một
cờ môi trường để quyết định có tin lời khai đó hay không. Nay mỗi HIS được cấp
một khóa; khóa tra ra cơ sở, và cơ sở đó là danh tính đã xác thực của bên gọi.

Thiết kế cố ý gói trọn trong một module, tách khỏi tầng FHIR:

* `KhoKhoa`       - kho khóa trên đĩa, lưu BĂM SHA-256, không lưu bản rõ.
* `xac_thuc_ben_goi` - dependency của FastAPI, đọc header `X-SMIG-Api-Key`,
                    đặt danh tính vào một ContextVar cho phần còn lại của yêu cầu.
* `ben_goi_hien_tai` - phần nghiệp vụ (`resolve_facility`) hỏi danh tính ở đây.
* CLI `python -m backend.auth` - cấp, liệt kê, thu hồi khóa.

Triển khai thật thay khóa API bằng OAuth2 client credentials hoặc mTLS thì chỉ
thay `xac_thuc_ben_goi` (cách lấy danh tính), còn `BenGoi` và mọi chỗ dùng nó
giữ nguyên. Đó là lý do phần nghiệp vụ không bao giờ đọc header trực tiếp.

Khóa đi trong HEADER, không đi trong query string: URL nằm trong access log,
Referer và log của proxy, còn header thì không.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Header, HTTPException, Request

from backend.fhir_helper import validate_facility_code

# --- Cấu hình -------------------------------------------------------------
# Kho khóa mặc định nằm cạnh dữ liệu chạy của Gateway và đã có trong .gitignore:
# tệp này chứa băm của khóa và mã cơ sở thật, không thuộc về kho mã nguồn.
KEY_FILE = os.getenv(
    "SMIG_API_KEY_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "api_keys.json"),
)

# Có bắt buộc khóa với các đường liên thông hay không. Ba giá trị:
#   "1"    - luôn bắt buộc. Không có khóa nào đang hiệu lực thì Gateway DỪNG lúc
#            khởi động, vì chạy tiếp là mọi yêu cầu đều 401 mà không ai hiểu vì sao.
#   "0"    - không bắt buộc. Bên gọi không mang khóa đi đường cũ (mã cơ sở lấy
#            từ cấu hình, hoặc tự khai nếu bật SMIG_ALLOW_CLIENT_FACILITY).
#   "auto" - MẶC ĐỊNH: bắt buộc ngay khi kho có ít nhất một khóa đang hiệu lực.
#            Cấp khóa là hành động quyết định "từ nay phải xác thực"; cấp rồi mà
#            vẫn nhận bên gọi nặc danh là mặc định sai. Ngược lại, máy chưa cấp
#            khóa nào là môi trường trình diễn mới dựng, chạy thẳng được.
# Dù ở chế độ nào, một khóa SAI hoặc ĐÃ THU HỒI luôn bị 401 - không bao giờ âm
# thầm hạ xuống thành bên gọi nặc danh.
REQUIRE_API_KEY = os.getenv("SMIG_REQUIRE_API_KEY", "auto").strip().lower()

# Chỉ nhóm liên thông cần khóa. Nhóm NLP (`/api/standardize`, `/api/icd10`) để
# mở: đó là tra cứu văn bản -> mã, không mang danh tính bệnh nhân hay cơ sở, và
# khối NLP là phần cố ý không đổi trong T1. `GET /health` cũng mở để giám sát.
DUONG_DAN_CAN_KHOA = ("/api/fhir/",)

HEADER_KHOA = "X-SMIG-Api-Key"
TIEN_TO = "smig_"


# --- Danh tính bên gọi ------------------------------------------------------
@dataclass(frozen=True)
class BenGoi:
    """Danh tính đã xác thực của một yêu cầu: cơ sở nào, bằng khóa nào."""
    facility_code: str
    facility_name: str
    key_prefix: str
    label: str = ""


_ben_goi: ContextVar[Optional[BenGoi]] = ContextVar("smig_ben_goi", default=None)


def ben_goi_hien_tai() -> Optional[BenGoi]:
    """
    Danh tính của yêu cầu đang xử lý, hoặc None nếu bên gọi không mang khóa.

    Đọc từ ContextVar chứ không từ tham số endpoint, để chữ ký các endpoint giữ
    nguyên: bộ test gọi thẳng hàm endpoint và các mục T1.2-T1.5 thêm tài nguyên
    mới không phải mang theo tham số xác thực.
    """
    return _ben_goi.get()


def dat_ben_goi(ben_goi: Optional[BenGoi]) -> None:
    """Dành cho test và cho tầng xác thực; phần nghiệp vụ không gọi hàm này."""
    _ben_goi.set(ben_goi)


# --- Kho khóa --------------------------------------------------------------
def _bam(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tien_to_cua(token: str) -> str:
    """`smig_3f9a1c2e_<bí mật>` -> `smig_3f9a1c2e`. Tiền tố là nhãn tra cứu, không bí mật."""
    parts = token.split("_", 2)
    return "_".join(parts[:2]) if len(parts) >= 2 else ""


class KhoKhoa:
    """
    Kho khóa API trên đĩa.

    Mỗi bản ghi giữ: tiền tố (để tra cứu và thu hồi), băm SHA-256 của khóa, cơ
    sở mà khóa đại diện, nhãn, thời điểm cấp và thời điểm thu hồi. Bản rõ của
    khóa chỉ hiện ra ĐÚNG MỘT LẦN lúc cấp; mất thì cấp khóa mới, không khôi phục.

    Tệp được đọc lại khi thay đổi trên đĩa, nên thu hồi một khóa có hiệu lực mà
    không phải khởi động lại Gateway - lúc cần thu hồi thường là lúc gấp.
    """

    def __init__(self, path: str):
        self.path = path
        self._records: Dict[str, dict] = {}
        self._mtime: Optional[float] = None
        self.nap()

    # -- đọc/ghi --
    def nap(self) -> None:
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            self._records, self._mtime = {}, None
            return
        if mtime == self._mtime:
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("keys", []) if isinstance(data, dict) else []
        self._records = {r["prefix"]: r for r in records if r.get("prefix")}
        self._mtime = mtime

    def _ghi(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "keys": list(self._records.values())},
                      f, ensure_ascii=False, indent=2)
        self._mtime = os.path.getmtime(self.path)

    # -- nghiệp vụ --
    def cap(self, facility_code: str, facility_name: str, label: str = "",
            allow_demo: bool = False) -> str:
        """
        Cấp một khóa mới cho một cơ sở, trả về BẢN RÕ - lần duy nhất nó xuất hiện.

        Mã cơ sở đi qua đúng cửa kiểm dạng của T1.1(b): cấp khóa cho mã sai dạng
        là cho một bên gọi ghi hồ sơ dưới một mã không đối chiếu được với đâu.
        Đây cũng là chỗ khép lỗ hở "mã tự khai không bị kiểm dạng" của T1.1.
        """
        self.nap()
        code = validate_facility_code(facility_code, allow_demo)
        name = (facility_name or "").strip()
        if not name:
            raise ValueError("Thiếu tên cơ sở: tên đi vào Organization.name của mọi bản ghi.")

        while True:
            # Toàn hex nên khóa chỉ có đúng hai dấu gạch dưới: tách tiền tố
            # bằng mắt hay bằng mã đều không nhầm. 24 byte = 192 bit ngẫu nhiên.
            token = f"{TIEN_TO}{secrets.token_hex(4)}_{secrets.token_hex(24)}"
            prefix = tien_to_cua(token)
            if prefix not in self._records:
                break

        self._records[prefix] = {
            "prefix": prefix,
            "sha256": _bam(token),
            "facility_code": code,
            "facility_name": name,
            "label": (label or "").strip(),
            "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "revoked_at": None,
        }
        self._ghi()
        return token

    def thu_hoi(self, prefix: str) -> bool:
        """Đánh dấu thu hồi, KHÔNG xóa: giữ lại để nhật ký còn tra được khóa nào đã dùng."""
        self.nap()
        rec = self._records.get((prefix or "").strip())
        if not rec or rec.get("revoked_at"):
            return False
        rec["revoked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._ghi()
        return True

    def tra(self, token: str) -> Optional[BenGoi]:
        """Khóa hợp lệ và còn hiệu lực -> danh tính; mọi trường hợp khác -> None."""
        self.nap()
        token = (token or "").strip()
        rec = self._records.get(tien_to_cua(token))
        if not rec or rec.get("revoked_at"):
            return None
        # So sánh thời gian hằng: so chuỗi thường thoát sớm ở ký tự đầu lệch,
        # và độ trễ đó đo được. Với băm 64 ký tự thì rủi ro nhỏ, nhưng làm đúng
        # rẻ hơn là giải thích vì sao không làm.
        if not hmac.compare_digest(_bam(token), rec.get("sha256", "")):
            return None
        return BenGoi(rec["facility_code"], rec["facility_name"],
                      rec["prefix"], rec.get("label", ""))

    def dang_hieu_luc(self) -> int:
        self.nap()
        return sum(1 for r in self._records.values() if not r.get("revoked_at"))

    def liet_ke(self) -> List[dict]:
        self.nap()
        return [{k: v for k, v in r.items() if k != "sha256"} for r in self._records.values()]


kho = KhoKhoa(KEY_FILE)


# --- Chế độ ----------------------------------------------------------------
def yeu_cau_khoa() -> bool:
    """Hiệu lực thực tế của SMIG_REQUIRE_API_KEY sau khi tính cả chế độ auto."""
    if REQUIRE_API_KEY in {"1", "true", "yes", "on"}:
        return True
    if REQUIRE_API_KEY in {"0", "false", "no", "off"}:
        return False
    return kho.dang_hieu_luc() > 0


def chot_cau_hinh_khoa() -> str:
    """
    Kiểm cấu hình khóa lúc khởi động; trả về một dòng mô tả để in ra console.

    Cùng triết lý với `_chot_ma_co_so`: sai cấu hình phải lộ ra lúc bật máy.
    Bắt buộc khóa mà kho trống là cấu hình chết - mọi yêu cầu liên thông đều
    401 và không HIS nào có cách nào lấy được khóa.
    """
    if REQUIRE_API_KEY not in {"1", "true", "yes", "on", "0", "false", "no", "off", "auto"}:
        raise RuntimeError(
            f"Cấu hình SMIG_REQUIRE_API_KEY='{REQUIRE_API_KEY}' không hợp lệ. "
            f"Dùng 1, 0 hoặc auto.")
    so_khoa = kho.dang_hieu_luc()
    if yeu_cau_khoa() and so_khoa == 0:
        raise RuntimeError(
            f"SMIG_REQUIRE_API_KEY bật nhưng kho khóa '{kho.path}' không có khóa nào "
            f"đang hiệu lực. Cấp khóa bằng: python -m backend.auth issue "
            f"--facility <mã CSKCB> --name \"<tên cơ sở>\", hoặc đặt "
            f"SMIG_REQUIRE_API_KEY=0 nếu đang trình diễn không cần xác thực.")
    if yeu_cau_khoa():
        return (f"[AUTH] Đường liên thông /api/fhir/* YÊU CẦU khóa API "
                f"({so_khoa} khóa đang hiệu lực, kho: {kho.path}).")
    return ("[AUTH] KHÔNG yêu cầu khóa API: bên gọi nặc danh được chấp nhận trên "
            "/api/fhir/*. Chỉ dùng để trình diễn. Cấp khóa bằng "
            "`python -m backend.auth issue ...` để bật xác thực.")


# --- Dependency của FastAPI --------------------------------------------------
async def xac_thuc_ben_goi(
    request: Request,
    x_smig_api_key: Optional[str] = Header(
        None, alias=HEADER_KHOA,
        description="Khóa API do Gateway cấp cho cơ sở khám chữa bệnh."),
) -> None:
    """
    Cửa xác thực cho mọi route, gắn ở mức ứng dụng.

    Là hàm `async` có chủ ý: dependency đồng bộ bị FastAPI đưa sang thread pool,
    và ContextVar đặt trong thread đó không lan ngược về yêu cầu.
    """
    dat_ben_goi(None)
    if not request.url.path.startswith(DUONG_DAN_CAN_KHOA):
        return

    token = (x_smig_api_key or "").strip()
    if not token:
        if yeu_cau_khoa():
            raise HTTPException(
                status_code=401,
                detail=(f"Thiếu khóa API. Đường liên thông yêu cầu header "
                        f"'{HEADER_KHOA}: <khóa do Gateway cấp cho cơ sở>'."),
                headers={"WWW-Authenticate": f'ApiKey header="{HEADER_KHOA}"'},
            )
        return

    ben_goi = kho.tra(token)
    if ben_goi is None:
        # Không nói khóa sai hay đã thu hồi: hai thông tin đó chỉ giúp người
        # đang dò khóa, không giúp HIS đang cấu hình đúng.
        raise HTTPException(
            status_code=401,
            detail="Khóa API không hợp lệ hoặc đã bị thu hồi.",
            headers={"WWW-Authenticate": f'ApiKey header="{HEADER_KHOA}"'},
        )
    dat_ben_goi(ben_goi)


# --- CLI -------------------------------------------------------------------
def _cli(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.auth",
        description="Cấp, liệt kê, thu hồi khóa API của SMIG Gateway.")
    parser.add_argument("--file", default=KEY_FILE,
                        help=f"Kho khóa (mặc định: {KEY_FILE}, đổi bằng SMIG_API_KEY_FILE)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="Cấp khóa mới cho một cơ sở")
    p_issue.add_argument("--facility", required=True, help="Mã CSKCB 5 chữ số do BHXH cấp")
    p_issue.add_argument("--name", required=True, help="Tên cơ sở đã đăng ký với BHXH")
    p_issue.add_argument("--label", default="", help="Nhãn gợi nhớ, vd 'HIS Python cổng 8085'")
    p_issue.add_argument("--allow-demo", action="store_true",
                         help="Cho phép mã tự đặt kiểu BV-A-001 (chỉ để trình diễn)")

    sub.add_parser("list", help="Liệt kê khóa (không hiện bản rõ, không hiện băm)")

    p_revoke = sub.add_parser("revoke", help="Thu hồi một khóa theo tiền tố")
    p_revoke.add_argument("prefix", help="Tiền tố, vd smig_3f9a1c2e")

    args = parser.parse_args(argv)
    kho_cli = KhoKhoa(args.file)

    if args.cmd == "issue":
        try:
            token = kho_cli.cap(args.facility, args.name, args.label, args.allow_demo)
        except ValueError as exc:
            print(f"Không cấp được khóa: {exc}", file=sys.stderr)
            return 2
        print("Đã cấp khóa. Bản rõ dưới đây CHỈ HIỆN MỘT LẦN, hãy chép vào cấu hình của HIS:")
        print()
        print(f"    {token}")
        print()
        print(f"Cơ sở : {args.facility.strip()} - {args.name.strip()}")
        print(f"Tiền tố (để thu hồi): {tien_to_cua(token)}")
        print(f"Kho   : {args.file}")
        print(f"HIS gửi kèm header:  {HEADER_KHOA}: <khóa>")
        return 0

    if args.cmd == "list":
        ds = kho_cli.liet_ke()
        if not ds:
            print(f"Kho '{args.file}' chưa có khóa nào.")
            return 0
        for r in ds:
            trang_thai = f"THU HỒI {r['revoked_at']}" if r.get("revoked_at") else "hiệu lực"
            print(f"{r['prefix']}  {r['facility_code']}  {r['facility_name']}"
                  f"  [{r.get('label') or '-'}]  cấp {r['issued_at']}  {trang_thai}")
        return 0

    if args.cmd == "revoke":
        if kho_cli.thu_hoi(args.prefix):
            print(f"Đã thu hồi {args.prefix}. Gateway đang chạy sẽ từ chối khóa này ngay.")
            return 0
        print(f"Không có khóa '{args.prefix}' đang hiệu lực trong {args.file}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
