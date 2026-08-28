# Smart Medical Interoperability Gateway (SMIG)

Đề tài Nghiên cứu Khoa học: **Đồng bộ chuẩn hóa dữ liệu liên thông các bệnh viện với mô hình NLP tự động chuẩn hóa và mã hóa sang chuẩn ICD-10**.

Hệ thống nhận chẩn đoán lâm sàng viết tự do bằng tiếng Việt, ánh xạ sang mã ICD-10 chuẩn, đóng gói theo **HL7 FHIR R4 (Condition Resource)** và đồng bộ lên trục dữ liệu EMR Cloud.

---

## 1. Kiến trúc

```
Trình duyệt
    │
    ├──► HIS mô phỏng  :8085   hospital_his/server.py + SQLite
    │         │ HTTP
    │         ▼
    └──► SMIG Gateway  :8000   backend/main.py + nlp/nlp_engine.py
              │ HL7 FHIR R4
              ▼
         EMR Cloud     :8090   HAPI FHIR (docker-compose.yml)
```

| Thành phần             | Cổng | Vai trò                                                                   |
| ------------------------ | ----- | -------------------------------------------------------------------------- |
| **SMIG Gateway**   | 8000  | API chuẩn hóa NLP, sinh FHIR, cầu nối tới EMR Cloud + giao diện demo |
| **HIS mô phỏng** | 8085  | Đóng vai bệnh viện gửi dữ liệu, có CSDL bệnh nhân riêng         |
| **EMR Cloud**      | 8090  | Máy chủ HAPI FHIR đóng vai trục dữ liệu y tế quốc gia             |

---

## 2. Yêu cầu hệ thống

* **Hệ điều hành:** Windows (PowerShell hoặc Command Prompt)
* **Python:** 3.10 hoặc 3.11 (khuyến nghị)
* **Docker Desktop:** bắt buộc nếu muốn dùng chức năng đồng bộ EMR Cloud
* **Dung lượng:** ~1 GB (mô hình 540 MB + cache embedding ~150 MB)

---

## 3. Khởi chạy

Hệ thống có **ba** tiến trình. Chạy theo thứ tự:

### Bước 1 — EMR Cloud (HAPI FHIR)

```powershell
docker compose up -d
```

Chờ ~60 giây rồi kiểm tra: [http://127.0.0.1:8090/fhir/metadata](http://127.0.0.1:8090/fhir/metadata)

> Bỏ qua bước này thì mọi thao tác đồng bộ sẽ báo lỗi 503.

### Bước 2 — SMIG Gateway

```powershell
.\run.ps1
```

hoặc bấm đúp **`run.bat`**. Lần đầu chạy sẽ tự tạo `.venv` và cài thư viện.
Giao diện: [http://127.0.0.1:8000](http://127.0.0.1:8000)

### Bước 3 — HIS mô phỏng (tùy chọn)

```powershell
.\hospital_his\run_his.ps1
```

Giao diện: [http://127.0.0.1:8085](http://127.0.0.1:8085)

### Bước 4 — Demo liên thông **hai** bệnh viện (tùy chọn)

Mỗi bệnh viện là **một bản Gateway riêng** với mã cơ sở riêng. Mở bốn cửa sổ:

```powershell
# Bệnh viện A — Gateway :8000
.\run.ps1 -Port 8000 -FacilityCode "BV-A-001" -FacilityName "Bệnh viện Đa khoa A"

# Bệnh viện B — Gateway :8001
.\run.ps1 -Port 8001 -FacilityCode "BV-B-002" -FacilityName "Bệnh viện Đa khoa B"

# HIS của bệnh viện B (Python) :8086
.\hospital_his\run_his.ps1 -Port 8086 -GatewayUrl "http://127.0.0.1:8001" -FacilityCode "BV-B-002"

# HIS của bệnh viện A (VNPT HIS - Java) :8089, trỏ sẵn tới Gateway :8000
.\VNPT_HIS\run_vnpt_his.ps1
```

`-FacilityCode` của HIS **phải trùng** với `-FacilityCode` của Gateway mà nó gọi.
Hai bên lệch nhau thì Gateway trả 403 và HIS tra cứu sai namespace mã bệnh án.

#### Cách nhẹ hơn: một Gateway phục vụ cả hai bệnh viện

Mô hình NLP chiếm vài GB RAM nên chạy hai bản Gateway trên một máy là quá nặng. Khi chỉ
thử nghiệm cục bộ, chạy **một** Gateway với `-AllowClientFacility` rồi cho các HIS trỏ
chung vào đó, mỗi HIS giữ mã cơ sở riêng:

```powershell
# Một Gateway duy nhất :8000, chấp nhận mã cơ sở do HIS khai
.\run.ps1 -Port 8000 -AllowClientFacility

# HIS bệnh viện A :8085
.\hospital_his\run_his.ps1 -Port 8085 -FacilityCode "BV-A-001" -FacilityName "Bệnh viện Đa khoa A"

# HIS bệnh viện B :8086 — CÙNG Gateway, khác mã cơ sở
.\hospital_his\run_his.ps1 -Port 8086 -FacilityCode "BV-B-002" -FacilityName "Bệnh viện Đa khoa B"
```

Hai bản HIS Python tự động dùng **hai tệp SQLite riêng** (suy ra từ `-FacilityCode`, đổi
được bằng `-DbPath`) — dùng chung một tệp thì hai "bệnh viện" nhìn thấy y nguyên danh
sách bệnh nhân của nhau.

Kịch bản trình diễn ở dưới vẫn chạy đúng như khi có hai Gateway: mã cơ sở đi kèm từng
yêu cầu nên `identifier.system` của mã bệnh án và khóa Condition vẫn tách bạch theo nơi ghi.

> **Chỉ dùng để thử nghiệm.** Mã cơ sở là *danh tính* của bên ghi hồ sơ. Để bên gọi tự
> khai thì bệnh viện B khai mình là bệnh viện A được ngay. Khi triển khai thật, giữ cờ này
> tắt (mặc định) và mỗi bệnh viện chạy một bản Gateway riêng, danh tính do phía máy chủ
> xác lập. Gateway đang bật chế độ này báo `allow_client_facility: true` ở `/health`.

Kịch bản đáng trình diễn: tạo ở **mỗi** bệnh viện một bệnh nhân **trùng mã bệnh án**
(ví dụ cùng `BN0001`) nhưng khác người, rồi đồng bộ cả hai. Trục giữ hai hồ sơ riêng và
mỗi chẩn đoán ghi rõ bệnh viện nào lập. Sau đó tạo ở hai nơi hai hồ sơ **cùng số CCCD** —
lần này trục gộp về một bệnh nhân với hai chẩn đoán từ hai bệnh viện (mục 6.6).

Kịch bản **chuyển tuyến**: bệnh viện A chẩn đoán rồi chuyển bệnh nhân lên B, B khám ra
thêm bệnh. Ô tìm kiếm của HIS nhận **mã bệnh án, số CCCD hoặc thẻ BHYT** — bác sĩ ở B
gõ CCCD là kéo được bệnh sử từ trục về, kể cả khi chưa từng có hồ sơ nội viện. Chẩn đoán
do nơi khác lập hiện huy hiệu tên bệnh viện đó và **không sửa được** từ đây.

---

## 4. Kiểm thử và đánh giá

### 4.1 Kiểm thử chức năng

Hai bộ tách bạch theo thứ chúng kiểm: `nlp/` kiểm **chất lượng nhận diện mã ICD-10**,
`tests/` kiểm **đường liên thông** (sinh HL7 FHIR, mã cơ sở, bệnh án của HIS).

```powershell
# Nhận diện ICD-10 - cần mô hình NLP đã tải về
.venv\Scripts\python -m pytest nlp/test_nlp.py -v

# Liên thông - KHÔNG cần mô hình NLP, KHÔNG cần Docker, chạy ~20 giây
.venv\Scripts\python -m pytest tests/ -v
```

Bộ `tests/` dựng sẵn một EMR Cloud giả lập trong bộ nhớ, bắt chước đúng cơ chế
conditional update mà việc đồng nhất bệnh nhân dựa vào:

| Tệp                                  | Kiểm điều gì                                                                  |
| ------------------------------------- | --------------------------------------------------------------------------------- |
| `tests/test_sua_chan_doan.py`       | Bác sĩ sửa/gỡ chẩn đoán đã ghi nhận, tính lại chẩn đoán chính     |
| `tests/test_nhieu_co_so.py`         | Một Gateway phục vụ nhiều bệnh viện, chốt chặn mã cơ sở                |
| `tests/test_chuyen_tuyen.py`        | BV A chẩn đoán → chuyển tuyến → BV B thêm bệnh, tra bệnh sử bằng CCCD |
| `tests/test_luong_nlp_khong_doi.py` | Chốt hồi quy: các chức năng thêm vào không đụng luồng NLP tự động   |

### 4.2 Đánh giá định lượng

```powershell
.venv\Scripts\python -m nlp.evaluate                                  # tập phát triển
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
```

### 4.3 Kết quả hiện tại

| Chỉ số              | Tập phát triển (110 ca) | **Tập kiểm tra độc lập (51 ca)** |
| --------------------- | -------------------------- | ------------------------------------------- |
| Top-1 accuracy        | 100,0%                     | **70,6%**                             |
| Top-3 accuracy        | 100,0%                     | 88,2%                                       |
| Top-5 accuracy        | 100,0%                     | 92,2%                                       |
| MRR                   | 1,000                      | 0,796                                       |
| Độ trễ trung bình | 60 ms                      | 59 ms                                       |

> **Cách đọc bảng này.** Tập phát triển (`eval_set.json`) đã được dùng để hiệu chỉnh
> luật, alias và trọng số, nên con số 100% trên đó **không** phản ánh năng lực tổng
> quát hóa — chỉ chứng tỏ các luật đã được cài đúng. Con số cần trích dẫn trong báo
> cáo khoa học là cột **tập kiểm tra độc lập** (`eval_holdout.json`), được soạn sau
> khi hoàn tất tinh chỉnh.
>
> Lần đo đầu tiên trên tập độc lập cho Top-1 62,7% / Top-5 86,3%. Phân tích lỗi phát
> hiện bốn khiếm khuyết mang tính hệ thống (xem mục 6.4), sau khi khắc phục đạt
> 70,6% / 92,2%. Vì đã nhìn vào lỗi của tập này nên nó **không còn hoàn toàn "sạch"**;
> lần báo cáo tiếp theo cần soạn một tập độc lập mới.

### 4.4 Diễn giải theo nhóm đầu vào (tập độc lập)

| Nhóm                         | Số ca | Top-1  | Top-5  |
| ----------------------------- | ------ | ------ | ------ |
| Có viết tắt lâm sàng     | 4      | 100,0% | 100,0% |
| Gõ không dấu               | 4      | 75,0%  | 75,0%  |
| Chẩn đoán viết đầy đủ | 32     | 71,9%  | 90,6%  |
| Có tiền tố hành văn      | 3      | 66,7%  | 100,0% |
| Cặp đối lập ngữ nghĩa   | 4      | 50,0%  | 100,0% |
| Chọn mức chi tiết của mã | 4      | 50,0%  | 100,0% |

Hai nhóm yếu nhất (đối lập ngữ nghĩa, mức chi tiết) đều đạt **Top-5 = 100%**, tức mã
đúng luôn nằm trong danh sách gợi ý — phù hợp với thiết kế "máy gợi ý, bác sĩ chọn".

---

## 5. Cấu trúc thư mục

```
NCKH/
├── backend/
│   ├── main.py                     # API Gateway (FastAPI): standardize, FHIR, sync
│   ├── fhir_helper.py              # Sinh HL7 FHIR R4 Condition & Patient
│   └── requirements.txt            # Trỏ về requirements.txt gốc
├── nlp/
│   ├── nlp_engine.py               # Bộ truy hồi lai: embedding + tái xếp hạng
│   ├── clinical_rules.py           # Tri thức lâm sàng: alias, trục đối lập, ngưỡng
│   ├── evaluate.py                 # Đo Top-k, MRR, hiệu chuẩn độ tin cậy
│   ├── test_nlp.py                 # Kiểm thử chức năng + chặn hồi quy
│   ├── my_medical_nlp_model/       # SBERT đã fine-tune (540 MB, không đưa vào git)
│   └── data/
│       ├── icd10_db.json           # Danh mục ICD-10 (12.137 mã)
│       ├── icd10_supplement.json   # Mã bổ sung thủ công, bắt buộc ghi nguồn
│       ├── eval_set.json           # Tập phát triển (110 ca)
│       ├── eval_holdout.json       # Tập kiểm tra độc lập (51 ca)
│       └── embeddings_*.npy        # Cache embedding, sinh tự động
├── docs/
│   ├── luong-xu-ly-chan-doan.md    # Truy vết luồng xử lý một câu chẩn đoán
│   ├── thaydoi.md                  # Nhật ký thay đổi, kèm số đo trước/sau
│   └── T0.md                       # Việc củng cố an ninh trục EMR
├── frontend/                       # Giao diện Gateway (cổng 8000)
├── hospital_his/                   # HIS mô phỏng (cổng 8085)
├── docker-compose.yml              # EMR Cloud - HAPI FHIR (cổng 8090)
├── ChangeJson.py                   # Chuyển danh mục Excel -> JSON
├── requirements.txt
├── run.bat / run.ps1
└── README.md
```

---

## 6. Phương pháp

### 6.1 Vấn đề

Mô hình SBERT chỉ đo độ tương đồng ngữ nghĩa nên không phân biệt được các cặp đối
lập vốn quyết định mã ICD-10. Ví dụ với truy vấn *"đái tháo đường tuýp 2"*, sáu kết
quả đầu của cosine similarity thuần túy:

```
E10.3  0.6102   ĐTĐ phụ thuộc insuline (biến chứng mắt)   <- típ 1, SAI
E10.5  0.5932   ĐTĐ phụ thuộc insuline                     <- típ 1, SAI
E10.4  0.5882   ...                                        <- típ 1, SAI
E11.3  0.5785   ĐTĐ không phụ thuộc insuline               <- típ 2
```

Nguyên nhân kép: (a) khoảng cách cosine giữa "phụ thuộc insuline" và "không phụ
thuộc insuline" chỉ ~0,02; (b) danh mục Bộ Y tế **không dùng chữ "tuýp 2"** ở bất
kỳ mã nào — thuật ngữ tương ứng là "không phụ thuộc insuline".

### 6.2 Kiến trúc truy hồi lai

| Tầng                        | Chức năng                                                                                                                                                                              |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Chuẩn hóa**     | Bóc tiền tố/hậu tố hành văn (lặp đến khi ổn định), giải nghĩa viết tắt,**khôi phục dấu tiếng Việt** từ từ điển 47k thuật ngữ, nối cầu nối từ vựng |
| **2. Truy hồi**       | Cosine similarity trên embedding SBERT đã tiền tính, lấy 400 ứng viên                                                                                                            |
| **3. Tái xếp hạng** | Điểm ngữ nghĩa + trùng lặp từ vựng + alias đã kiểm chứng − vi phạm trục đối lập − cổng chương ± mức chi tiết                                                    |
| **4. Hiệu chuẩn**    | Softmax (gộp theo khối 3 ký tự) kết hợp độ tương đồng tuyệt đối → độ tin cậy                                                                                          |

### 6.3 Bốn cơ chế tri thức (`nlp/clinical_rules.py`)

1. **Cầu nối từ vựng** — nối thuật ngữ chuẩn của danh mục vào câu truy vấn.
   Ví dụ *"ung thư"* → *"u ác"*: toàn bộ 553 mã chương C dùng "u ác", không mã nào
   ghi "ung thư".
2. **Trục đối lập ngữ nghĩa** — 5 trục (típ đái tháo đường, nguyên nhân tăng huyết
   áp, biến chứng, cấp/mạn, dị ứng). Nhãn của truy vấn xung đột với nhãn của mã ứng
   viên thì trừ điểm.
3. **Cổng chương** — chương O (thai sản), S/T (chấn thương), V–Y (ngoại sinh), Z chỉ
   hợp lệ khi câu chẩn đoán có ngữ cảnh tương ứng.
4. **Mức chi tiết** — không nêu thể bệnh thì ưu tiên mã "không đặc hiệu" (.9), có nêu
   thể bệnh thì hạ bậc mã "không đặc hiệu".

### 6.4 Khiếm khuyết phát hiện qua tập độc lập

| Khiếm khuyết                                                | Khắc phục                           |
| ------------------------------------------------------------- | ------------------------------------- |
| Danh mục dùng "u ác", bác sĩ nói "ung thư" (553 mã)   | Cầu nối từ vựng                   |
| Danh mục ghi "(Có hôn mê)" chứ không ghi "biến chứng" | Mở rộng phạm vi trục biến chứng |
| Truy vấn không nêu thể bệnh lại ra mã ".8 khác"       | Luật mức chi tiết                  |
| Tương tự với "hạ glucose máu", "u cơ trơn tử cung"   | Cầu nối từ vựng                   |

### 6.5 Chính sách an toàn lâm sàng

Kết quả của mô hình **không** được tự động ghi nhận là chẩn đoán đã xác nhận:

| Độ tin cậy | `Condition.verificationStatus` | Hành vi hệ thống                         |
| ------------- | -------------------------------- | ------------------------------------------- |
| ≥ 85%        | `confirmed`                    | Liên thông tự động                     |
| 60 – 85%     | `provisional`                  | Dừng, chờ bác sĩ duyệt                 |
| 40 – 60%     | `unconfirmed`                  | Dừng, chờ bác sĩ duyệt                 |
| < 40%         | —                               | Từ chối sinh tài nguyên FHIR (HTTP 422) |

### 6.6 Đồng nhất bệnh nhân giữa nhiều bệnh viện

Khi có từ hai bệnh viện cùng liên thông, câu hỏi khó nhất không phải là mã ICD-10 mà là
**"hai hồ sơ này có phải cùng một người không"**. Bản đầu dùng mã bệnh án làm khóa và
mắc đúng lỗi kinh điển: `BN001` của bệnh viện A và `BN001` của bệnh viện B bị coi là
một người, hồ sơ ghi đè lẫn nhau. Thực nghiệm tái hiện được: sau hai lần đồng bộ, trên
trục chỉ còn một bệnh nhân với họ tên, giới tính và ngày sinh của người thứ hai.

Khóa đồng nhất hiện tại chọn theo **phạm vi hiệu lực** của định danh:

| Ưu tiên | Định danh   | Phạm vi               | `identifier.system`                |
| --------- | ------------- | ---------------------- | ------------------------------------ |
| 1         | Số CCCD      | Toàn quốc            | `.../identifier/cccd`              |
| 2         | Thẻ BHYT     | Toàn quốc            | `.../identifier/bhyt`              |
| 3         | Mã bệnh án | **Một cơ sở** | `.../identifier/mrn/{mã cơ sở}` |

Mã bệnh án vẫn dùng được, nhưng `system` phải mang mã cơ sở đã cấp nó — đúng vai trò mà
đặc tả FHIR giao cho `identifier.system`: chỉ ra **ai** cấp định danh đó.

Khóa của mỗi chẩn đoán vì vậy gồm bốn thành phần:

```
{mã cơ sở}-{định danh bệnh nhân}-{mã ICD-10}-{ngày khám}
```

Mỗi thành phần chặn một kiểu trộn dữ liệu: thiếu **mã cơ sở** thì hai bệnh viện đè lên
nhau; thiếu **ngày khám** thì lần tái khám đè lên chẩn đoán cũ, mất lịch sử điều trị.

Mỗi Condition còn mang tên cơ sở đã ghi nhận nó, ở hai chỗ bổ trợ nhau:

* `meta.tag` — lọc trực tiếp trên API: `GET /Condition?_tag={system}|{mã cơ sở}`
* `extension[source-facility]` — tham chiếu tới tài nguyên `Organization`, kèm `display`
  là tên bệnh viện để đọc JSON là thấy ngay

Nhờ vậy khi cùng một người (cùng CCCD) khám ở hai nơi, trục giữ **một** hồ sơ bệnh nhân
với **hai** chẩn đoán riêng, mỗi chẩn đoán ghi rõ bệnh viện nào đã lập.

---

## 7. Kịch bản trình bày

### 7.1 Trên Gateway ([http://127.0.0.1:8000](http://127.0.0.1:8000))

1. Bấm nút mẫu **`ĐTĐ tuýp 2 & THA`** (hoặc gõ tay).
2. **Bước 1 — Chuẩn hóa:** `ĐTĐ` → `đái tháo đường`, và hệ thống nối thêm
   `không phụ thuộc insuline`; các từ được thêm/đổi sẽ được bôi màu.
3. **Bước 2 — NER:** thực thể y khoa được bôi màu ngay trong nguyên văn của bác sĩ.
4. **Bước 3 — Ánh xạ ICD-10:** hiển thị `E11.9` kèm độ tin cậy **và lý do**
   ("khớp alias lâm sàng đã kiểm chứng", "xung đột diabetes_type…").
5. Chọn mã → cột phải sinh **HL7 FHIR Condition Resource**.
6. Bấm **Đồng bộ lên EMR Cloud** → bản ghi xuất hiện ở trục liên thông phía dưới.

**Điểm nên nhấn:** bấm nút **`Gõ không dấu`** để cho thấy hệ thống xử lý được
`hen phe quan cap tinh khong di ung` — tình huống rất thật trong bệnh án Việt Nam.

### 7.2 Trên HIS ([http://127.0.0.1:8085](http://127.0.0.1:8085))

1. Bấm **Đồng bộ** ở một dòng bệnh nhân.
2. Tab **Log chi tiết** hiện đủ 3 bước: chuẩn hóa NLP → sinh FHIR → truyền EMR.
3. Tab **HL7 FHIR JSON** hiện tài nguyên **đọc ngược lại từ EMR Cloud** — chứng minh
   dữ liệu đã thực sự nằm trên trục liên thông chứ không chỉ được gửi đi.
4. Với ca độ tin cậy thấp, hệ thống dừng ở trạng thái **"Chờ bác sĩ duyệt"** và liệt
   kê các mã ứng viên thay vì tự ý liên thông.

---

## 8. Cấu hình

Đọc từ biến môi trường, đều có giá trị mặc định cho môi trường demo:

| Biến                    | Mặc định                    | Ý nghĩa                                                      |
| ------------------------ | ------------------------------ | -------------------------------------------------------------- |
| `SMIG_FHIR_SERVER_URL` | `http://127.0.0.1:8090/fhir` | Địa chỉ EMR Cloud                                           |
| `SMIG_ALLOWED_ORIGINS` | `127.0.0.1:8000,8085`        | Danh sách nguồn CORS                                         |
| `SMIG_GATEWAY_URL`     | `http://127.0.0.1:8000`      | Địa chỉ Gateway (dùng bởi HIS)                            |
| `SMIG_FHIR_TIMEOUT`    | `8`                          | Thời gian chờ gọi FHIR (giây)                              |
| `SMIG_HIS_TIMEOUT`     | `60`                         | Thời gian chờ HIS gọi Gateway (giây)                       |
| `SMIG_FACILITY_CODE`   | `BV-DEMO-01`                 | **Mã cơ sở khám chữa bệnh** của bản Gateway này |
| `SMIG_FACILITY_NAME`   | `Bệnh viện Demo SMIG`      | Tên cơ sở, hiện trên mỗi chẩn đoán                    |

> **Mỗi bệnh viện triển khai một bản Gateway với `SMIG_FACILITY_CODE` riêng.** Hai bản
> dùng trùng mã sẽ trộn hồ sơ của hai bệnh viện vào nhau — xem mục 6.6. HIS đọc cùng
> biến này để tra cứu đúng namespace mã bệnh án.

EMR Cloud lưu dữ liệu trong PostgreSQL gắn named volume `hapi-pgdata`, nên
`docker compose restart` hay `down` rồi `up -d` đều **không** mất dữ liệu đã liên
thông. Chỉ `docker compose down -v` mới xóa sạch. Lần khởi động đầu tiên chậm hơn
(~90 giây) vì HAPI phải tạo schema.

---

## 9. Giới hạn đã biết

* **Một chẩn đoán → một mã.** Câu "ĐTĐ tuýp 2 kèm tăng huyết áp" chứa hai bệnh nhưng
  luồng FHIR hiện chỉ sinh một Condition. Bước NER đã tách được cả hai thực thể; việc
  sinh nhiều Condition là hướng phát triển tiếp.
* **Không có xác thực.** Mọi endpoint đều mở. Cả ba dịch vụ chỉ nghe trên `127.0.0.1`
  nên phạm vi rủi ro giới hạn ở máy chạy demo, nhưng triển khai thật với dữ liệu y tế
  bắt buộc phải có OAuth2/SMART on FHIR. **Không** đưa cụm này ra Internet (kể cả qua
  ngrok hay Cloudflare Tunnel) ở trạng thái hiện tại.
* **Đồng bộ cập nhật theo khóa nghiệp vụ.** Đồng bộ lại cùng một chẩn đoán **trong cùng
  ngày** sẽ cập nhật bản ghi cũ thay vì tạo bản mới — đánh đổi để bảo đảm tính
  idempotent. Cơ chế là **conditional update** của FHIR (`PUT /Condition?identifier=...`):
  id tài nguyên do EMR cấp, client không tự đặt được nên không ghi đè được bản ghi của
  bệnh nhân khác. Khóa gồm mã cơ sở, định danh bệnh nhân, mã ICD-10 và ngày khám (mục 6.6).
* **Đồng nhất bệnh nhân dựa hoàn toàn vào định danh khai báo.** Không có CCCD/BHYT thì hệ
  thống chỉ quy được hồ sơ trong phạm vi một bệnh viện; cùng một người khám ở hai nơi mà
  không nơi nào ghi CCCD sẽ thành hai hồ sơ. Hệ thống **không** đối sánh xác suất theo
  họ tên/ngày sinh — đó là bài toán riêng, cần dữ liệu thật và thẩm định lâm sàng.
* **Mật khẩu PostgreSQL `hapi/hapi`** trong `docker-compose.yml` chỉ dành cho demo cục
  bộ. Cổng 5432 không publish ra host, nhưng đây không phải cấu hình triển khai thật.
* **Tập đánh giá 161 ca do nhóm tự soạn**, chưa có thẩm định của bác sĩ chuyên khoa
  và chưa lấy từ bệnh án thật.
* **Cache embedding phụ thuộc mô hình và danh mục.** Đổi một trong hai sẽ phải mã hóa
  lại ~47.000 thuật ngữ (15–40 phút trên CPU); hệ thống có in cảnh báo trước khi chạy.

---

## 10. Hướng phát triển

1. Sinh nhiều FHIR Condition cho câu chẩn đoán chứa nhiều bệnh.
2. Mở rộng tập đánh giá lên 500+ ca lấy từ bệnh án thật, có bác sĩ gán nhãn độc lập.
3. Fine-tune lại SBERT với negative-pair mining trên chính các cặp đối lập, để mô hình
   tự học được ranh giới thay vì dựa vào luật hậu xử lý.
4. Bổ sung OAuth2 / SMART on FHIR và ghi nhật ký truy cập theo yêu cầu bảo mật dữ liệu y tế.

## Test Local

1. docker compose up -d
   .\run.ps1 -Port 8000 -AllowClientFacility
2. .\VNPT_HIS\run_vnpt_his.ps1
3. .\hospital_his\run_his.ps1
