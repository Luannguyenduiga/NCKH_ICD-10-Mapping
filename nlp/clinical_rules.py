# -*- coding: utf-8 -*-
"""
Tri thức lâm sàng dùng cho tầng hậu xử lý của NLPEngine.

Lý do tồn tại của module này: mô hình SBERT chỉ đo độ tương đồng ngữ nghĩa, nên
nó KHÔNG phân biệt được các cặp đối lập vốn quyết định mã ICD-10:

    "phụ thuộc insuline" (E10) vs "không phụ thuộc insuline" (E11)
    "tăng huyết áp vô căn" (I10) vs "tăng huyết áp thứ phát" (I15)
    "chưa có biến chứng"  (x.9) vs "có biến chứng ..."      (x.0-x.8)

Ba cơ chế được định nghĩa ở đây:

1. BRIDGE_TERMS   - cầu nối từ vựng: thuật ngữ bác sĩ hay dùng ("tuýp 2") không
                    hề xuất hiện trong danh mục ICD-10 của Bộ Y tế (dùng
                    "không phụ thuộc insuline"). Ta nối thêm cụm chuẩn vào câu
                    truy vấn trước khi mã hóa embedding.
2. POLARITY_AXES  - trục đối lập: so nhãn của truy vấn với nhãn của ứng viên,
                    xung đột thì trừ điểm.
3. DIRECT_ALIASES - alias lâm sàng đã kiểm chứng, vừa dùng để cộng điểm cho mã
                    đích, vừa được mã hóa embedding bổ sung (từ đồng nghĩa thật,
                    thay cho các "synonym" chỉ là bản sao lowercase của tên bệnh).
"""

# ---------------------------------------------------------------------------
# 1. Viết tắt
# ---------------------------------------------------------------------------

# Bộ gốc - áp dụng cho CẢ câu truy vấn lẫn văn bản tham chiếu khi tạo embedding.
# KHÔNG sửa/thêm vào bộ này nếu không muốn phải tính lại toàn bộ embedding cache.
ABBREVIATIONS = {
    r"\bđtđ\b": "đái tháo đường",
    r"\btha\b": "tăng huyết áp",
    r"\bnmct\b": "nhồi máu cơ tim",
    r"\bhpq\b": "hen phế quản",
    r"\bcopd\b": "bệnh phổi tắc nghẽn mạn tính",
    r"\bgerd\b": "trào ngược dạ dày thực quản",
    r"\bckd\b": "suy thận mạn",
    r"\bđd\b": "dạ dày",
    r"\bt2\b": "tuýp 2",
    r"\btype 2\b": "tuýp 2",
    r"\btyp 2\b": "tuýp 2",
    r"\btype ii\b": "tuýp 2",
    r"\btyp ii\b": "tuýp 2",
}

# Bộ mở rộng - CHỈ áp dụng cho câu truy vấn của bác sĩ, không đụng tới embedding
# cache. Thêm viết tắt mới vào đây là an toàn.
QUERY_ABBREVIATIONS = {
    r"\bt1\b": "tuýp 1",
    r"\btype 1\b": "tuýp 1",
    r"\btyp 1\b": "tuýp 1",
    r"\btype i\b": "tuýp 1",
    r"\bt1dm\b": "đái tháo đường tuýp 1",
    r"\bt2dm\b": "đái tháo đường tuýp 2",
    r"\bdm\b": "đái tháo đường",
    r"\bhta\b": "tăng huyết áp",
    r"\bhtn\b": "tăng huyết áp",
    r"\bcha\b": "tăng huyết áp",
    r"\bcao huyết áp\b": "tăng huyết áp",
    r"\bhuyết áp cao\b": "tăng huyết áp",
    r"\bnmn\b": "nhồi máu não",
    r"\btbmmn\b": "tai biến mạch máu não",
    r"\bđqn\b": "đột quỵ não",
    r"\bsuy tim\b": "suy tim",
    r"\bstm\b": "suy thận mạn",
    r"\bstc\b": "suy thận cấp",
    r"\bvpq\b": "viêm phế quản",
    r"\bvp\b": "viêm phổi",
    r"\bvgb\b": "viêm gan b",
    r"\bvgc\b": "viêm gan c",
    r"\bxgan\b": "xơ gan",
    r"\bloét dd\b": "loét dạ dày",
    r"\bvdd\b": "viêm dạ dày",
    r"\bhctht\b": "hội chứng thận hư",
    r"\bhc\b": "hội chứng",
    r"\brlln\b": "rối loạn lipid máu",
    r"\brlcn\b": "rối loạn chức năng",
    r"\bkph\b": "không phát hiện",
    r"\btd\b": "theo dõi",
    r"\bcđ\b": "chẩn đoán",
    r"\bcdls\b": "chẩn đoán lâm sàng",
    r"\bbn\b": "bệnh nhân",
    r"\bgđ\b": "giai đoạn",
    r"\bbc\b": "biến chứng",
    r"\bmạn tính\b": "mãn tính",
    r"\bmạn\b": "mãn tính",
    r"\btiểu đường\b": "đái tháo đường",
    r"\bnguyên phát\b": "vô căn",
    r"\bvô căn\b": "vô căn",
    r"\btiên phát\b": "vô căn",
}

# Tiền tố "nhiễu" ở đầu câu chẩn đoán - loại bỏ để câu truy vấn sát thuật ngữ y khoa.
# Được áp dụng LẶP cho tới khi không còn thay đổi, vì các tiền tố hay xếp chồng
# lên nhau ("Hiện tại bệnh nhân được chẩn đoán: ...").
NOISE_PREFIXES = [
    r"^chẩn đoán lâm sàng\s*[:\-]?\s*",
    r"^chẩn đoán xác định\s*[:\-]?\s*",
    r"^chẩn đoán\s*[:\-]?\s*",
    r"^bệnh nhân (bị|có|được chẩn đoán|được chẩn|mắc|vào viện vì|nhập viện vì)\s+",
    r"^bệnh nhân\s+",
    r"^bn (nam|nữ)?\s*\d*\s*(tuổi|t)?\s*[,\-]?\s*",
    r"^theo dõi\s+",
    r"^nghi ngờ\s+",
    r"^hiện tại\s+",
    r"^hiện\s+",
    r"^kết luận\s*[:\-]?\s*",
]

# Đuôi "nhiễu" - thông tin điều trị/hành chính không giúp xác định mã bệnh nhưng
# lại kéo lệch vector ngữ nghĩa của cả câu.
NOISE_SUFFIXES = [
    r"\s+đang điều trị.*$",
    r"\s+điều trị (ngoại trú|nội trú).*$",
    r"\s+đã điều trị.*$",
    r"\s+tiếp tục theo dõi.*$",
    r"\s+hẹn (tái khám|khám lại).*$",
    r"\s+ra viện.*$",
    r"\s+chuyển tuyến.*$",
]

# Từ dừng - bỏ khi tính điểm trùng lặp từ vựng.
STOPWORDS = {
    "bệnh", "nhân", "bị", "có", "và", "kèm", "theo", "dõi", "của", "với", "các",
    "được", "chẩn", "đoán", "lâm", "sàng", "hiện", "tại", "trên", "do", "là",
    "một", "những", "này", "đó", "ở", "về", "cho", "đang", "khi", "mức", "độ",
    "nghi", "ngờ", "tiền", "sử", "thể", "dạng", "loại", "khác", "kèm theo",
}


# ---------------------------------------------------------------------------
# 2. Cầu nối từ vựng
# ---------------------------------------------------------------------------
# (cụm bác sĩ dùng, cụm chuẩn trong danh mục ICD-10 Bộ Y tế)
# Cụm chuẩn được NỐI THÊM vào câu truy vấn, không thay thế, để giữ ngữ cảnh gốc.
BRIDGE_TERMS = [
    ("tuýp 2", "không phụ thuộc insuline"),
    ("tuýp 1", "phụ thuộc insuline"),
    ("vô căn", "nguyên phát"),
    # Toàn bộ chương C (553 mã) dùng "u ác", không có mã nào ghi "ung thư" - đây
    # là khoảng cách từ vựng lớn nhất giữa lời bác sĩ và danh mục Bộ Y tế.
    ("ung thư", "u ác"),
    ("ung bướu", "u ác"),
    ("khối u ác tính", "u ác"),
    ("đường huyết", "glucose máu"),
    ("đường máu", "glucose máu"),
    ("u xơ tử cung", "u cơ trơn tử cung"),
    ("nhiễm khuẩn huyết", "nhiễm trùng huyết"),
    ("nhiễm khuẩn", "nhiễm trùng"),
    ("viêm bể thận", "viêm mô kẽ ống thận"),
    ("nghiện rượu", "hội chứng nghiện"),
]


# ---------------------------------------------------------------------------
# 3. Trục đối lập ngữ nghĩa
# ---------------------------------------------------------------------------
# Mỗi trục: nhãn được dò theo THỨ TỰ, nhãn đầu tiên khớp sẽ thắng.
# Đặt biến thể phủ định ("không phụ thuộc") TRƯỚC biến thể khẳng định
# ("phụ thuộc"), vì chuỗi khẳng định là chuỗi con của chuỗi phủ định.
POLARITY_AXES = [
    {
        "name": "diabetes_type",
        "scope": ["đái tháo đường", "diabetes", "insuline", "insulin"],
        "labels": [
            ("type2", ["không phụ thuộc insulin", "tuýp 2", "típ 2", "non-insulin"]),
            ("type1", ["phụ thuộc insulin", "tuýp 1", "típ 1", "insulin-dependent"]),
        ],
        "penalty": 0.18,
        "bonus": 0.04,
    },
    {
        "name": "hypertension_cause",
        "scope": ["huyết áp", "hypertension"],
        "labels": [
            ("secondary", ["thứ phát", "secondary"]),
            ("primary", ["vô căn", "nguyên phát", "tiên phát", "essential", "primary"]),
        ],
        "penalty": 0.16,
        "bonus": 0.05,
    },
    {
        # Phạm vi phải bao gồm cả tên gọi của từng biến chứng cụ thể: danh mục ghi
        # "(Có hôn mê)", "(Có nhiễm toan ceton)" chứ không ghi chữ "biến chứng".
        "name": "complication",
        "scope": ["biến chứng", "complication", "hôn mê", "nhiễm toan", "coma", "ketoacidosis"],
        "labels": [
            ("without", ["chưa có biến chứng", "không biến chứng", "không có biến chứng",
                         "without complication"]),
            ("with", ["có biến chứng", "có đa biến chứng", "with complication",
                      "có hôn mê", "hôn mê", "có nhiễm toan", "nhiễm toan"]),
        ],
        "penalty": 0.14,
        "bonus": 0.04,
    },
    {
        "name": "acuity",
        "scope": ["cấp", "mãn", "mạn", "acute", "chronic"],
        "labels": [
            ("chronic", ["mãn tính", "mạn tính", "chronic"]),
            ("acute", ["cấp tính", "cấp", "acute"]),
        ],
        "penalty": 0.10,
        "bonus": 0.03,
    },
    {
        "name": "allergy",
        "scope": ["dị ứng", "allergic"],
        "labels": [
            ("non_allergic", ["không dị ứng", "non-allergic", "nonallergic"]),
            ("allergic", ["dị ứng", "allergic"]),
        ],
        "penalty": 0.10,
        "bonus": 0.03,
    },
]

# ---------------------------------------------------------------------------
# 3b. Mức chi tiết của mã
# ---------------------------------------------------------------------------
# Trong mỗi khối ICD-10 luôn có một mã "không đặc hiệu" (thường là .9) và một mã
# "khác" (thường là .8). Khi bác sĩ chỉ ghi tên bệnh mà không nêu thể/biến chứng,
# mã đúng là mã "không đặc hiệu" - KHÔNG phải mã "khác". Ngược lại, khi bác sĩ có
# nêu thể bệnh thì mã "không đặc hiệu" là sai vì đã bỏ mất thông tin đó.
UNSPECIFIED_MARKERS = [
    "không đặc hiệu", "không phân loại", "không xác định", "không rõ",
    "unspecified", "nos",
]
OTHER_MARKERS = ["khác", "other"]

UNSPECIFIED_BONUS = 0.06     # bác sĩ không nêu thể bệnh -> mã .9 là đúng
UNSPECIFIED_PENALTY = 0.06   # bác sĩ có nêu thể bệnh -> mã .9 làm mất thông tin
OTHER_PENALTY = 0.04         # mã .8 chỉ dùng khi thể bệnh đã nêu mà không có mã riêng


# ---------------------------------------------------------------------------
# 3c. Nhãn phụ thuộc nhóm cha
# ---------------------------------------------------------------------------
# Chương II đặt tên một số mã bằng VỊ TRÍ GIẢI PHẪU trần: D16.6 tên chỉ là "Cột
# sống", nghĩa "u lành của xương và sụn khớp" nằm ở nhóm cha chứ không nằm trong
# tên mã. Những nhãn như vậy khớp NER y hệt một tên bệnh, nên khi tách một vế
# thành nhiều chẩn đoán thì "cột sống" trong "thoát vị đĩa đệm cột sống" bị nhận
# thành chẩn đoán u xương riêng - sinh ra một bệnh không có trong hồ sơ.
#
# Dấu hiệu nhận biết: tên mã KHÔNG chứa từ khóa khối u nào trong khi tên nhóm cha
# thì có, tức nghĩa bệnh chỉ tồn tại ở nhóm cha. Toàn danh mục chỉ 27/889 mã
# chương II rơi vào diện này, và luật chỉ áp khi tách vế nên không ảnh hưởng tới
# câu chẩn đoán chỉ có một bệnh.
NEOPLASM_TERMS = [
    r"u", r"ung thư", r"bướu", r"khối u", r"đa u tủy", r"polyp", r"nốt ruồi",
    r"bạch cầu", r"tân sinh", r"loạn sản", r"di căn", r"ác tính", r"lành tính",
    r"carcinom\w*", r"sarcom\w*", r"lympho\w*", r"melanom\w*", r"mesotheliom\w*",
]


# ---------------------------------------------------------------------------
# 4. Cổng chương ICD-10
# ---------------------------------------------------------------------------
# Chương O (thai sản), S/T (chấn thương, ngộ độc), V-Y (nguyên nhân ngoại sinh)
# chỉ hợp lệ khi câu chẩn đoán có ngữ cảnh tương ứng. Nếu không, trừ điểm để
# tránh trường hợp "đái tháo đường tuýp 2" bị gán O24.0 (ĐTĐ ở thai phụ).
CHAPTER_GATES = [
    {
        "prefixes": ("O",),
        "required_any": ["thai", "sản", "chuyển dạ", "sau đẻ", "sau sinh", "mang thai",
                         "sản phụ", "thai kỳ", "hậu sản", "sảy thai", "thai nghén"],
        "penalty": 0.20,
    },
    {
        "prefixes": ("S", "T"),
        "required_any": ["chấn thương", "vết thương", "gãy", "bỏng", "ngộ độc", "tai nạn",
                         "trật khớp", "bong gân", "đứt", "rách", "dập", "va đập", "té",
                         "ngã", "dị vật"],
        "penalty": 0.18,
    },
    {
        "prefixes": ("V", "W", "X", "Y"),
        "required_any": ["tai nạn", "ngã", "té", "va chạm", "đuối nước", "hỏa hoạn",
                         "tự tử", "hành hung", "giao thông", "điện giật"],
        "penalty": 0.22,
    },
    {
        "prefixes": ("Z",),
        "required_any": ["khám", "tiêm chủng", "sàng lọc", "tư vấn", "tái khám",
                         "theo dõi sức khỏe", "tiền sử gia đình"],
        "penalty": 0.12,
    },
]


# ---------------------------------------------------------------------------
# 5. Alias lâm sàng đã kiểm chứng
# ---------------------------------------------------------------------------
# Đây là "từ đồng nghĩa tiếng Việt" thật sự của hệ thống. Mỗi alias được:
#   (a) mã hóa embedding bổ sung và gắn vào mã ICD-10 tương ứng,
#   (b) cộng điểm trực tiếp cho mã đó khi alias xuất hiện trong câu chẩn đoán.
# Mã đầu tiên trong danh sách là mã ưu tiên nhất.
DIRECT_ALIASES = {
    # --- Nội tiết ---
    "đái tháo đường tuýp 2": ["E11.9", "E11"],
    "đái tháo đường tuýp 2 không biến chứng": ["E11.9"],
    "đái tháo đường tuýp 2 có biến chứng thận": ["E11.2†"],
    "đái tháo đường tuýp 2 có biến chứng mắt": ["E11.3†"],
    "tiểu đường tuýp 2": ["E11.9", "E11"],
    "đái tháo đường tuýp 1": ["E10.9", "E10"],
    "tiểu đường tuýp 1": ["E10.9", "E10"],
    "đái tháo đường thai kỳ": ["O24.4"],
    "rối loạn lipid máu": ["E78.5"],
    "tăng cholesterol máu": ["E78.0"],
    "béo phì": ["E66.9"],
    "suy giáp": ["E03.9"],
    "cường giáp": ["E05.9"],
    "bướu giáp đơn thuần": ["E04.9"],
    "gút": ["M10.9"],
    "bệnh gout": ["M10.9"],

    # --- Tim mạch ---
    "tăng huyết áp vô căn": ["I10"],
    "tăng huyết áp nguyên phát": ["I10"],
    "tăng huyết áp": ["I10"],
    "cao huyết áp": ["I10"],
    "cao huyết áp vô căn": ["I10"],
    "tăng huyết áp thứ phát": ["I15.9", "I15"],
    "bệnh tim do tăng huyết áp": ["I11.9"],
    "nhồi máu cơ tim cấp": ["I21.9", "I21"],
    "nhồi máu cơ tim": ["I21.9", "I21"],
    "đau thắt ngực": ["I20.9"],
    "đau thắt ngực không ổn định": ["I20.0"],
    "suy tim": ["I50.9"],
    "suy tim sung huyết": ["I50.0"],
    "rung nhĩ": ["I48"],
    "nhồi máu não": ["I63.9"],
    "tai biến mạch máu não": ["I64"],
    "đột quỵ não": ["I64"],
    "xuất huyết não": ["I61.9"],
    "xơ vữa động mạch": ["I70.9"],
    "giãn tĩnh mạch chi dưới": ["I83.9"],

    # --- Hô hấp ---
    "hen phế quản": ["J45.9", "J45"],
    "hen phế quản cấp": ["J45.9", "J45"],
    "hen suyễn": ["J45.9", "J45"],
    "hen phế quản không dị ứng": ["J45.1"],
    "hen không dị ứng": ["J45.1"],
    "hen phế quản dị ứng": ["J45.0"],
    "hen dị ứng": ["J45.0"],
    "hen hỗn hợp": ["J45.8"],
    "cơn hen ác tính": ["J46"],
    "bệnh phổi tắc nghẽn mãn tính": ["J44.9", "J44"],
    "viêm phổi": ["J18.9"],
    "viêm phế quản cấp": ["J20.9"],
    "viêm phế quản mãn tính": ["J42"],
    "viêm họng cấp": ["J02.9"],
    "viêm xoang mãn tính": ["J32.9"],
    "viêm amidan cấp": ["J03.9"],
    "lao phổi": ["A15.3", "A16.2"],

    # --- Tiêu hóa ---
    "trào ngược dạ dày thực quản": ["K21.9", "K21"],
    "viêm dạ dày": ["K29.7"],
    "loét dạ dày": ["K25.9"],
    "loét tá tràng": ["K26.9"],
    "loét dạ dày tá tràng": ["K27.9", "K27"],
    "xơ gan": ["K74.6"],
    "viêm gan b mãn tính": ["B18.1"],
    "viêm gan c mãn tính": ["B18.2"],
    "viêm gan siêu vi b": ["B16.9"],
    "sỏi túi mật": ["K80.2"],
    "viêm ruột thừa cấp": ["K35.8"],
    "trĩ": ["I84.9"],
    "táo bón": ["K59.0"],

    # --- Thận tiết niệu ---
    "suy thận mãn tính": ["N18.9", "N18"],
    "suy thận mạn": ["N18.9", "N18"],
    "suy thận cấp": ["N17.9"],
    "sỏi thận": ["N20.0"],
    "nhiễm khuẩn đường tiết niệu": ["N39.0"],
    "viêm cầu thận cấp": ["N00.9"],
    "hội chứng thận hư": ["N04.9"],
    "phì đại tuyến tiền liệt": ["N40"],

    # --- Cơ xương khớp / Thần kinh ---
    "thoái hóa khớp gối": ["M17.9"],
    "thoái hóa cột sống thắt lưng": ["M47.8"],
    "thoát vị đĩa đệm": ["M51.2"],
    "viêm khớp dạng thấp": ["M06.9"],
    "loãng xương": ["M81.9"],
    "đau thần kinh tọa": ["M54.3"],
    "đau lưng": ["M54.5"],
    "động kinh": ["G40.9"],
    "đau nửa đầu": ["G43.9"],
    "mất ngủ": ["G47.0"],
    "sa sút trí tuệ": ["F03"],
    "trầm cảm": ["F32.9"],
    "rối loạn lo âu": ["F41.9"],

    # --- Nhiễm khuẩn / khác ---
    "sốt xuất huyết dengue": ["A90"],
    "sốt xuất huyết": ["A90"],
    "sốt rét": ["B54"],
    "tiêu chảy cấp": ["A09"],
    "thủy đậu": ["B01.9"],
    "quai bị": ["B26.9"],
    "zona": ["B02.9"],
    "thiếu máu thiếu sắt": ["D50.9"],
    "viêm kết mạc": ["H10.9"],
    "đục thủy tinh thể": ["H26.9"],
    "viêm tai giữa cấp": ["H66.9"],
    "viêm da cơ địa": ["L20.9"],
    "mày đay": ["L50.9"],
    "vẩy nến": ["L40.9"],
}


# ---------------------------------------------------------------------------
# 6. Sửa lỗi dữ liệu nguồn
# ---------------------------------------------------------------------------
# Một số dòng trong file Excel danh mục có nội dung sai lệch. Áp dụng ở tầng
# ứng dụng để không phải sinh lại toàn bộ embedding cache.
DATA_FIXES = {
    # File nguồn ghi nhầm tên tiếng Anh của I10 thành "Other rheumatic heart
    # diseases" (vốn là của I09.8). Tên đúng theo WHO ICD-10:
    "I10": {"name_en": "Essential (primary) hypertension"},
}


# ---------------------------------------------------------------------------
# 7. Chính sách độ tin cậy
# ---------------------------------------------------------------------------
# Ngưỡng quyết định trạng thái xác nhận của FHIR Condition. Mã có độ tin cậy
# thấp KHÔNG được tự động đánh dấu "confirmed" - bắt buộc bác sĩ duyệt lại.
CONFIDENCE_POLICY = {
    "auto_confirm": 85.0,   # >= 85%  -> confirmed
    "provisional": 60.0,    # >= 60%  -> provisional (chờ bác sĩ duyệt)
    "reject": 40.0,         # <  40%  -> không đủ căn cứ để liên thông
}


def confidence_band(confidence: float) -> str:
    """Phân loại độ tin cậy thành 3 mức dùng chung cho backend và giao diện."""
    if confidence >= CONFIDENCE_POLICY["auto_confirm"]:
        return "high"
    if confidence >= CONFIDENCE_POLICY["provisional"]:
        return "medium"
    return "low"


def verification_status_for(confidence: float) -> str:
    """Ánh xạ độ tin cậy sang Condition.verificationStatus theo HL7 FHIR R4."""
    if confidence >= CONFIDENCE_POLICY["auto_confirm"]:
        return "confirmed"
    if confidence >= CONFIDENCE_POLICY["provisional"]:
        return "provisional"
    return "unconfirmed"
