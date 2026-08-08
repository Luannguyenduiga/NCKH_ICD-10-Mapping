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

| Thành phần | Cổng | Vai trò |
|---|---|---|
| **SMIG Gateway** | 8000 | API chuẩn hóa NLP, sinh FHIR, cầu nối tới EMR Cloud + giao diện demo |
| **HIS mô phỏng** | 8085 | Đóng vai bệnh viện gửi dữ liệu, có CSDL bệnh nhân riêng |
| **EMR Cloud** | 8090 | Máy chủ HAPI FHIR đóng vai trục dữ liệu y tế quốc gia |

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

Chờ ~60 giây rồi kiểm tra: <http://127.0.0.1:8090/fhir/metadata>

> Bỏ qua bước này thì mọi thao tác đồng bộ sẽ báo lỗi 503.

### Bước 2 — SMIG Gateway

```powershell
.\run.ps1
```

hoặc bấm đúp **`run.bat`**. Lần đầu chạy sẽ tự tạo `.venv` và cài thư viện.
Giao diện: <http://127.0.0.1:8000>

### Bước 3 — HIS mô phỏng (tùy chọn, để demo liên thông hai bệnh viện)

```powershell
.\hospital_his\run_his.ps1
```

Giao diện: <http://127.0.0.1:8085>

---

## 4. Kiểm thử và đánh giá

### 4.1 Kiểm thử chức năng

```powershell
.venv\Scripts\python -m nlp.test_nlp
```

hoặc dùng pytest:

```powershell
.venv\Scripts\python -m pytest nlp/test_nlp.py -v
```

### 4.2 Đánh giá định lượng

```powershell
.venv\Scripts\python -m nlp.evaluate                                  # tập phát triển
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
```

### 4.3 Kết quả hiện tại

| Chỉ số | Tập phát triển (110 ca) | **Tập kiểm tra độc lập (51 ca)** |
|---|---|---|
| Top-1 accuracy | 100,0% | **70,6%** |
| Top-3 accuracy | 100,0% | 88,2% |
| Top-5 accuracy | 100,0% | 92,2% |
| MRR | 1,000 | 0,796 |
| Độ trễ trung bình | 60 ms | 59 ms |

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

| Nhóm | Số ca | Top-1 | Top-5 |
|---|---|---|---|
| Có viết tắt lâm sàng | 4 | 100,0% | 100,0% |
| Gõ không dấu | 4 | 75,0% | 75,0% |
| Chẩn đoán viết đầy đủ | 32 | 71,9% | 90,6% |
| Có tiền tố hành văn | 3 | 66,7% | 100,0% |
| Cặp đối lập ngữ nghĩa | 4 | 50,0% | 100,0% |
| Chọn mức chi tiết của mã | 4 | 50,0% | 100,0% |

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
│       ├── icd10_db.json           # Danh mục ICD-10 (12.219 mã)
│       ├── eval_set.json           # Tập phát triển (110 ca)
│       ├── eval_holdout.json       # Tập kiểm tra độc lập (51 ca)
│       └── embeddings_*.npy        # Cache embedding, sinh tự động
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

| Tầng | Chức năng |
|---|---|
| **1. Chuẩn hóa** | Bóc tiền tố/hậu tố hành văn (lặp đến khi ổn định), giải nghĩa viết tắt, **khôi phục dấu tiếng Việt** từ từ điển 47k thuật ngữ, nối cầu nối từ vựng |
| **2. Truy hồi** | Cosine similarity trên embedding SBERT đã tiền tính, lấy 400 ứng viên |
| **3. Tái xếp hạng** | Điểm ngữ nghĩa + trùng lặp từ vựng + alias đã kiểm chứng − vi phạm trục đối lập − cổng chương ± mức chi tiết |
| **4. Hiệu chuẩn** | Softmax (gộp theo khối 3 ký tự) kết hợp độ tương đồng tuyệt đối → độ tin cậy |

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

| Khiếm khuyết | Khắc phục |
|---|---|
| Danh mục dùng "u ác", bác sĩ nói "ung thư" (553 mã) | Cầu nối từ vựng |
| Danh mục ghi "(Có hôn mê)" chứ không ghi "biến chứng" | Mở rộng phạm vi trục biến chứng |
| Truy vấn không nêu thể bệnh lại ra mã ".8 khác" | Luật mức chi tiết |
| Tương tự với "hạ glucose máu", "u cơ trơn tử cung" | Cầu nối từ vựng |

### 6.5 Chính sách an toàn lâm sàng

Kết quả của mô hình **không** được tự động ghi nhận là chẩn đoán đã xác nhận:

| Độ tin cậy | `Condition.verificationStatus` | Hành vi hệ thống |
|---|---|---|
| ≥ 85% | `confirmed` | Liên thông tự động |
| 60 – 85% | `provisional` | Dừng, chờ bác sĩ duyệt |
| 40 – 60% | `unconfirmed` | Dừng, chờ bác sĩ duyệt |
| < 40% | — | Từ chối sinh tài nguyên FHIR (HTTP 422) |

---

## 7. Kịch bản trình bày

### 7.1 Trên Gateway (<http://127.0.0.1:8000>)

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

### 7.2 Trên HIS (<http://127.0.0.1:8085>)

1. Bấm **Đồng bộ** ở một dòng bệnh nhân.
2. Tab **Log chi tiết** hiện đủ 3 bước: chuẩn hóa NLP → sinh FHIR → truyền EMR.
3. Tab **HL7 FHIR JSON** hiện tài nguyên **đọc ngược lại từ EMR Cloud** — chứng minh
   dữ liệu đã thực sự nằm trên trục liên thông chứ không chỉ được gửi đi.
4. Với ca độ tin cậy thấp, hệ thống dừng ở trạng thái **"Chờ bác sĩ duyệt"** và liệt
   kê các mã ứng viên thay vì tự ý liên thông.

---

## 8. Cấu hình

Đọc từ biến môi trường, đều có giá trị mặc định cho môi trường demo:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `SMIG_FHIR_SERVER_URL` | `http://127.0.0.1:8090/fhir` | Địa chỉ EMR Cloud |
| `SMIG_ALLOWED_ORIGINS` | `127.0.0.1:8000,8085` | Danh sách nguồn CORS |
| `SMIG_GATEWAY_URL` | `http://127.0.0.1:8000` | Địa chỉ Gateway (dùng bởi HIS) |
| `SMIG_FHIR_TIMEOUT` | `8` | Thời gian chờ gọi FHIR (giây) |

---

## 9. Giới hạn đã biết

* **Một chẩn đoán → một mã.** Câu "ĐTĐ tuýp 2 kèm tăng huyết áp" chứa hai bệnh nhưng
  luồng FHIR hiện chỉ sinh một Condition. Bước NER đã tách được cả hai thực thể; việc
  sinh nhiều Condition là hướng phát triển tiếp.
* **Không có xác thực.** Mọi endpoint đều mở. Chấp nhận được cho demo nội bộ, nhưng
  triển khai thật với dữ liệu y tế bắt buộc phải có OAuth2/SMART on FHIR.
* **Đồng bộ ghi đè theo (bệnh nhân, mã bệnh).** Đồng bộ lại cùng một chẩn đoán sẽ cập
  nhật bản ghi cũ thay vì tạo bản mới — đánh đổi để bảo đảm tính idempotent, nhưng
  không lưu được lịch sử nhiều lần chẩn đoán cùng bệnh.
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
