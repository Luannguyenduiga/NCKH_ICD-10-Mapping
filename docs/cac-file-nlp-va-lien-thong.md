# Cấu trúc mã nguồn SMIG: khối NLP và khối liên thông

Tài liệu này liệt kê **từng file mã nguồn** của hệ thống, chia theo hai khối chức năng:

* **Khối NLP** — chuẩn hóa chẩn đoán lâm sàng tiếng Việt viết tự do thành mã ICD-10.
* **Khối liên thông** — đóng gói mã ICD-10 theo chuẩn HL7 FHIR R4 và đồng bộ giữa các
  bệnh viện qua trục dữ liệu y tế.

Đối tượng đọc: người viết báo cáo khoa học cần mô tả kiến trúc phần mềm của đề tài.

> Tài liệu bổ trợ: `docs/luong-xu-ly-chan-doan.md` truy vết đường đi của **một câu chẩn
> đoán** qua từng hàm; tài liệu này mô tả **bản đồ file**, không đi vào thuật toán.

---

## 1. Tổng quan

Hệ thống gồm ba tiến trình độc lập, giao tiếp qua HTTP:

```
   HIS bệnh viện A            HIS bệnh viện B
  (VNPT HIS — Java)      (HIS mô phỏng — Python)
        :8089                     :8085
          │                         │
          └──────────┬──────────────┘
                     │  POST /api/standardize      ← KHỐI NLP
                     │  POST /api/fhir/condition   ← KHỐI LIÊN THÔNG
                     │  POST /api/fhir/sync
                     ▼
              SMIG Gateway :8000
                     │  HL7 FHIR R4
                     ▼
              EMR Cloud :8090
           (HAPI FHIR — trục dữ liệu)
```

Ranh giới giữa hai khối nằm ở đúng một điểm: **khối NLP kết thúc khi trả ra danh sách
mã ICD-10 kèm độ tin cậy; khối liên thông bắt đầu từ đó.** Hai khối không dùng chung
trạng thái nào, nên có thể thay mô hình NLP mà không đụng tới phần liên thông, và ngược
lại. Ranh giới này được kiểm chứng bằng `tests/test_luong_nlp_khong_doi.py`.

| Khối | Số file mã nguồn | Tổng số dòng |
| --- | --- | --- |
| NLP | 5 | 2 905 |
| Liên thông (Gateway + 2 HIS + 2 giao diện) | 12 | 7 887 |
| Kiểm thử | 6 | 913 |

---

## 2. Khối NLP

### 2.1 Bảng tóm tắt

| File | Dòng | Vai trò |
| --- | --- | --- |
| `nlp/nlp_engine.py` | 1 360 | Bộ máy truy hồi lai 4 tầng — trái tim của hệ thống |
| `nlp/clinical_rules.py` | 502 | Tri thức lâm sàng: viết tắt, trục đối lập, alias, ngưỡng tin cậy |
| `nlp/evaluate.py` | 189 | Đo Top-k accuracy, MRR, độ trễ trên tập chuẩn |
| `nlp/test_nlp.py` | 290 | Kiểm thử chức năng và chặn hồi quy chất lượng nhận diện |
| `ChangeJson.py` | 564 | Dựng danh mục ICD-10 từ file Excel của Bộ Y tế |

### 2.2 `nlp/nlp_engine.py` — bộ máy NLP

Chứa lớp `NLPEngine`, cài đặt kiến trúc **truy hồi lai (hybrid retrieval)** bốn tầng:

| Tầng | Việc làm | Hàm chính |
| --- | --- | --- |
| 1. Chuẩn hóa | Bỏ tiền tố nhiễu, giải nghĩa viết tắt, phục hồi dấu tiếng Việt, nối cầu nối từ vựng | `normalize_text()`, `expand_query()`, `restore_diacritics()` |
| 2. Truy hồi | Cosine similarity trên embedding SBERT đã tiền tính cho 12 137 mã | `query()` |
| 3. Tái xếp hạng | Cộng/trừ điểm theo trùng lặp từ vựng, alias lâm sàng, trục đối lập ngữ nghĩa, cổng chương | `_polarity_delta()`, `_alias_targets()`, `_chapter_delta()`, `_specificity_delta()` |
| 4. Hiệu chuẩn | Quy đổi điểm thô sang độ tin cậy 0–100% | `_calibrate()` |

**Vì sao cần tầng 3.** SBERT chỉ đo độ tương đồng ngữ nghĩa nên xếp gần như ngang nhau
những cặp mã mà lâm sàng phân biệt rạch ròi: E10 (phụ thuộc insulin) với E11 (không phụ
thuộc insulin), I10 (tăng huyết áp vô căn) với I15 (thứ phát). Tầng 3 là lý do hệ thống
chọn đúng.

Hai hàm đối ngoại quan trọng:

* `query(user_query, top_k)` — trả về danh sách top-k mã cho **một** chẩn đoán.
* `query_composite(user_query, top_k)` — nhận diện **nhiều bệnh trong một câu** và cặp
  mã dagger/asterisk (†/*) của ICD-10. Trả thêm trường `diagnoses`, mỗi phần tử là một
  vế bệnh riêng. Đây là đầu vào để phía liên thông sinh ra **nhiều FHIR Condition** từ
  một dòng bệnh án.
* `extract_entities_regex(text)` — bôi màu thực thể lâm sàng trong câu, phục vụ hiển thị.

Ngoài ra lớp này quản lý **cache embedding**: mã hóa 12 137 mục mất 15–40 phút trên CPU
nên kết quả được lưu ra `.npy`, kèm vân tay mô hình (`_model_fingerprint`) để cache
không bị dùng nhầm khi đổi mô hình.

### 2.3 `nlp/clinical_rules.py` — tri thức lâm sàng

Tách riêng khỏi bộ máy để sửa tri thức mà không đụng thuật toán. Bảy nhóm dữ liệu:

| Hằng số | Nội dung |
| --- | --- |
| `ABBREVIATIONS`, `QUERY_ABBREVIATIONS` | Viết tắt bác sĩ hay dùng: `đtđ` → đái tháo đường, `THA` → tăng huyết áp |
| `NOISE_PREFIXES`, `NOISE_SUFFIXES`, `STOPWORDS` | Cụm không mang thông tin chẩn đoán: "bệnh nhân bị", "theo dõi", "nhẹ" |
| `BRIDGE_TERMS` | Cầu nối từ vựng: thuật ngữ lâm sàng ("tuýp 2") không có trong danh mục Bộ Y tế (dùng "không phụ thuộc insuline") |
| `POLARITY_AXES` | Trục đối lập ngữ nghĩa — cơ chế phân biệt E10/E11, I10/I15 |
| `CHAPTER_GATES` | Cổng chương ICD-10: chặn ứng viên sai chương ngay từ đầu |
| `DIRECT_ALIASES` | Alias lâm sàng đã kiểm chứng, vừa cộng điểm vừa được mã hóa embedding bổ sung |
| `CONFIDENCE_POLICY` | Ba ngưỡng: `auto_confirm` 85%, `provisional` 60%, `reject` 40% |

**Điểm cần nêu trong báo cáo — hàm `verification_status_for()`.** Máy **không bao giờ**
tự gán `confirmed` cho một chẩn đoán. Trong đặc tả HL7 FHIR, `confirmed` nghĩa là chẩn
đoán đã được xác nhận, hàm ý có người đủ thẩm quyền đứng sau, chứ không phải thuật toán
tự chấm mình 85 điểm. Đo trên tập kiểm tra độc lập cho thấy trong nhóm ≥85% vẫn còn ca
sai (cao nhất: "viêm kết mạc dị ứng" ra H10.9 "không đặc hiệu" với 96,78%). Những bản
ghi ấy lên trục mang nhãn "đã xác nhận" thì bệnh viện khác đọc về không có cách nào biết
là chưa ai duyệt — đó là sai lệch thông tin y tế **do khâu gắn nhãn tạo ra**, không phải
do mô hình đoán sai. Ba mức máy được phép dùng: `provisional`, `differential`,
`unconfirmed`. Giá trị `confirmed` chỉ sinh ra khi có thao tác duyệt của bác sĩ.

### 2.4 `nlp/evaluate.py` — đánh giá định lượng

Tính Top-1/3/5 accuracy, MRR (Mean Reciprocal Rank) và độ trễ trung bình trên hai tập
chuẩn. Chạy:

```powershell
.venv\Scripts\python -m nlp.evaluate
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
```

### 2.5 `ChangeJson.py` — dựng danh mục ICD-10

Đọc file Excel danh mục ICD-10 của Bộ Y tế (14 sheet) và sinh ra `nlp/data/icd10_db.json`.
Ngoài sheet mã chính, script còn đọc các phụ lục mang **ràng buộc lâm sàng**: A1 ghép cặp
dagger/asterisk, A2 đánh dấu mã không được dùng làm bệnh chính, A3.x giới hạn tuổi, A4.x
giới hạn giới tính. Nhờ đó Gateway loại được ứng viên sai về mặt lâm sàng thay vì chỉ
xếp hạng theo độ tương đồng văn bản.

> Chạy lại script này sẽ đổi khóa cache embedding, lần khởi động kế tiếp phải mã hóa lại
> toàn bộ danh mục (15–40 phút trên CPU).

### 2.6 Dữ liệu của khối NLP

| Đường dẫn | Nội dung |
| --- | --- |
| `nlp/data/icd10_db.json` | Danh mục ICD-10 — **12 137 mã**, mỗi mã có `code`, `code_no_dot`, `name_vi`, `name_en`, `synonyms`, `meta` (chương, nhóm, nguồn) |
| `nlp/data/icd10_supplement.json` | Mã bổ sung thủ công, bắt buộc ghi nguồn |
| `nlp/data/eval_set.json` | Tập phát triển — **110 ca**, dùng để hiệu chỉnh luật |
| `nlp/data/eval_holdout.json` | Tập kiểm tra độc lập — **51 ca**, soạn sau khi đã khóa luật |
| `nlp/data/embeddings_*.npy` | Cache embedding (~145 MB/bản), sinh tự động |
| `nlp/data/entries_*.json` | Văn bản tham chiếu tương ứng với cache, để đối chiếu |
| `nlp/my_medical_nlp_model/` | Mô hình SBERT tiếng Việt đã fine-tune (~540 MB, không đưa vào git) |

Định dạng một ca đánh giá:

```json
{ "query": "đái tháo đường tuýp 2", "expected": ["E11", "E11.9"], "tag": "plain" }
```

**Lưu ý khi trích dẫn số liệu.** Tập phát triển đã được dùng để hiệu chỉnh luật, alias và
trọng số, nên con số trên đó **không** phản ánh năng lực tổng quát hóa. Con số cần đưa
vào báo cáo khoa học là kết quả trên **tập kiểm tra độc lập**.

---

## 3. Khối liên thông

### 3.1 Bảng tóm tắt

| File | Dòng | Vai trò |
| --- | --- | --- |
| `backend/main.py` | 689 | SMIG Gateway — 8 endpoint, điều phối NLP và FHIR |
| `backend/fhir_helper.py` | 349 | Dựng tài nguyên FHIR R4, định nghĩa hệ định danh |
| `hospital_his/server.py` | 994 | HIS mô phỏng (Python) — bệnh án cục bộ, gọi Gateway |
| `hospital_his/frontend/app.js` | 899 | Giao diện HIS: bảng bệnh án, sửa chẩn đoán, nhật ký |
| `hospital_his/frontend/index.html` | 244 | Bố cục giao diện HIS |
| `hospital_his/frontend/style.css` | 1 017 | Định dạng giao diện HIS |
| `frontend/app.js` | 509 | Giao diện demo của Gateway (cổng 8000) |
| `frontend/index.html` | 213 | Bố cục giao diện Gateway |
| `frontend/style.css` | 913 | Định dạng giao diện Gateway |
| `VNPT_HIS/.../GatewayService.java` | 219 | HIS thứ hai (Java) — client gọi Gateway |
| `VNPT_HIS/.../HisController.java` | 432 | Luồng nghiệp vụ bệnh viện: tiếp đón, khám, dược, viện phí |
| `VNPT_HIS/frontend/src/App.jsx` | 1 409 | Giao diện React của HIS thứ hai |
| `docker-compose.yml` | — | EMR Cloud: HAPI FHIR + PostgreSQL |

### 3.2 `backend/main.py` — SMIG Gateway

Tám endpoint, chia đúng hai nhóm theo hai khối:

| Endpoint | Khối | Việc làm |
| --- | --- | --- |
| `GET /health` | — | Trạng thái mô hình, chính sách tin cậy, mã cơ sở |
| `POST /api/standardize` | NLP | Chuẩn hóa câu chẩn đoán → danh sách mã ICD-10 |
| `POST /api/fhir/condition` | Liên thông | Sinh tài nguyên FHIR Condition từ một mã |
| `POST /api/fhir/sync` | Liên thông | Đẩy Condition + Patient + Organization lên EMR Cloud |
| `GET /api/fhir/sync` | Liên thông | Đọc các Condition mới nhất trên trục |
| `GET /api/fhir/condition/{id}` | Liên thông | Đọc lại một Condition để đối chiếu |
| `DELETE /api/fhir/condition/{id}` | Liên thông | Gỡ một Condition (dùng khi bác sĩ sửa mã) |
| `DELETE /api/fhir/sync` | Liên thông | Dọn dữ liệu demo |

Ba cơ chế đáng nêu trong báo cáo:

**(a) Khóa nghiệp vụ ổn định — `stable_condition_key()`.** Mỗi Condition mang một khóa
gồm bốn thành phần:

```
{mã cơ sở}-{định danh bệnh nhân}-{mã ICD-10}-{ngày khám}
```

Mỗi thành phần chặn một kiểu trộn dữ liệu: thiếu **mã cơ sở** thì chẩn đoán của bệnh
viện B đè lên bệnh viện A khi hai nơi tình cờ trùng mã bệnh án; thiếu **ngày khám** thì
cùng người mắc lại cùng bệnh ở lần sau sẽ đè lên lần trước, làm mất lịch sử điều trị.

**(b) Conditional update.** Đồng bộ dùng `PUT /{Type}?identifier=...` thay vì POST, nên
thao tác **idempotent**: không khớp bản nào thì máy chủ tạo mới và tự cấp id, khớp đúng
một bản thì cập nhật bản đó, khớp nhiều bản thì trả 412 và không sửa gì. Client không tự
đặt id tài nguyên — id đoán được cộng với PUT theo id là một lỗ ghi đè.

**(c) Chế độ nhiều cơ sở — `resolve_facility()`.** Khi triển khai thật, mỗi bệnh viện
chạy một bản Gateway riêng và mã cơ sở do phía máy chủ xác lập. Cờ
`SMIG_ALLOW_CLIENT_FACILITY` (mặc định **tắt**) cho phép một bản Gateway phục vụ nhiều
bệnh viện trong môi trường thử nghiệm — cần thiết vì mô hình NLP chiếm vài GB RAM. Khai
mã lạ khi cờ tắt sẽ bị từ chối 403 chứ không âm thầm lấy mã của Gateway.

### 3.3 `backend/fhir_helper.py` — tầng chuẩn HL7 FHIR

Định nghĩa toàn bộ hệ định danh của đề tài dưới namespace `https://smig.nckh.vn/fhir`:

| Hằng số | Ý nghĩa |
| --- | --- |
| `ICD10_SYSTEM` | `http://hl7.org/fhir/sid/icd-10` — hệ mã quốc tế chuẩn |
| `SYSTEM_CCCD`, `SYSTEM_BHYT` | Định danh cấp quốc gia của bệnh nhân |
| `mrn_system(facility_code)` | Mã bệnh án — kèm mã cơ sở, vì chỉ có nghĩa trong nội bộ một viện |
| `SYSTEM_FACILITY` | Mã cơ sở khám chữa bệnh |
| `SYSTEM_CONDITION_KEY` | Khóa nghiệp vụ của Condition |
| `EXT_CONFIDENCE` | Extension mang **độ tin cậy của mô hình NLP** |
| `EXT_ENGINE` | Extension mang **phiên bản mô hình** đã sinh ra mã |
| `EXT_SOURCE_FACILITY` | Extension trỏ tới Organization đã ghi chẩn đoán |

Bốn hàm dựng tài nguyên: `build_fhir_condition_resource()`,
`build_fhir_patient_resource()`, `build_fhir_organization_resource()`, và
`patient_match_key()`.

**`patient_match_key()` là cơ chế đồng nhất bệnh nhân giữa các bệnh viện.** Thứ tự ưu
tiên phản ánh phạm vi hiệu lực của từng loại định danh:

1. **Số CCCD** — duy nhất toàn quốc, nên cùng một người khám ở hai bệnh viện vẫn quy về
   đúng một hồ sơ trên trục.
2. **Thẻ BHYT** — cũng toàn quốc, dùng khi chưa có CCCD.
3. **Mã bệnh án + mã cơ sở** — phương án cuối. Mã bệnh án chỉ có nghĩa trong phạm vi một
   bệnh viện, nên bắt buộc phải kèm mã cơ sở; thiếu nó thì "BN001" của hai bệnh viện
   khác nhau sẽ bị trộn thành một người.

Hai extension `EXT_CONFIDENCE` và `EXT_ENGINE` là đóng góp riêng của đề tài: chúng làm
cho **độ tin cậy của mô hình đi kèm bản ghi lên trục**, nên bên nhận biết được mã này do
máy suy ra ở mức chắc chắn nào và bằng phiên bản mô hình nào — điều mà một Condition
tiêu chuẩn không mang theo.

### 3.4 `hospital_his/server.py` — HIS mô phỏng (Python)

Đóng vai bệnh viện gửi dữ liệu. Lưu bệnh án cục bộ bằng SQLite (hai bảng: `patients` và
`patient_conditions`), gọi Gateway để chuẩn hóa và liên thông.

| Nhóm endpoint | Chức năng |
| --- | --- |
| `GET/POST/DELETE /api/patients` | Quản lý hồ sơ bệnh nhân nội viện |
| `GET /api/patients?search_id=` | Tra cứu **mã bệnh án, CCCD hoặc thẻ BHYT**; không có cục bộ thì kéo từ EMR Cloud |
| `POST /api/sync/{id}` | Liên thông lại toàn bộ hồ sơ |
| `POST /api/patients/{id}/diagnosis` | Chẩn đoán thêm bệnh mới, **giữ nguyên** chẩn đoán cũ |
| `PUT /api/patients/{id}/conditions/{code}` | **Sửa** một chẩn đoán đã ghi nhận |
| `DELETE /api/patients/{id}/conditions/{code}` | Gỡ một chẩn đoán khỏi bệnh án và khỏi trục |

Hai điểm nghiệp vụ đáng nêu:

* **Một dòng bệnh án sinh nhiều Condition.** Câu "sỏi bàng quang, suy thận cấp" được tách
  thành hai chẩn đoán độc lập, mỗi chẩn đoán một tài nguyên FHIR riêng. Chốt chặn an
  toàn lâm sàng xét **cho từng mã**: mã dưới ngưỡng dừng lại chờ bác sĩ duyệt nhưng không
  chặn những mã đã đủ tin cậy trong cùng câu.
* **Sửa chẩn đoán.** Mã ICD-10 nằm trong khóa nghiệp vụ, nên đổi mã không cập nhật được
  bản ghi cũ mà sinh tài nguyên mới. Thứ tự bắt buộc là: đẩy bản đúng lên trục trước, rồi
  mới gỡ bản mang mã sai — ngược lại thì có lúc hồ sơ trên trục không còn chẩn đoán nào.
  Bác sĩ tự chọn mã thì độ tin cậy ghi nhận là 100% và trạng thái xác minh là `confirmed`,
  phân biệt rõ với điểm số của mô hình.

### 3.5 `VNPT_HIS/` — HIS thứ hai (Java + React)

Bản HIS mô phỏng phần mềm bệnh viện thương mại, dùng để chứng minh **hai hệ thống khác
công nghệ vẫn liên thông được qua cùng một Gateway**.

Đường dẫn đầy đủ dưới `VNPT_HIS/backend/src/main/java/com/vnpt/his/backend/`:

* `service/GatewayService.java` — client gọi Gateway. Có **từ điển dự phòng cục bộ**: khi
  Gateway offline thì vẫn tra được mã cơ bản và ghi rõ trong bản ghi rằng kết quả không
  đến từ mô hình NLP.
* `controller/HisController.java` — luồng nghiệp vụ đầy đủ của một bệnh viện: tiếp đón,
  hàng chờ, phòng khám, cận lâm sàng, kho dược, viện phí BHYT. Chẩn đoán được đẩy lên trục
  tại thời điểm bác sĩ bấm **Hoàn thành khám**.
* `model/Patient.java` — có `citizenId` và `insuranceCard`, là điều kiện để trục nhận ra
  cùng một người khám ở hai bệnh viện.

Giao diện: `VNPT_HIS/frontend/src/App.jsx` — React, chạy trên cổng 3000, proxy `/api`
sang 8089.

Ngăn xếp: Spring Boot + PostgreSQL (cổng 8089) và React + Vite (cổng 3000).

### 3.6 `docker-compose.yml` — EMR Cloud

Dựng HAPI FHIR R4 đóng vai trục dữ liệu y tế quốc gia, kèm PostgreSQL để dữ liệu không
mất khi khởi động lại. Ba cấu hình an toàn được bật:

* `enforce_referential_integrity_on_write` — chặn Condition trỏ tới Patient không tồn tại.
* `client_id_strategy=NOT_ALLOWED` — client không được tự đặt id tài nguyên.
* Chỉ nghe trên `127.0.0.1` — cụm này giữ dữ liệu bệnh án mà chưa có xác thực.

---

## 4. Khối kiểm thử

| File | Dòng | Kiểm điều gì |
| --- | --- | --- |
| `nlp/test_nlp.py` | 290 | **Chất lượng NLP**: ca lâm sàng lõi, giải viết tắt, phục hồi dấu, chuẩn hóa Unicode NFD/NFC, tách nhiều bệnh, cặp †/*, chặn hồi quy trên cả hai tập đánh giá |
| `tests/conftest.py` | 145 | EMR Cloud giả lập trong bộ nhớ, mô phỏng đúng cơ chế conditional update |
| `tests/test_sua_chan_doan.py` | 167 | Sửa/gỡ chẩn đoán, tính lại chẩn đoán chính, chặn trạng thái không hợp lệ |
| `tests/test_nhieu_co_so.py` | 98 | Một Gateway phục vụ nhiều bệnh viện, chốt chặn mã cơ sở |
| `tests/test_chuyen_tuyen.py` | 92 | Kịch bản chuyển tuyến: A chẩn đoán → B thêm bệnh → tra bệnh sử bằng CCCD |
| `tests/test_luong_nlp_khong_doi.py` | 121 | Chốt hồi quy ranh giới hai khối: chức năng liên thông không được đụng kết quả NLP |

```powershell
.venv\Scripts\python -m pytest nlp/test_nlp.py -v    # cần mô hình NLP
.venv\Scripts\python -m pytest tests/ -v             # không cần mô hình, không cần Docker
```

---

## 5. Bảng tổng hợp toàn bộ file

| # | File | Khối | Ngôn ngữ | Dòng |
| --- | --- | --- | --- | --- |
| 1 | `nlp/nlp_engine.py` | NLP | Python | 1 360 |
| 2 | `nlp/clinical_rules.py` | NLP | Python | 502 |
| 3 | `nlp/evaluate.py` | NLP | Python | 189 |
| 4 | `nlp/test_nlp.py` | NLP | Python | 290 |
| 5 | `ChangeJson.py` | NLP (dữ liệu) | Python | 564 |
| 6 | `backend/main.py` | Liên thông | Python | 689 |
| 7 | `backend/fhir_helper.py` | Liên thông | Python | 349 |
| 8 | `hospital_his/server.py` | Liên thông | Python | 994 |
| 9 | `hospital_his/frontend/app.js` | Liên thông | JavaScript | 899 |
| 10 | `hospital_his/frontend/index.html` | Liên thông | HTML | 244 |
| 11 | `hospital_his/frontend/style.css` | Liên thông | CSS | 1 017 |
| 12 | `frontend/app.js` | Liên thông | JavaScript | 509 |
| 13 | `frontend/index.html` | Liên thông | HTML | 213 |
| 14 | `frontend/style.css` | Liên thông | CSS | 913 |
| 15 | `VNPT_HIS/.../GatewayService.java` | Liên thông | Java | 219 |
| 16 | `VNPT_HIS/.../HisController.java` | Liên thông | Java | 432 |
| 17 | `VNPT_HIS/frontend/src/App.jsx` | Liên thông | JSX | 1 409 |
| 18 | `tests/conftest.py` | Kiểm thử | Python | 145 |
| 19 | `tests/test_sua_chan_doan.py` | Kiểm thử | Python | 167 |
| 20 | `tests/test_nhieu_co_so.py` | Kiểm thử | Python | 98 |
| 21 | `tests/test_chuyen_tuyen.py` | Kiểm thử | Python | 92 |
| 22 | `tests/test_luong_nlp_khong_doi.py` | Kiểm thử | Python | 121 |

Script khởi chạy: `run.ps1` (Gateway), `hospital_his/run_his.ps1` (HIS Python),
`VNPT_HIS/run_vnpt_his.ps1` (HIS Java + React), `docker-compose.yml` (EMR Cloud).

---

## 6. Luồng dữ liệu đầy đủ của một ca khám

```
[1] Bác sĩ gõ: "bn bị đtđ tuýp 2 kèm cao huyết áp"
        │                                     hospital_his/frontend/app.js
        ▼
[2] HIS gửi POST /api/standardize             hospital_his/server.py
        │
        ▼
[3] NLPEngine.query_composite()               nlp/nlp_engine.py
    ├─ Tầng 1: chuẩn hóa + giải viết tắt      nlp/clinical_rules.py
    ├─ Tầng 2: cosine trên 12.137 mã          nlp/data/embeddings_*.npy
    ├─ Tầng 3: tái xếp hạng theo luật lâm sàng
    └─ Tầng 4: hiệu chuẩn độ tin cậy
        │
        ▼  → 2 chẩn đoán: E11.9 (93,4%) và I10 (71,5%)
        │
════════╪══════ RANH GIỚI HAI KHỐI ══════════════════════════════
        │
[4] Xét ngưỡng cho TỪNG mã                    hospital_his/server.py
    ├─ E11.9 ≥ ngưỡng → liên thông tiếp
    └─ I10  < ngưỡng → DỪNG, chờ bác sĩ duyệt
        │
        ▼
[5] POST /api/fhir/condition                  backend/main.py
    └─ build_fhir_condition_resource()        backend/fhir_helper.py
       + extension độ tin cậy, phiên bản mô hình, cơ sở ghi nhận
       + identifier = khóa nghiệp vụ ổn định
        │
        ▼
[6] POST /api/fhir/sync                       backend/main.py
    └─ conditional update: Organization → Patient → Condition
        │
        ▼
[7] EMR Cloud (HAPI FHIR R4)                  docker-compose.yml
        │
        ▼
[8] Bệnh viện khác tra bằng CCCD → thấy chẩn đoán này,
    gắn nhãn cơ sở đã ghi nhận, không sửa được từ bên ngoài
```
