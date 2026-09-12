# Smart Medical Interoperability Gateway (SMIG)

Đề tài Nghiên cứu Khoa học: **Đồng bộ chuẩn hóa dữ liệu liên thông các bệnh viện với mô
hình NLP tự động chuẩn hóa và mã hóa sang chuẩn ICD-10**.

Hệ thống nhận chẩn đoán lâm sàng viết tự do bằng tiếng Việt, ánh xạ sang mã ICD-10 chuẩn,
đóng gói theo **HL7 FHIR R4** và đồng bộ lên trục dữ liệu EMR Cloud để các bệnh viện đọc
được bệnh sử của nhau.

Toàn bộ mã nguồn chia thành **hai khối tách bạch**, và tài liệu này cũng chia theo đúng hai
khối đó:

|                                                      | Khối                      | Trả lời câu hỏi                                                              | Thư mục chính                               |
| ---------------------------------------------------- | -------------------------- | -------------------------------------------------------------------------------- | ---------------------------------------------- |
| **[Phần A](#phần-a--khối-nlp)**              | **NLP**              | Câu chẩn đoán này là mã ICD-10 nào, chắc bao nhiêu phần trăm?        | `nlp/`, `ChangeJson.py`                    |
| **[Phần B](#phần-b--khối-api-liên-thông)** | **API liên thông** | Mã đó đi lên trục dữ liệu thế nào để bệnh viện khác đọc đúng? | `backend/`, `hospital_his/`, `VNPT_HIS/` |

> **Ranh giới giữa hai khối nằm ở đúng một điểm:** khối NLP kết thúc khi trả ra danh sách mã
> ICD-10 kèm độ tin cậy; khối liên thông bắt đầu từ đó. Hai khối không dùng chung trạng thái
> nào — thay được mô hình NLP mà không đụng phần liên thông, và ngược lại. Ranh giới này
> được **kiểm chứng bằng test**: `tests/test_luong_nlp_khong_doi.py`.

---

## Mục lục

* [1. Kiến trúc tổng thể](#1-kiến-trúc-tổng-thể)
* [**Phần A — Khối NLP**](#phần-a--khối-nlp)
  * [A.1 Cấu trúc thư mục khối NLP](#a1-cấu-trúc-thư-mục-khối-nlp)
  * [A.2 Vấn đề khoa học](#a2-vấn-đề-khoa-học)
  * [A.3 Kiến trúc truy hồi lai 4 tầng](#a3-kiến-trúc-truy-hồi-lai-4-tầng)
  * [A.4 Tri thức lâm sàng](#a4-tri-thức-lâm-sàng-nlpclinical_rulespy)
  * [A.5 Ràng buộc lâm sàng và cặp dagger/asterisk](#a5-ràng-buộc-lâm-sàng-và-cặp-daggerasterisk)
  * [A.6 Dữ liệu và cache embedding](#a6-dữ-liệu-và-cache-embedding)
  * [A.7 API của khối NLP](#a7-api-của-khối-nlp)
  * [A.8 Đánh giá định lượng](#a8-đánh-giá-định-lượng)
  * [A.9 Kiểm thử khối NLP](#a9-kiểm-thử-khối-nlp)
* [**Phần B — Khối API liên thông**](#phần-b--khối-api-liên-thông)
  * [B.1 Cấu trúc thư mục khối liên thông](#b1-cấu-trúc-thư-mục-khối-liên-thông)
  * [B.2 API của SMIG Gateway](#b2-api-của-smig-gateway)
  * [B.3 Tầng chuẩn HL7 FHIR R4](#b3-tầng-chuẩn-hl7-fhir-r4-backendfhir_helperpy)
  * [B.4 Khóa nghiệp vụ và conditional update](#b4-khóa-nghiệp-vụ-và-conditional-update)
  * [B.5 Đồng nhất bệnh nhân giữa nhiều bệnh viện](#b5-đồng-nhất-bệnh-nhân-giữa-nhiều-bệnh-viện)
  * [B.6 Mã cơ sở là danh tính, không phải tham số](#b6-mã-cơ-sở-là-danh-tính-không-phải-tham-số)
  * [B.7 API của hai bản HIS](#b7-api-của-hai-bản-his)
  * [B.8 Kiểm thử khối liên thông](#b8-kiểm-thử-khối-liên-thông)
* [C. Chính sách an toàn lâm sàng — nơi hai khối gặp nhau](#c-chính-sách-an-toàn-lâm-sàng--nơi-hai-khối-gặp-nhau)
* [D. Cấu trúc thư mục toàn dự án](#d-cấu-trúc-thư-mục-toàn-dự-án)
* [E. Cài đặt và khởi chạy](#e-cài-đặt-và-khởi-chạy)
* [F. Cấu hình](#f-cấu-hình)
* [G. Kiểm thử toàn hệ thống](#g-kiểm-thử-toàn-hệ-thống)
* [H. Kịch bản trình diễn](#h-kịch-bản-trình-diễn)
* [I. Giới hạn đã biết](#i-giới-hạn-đã-biết)
* [J. Hướng phát triển](#j-hướng-phát-triển)
* [K. Tài liệu chi tiết](#k-tài-liệu-chi-tiết)

---

## 1. Kiến trúc tổng thể

```
   HIS bệnh viện A                        HIS bệnh viện B
  (VNPT HIS — Java/React)              (HIS mô phỏng — Python)
        :8089 / :3000                          :8085
              │                                   │
              └────────────────┬──────────────────┘
                               │
             POST /api/standardize      ◄── KHỐI NLP        (Phần A)
             POST /api/fhir/condition   ◄── KHỐI LIÊN THÔNG (Phần B)
             POST /api/fhir/sync        ◄── KHỐI LIÊN THÔNG
                               │
                               ▼
                    SMIG Gateway  :8000
                    backend/main.py + nlp/nlp_engine.py
                               │  HL7 FHIR R4
                               ▼
                     EMR Cloud  :8090
                (HAPI FHIR + PostgreSQL — trục dữ liệu)
```

| Thành phần             | Cổng       | Vai trò                                                               | Công nghệ               |
| ------------------------ | ----------- | ---------------------------------------------------------------------- | ------------------------- |
| **SMIG Gateway**   | 8000        | Chuẩn hóa NLP, sinh FHIR, cầu nối tới trục                       | FastAPI + SBERT           |
| **EMR Cloud**      | 8090        | Trục dữ liệu y tế quốc gia (mô phỏng)                           | HAPI FHIR R4 + PostgreSQL |
| **HIS mô phỏng** | 8085        | Bệnh viện gửi dữ liệu, có bệnh án riêng                       | FastAPI + SQLite          |
| **VNPT HIS**       | 8089 / 3000 | HIS thứ hai,**khác công nghệ** để chứng minh liên thông | Spring Boot + React       |

Quy mô mã nguồn:

| Khối                                             | Số file mã nguồn | Số dòng |
| ------------------------------------------------- | ------------------- | --------- |
| NLP                                               | 5                   | ~3 100    |
| API liên thông (Gateway + 2 HIS + 2 giao diện) | 12                  | ~8 400    |
| Kiểm thử                                        | 13                  | ~2 000    |

---

# Phần A — Khối NLP

> **Nhiệm vụ:** biến một dòng bệnh án viết tự do tiếng Việt thành danh sách mã ICD-10 kèm
> độ tin cậy. Kết thúc ở đó — không biết gì về FHIR, về trục dữ liệu hay về bệnh viện nào.

## A.1 Cấu trúc thư mục khối NLP

```
NCKH/
├── nlp/
│   ├── __init__.py
│   ├── nlp_engine.py                # 1 446 dòng — bộ máy truy hồi lai 4 tầng
│   ├── clinical_rules.py            #   707 dòng — tri thức lâm sàng, tách khỏi thuật toán
│   ├── evaluate.py                  #   189 dòng — đo Top-k, MRR, độ trễ, hiệu chuẩn
│   │
│   ├── test_nlp.py                  #   343 dòng — chất lượng nhận diện mã (CẦN mô hình)
│   ├── test_rang_buoc.py            #   234 dòng — luật thuần trên danh mục (KHÔNG cần mô hình)
│   │
│   ├── my_medical_nlp_model/        # SBERT tiếng Việt đã fine-tune, ~540 MB
│   │   ├── model.safetensors        #   trọng số — KHÔNG đưa vào git
│   │   ├── config.json
│   │   ├── config_sentence_transformers.json
│   │   ├── modules.json
│   │   ├── 1_Pooling/config.json
│   │   ├── tokenizer_config.json, vocab.txt, bpe.codes, added_tokens.json
│   │   └── sentence_bert_config.json
│   │
│   └── data/
│       ├── icd10_db.json            # Danh mục ICD-10 — 12 137 mã
│       ├── icd10_supplement.json    # Mã bổ sung thủ công, bắt buộc ghi nguồn
│       ├── eval_set.json            # Tập phát triển — 110 ca
│       ├── eval_holdout.json        # Tập kiểm tra độc lập — 51 ca
│       ├── embeddings_*.npy         # Cache embedding danh mục (~145 MB), sinh tự động
│       ├── alias_embeddings_*.npy   # Cache embedding của alias lâm sàng
│       └── entries_*.json           # Văn bản tham chiếu ứng với cache, để đối chiếu
│
├── ChangeJson.py                    #   641 dòng — dựng danh mục ICD-10 từ Excel Bộ Y tế
├── dataICD10.xlsx                   # Nguồn: danh mục ICD-10 của Bộ Y tế (14 sheet)
├── ICD10_100_chan_doan_CDLS_bo_sung.xlsx   # Phụ lục chẩn đoán lâm sàng bổ sung
└── Cong_thuc_toan_hoc_NLP_ICD10.docx       # Công thức toán học của phần tái xếp hạng
```

| File                      | Vai trò                                                                                                                                                         |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nlp/nlp_engine.py`     | Lớp`NLPEngine` — trái tim hệ thống. Chuẩn hóa, truy hồi, tái xếp hạng, hiệu chuẩn, tách nhiều bệnh, ghép cặp †/*, quản lý cache embedding |
| `nlp/clinical_rules.py` | Dữ liệu tri thức: viết tắt, tiền tố nhiễu, cầu nối từ vựng, trục đối lập, cổng chương, alias, ngưỡng tin cậy, ràng buộc tuổi/giới      |
| `nlp/evaluate.py`       | Đo Top-1/3/5 accuracy, MRR, độ trễ và độ chính xác**theo từng dải tin cậy**                                                                    |
| `nlp/test_nlp.py`       | Kiểm thử chất lượng nhận diện — chỉ đích danh mã đúng, không dùng tiền tố lỏng                                                                |
| `nlp/test_rang_buoc.py` | Kiểm thử luật ràng buộc lâm sàng trên dữ liệu danh mục, chạy dưới 1 giây                                                                          |
| `ChangeJson.py`         | Chuyển Excel Bộ Y tế →`icd10_db.json`, kèm phụ lục A1–A4                                                                                               |

## A.2 Vấn đề khoa học

Mô hình SBERT chỉ đo độ tương đồng ngữ nghĩa nên **không phân biệt được các cặp đối lập vốn
quyết định mã ICD-10**. Với truy vấn *"đái tháo đường tuýp 2"*, cosine similarity thuần túy
cho:

```
E10.3  0.6102   ĐTĐ phụ thuộc insuline (biến chứng mắt)   ← típ 1, SAI
E10.5  0.5932   ĐTĐ phụ thuộc insuline                     ← típ 1, SAI
E10.4  0.5882   ...                                        ← típ 1, SAI
E11.3  0.5785   ĐTĐ không phụ thuộc insuline               ← típ 2, đúng nhưng xếp thứ 4
```

Nguyên nhân kép:

* **(a)** Khoảng cách cosine giữa "phụ thuộc insuline" và "**không** phụ thuộc insuline" chỉ
  ~0,02 — một chữ "không" đảo ngược ý nghĩa lâm sàng nhưng gần như không đổi vector.
* **(b)** Danh mục Bộ Y tế **không dùng chữ "tuýp 2"** ở bất kỳ mã nào; thuật ngữ tương ứng
  là "không phụ thuộc insuline". Bác sĩ và danh mục nói hai thứ tiếng khác nhau.

Đây là lý do tồn tại của tầng tái xếp hạng theo luật lâm sàng — phần đóng góp chính của đề tài.

## A.3 Kiến trúc truy hồi lai 4 tầng

| Tầng                        | Chức năng                                                                                                                                                                                                                 | Hàm chính trong`nlp_engine.py`                                                                                                       |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **1. Chuẩn hóa**     | Bóc tiền tố/hậu tố hành văn (lặp đến khi ổn định), giải nghĩa viết tắt,**khôi phục dấu tiếng Việt**, thống nhất dấu thanh Unicode (NFD/NFC), nối cầu nối từ vựng                        | `normalize_text()`, `expand_query()`, `restore_diacritics()`, `thong_nhat_dau_thanh()`                                           |
| **2. Truy hồi**       | Cosine similarity trên embedding SBERT đã tiền tính cho 12 137 mã, lấy 400 ứng viên                                                                                                                                | `query()`, `_prepare_embeddings()`                                                                                                   |
| **3. Tái xếp hạng** | Điểm ngữ nghĩa**+** trùng lặp từ vựng **+** alias đã kiểm chứng **−** vi phạm trục đối lập **−** cổng chương **±** mức chi tiết **−** ràng buộc tuổi/giới | `_polarity_delta()`, `_lexical_f1()`, `_alias_targets()`, `_chapter_delta()`, `_specificity_delta()`, `rang_buoc_lam_sang()` |
| **4. Hiệu chuẩn**    | Softmax gộp theo khối 3 ký tự, kết hợp độ tương đồng tuyệt đối → độ tin cậy 0–100%                                                                                                                      | `_calibrate()`                                                                                                                         |

**Vì sao tầng 3 là mấu chốt.** SBERT xếp gần như ngang nhau những cặp mã mà lâm sàng phân
biệt rạch ròi: E10 (phụ thuộc insulin) với E11 (không phụ thuộc insulin), I10 (tăng huyết áp
vô căn) với I15 (thứ phát). Tầng 3 là lý do hệ thống chọn đúng — và cũng là lý do kết quả
**giải thích được**: mỗi mã trả về kèm trường `explanation` liệt kê đúng những luật đã cộng
hoặc trừ điểm cho nó.

Ba hàm đối ngoại:

| Hàm                                   | Trả về                                                                                                                                                                                     |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `query(user_query, top_k)`           | Top-k mã cho**một** chẩn đoán                                                                                                                                                     |
| `query_composite(user_query, top_k)` | Tách**nhiều bệnh trong một câu** thành nhiều chẩn đoán độc lập, mỗi chẩn đoán giữ độ tin cậy riêng; nhận diện thêm **cặp mã dagger/asterisk (†/\*)** |
| `extract_entities_regex(text)`       | Vị trí thực thể lâm sàng trong nguyên văn, phục vụ bôi màu trên giao diện                                                                                                      |

## A.4 Tri thức lâm sàng (`nlp/clinical_rules.py`)

Tách riêng khỏi bộ máy để sửa **tri thức** mà không đụng **thuật toán**:

| Hằng số                                             | Nội dung                                                                                                                                                                                                     |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ABBREVIATIONS`, `QUERY_ABBREVIATIONS`            | Viết tắt bác sĩ hay dùng:`đtđ` → đái tháo đường, `THA` → tăng huyết áp                                                                                                                  |
| `NOISE_PREFIXES`, `NOISE_SUFFIXES`, `STOPWORDS` | Cụm không mang thông tin chẩn đoán: "bệnh nhân bị", "theo dõi", "nhẹ"                                                                                                                              |
| `BRIDGE_TERMS`                                      | **Cầu nối từ vựng** — nối thuật ngữ chuẩn của danh mục vào truy vấn. *"ung thư"* → *"u ác"*: cả 553 mã chương C dùng "u ác", không mã nào ghi "ung thư"                   |
| `POLARITY_AXES`                                     | **Trục đối lập ngữ nghĩa** — 5 trục: típ đái tháo đường, nguyên nhân tăng huyết áp, biến chứng, cấp/mạn, dị ứng. Nhãn truy vấn xung đột nhãn ứng viên thì trừ điểm |
| `CHAPTER_GATES`                                     | **Cổng chương** — chương O (thai sản), S/T (chấn thương), V–Y (ngoại sinh), Z chỉ hợp lệ khi câu chẩn đoán có ngữ cảnh tương ứng                                               |
| `UNSPECIFIED_*`, `OTHER_*`                        | **Mức chi tiết** — không nêu thể bệnh thì ưu tiên mã ".9 không đặc hiệu"; có nêu thì hạ bậc ".9" và ".8 khác"                                                                     |
| `DIRECT_ALIASES`                                    | Alias lâm sàng đã kiểm chứng — vừa cộng điểm, vừa được mã hóa embedding bổ sung                                                                                                             |
| `DATA_FIXES`                                        | Sửa lỗi dữ liệu trong danh mục nguồn, ghi rõ từng chỗ                                                                                                                                                |
| `CONFIDENCE_POLICY`                                 | Ba ngưỡng:`auto_confirm` 85%, `provisional` 60%, `reject` 40%                                                                                                                                         |
| `SEX_/AGE_/NON_PRIMARY_PENALTY`                     | Mức phạt cho ứng viên vi phạm ràng buộc giới tính, khoảng tuổi, hoặc không được làm bệnh chính                                                                                             |

### Bốn khiếm khuyết phát hiện qua tập kiểm tra độc lập

| Khiếm khuyết                                                | Khắc phục                           |
| ------------------------------------------------------------- | ------------------------------------- |
| Danh mục dùng "u ác", bác sĩ nói "ung thư" (553 mã)   | Cầu nối từ vựng                   |
| Danh mục ghi "(Có hôn mê)" chứ không ghi "biến chứng" | Mở rộng phạm vi trục biến chứng |
| Truy vấn không nêu thể bệnh lại ra mã ".8 khác"       | Luật mức chi tiết                  |
| Tương tự với "hạ glucose máu", "u cơ trơn tử cung"   | Cầu nối từ vựng                   |

## A.5 Ràng buộc lâm sàng và cặp dagger/asterisk

`ChangeJson.py` không chỉ đọc sheet mã chính, mà còn đọc các phụ lục mang **ràng buộc lâm
sàng** của Bộ Y tế và đưa vào `meta` của từng mã:

| Phụ lục      | Ràng buộc                                                                      | Cách dùng khi xếp hạng                                                                                                |
| -------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **A1**   | Ghép cặp**dagger (†) / asterisk (\*)** — bệnh nguyên và biểu hiện | `query_composite()` nhận diện và trả cặp mã, thay vì chọn một trong hai                                        |
| **A2**   | Mã**không được dùng làm bệnh chính**                              | Trừ`NON_PRIMARY_PENALTY` khi mã đó định làm chẩn đoán chính                                                  |
| **A3.x** | Giới hạn**khoảng tuổi**                                                | Trừ`AGE_CONFLICT_PENALTY` nếu HIS gửi kèm `patient_birth_date`                                                    |
| **A4.x** | Giới hạn**giới tính**                                                  | Trừ`SEX_CONFLICT_PENALTY` nếu HIS gửi kèm `patient_sex` — phạt nặng nhất vì giới tính gần như bất biến |

Bối cảnh bệnh nhân là **tùy chọn**: không gửi thì kết quả y hệt như trước, nên HIS chưa cập
nhật vẫn chạy nguyên. Gửi thì bệnh nhân nam không nhận mã sản khoa, người 60 tuổi không nhận
mã sơ sinh.

> **Vì sao có bộ test riêng cho phần này** (`nlp/test_rang_buoc.py`): dữ liệu ràng buộc từng
> **sai mà không ai biết**. Hai phụ lục nằm chung một sheet Excel, bộ nạp gán một nhãn cho cả
> sheet, nên 515 mã mang khoảng tuổi của phụ lục khác — trong đó 505 mã sản phụ khoa (hợp lệ
> 9–60 tuổi) bị gán 8–19 tuổi. Đem ràng buộc đó ra lọc thì sản phụ 30 tuổi bị loại đúng mã
> của mình. Lỗi dữ liệu im lặng như vậy chỉ bắt được bằng test chạy trực tiếp trên danh mục.

## A.6 Dữ liệu và cache embedding

| Đường dẫn                       | Nội dung                                                                                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `nlp/data/icd10_db.json`          | **12 137 mã**, mỗi mã có `code`, `code_no_dot`, `name_vi`, `name_en`, `synonyms`, `meta` (chương, nhóm, nguồn, ràng buộc A1–A4) |
| `nlp/data/icd10_supplement.json`  | Mã bổ sung thủ công, bắt buộc ghi nguồn                                                                                                               |
| `nlp/data/eval_set.json`          | Tập phát triển —**110 ca**, dùng để hiệu chỉnh luật                                                                                          |
| `nlp/data/eval_holdout.json`      | Tập kiểm tra độc lập —**51 ca**, soạn sau khi đã khóa luật                                                                                  |
| `nlp/data/embeddings_*.npy`       | Cache embedding danh mục (~145 MB/bản), sinh tự động                                                                                                    |
| `nlp/data/alias_embeddings_*.npy` | Cache embedding của alias lâm sàng                                                                                                                        |
| `nlp/my_medical_nlp_model/`       | SBERT tiếng Việt đã fine-tune (~540 MB,**không đưa vào git**)                                                                                  |

Định dạng một ca đánh giá:

```json
{ "query": "đái tháo đường tuýp 2", "expected": ["E11", "E11.9"], "tag": "plain" }
```

**Cache embedding.** Mã hóa 12 137 mục mất 15–40 phút trên CPU, nên kết quả được lưu ra
`.npy` kèm **vân tay mô hình** (`_model_fingerprint()`) để cache không bị dùng nhầm khi đổi
mô hình hoặc đổi danh mục. Chạy lại `ChangeJson.py` sẽ đổi khóa cache — lần khởi động kế
tiếp phải mã hóa lại toàn bộ; hệ thống có in cảnh báo trước khi chạy.

## A.7 API của khối NLP

Khối NLP lộ ra **đúng hai endpoint** trên Gateway. Mọi endpoint khác trong `backend/` thuộc
Phần B.

### `POST /api/standardize`

```jsonc
// Request
{
  "query": "bn bị đtđ tuýp 2 kèm cao huyết áp",
  "top_k": 4,
  "patient_sex": "male",             // tùy chọn — nhận male/female hoặc Nam/Nữ
  "patient_birth_date": "1965-03-12" // tùy chọn — bật ràng buộc A3/A4
}
```

```jsonc
// Response (rút gọn)
{
  "query": "...",
  "normalized_query": "đái tháo đường không phụ thuộc insuline tăng huyết áp",
  "entities": [ { "text": "đtđ", "normalized": "...", "code": "E11.9",
                  "code_no_dot": "E119", "type": "..." } ],
  "predictions": [ {
      "code": "E11.9", "code_no_dot": "E119", "raw_code": "E11.9",
      "name_vi": "...", "name_en": "...",
      "confidence": 93.4, "confidence_band": "auto_confirm",
      "similarity_score": 0.61, "rerank_score": 0.79,
      "matched_by": "alias", "match_type": "...",
      "explanation": ["khớp alias lâm sàng đã kiểm chứng",
                      "xung đột diabetes_type với E10.x"],
      "suggested_verification_status": "provisional",
      "requires_review": false
  } ],
  "diagnoses": [                      // câu nhiều bệnh → nhiều mục, mỗi mục độ tin cậy riêng
     { "fragment": "đtđ tuýp 2",   "predictions": [ /* ... */ ] },
     { "fragment": "cao huyết áp", "predictions": [ /* ... */ ] }
  ],
  "combination": null,                // khác null khi phát hiện cặp dagger/asterisk †/*
  "latency_ms": 59.3
}
```

Ba trường đáng chú ý cho báo cáo khoa học:

* **`explanation`** — kết quả *giải thích được*: liệt kê đúng luật nào đã cộng/trừ điểm.
* **`diagnoses`** — một dòng bệnh án có thể sinh nhiều Condition ở Phần B.
* **`suggested_verification_status`** — điểm nối sang chính sách an toàn lâm sàng (mục C).

### `GET /api/icd10/{code}`

Tra tên bệnh theo mã trong danh mục 12 137 mã. Tồn tại vì lý do liên thông: mỗi HIS chỉ mang
theo một bảng mã nhỏ, để HIS tự điền tên thì chẩn đoán lên trục mang nhãn chỗ điền tạm thay
vì tên bệnh — lỗi này đã xảy ra thật và được chốt bằng `tests/test_ten_benh_theo_danh_muc.py`.

## A.8 Đánh giá định lượng

```powershell
.venv\Scripts\python -m nlp.evaluate                                            # tập phát triển
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
.venv\Scripts\python -m nlp.evaluate --json ket_qua.json
```

### Kết quả hiện tại

| Chỉ số              | Tập phát triển (110 ca) | **Tập kiểm tra độc lập (51 ca)** |
| --------------------- | -------------------------- | ------------------------------------------- |
| Top-1 accuracy        | 100,0%                     | **70,6%**                             |
| Top-3 accuracy        | 100,0%                     | 88,2%                                       |
| Top-5 accuracy        | 100,0%                     | 92,2%                                       |
| MRR                   | 1,000                      | 0,796                                       |
| Độ trễ trung bình | 60 ms                      | 59 ms                                       |

> **Cách đọc bảng này.** Tập phát triển (`eval_set.json`) đã được dùng để hiệu chỉnh luật,
> alias và trọng số, nên con số 100% trên đó **không** phản ánh năng lực tổng quát hóa — chỉ
> chứng tỏ các luật đã được cài đúng. Con số cần trích dẫn trong báo cáo khoa học là cột
> **tập kiểm tra độc lập** (`eval_holdout.json`), soạn sau khi hoàn tất tinh chỉnh.
>
> Lần đo đầu trên tập độc lập cho Top-1 62,7% / Top-5 86,3%. Phân tích lỗi phát hiện bốn
> khiếm khuyết hệ thống (mục A.4), khắc phục xong đạt 70,6% / 92,2%. Vì đã nhìn vào lỗi của
> tập này nên nó **không còn hoàn toàn "sạch"**; lần báo cáo tiếp theo cần một tập độc lập mới.

### Diễn giải theo nhóm đầu vào (tập độc lập)

| Nhóm                         | Số ca | Top-1  | Top-5  |
| ----------------------------- | ------ | ------ | ------ |
| Có viết tắt lâm sàng     | 4      | 100,0% | 100,0% |
| Gõ không dấu               | 4      | 75,0%  | 75,0%  |
| Chẩn đoán viết đầy đủ | 32     | 71,9%  | 90,6%  |
| Có tiền tố hành văn      | 3      | 66,7%  | 100,0% |
| Cặp đối lập ngữ nghĩa   | 4      | 50,0%  | 100,0% |
| Chọn mức chi tiết của mã | 4      | 50,0%  | 100,0% |

Hai nhóm yếu nhất (đối lập ngữ nghĩa, mức chi tiết) đều đạt **Top-5 = 100%** — mã đúng luôn
nằm trong danh sách gợi ý, phù hợp với thiết kế *"máy gợi ý, bác sĩ chọn"*.

## A.9 Kiểm thử khối NLP

```powershell
.venv\Scripts\python -m pytest nlp/test_nlp.py -v        # CẦN mô hình đã tải về
.venv\Scripts\python -m pytest nlp/test_rang_buoc.py -v  # KHÔNG cần mô hình, chạy <1 giây
```

| File                      | Kiểm điều gì                                                                                                                                                            |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nlp/test_nlp.py`       | Ca lâm sàng lõi, giải viết tắt, phục hồi dấu, chuẩn hóa Unicode NFD/NFC, tách nhiều bệnh, cặp †/\*, chặn hồi quy trên **cả hai** tập đánh giá |
| `nlp/test_rang_buoc.py` | Ràng buộc A2/A3/A4 trên dữ liệu danh mục: khoảng tuổi, giới tính, mã không được làm bệnh chính                                                            |

> **Nguyên tắc của bộ test này: mọi ca phải chỉ đích danh mã ICD-10 đúng.** Phiên bản trước
> dùng tiền tố lỏng ("E1" khớp cả E10 lẫn E11) nên báo 4/4 PASS trong khi mô hình đang trả
> E10.3 cho "đái tháo đường tuýp 2" (sai típ bệnh) và I15 cho "cao huyết áp vô căn" (sai
> nguyên phát/thứ phát). Một bộ test xanh mà che giấu lỗi lâm sàng còn nguy hiểm hơn test đỏ.

---

# Phần B — Khối API liên thông

> **Nhiệm vụ:** nhận mã ICD-10 kèm độ tin cậy từ Phần A, đóng gói theo HL7 FHIR R4, đẩy lên
> trục dữ liệu sao cho bệnh viện khác đọc về **đúng người, đúng bệnh, đúng nơi lập**.

## B.1 Cấu trúc thư mục khối liên thông

```
NCKH/
├── backend/                         ◄── SMIG Gateway — lõi liên thông
│   ├── __init__.py
│   ├── main.py                      # 1 007 dòng — 9 endpoint, điều phối NLP ↔ FHIR
│   ├── fhir_helper.py               #   461 dòng — dựng tài nguyên FHIR R4, hệ định danh
│   └── requirements.txt             # trỏ về requirements.txt gốc
│
├── frontend/                        ◄── Giao diện demo của Gateway (cổng 8000)
│   ├── index.html                   #   213 dòng — bố cục 3 bước: chuẩn hóa → NER → ICD-10
│   ├── app.js                       #   511 dòng — gọi API, bôi màu thực thể, sinh FHIR
│   └── style.css                    #   913 dòng
│
├── hospital_his/                    ◄── HIS mô phỏng #1 (Python) — cổng 8085
│   ├── server.py                    # 1 408 dòng — bệnh án SQLite, 10 endpoint, gọi Gateway
│   ├── his_db.sqlite                #   CSDL cục bộ (2 bảng: patients, patient_conditions)
│   ├── run_his.ps1 / run_his.bat    #   khởi chạy, nhận -Port -FacilityCode -GatewayUrl -DbPath
│   └── frontend/
│       ├── index.html               #   262 dòng — bảng bệnh án, tab log, tab FHIR JSON
│       ├── app.js                   # 1 072 dòng — sửa/gỡ chẩn đoán, tra bệnh sử toàn tuyến
│       └── style.css                # 1 114 dòng
│
├── VNPT_HIS/                        ◄── HIS mô phỏng #2 (Java + React) — cổng 8089 / 3000
│   ├── backend/                     #   Spring Boot + PostgreSQL
│   │   ├── pom.xml
│   │   ├── mvnw / mvnw.cmd
│   │   └── src/main/
│   │       ├── java/com/vnpt/his/backend/
│   │       │   ├── BackendApplication.java
│   │       │   ├── controller/HisController.java   # 497 dòng — 20 endpoint nghiệp vụ viện
│   │       │   ├── service/
│   │       │   │   ├── GatewayService.java         # 223 dòng — client gọi SMIG Gateway
│   │       │   │   └── EmrLookupService.java       # 247 dòng — tra bệnh sử trên trục
│   │       │   ├── model/          # Patient, ExamRecord, DrugItem,
│   │       │   │                   # PrescriptionItem, QueueTicket, ServiceBill
│   │       │   └── repository/     # 6 JPA repository tương ứng
│   │       └── resources/application.properties
│   ├── frontend/                    #   React + Vite
│   │   ├── index.html
│   │   └── src/App.jsx              # 1 562 dòng — tiếp đón, hàng chờ, khám, dược, viện phí
│   └── run_vnpt_his.ps1
│
├── docker-compose.yml               ◄── EMR Cloud: HAPI FHIR R4 + PostgreSQL (cổng 8090)
└── tests/                           ◄── Kiểm thử khối liên thông — xem B.8
```

| File                                 | Vai trò                                                                                                                                                                                            |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `backend/main.py`                  | 9 endpoint, khóa nghiệp vụ ổn định, conditional update, chốt chặn mã cơ sở                                                                                                               |
| `backend/fhir_helper.py`           | Toàn bộ hệ định danh dưới namespace`https://smig.nckh.vn/fhir`, 4 hàm dựng tài nguyên, kiểm định dạng CCCD/BHYT                                                                    |
| `hospital_his/server.py`           | Bệnh án nội viện, một dòng bệnh án → nhiều Condition, sửa/gỡ chẩn đoán, tra bệnh sử toàn tuyến                                                                                   |
| `VNPT_HIS/.../GatewayService.java` | Client gọi Gateway,**có từ điển dự phòng cục bộ**: Gateway offline vẫn tra được mã cơ bản và **ghi rõ trong bản ghi rằng kết quả không đến từ mô hình NLP** |
| `VNPT_HIS/.../HisController.java`  | Luồng nghiệp vụ đầy đủ: tiếp đón → hàng chờ → phòng khám → cận lâm sàng → kho dược → viện phí BHYT. Chẩn đoán lên trục khi bác sĩ bấm**Hoàn thành khám** |
| `docker-compose.yml`               | Trục dữ liệu, bật 3 cấu hình an toàn (xem B.4)                                                                                                                                               |

## B.2 API của SMIG Gateway

Chín endpoint, chia đúng theo hai khối:

| Endpoint                            | Khối                  | Việc làm                                                                               |
| ----------------------------------- | ---------------------- | ---------------------------------------------------------------------------------------- |
| `GET /health`                     | —                     | Trạng thái mô hình, chính sách tin cậy, mã cơ sở, cờ `allow_client_facility`, `allow_demo_facility`, `require_api_key` |
| `POST /api/standardize`           | **NLP**          | Chuẩn hóa câu chẩn đoán → danh sách mã ICD-10 (mục A.7)                        |
| `GET /api/icd10/{code}`           | **NLP**          | Tra tên bệnh theo mã trong danh mục 12 137 mã                                       |
| `POST /api/fhir/condition`        | **Liên thông** | Sinh tài nguyên FHIR Condition từ một mã                                            |
| `POST /api/fhir/sync`             | **Liên thông** | Đẩy Organization → Patient → Condition lên EMR Cloud                                |
| `GET /api/fhir/sync`              | **Liên thông** | Đọc các Condition mới nhất trên trục (mọi cơ sở, kèm`id`)                   |
| `GET /api/fhir/condition/{id}`    | **Liên thông** | Đọc lại một Condition để đối chiếu                                              |
| `DELETE /api/fhir/condition/{id}` | **Liên thông** | Gỡ một Condition —**chỉ trong phạm vi cơ sở của mình**                    |
| `DELETE /api/fhir/sync`           | **Liên thông** | Dọn dữ liệu demo — cũng chỉ trong phạm vi cơ sở của mình                      |

> **Nhóm Liên thông (`/api/fhir/*`) yêu cầu header `X-SMIG-Api-Key`** khi Gateway đã cấp
> ít nhất một khóa — xem [B.6](#b6-mã-cơ-sở-là-danh-tính-không-phải-tham-số). Nhóm NLP và
> `GET /health` không đòi khóa.

### `POST /api/fhir/condition` — hợp đồng dữ liệu

```jsonc
{
  "patient_id": "BN0001",             // mã bệnh án — chỉ có nghĩa TRONG một bệnh viện
  "patient_name": "Nguyễn Văn A",
  "raw_clinical_note": "bn bị đtđ tuýp 2",
  "icd10_code": "E11.9",
  "icd10_display": null,              // bỏ trống → Gateway tự tra tên trong danh mục
  "confidence_score": 93.4,           // 0–100, từ khối NLP
  "clinical_status": "active",
  "verification_status": null,        // bỏ trống → suy ra từ CONFIDENCE_POLICY
  "gender": "male",
  "birth_date": "1965-03-12",
  "citizen_id": "079065001234",       // CCCD 12 số — định danh toàn quốc
  "insurance_card": "0106500123",     // BHYT 10 số (mẫu cấp từ 01/4/2021)
  "encounter_date": "2026-09-09",     // nằm trong khóa nghiệp vụ
  "facility_code": "BV-A-001",        // chỉ được khai khi Gateway bật cờ — xem B.6
  "facility_name": "Bệnh viện Đa khoa A"
}
```

`citizen_id` và `insurance_card` được **kiểm định dạng ngay tại tầng nhận yêu cầu** — sai
định dạng là HTTP **422** trước khi bất kỳ tài nguyên nào được dựng, và giá trị đi tiếp vào
`identifier` là **bản đã chuẩn hóa** (bỏ dấu cách/chấm/gạch). Giữ nguyên thì
`079 095 010245` và `079095010245` là hai định danh khác nhau, tách hồ sơ một người thành hai.

> Lý do có lớp kiểm này — ca thật đã xảy ra trên trục: tên bệnh *"tiêu chảy nhiệt đới"* bị gõ
> nhầm vào ô CCCD. Gateway nhận, coi đó là một danh tính cấp quốc gia và dựng thêm hồ sơ bệnh
> nhân thứ hai cho cùng một người. Từ đó tra bằng CCCD thật chỉ ra chẩn đoán của bệnh viện
> kia, còn chẩn đoán vừa lập thì treo ở hồ sơ mang định danh rác — không mất, nhưng không ai
> tìm thấy. Chốt bằng `tests/test_kiem_dinh_danh.py`.

## B.3 Tầng chuẩn HL7 FHIR R4 (`backend/fhir_helper.py`)

Toàn bộ hệ định danh của đề tài nằm dưới namespace `https://smig.nckh.vn/fhir`:

| Hằng số                        | Ý nghĩa                                                                                 |
| -------------------------------- | ----------------------------------------------------------------------------------------- |
| `ICD10_SYSTEM`                 | `http://hl7.org/fhir/sid/icd-10` — hệ mã quốc tế chuẩn                            |
| `SYSTEM_CCCD`, `SYSTEM_BHYT` | Định danh**cấp quốc gia** của bệnh nhân                                      |
| `mrn_system(facility_code)`    | Mã bệnh án —**kèm mã cơ sở**, vì chỉ có nghĩa trong nội bộ một viện |
| `SYSTEM_FACILITY`              | Mã cơ sở khám chữa bệnh                                                             |
| `SYSTEM_CONDITION_KEY`         | Khóa nghiệp vụ của Condition                                                          |
| `EXT_CONFIDENCE`               | Extension mang**độ tin cậy của mô hình NLP**                                  |
| `EXT_ENGINE`                   | Extension mang**phiên bản mô hình** đã sinh ra mã                            |
| `EXT_SOURCE_FACILITY`          | Extension trỏ tới`Organization` đã ghi chẩn đoán                                 |

Bốn hàm dựng tài nguyên: `build_fhir_condition_resource()`, `build_fhir_patient_resource()`,
`build_fhir_organization_resource()`, và `patient_match_key()`.

**Hai extension `EXT_CONFIDENCE` và `EXT_ENGINE` là đóng góp riêng của đề tài.** Chúng làm
cho **độ tin cậy của mô hình đi kèm bản ghi lên trục**, nên bên nhận biết được mã này do máy
suy ra ở mức chắc chắn nào và bằng phiên bản mô hình nào — điều mà một Condition tiêu chuẩn
không mang theo.

Mỗi Condition còn ghi cơ sở đã lập nó ở **hai chỗ bổ trợ nhau**:

* `meta.tag` — lọc trực tiếp trên API: `GET /Condition?_tag={system}|{mã cơ sở}`
* `extension[source-facility]` — tham chiếu `Organization`, kèm `display` là tên bệnh viện để
  đọc JSON là thấy ngay

## B.4 Khóa nghiệp vụ và conditional update

**(a) Khóa nghiệp vụ ổn định — `stable_condition_key()`.** Mỗi Condition mang một khóa gồm
bốn thành phần:

```
{mã cơ sở}-{định danh bệnh nhân}-{mã ICD-10}-{ngày khám}
```

Mỗi thành phần chặn một kiểu trộn dữ liệu:

* Thiếu **mã cơ sở** → chẩn đoán của bệnh viện B đè lên bệnh viện A khi hai nơi trùng mã bệnh án.
* Thiếu **ngày khám** → cùng người mắc lại cùng bệnh ở lần sau sẽ đè lên lần trước, **mất
  lịch sử điều trị**.

**(b) Conditional update.** Đồng bộ dùng `PUT /{Type}?identifier=...` thay vì `POST`, nên
thao tác **idempotent**:

| Số bản khớp | Hành vi máy chủ                 |
| -------------- | ---------------------------------- |
| 0              | Tạo mới, máy chủ tự cấp id   |
| 1              | Cập nhật đúng bản đó        |
| >1             | Trả**412**, không sửa gì |

Client **không tự đặt id tài nguyên** — id đoán được cộng với `PUT` theo id là một lỗ ghi đè.
Ba cấu hình an toàn được bật trên EMR Cloud (`docker-compose.yml`):

* `enforce_referential_integrity_on_write` — chặn Condition trỏ tới Patient không tồn tại.
* `client_id_strategy=NOT_ALLOWED` — client không được tự đặt id tài nguyên.
* Chỉ nghe trên `127.0.0.1` — cụm này giữ dữ liệu bệnh án mà chưa có xác thực.

## B.5 Đồng nhất bệnh nhân giữa nhiều bệnh viện

Khi có từ hai bệnh viện cùng liên thông, câu hỏi khó nhất **không phải** là mã ICD-10 mà là
*"hai hồ sơ này có phải cùng một người không"*. Bản đầu dùng mã bệnh án làm khóa và mắc đúng
lỗi kinh điển: `BN001` của bệnh viện A và `BN001` của bệnh viện B bị coi là một người, hồ sơ
ghi đè lẫn nhau. Thực nghiệm tái hiện được: sau hai lần đồng bộ, trên trục chỉ còn một bệnh
nhân mang họ tên, giới tính và ngày sinh của **người thứ hai**.

`patient_match_key()` chọn khóa theo **phạm vi hiệu lực** của định danh:

| Ưu tiên | Định danh        | Phạm vi               | `identifier.system`                |
| --------- | ------------------ | ---------------------- | ------------------------------------ |
| 1         | Số CCCD (12 số)  | Toàn quốc            | `.../identifier/cccd`              |
| 2         | Thẻ BHYT (10 số mẫu mới, hoặc 15 ký tự mẫu cũ) | Toàn quốc            | `.../identifier/bhyt`              |
| 3         | Mã bệnh án      | **Một cơ sở** | `.../identifier/mrn/{mã cơ sở}` |

Khóa đồng nhất chỉ được là **một** cặp `(system, value)` — nó là khóa để conditional update
tìm lại đúng một bản ghi. BHYT mang `system` riêng chứ không mượn `SYSTEM_CCCD`: dán nhầm
nhãn thì lớp kiểm định dạng CCCD sẽ trả 422 oan.

Mã bệnh án vẫn dùng được, nhưng `system` **phải mang mã cơ sở đã cấp nó** — đúng vai trò mà
đặc tả FHIR giao cho `identifier.system`: chỉ ra **ai** cấp định danh đó.

> **Khóa không được đổi giữa chừng.** Lần đầu tiếp nhận gấp, lễ tân chỉ kịp cấp mã bệnh án;
> lần sau bệnh nhân mang giấy tờ tới thì CCCD mới được nhập. Nếu khóa đổi theo việc HIS có
> gửi kèm CCCD hay không, trục sinh thêm bệnh nhân thứ hai cho cùng một người, và từ lần thứ
> ba conditional update khớp cả hai bản nên máy chủ trả 412 — luồng liên thông chết hẳn mà
> không tự phục hồi. Chốt bằng `tests/test_dinh_danh_bo_sung_sau.py`.

Nhờ vậy khi cùng một người (cùng CCCD) khám ở hai nơi, trục giữ **một** hồ sơ bệnh nhân với
**hai** chẩn đoán riêng, mỗi chẩn đoán ghi rõ bệnh viện nào đã lập.

## B.6 Mã cơ sở là danh tính, không phải tham số

**`resolve_facility()`.** Khi triển khai thật, mỗi bệnh viện chạy một bản Gateway riêng và mã
cơ sở do **phía máy chủ** xác lập. Cờ `SMIG_ALLOW_CLIENT_FACILITY` (mặc định **tắt**) cho
phép một bản Gateway phục vụ nhiều bệnh viện trong môi trường thử nghiệm — cần thiết vì mô
hình NLP chiếm vài GB RAM. Khai mã lạ khi cờ tắt sẽ bị **403** chứ không âm thầm lấy mã của
Gateway.

Chốt chặn này áp cho **cả đường ghi lẫn đường xóa**:

* `DELETE /api/fhir/sync` — dọn dữ liệu demo chỉ trong phạm vi cơ sở của mình.
* `DELETE /api/fhir/condition/{id}` — gỡ một bản ghi cũng phải hỏi bản ghi đó của ai.

> Vì sao đường xóa quan trọng ngang đường ghi: `GET /api/fhir/sync` **cố ý** trả chẩn đoán
> của mọi cơ sở kèm `id` — nhìn thấy hồ sơ nơi khác lập chính là điều cần trình bày. Ghép hai
> đường lại là xóa được chéo: đọc danh sách, lấy `id` của bệnh viện B, gọi gỡ. Một nút "dọn
> dữ liệu demo" xóa mất bệnh án của viện khác thì hậu quả nặng hơn ghi nhầm, vì **không phục
> hồi được**. Chốt bằng `tests/test_xoa_theo_co_so.py` và `tests/test_go_mot_chan_doan.py`.

### Khóa API gắn với cơ sở (T1.4a)

Cờ `SMIG_ALLOW_CLIENT_FACILITY` chỉ trả lời câu *"có tin lời khai không"*. Từ T1.4a, bên gọi
có thể **chứng minh** mình là ai: mỗi HIS được cấp một khóa API, và khóa **tra ra** mã cơ sở.
`resolve_facility()` khi thấy khóa sẽ lấy cơ sở của khóa làm danh tính; `facility_code` trong
thân yêu cầu, nếu có, phải trùng — khác là **403**, kể cả khi cờ nhiều cơ sở đang bật.

```powershell
# Cấp khóa cho một cơ sở (bản rõ chỉ hiện MỘT lần, kho chỉ giữ băm SHA-256)
.venv\Scripts\python -m backend.auth issue --facility 01001 --name "Bệnh viện Đa khoa A" --label "HIS Python"
.venv\Scripts\python -m backend.auth list
.venv\Scripts\python -m backend.auth revoke smig_3f9a1c2e      # có hiệu lực ngay, không cần khởi động lại
```

| Áp khóa                              | Không áp khóa                                         |
| ------------------------------------ | ----------------------------------------------------- |
| `POST/GET/DELETE /api/fhir/*`        | `GET /health`                                         |
|                                      | `POST /api/standardize`, `GET /api/icd10/{code}` — khối NLP, tra cứu văn bản → mã, không mang danh tính |

Ba quy tắc:

* **Khóa sai hoặc đã thu hồi → 401 ở mọi chế độ.** Không bao giờ hạ xuống thành bên gọi nặc danh.
* **Thiếu khóa → 401** khi Gateway đang bắt buộc khóa. Mặc định (`SMIG_REQUIRE_API_KEY=auto`)
  là bắt buộc **ngay khi kho có ít nhất một khóa**: cấp khóa là hành động quyết định "từ nay
  phải xác thực". Máy chưa cấp khóa nào vẫn chạy như trước — đó là môi trường trình diễn.
* **Mã cơ sở của khóa đi qua đúng cửa kiểm dạng CSKCB** của T1.1(b) lúc cấp (`--allow-demo`
  cho mã tạm kiểu `BV-A-001`). Lỗ hở "mã tự khai không bị kiểm dạng" khép ở đây.

**Tích hợp HIS: chỉ thêm một header.** HIS không phải viết lại gì — mọi payload giữ nguyên,
chỉ gắn `X-SMIG-Api-Key` vào các lời gọi `/api/fhir/*`:

```bash
curl -X POST http://127.0.0.1:8000/api/fhir/sync      -H "Content-Type: application/json"      -H "X-SMIG-Api-Key: smig_3f9a1c2e_..."      -d @condition.json
```

Khóa đi trong **header, không đi trong query string**: URL nằm trong access log, Referer và
log của proxy. Khóa là bí mật của **máy chủ** HIS, không đưa xuống trình duyệt — HIS Python vì
vậy có `GET /api/emr/condition/{id}` gọi hộ giao diện thay vì để trang web cầm khóa.

> **Điều cần ghi trong báo cáo.** Khóa API không phải chuẩn xác thực cho trục dữ liệu y tế
> thật — chuẩn là **OAuth2 client credentials** (SMART on FHIR / IHE IUA) hoặc mTLS. Toàn bộ
> cơ chế nằm trong `backend/auth.py` và cắm vào ứng dụng bằng **một dependency ở mức app**;
> thay bằng OAuth2 là thay cách lấy `Caller`, không đụng tầng FHIR và không đụng endpoint nào.
> Chốt bằng `tests/test_api_key.py` (22 ca).

## B.7 API của hai bản HIS

### HIS mô phỏng — Python (`hospital_his/server.py`, cổng 8085)

Lưu bệnh án cục bộ bằng SQLite (hai bảng: `patients`, `patient_conditions`), gọi Gateway để
chuẩn hóa và liên thông.

| Endpoint                                             | Chức năng                                                                                                |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `GET /api/config`                                  | Mã cơ sở, địa chỉ Gateway mà bản HIS này đang trỏ tới                                          |
| `GET /api/patients`                                | Danh sách bệnh án —**kèm luôn chẩn đoán của tuyến khác** trên trục, ghi rõ nơi khám |
| `GET /api/patients?search_id=`                     | Tra cứu bằng**mã bệnh án, CCCD hoặc thẻ BHYT**; không có cục bộ thì kéo từ EMR Cloud   |
| `GET /api/patients/{id}/history`                   | Bệnh sử**toàn tuyến** của một bệnh nhân đã tiếp nhận tại đây                          |
| `POST /api/patients`                               | Tiếp nhận bệnh nhân mới                                                                               |
| `POST /api/patients/{id}/diagnosis`                | Chẩn đoán thêm bệnh mới,**giữ nguyên** chẩn đoán cũ                                      |
| `PUT /api/patients/{id}/conditions/{code}`         | **Sửa** một chẩn đoán đã ghi nhận                                                            |
| `DELETE /api/patients/{id}/conditions/{code}`      | Gỡ một chẩn đoán khỏi bệnh án**và khỏi trục**                                             |
| `POST /api/sync/{id}`                              | Liên thông lại toàn bộ hồ sơ                                                                        |
| `DELETE /api/patients/{id}` · `POST /api/reset` | Dọn dữ liệu demo                                                                                        |

Hai điểm nghiệp vụ đáng nêu trong báo cáo:

* **Một dòng bệnh án sinh nhiều Condition.** Câu *"sỏi bàng quang, suy thận cấp"* tách thành
  hai chẩn đoán độc lập, mỗi chẩn đoán một tài nguyên FHIR riêng. Chốt chặn an toàn lâm sàng
  xét **cho từng mã**: mã dưới ngưỡng dừng chờ bác sĩ duyệt nhưng **không chặn** những mã đã
  đủ tin cậy trong cùng câu.
* **Sửa chẩn đoán.** Mã ICD-10 nằm trong khóa nghiệp vụ, nên đổi mã **không** cập nhật được
  bản ghi cũ mà sinh tài nguyên mới. Thứ tự bắt buộc: **đẩy bản đúng lên trục trước, rồi mới
  gỡ bản mang mã sai** — ngược lại thì có lúc hồ sơ trên trục không còn chẩn đoán nào. Bác sĩ
  tự chọn mã thì độ tin cậy ghi nhận là 100% và `verificationStatus` là `confirmed`, phân
  biệt rõ với điểm số của mô hình.

### VNPT HIS — Java + React (cổng 8089 / 3000)

Mô phỏng phần mềm bệnh viện thương mại, dùng để chứng minh **hai hệ thống khác công nghệ vẫn
liên thông được qua cùng một Gateway**. `HisController.java` lộ 20 endpoint dưới `/api`:

| Nhóm           | Endpoint                                                                                                                                                                                     |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tiếp đón     | `GET /patients`, `GET /patients/lookup`, `POST /patients`, `DELETE /patients/{id}`                                                                                                   |
| Hàng chờ      | `GET /queue`, `POST /queue/register`, `POST /queue/update-status`                                                                                                                      |
| Phòng khám    | `POST /exam/standardize` → gọi `POST /api/standardize` của Gateway; `POST /exam/save` → **đẩy chẩn đoán lên trục**; `GET /exam/patient/{id}`, `GET /exam/records` |
| Cận lâm sàng | `GET /cls/orders`, `POST /cls/result`                                                                                                                                                    |
| Kho dược      | `GET /drugs`, `POST /drugs`, `GET /pharmacy/prescriptions`, `POST /pharmacy/dispense/{id}`                                                                                           |
| Viện phí BHYT | `GET /billing/bills`, `POST /billing/pay/{id}`                                                                                                                                           |
| Quản trị      | `GET /admin/dashboard`, `POST /admin/reset`                                                                                                                                              |

`model/Patient.java` có `citizenId` và `insuranceCard` — điều kiện để trục nhận ra cùng một
người khám ở hai bệnh viện (mục B.5).

## B.8 Kiểm thử khối liên thông

```powershell
# KHÔNG cần mô hình NLP, KHÔNG cần Docker, chạy ~20 giây
.venv\Scripts\python -m pytest tests/ -v
```

Bộ này dựng sẵn một **EMR Cloud giả lập trong bộ nhớ**, bắt chước đúng cơ chế conditional
update mà việc đồng nhất bệnh nhân dựa vào.

| File                                     | Kiểm điều gì                                                                                                    |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `tests/conftest.py`                    | EMR Cloud giả lập, mô phỏng đúng conditional update (0/1/nhiều bản khớp)                                   |
| `tests/test_sua_chan_doan.py`          | Bác sĩ sửa/gỡ chẩn đoán đã ghi nhận, tính lại chẩn đoán chính, chặn trạng thái không hợp lệ   |
| `tests/test_nhieu_co_so.py`            | Một Gateway phục vụ nhiều bệnh viện, chốt chặn mã cơ sở (B.6)                                            |
| `tests/test_chuyen_tuyen.py`           | BV A chẩn đoán → chuyển tuyến → BV B thêm bệnh, tra bệnh sử bằng CCCD                                   |
| `tests/test_benh_su_toan_truc.py`      | Bệnh sử đầy đủ: nội viện**cộng** mọi tuyến khác; không đếm đôi, nói đúng lý do khi rỗng |
| `tests/test_danh_sach_lien_thong.py`   | Màn hình chính phải hiện luôn chẩn đoán tuyến khác kèm nơi khám                                       |
| `tests/test_kiem_dinh_danh.py`         | Định danh toàn quốc sai định dạng phải bị từ chối, không âm thầm tách hồ sơ                        |
| `tests/test_dinh_danh_bo_sung_sau.py`  | Bổ sung CCCD ở lần khám sau**không** được đổi khóa đồng nhất                                    |
| `tests/test_xoa_theo_co_so.py`         | Xóa hàng loạt phải dừng trong phạm vi cơ sở của mình                                                      |
| `tests/test_go_mot_chan_doan.py`       | Gỡ**một** bản ghi cũng phải kiểm cơ sở — chặn xóa chéo                                            |
| `tests/test_ten_benh_theo_danh_muc.py` | Chẩn đoán lên trục phải mang**tên bệnh**, không phải nhãn chỗ điền tạm                         |
| `tests/test_luong_nlp_khong_doi.py`    | **Chốt ranh giới hai khối**: chức năng liên thông thêm vào không được đụng kết quả NLP       |
| `tests/test_api_key.py`               | Khóa API gắn với cơ sở: 401 khi thiếu/sai khóa, khóa viện A không ghi/gỡ được hồ sơ viện B, NLP không bị hỏi khóa |

> `tests/test_ten_benh_theo_danh_muc.py` sinh ra từ một lỗi đo được: VNPT HIS đẩy chẩn đoán
> kèm theo lên trục với `icd10_display` là đúng chuỗi *"Chẩn đoán kèm theo"*. Kết quả: I21.9
> nằm trên trục dưới cái tên đó, trong khi mã ấy là **Nhồi máu cơ tim cấp**. Bác sĩ tuyến sau
> đọc bệnh án về không biết bệnh nhân từng nhồi máu cơ tim. Chữa ở **Gateway** chứ không ở
> HIS: Gateway là nơi duy nhất chắc chắn có đủ 12 137 mã.

---

## C. Chính sách an toàn lâm sàng — nơi hai khối gặp nhau

Kết quả của mô hình **không** được tự động ghi nhận là chẩn đoán đã xác nhận:

| Độ tin cậy | `Condition.verificationStatus`        | Hành vi hệ thống                                             |
| ------------- | --------------------------------------- | --------------------------------------------------------------- |
| ≥ 85%        | `provisional` (dải `auto_confirm`) | Liên thông tự động                                         |
| 60 – 85%     | `provisional`                         | Dừng, chờ bác sĩ duyệt                                     |
| 40 – 60%     | `unconfirmed`                         | Dừng, chờ bác sĩ duyệt                                     |
| < 40%         | —                                      | Từ chối sinh tài nguyên FHIR (HTTP 422)                     |
| —            | `confirmed`                           | **Chỉ** khi có thao tác duyệt/chọn mã của bác sĩ |

**Vì sao máy không bao giờ tự gán `confirmed`** (`verification_status_for()` trong
`nlp/clinical_rules.py`). Trong đặc tả HL7 FHIR, `confirmed` nghĩa là chẩn đoán **đã được xác
nhận** — hàm ý có người đủ thẩm quyền đứng sau, chứ không phải thuật toán tự chấm mình 85
điểm. Đo trên tập kiểm tra độc lập cho thấy trong nhóm ≥85% vẫn còn ca sai (cao nhất: *"viêm
kết mạc dị ứng"* ra H10.9 "không đặc hiệu" với 96,78%). Những bản ghi ấy lên trục mang nhãn
"đã xác nhận" thì bệnh viện khác đọc về **không có cách nào biết là chưa ai duyệt** — đó là
sai lệch thông tin y tế **do khâu gắn nhãn tạo ra**, không phải do mô hình đoán sai.

Ba mức máy được phép dùng: `provisional`, `differential`, `unconfirmed`.

### Luồng dữ liệu đầy đủ của một ca khám

```
[1] Bác sĩ gõ: "bn bị đtđ tuýp 2 kèm cao huyết áp"
        │                                     hospital_his/frontend/app.js
        ▼
[2] HIS gửi POST /api/standardize             hospital_his/server.py
        │
        ▼
[3] NLPEngine.query_composite()               nlp/nlp_engine.py       ─┐
    ├─ Tầng 1: chuẩn hóa + giải viết tắt      nlp/clinical_rules.py    │
    ├─ Tầng 2: cosine trên 12 137 mã          nlp/data/embeddings_*.npy│ PHẦN A
    ├─ Tầng 3: tái xếp hạng theo luật lâm sàng                         │
    └─ Tầng 4: hiệu chuẩn độ tin cậy                                  ─┘
        │
        ▼  → 2 chẩn đoán: E11.9 (93,4%) và I10 (71,5%)
        │
════════╪═══════════ RANH GIỚI HAI KHỐI ═══════════════════════════════
        │
[4] Xét ngưỡng cho TỪNG mã                    hospital_his/server.py  ─┐
    ├─ E11.9 ≥ ngưỡng → liên thông tiếp                                │
    └─ I10  < ngưỡng → DỪNG, chờ bác sĩ duyệt                          │
        │                                                              │
        ▼                                                              │
[5] POST /api/fhir/condition                  backend/main.py          │
    └─ build_fhir_condition_resource()        backend/fhir_helper.py   │ PHẦN B
       + extension độ tin cậy, phiên bản mô hình, cơ sở ghi nhận       │
       + identifier = khóa nghiệp vụ ổn định                           │
        │                                                              │
        ▼                                                              │
[6] POST /api/fhir/sync                       backend/main.py          │
    └─ conditional update: Organization → Patient → Condition          │
        │                                                              │
        ▼                                                              │
[7] EMR Cloud (HAPI FHIR R4)                  docker-compose.yml      ─┘
        │
        ▼
[8] Bệnh viện khác tra bằng CCCD → thấy chẩn đoán này, gắn nhãn cơ sở
    đã ghi nhận, và KHÔNG sửa được từ bên ngoài
```

---

## D. Cấu trúc thư mục toàn dự án

```
NCKH/
│
├── nlp/                    ◄── PHẦN A — KHỐI NLP (chi tiết: mục A.1)
│   ├── nlp_engine.py           bộ máy truy hồi lai 4 tầng
│   ├── clinical_rules.py       tri thức lâm sàng
│   ├── evaluate.py             đánh giá định lượng
│   ├── test_nlp.py             kiểm thử chất lượng nhận diện
│   ├── test_rang_buoc.py       kiểm thử ràng buộc A2/A3/A4
│   ├── my_medical_nlp_model/   SBERT fine-tune (~540 MB, ngoài git)
│   └── data/                   danh mục 12 137 mã, 2 tập đánh giá, cache embedding
│
├── backend/                ◄── PHẦN B — GATEWAY (chi tiết: mục B.1)
│   ├── main.py                 9 endpoint, điều phối NLP ↔ FHIR
│   └── fhir_helper.py          hệ định danh + dựng tài nguyên FHIR R4
│
├── hospital_his/           ◄── PHẦN B — HIS mô phỏng #1 (Python, :8085)
│   ├── server.py               bệnh án SQLite, 10 endpoint
│   ├── his_db.sqlite
│   ├── run_his.ps1 / .bat
│   └── frontend/               giao diện HIS
│
├── VNPT_HIS/               ◄── PHẦN B — HIS mô phỏng #2 (Java + React, :8089/:3000)
│   ├── backend/                Spring Boot + PostgreSQL
│   ├── frontend/               React + Vite
│   └── run_vnpt_his.ps1
│
├── frontend/               ◄── PHẦN B — giao diện demo của Gateway (:8000)
│
├── tests/                  ◄── Kiểm thử khối liên thông — 12 file (mục B.8)
│
├── docs/                   ◄── Tài liệu chi tiết (mục K)
│   ├── cac-file-nlp-va-lien-thong.md   bản đồ file theo hai khối
│   ├── luong-xu-ly-chan-doan.md        truy vết một câu chẩn đoán qua từng hàm
│   ├── chi-tiet-ky-thuat-7-file-loi.md chi tiết kỹ thuật 7 file lõi
│   ├── thaydoi.md                      nhật ký thay đổi, kèm số đo trước/sau
│   ├── T0.md                           củng cố an ninh trục EMR (đã làm)
│   └── T1.md                           kế hoạch liên thông theo khung pháp lý VN
│
├── ChangeJson.py           ◄── PHẦN A — dựng danh mục ICD-10 từ Excel
├── dataICD10.xlsx          Nguồn danh mục Bộ Y tế (14 sheet)
├── ICD10_100_chan_doan_CDLS_bo_sung.xlsx
├── Cong_thuc_toan_hoc_NLP_ICD10.docx
├── docker-compose.yml      ◄── PHẦN B — EMR Cloud (HAPI FHIR + PostgreSQL, :8090)
├── requirements.txt
├── run.ps1 / run.bat       Khởi chạy Gateway
└── README.md
```

---

## E. Cài đặt và khởi chạy

### Yêu cầu hệ thống

* **Hệ điều hành:** Windows (PowerShell hoặc Command Prompt)
* **Python:** 3.10 hoặc 3.11 (khuyến nghị)
* **Docker Desktop:** bắt buộc nếu muốn dùng chức năng đồng bộ EMR Cloud
* **Java 17 + Node.js:** chỉ khi chạy VNPT HIS
* **Dung lượng:** ~1 GB (mô hình 540 MB + cache embedding ~150 MB)

### Bước 1 — EMR Cloud (HAPI FHIR)

```powershell
docker compose up -d
```

Chờ ~60 giây rồi kiểm tra [http://127.0.0.1:8090/fhir/metadata](http://127.0.0.1:8090/fhir/metadata). Bỏ qua bước này thì mọi thao
tác đồng bộ báo lỗi **503**. Lần khởi động đầu chậm hơn (~90 giây) vì HAPI phải tạo schema.

### Bước 2 — SMIG Gateway

```powershell
.\run.ps1
```

hoặc bấm đúp **`run.bat`**. Lần đầu chạy sẽ tự tạo `.venv` và cài thư viện.
Giao diện: [http://127.0.0.1:8000](http://127.0.0.1:8000)

### Bước 3 — HIS mô phỏng (tùy chọn)

```powershell
.\hospital_his\run_his.ps1        # Giao diện: http://127.0.0.1:8085
.\VNPT_HIS\run_vnpt_his.ps1       # Giao diện: http://127.0.0.1:3000
```

### Bước 4 — Demo liên thông hai bệnh viện

Mỗi bệnh viện là **một bản Gateway riêng** với mã cơ sở riêng:

```powershell
.\run.ps1 -Port 8000 -FacilityCode "BV-A-001" -FacilityName "Bệnh viện Đa khoa A"
.\run.ps1 -Port 8001 -FacilityCode "BV-B-002" -FacilityName "Bệnh viện Đa khoa B"
.\hospital_his\run_his.ps1 -Port 8086 -GatewayUrl "http://127.0.0.1:8001" -FacilityCode "BV-B-002"
.\VNPT_HIS\run_vnpt_his.ps1
```

`-FacilityCode` của HIS **phải trùng** với `-FacilityCode` của Gateway mà nó gọi. Lệch nhau
thì Gateway trả 403 và HIS tra cứu sai namespace mã bệnh án.

> `BV-A-001` và `BV-B-002` là **mã đặt tạm cho kịch bản trình diễn**, không phải mã CSKCB.
> `run.ps1` nhận ra chúng qua tiền tố `BV-` và tự bật `SMIG_ALLOW_DEMO_FACILITY=1` — xem
> [Mã cơ sở khám chữa bệnh lấy ở đâu](#mã-cơ-sở-khám-chữa-bệnh-lấy-ở-đâu). Muốn diễn đúng
> điều kiện triển khai thì thay bằng **hai mã CSKCB thật** của hai cơ sở; khi đó cả hai bản
> Gateway chạy ở chế độ chặt và dữ liệu sinh ra đối chiếu được với cổng giám định BHYT.

#### Cách nhẹ hơn: một Gateway phục vụ cả hai bệnh viện

Mô hình NLP chiếm vài GB RAM nên chạy hai bản Gateway trên một máy là quá nặng. Khi chỉ thử
nghiệm cục bộ:

```powershell
.\run.ps1 -Port 8000 -AllowClientFacility
.\hospital_his\run_his.ps1 -Port 8085 -FacilityCode "BV-A-001" -FacilityName "Bệnh viện Đa khoa A"
.\hospital_his\run_his.ps1 -Port 8086 -FacilityCode "BV-B-002" -FacilityName "Bệnh viện Đa khoa B"
```

Hai bản HIS Python tự động dùng **hai tệp SQLite riêng** (suy ra từ `-FacilityCode`, đổi được
bằng `-DbPath`) — dùng chung một tệp thì hai "bệnh viện" nhìn thấy y nguyên danh sách bệnh
nhân của nhau.

Ở cách này mã cơ sở do HIS **tự khai trong từng yêu cầu**. Nó chỉ còn tác dụng với bên gọi
**không mang khóa**, tức khi Gateway chưa cấp khóa nào (hoặc đặt `SMIG_REQUIRE_API_KEY=0`).

> **Chỉ dùng để thử nghiệm.** Mã cơ sở là *danh tính* của bên ghi hồ sơ. Để bên gọi tự khai
> thì bệnh viện B khai mình là bệnh viện A được ngay. Gateway đang bật chế độ này báo
> `allow_client_facility: true` ở `/health`.

#### Cách đúng hơn: một Gateway, mỗi HIS một khóa API

Cùng một bản Gateway, nhưng danh tính lấy từ khóa chứ không từ lời khai — không cần
`-AllowClientFacility`, và khóa của viện A không ghi được hồ sơ mang mã viện B:

```powershell
.venv\Scripts\python -m backend.auth issue --facility BV-A-001 --name "Bệnh viện Đa khoa A" --allow-demo
.venv\Scripts\python -m backend.auth issue --facility BV-B-002 --name "Bệnh viện Đa khoa B" --allow-demo
.
un.ps1 -Port 8000                     # dòng [AUTH] báo: YÊU CẦU khóa (2 khóa đang hiệu lực)
.\hospital_his
un_his.ps1 -Port 8085 -FacilityCode "BV-A-001" -FacilityName "Bệnh viện Đa khoa A" -GatewayApiKey "<khóa A>"
.\hospital_his
un_his.ps1 -Port 8086 -FacilityCode "BV-B-002" -FacilityName "Bệnh viện Đa khoa B" -GatewayApiKey "<khóa B>"
```

VNPT HIS nhận khóa qua biến môi trường `SMIG_GATEWAY_API_KEY` (đọc vào `smig.gateway.api-key`).
Giao diện Gateway ở cổng 8000 cũng là một bên gọi: khi Gateway đòi khóa, thanh đầu trang hiện
ô nhập khóa. Chi tiết ở [B.6](#b6-mã-cơ-sở-là-danh-tính-không-phải-tham-số).

### Chạy nhanh trên một máy

```powershell
docker compose up -d
.\run.ps1 -Port 8000 -AllowClientFacility
.\VNPT_HIS\run_vnpt_his.ps1
.\hospital_his\run_his.ps1
```

---

## F. Cấu hình

Đọc từ biến môi trường, đều có giá trị mặc định cho môi trường demo:

| Biến                          | Mặc định                    | Ý nghĩa                                                                                             |
| ------------------------------ | ------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `SMIG_FHIR_SERVER_URL`       | `http://127.0.0.1:8090/fhir` | Địa chỉ EMR Cloud                                                                                  |
| `SMIG_ALLOWED_ORIGINS`       | `127.0.0.1:8000,8085`        | Danh sách nguồn CORS                                                                                |
| `SMIG_GATEWAY_URL`           | `http://127.0.0.1:8000`      | Địa chỉ Gateway (dùng bởi HIS)                                                                   |
| `SMIG_FHIR_TIMEOUT`          | `8`                          | Thời gian chờ gọi FHIR (giây)                                                                     |
| `SMIG_HIS_TIMEOUT`           | `60`                         | Thời gian chờ HIS gọi Gateway (giây)                                                              |
| `SMIG_FACILITY_CODE`         | `79001`                      | **Mã cơ sở khám chữa bệnh** của bản Gateway này — kiểm dạng lúc khởi động       |
| `SMIG_FACILITY_NAME`         | `Benh vien mo phong Viettel`      | Tên cơ sở, hiện trên mỗi chẩn đoán                                                           |
| `SMIG_ALLOW_CLIENT_FACILITY` | *tắt*                       | Cho phép HIS tự khai mã cơ sở —**chỉ để thử nghiệm**                                 |
| `SMIG_ALLOW_DEMO_FACILITY`   | *tắt*                       | Cho phép mã cơ sở**tự đặt** thay cho mã CSKCB thật — **chỉ để trình diễn** |
| `SMIG_API_KEY_FILE`          | `backend/data/api_keys.json` | Kho khóa API (băm SHA-256, không có bản rõ); nằm trong `.gitignore`                    |
| `SMIG_REQUIRE_API_KEY`       | `auto`                       | `1` bắt buộc khóa trên `/api/fhir/*`; `0` không; `auto` = bắt buộc khi kho có khóa     |
| `SMIG_GATEWAY_API_KEY`       | *trống*                      | Khóa mà HIS (Python và VNPT) gửi kèm header `X-SMIG-Api-Key`                          |

> **Mỗi bệnh viện triển khai một bản Gateway với `SMIG_FACILITY_CODE` riêng.** Hai bản dùng
> trùng mã sẽ trộn hồ sơ của hai bệnh viện vào nhau (mục B.5). HIS đọc cùng biến này để tra
> cứu đúng namespace mã bệnh án.

#### Mã cơ sở khám chữa bệnh lấy ở đâu

`SMIG_FACILITY_CODE` phải là **mã CSKCB do cơ quan Bảo hiểm xã hội cấp**, không phải mã tự
đặt. Mã gồm **5 chữ số**, hai số đầu là mã tỉnh, ba số sau là số thứ tự trong tỉnh — ví dụ
`01001`. Mã không chứa chữ cái. Đây là mã mà cơ sở đang
dùng hằng ngày ở hai chỗ:

* trường `MA_CSKCB` trong bộ XML gửi cổng tiếp nhận giám định BHYT theo **QĐ 130/QĐ-BYT**;
* hồ sơ ký hợp đồng khám chữa bệnh BHYT với cơ quan BHXH tỉnh.

Phòng Kế hoạch tổng hợp hoặc bộ phận phụ trách giám định BHYT của cơ sở nắm mã này. Danh mục
đầy đủ do BHXH Việt Nam công bố, và phần mềm HIS đang chạy tại cơ sở cũng đã cấu hình sẵn —
lấy đúng mã HIS đang xuất XML là chắc chắn nhất.

Mã này đi vào **ba chỗ định danh** của mọi bản ghi Gateway ghi lên trục: khóa nghiệp vụ của
`Condition`, `meta.tag`, và namespace của mã bệnh án. Đặt sai thì dữ liệu không đối chiếu
được với bất kỳ hệ thống nhà nước nào, và sửa về sau nghĩa là phải migrate toàn bộ. Vì vậy
Gateway **kiểm dạng ngay lúc khởi động và dừng hẳn nếu sai**, thay vì chạy tiếp:

```
Cấu hình SMIG_FACILITY_CODE không dùng được. Mã cơ sở khám chữa bệnh 'BV-DEMO-01'
không đúng dạng: mã CSKCB do cơ quan BHXH cấp gồm 5 chữ số, hai số đầu là mã
tỉnh, ví dụ 01001. ...
```

> **`79001` trong kho mã này là mã ví dụ đúng dạng, không trỏ tới cơ sở nào.** Tên đi kèm
> (`Benh vien mo phong Viettel`) cũng vậy. Triển khai thật phải thay **cả hai**: mã lấy theo
> hướng dẫn ngay trên, tên lấy đúng tên cơ sở đã đăng ký với cơ quan BHXH — tên này đi vào
> `Organization.name` và hiện trên mọi chẩn đoán mà cơ sở ghi lên trục.

Môi trường trình diễn chưa có mã thật thì đặt `SMIG_ALLOW_DEMO_FACILITY=1` để dùng mã tự đặt.
`run.ps1` tự bật cờ này khi `-FacilityCode` bắt đầu bằng `BV-`, và **giữ chế độ chặt với mọi
mã khác** — gõ nhầm một ký tự của mã thật thì Gateway dừng, chứ không âm thầm chạy tiếp. Trạng
thái cờ phơi ra ở `GET /health` (`allow_demo_facility`), nhìn là biết bản đang chạy ở chế độ nào.

EMR Cloud lưu dữ liệu trong PostgreSQL gắn named volume `hapi-pgdata`, nên
`docker compose restart` hay `down` rồi `up -d` đều **không** mất dữ liệu. Chỉ
`docker compose down -v` mới xóa sạch.

---

## G. Kiểm thử toàn hệ thống

Ba bộ tách bạch theo thứ chúng kiểm:

```powershell
# 1. Chất lượng nhận diện mã ICD-10 — CẦN mô hình NLP đã tải về
.venv\Scripts\python -m pytest nlp/test_nlp.py -v

# 2. Ràng buộc lâm sàng A2/A3/A4 — KHÔNG cần mô hình, chạy <1 giây
.venv\Scripts\python -m pytest nlp/test_rang_buoc.py -v

# 3. Đường liên thông — KHÔNG cần mô hình, KHÔNG cần Docker, chạy ~20 giây
.venv\Scripts\python -m pytest tests/ -v

# 4. Đánh giá định lượng
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
```

| Bộ                       | Phạm vi                        | Cần mô hình? | Cần Docker? |
| ------------------------- | ------------------------------- | --------------- | ------------ |
| `nlp/test_nlp.py`       | Chất lượng NLP (Phần A)     | Có             | Không       |
| `nlp/test_rang_buoc.py` | Luật trên danh mục (Phần A) | Không          | Không       |
| `tests/`                | Đường liên thông (Phần B) | Không          | Không       |

---

## H. Kịch bản trình diễn

### H.1 Trên Gateway ([http://127.0.0.1:8000](http://127.0.0.1:8000)) — làm nổi bật **khối NLP**

1. Bấm nút mẫu **`ĐTĐ tuýp 2 & THA`** (hoặc gõ tay).
2. **Bước 1 — Chuẩn hóa:** `ĐTĐ` → `đái tháo đường`, hệ thống nối thêm `không phụ thuộc insuline`; các từ được thêm/đổi được bôi màu.
3. **Bước 2 — NER:** thực thể y khoa bôi màu ngay trong nguyên văn của bác sĩ.
4. **Bước 3 — Ánh xạ ICD-10:** hiển thị `E11.9` kèm độ tin cậy **và lý do** ("khớp alias lâm
   sàng đã kiểm chứng", "xung đột diabetes_type…").
5. Chọn mã → cột phải sinh **HL7 FHIR Condition Resource**.
6. Bấm **Đồng bộ lên EMR Cloud** → bản ghi xuất hiện ở trục liên thông phía dưới.

> **Điểm nên nhấn:** bấm nút **`Gõ không dấu`** để cho thấy hệ thống xử lý được
> `hen phe quan cap tinh khong di ung` — tình huống rất thật trong bệnh án Việt Nam.

### H.2 Trên HIS ([http://127.0.0.1:8085](http://127.0.0.1:8085)) — làm nổi bật **khối liên thông**

1. Bấm **Đồng bộ** ở một dòng bệnh nhân.
2. Tab **Log chi tiết** hiện đủ 3 bước: chuẩn hóa NLP → sinh FHIR → truyền EMR.
3. Tab **HL7 FHIR JSON** hiện tài nguyên **đọc ngược lại từ EMR Cloud** — chứng minh dữ liệu
   đã thực sự nằm trên trục chứ không chỉ được gửi đi.
4. Với ca độ tin cậy thấp, hệ thống dừng ở **"Chờ bác sĩ duyệt"** và liệt kê mã ứng viên thay
   vì tự ý liên thông.

### H.3 Hai kịch bản đắt giá nhất

**Đồng nhất bệnh nhân.** Tạo ở **mỗi** bệnh viện một bệnh nhân **trùng mã bệnh án** (cùng
`BN0001`) nhưng khác người, rồi đồng bộ cả hai → trục giữ **hai** hồ sơ riêng, mỗi chẩn đoán
ghi rõ bệnh viện nào lập. Sau đó tạo ở hai nơi hai hồ sơ **cùng số CCCD** → lần này trục gộp
về **một** bệnh nhân với hai chẩn đoán từ hai bệnh viện (mục B.5).

**Chuyển tuyến.** Bệnh viện A chẩn đoán rồi chuyển bệnh nhân lên B, B khám ra thêm bệnh. Ô
tìm kiếm của HIS nhận **mã bệnh án, CCCD hoặc thẻ BHYT** — bác sĩ ở B gõ CCCD là kéo được
bệnh sử từ trục về, kể cả khi chưa từng có hồ sơ nội viện. Chẩn đoán do nơi khác lập hiện huy
hiệu tên bệnh viện đó và **không sửa được** từ đây.

---

## I. Giới hạn đã biết

### Khối NLP

* **Tập đánh giá 161 ca do nhóm tự soạn**, chưa có thẩm định của bác sĩ chuyên khoa và chưa
  lấy từ bệnh án thật.
* **Tập kiểm tra độc lập không còn hoàn toàn "sạch"** — đã dùng lỗi của nó để sửa bốn khiếm
  khuyết hệ thống (mục A.4). Lần báo cáo tiếp theo cần soạn tập độc lập mới.
* **Cache embedding phụ thuộc mô hình và danh mục.** Đổi một trong hai phải mã hóa lại toàn
  bộ danh mục (15–40 phút trên CPU); hệ thống có in cảnh báo trước khi chạy.
* **Luật hậu xử lý, không phải mô hình học được ranh giới.** Trục đối lập và cổng chương là
  tri thức cài tay; hướng đi đúng hơn là fine-tune lại SBERT với negative-pair mining.

### Khối liên thông

* **Một chẩn đoán → một Condition trên đường Gateway.** `query_composite()` đã tách được
  nhiều bệnh và HIS Python đã sinh nhiều Condition, nhưng luồng demo trên giao diện Gateway
  vẫn dừng ở một Condition mỗi lần.
* **Xác thực mới ở mức khóa API tại biên Gateway** (T1.4a), và chỉ trên đường liên thông
  `/api/fhir/*`. Trục HAPI FHIR ở cổng 8090 vẫn **không** có xác thực; chưa có `AuditEvent`.
  Cả ba dịch vụ chỉ nghe trên `127.0.0.1` nên phạm vi rủi ro giới hạn ở máy chạy demo, nhưng
  triển khai thật với dữ liệu y tế **bắt buộc** phải có OAuth2 / SMART on FHIR và mTLS tới
  trục. **Không** đưa cụm này ra Internet (kể cả qua ngrok hay Cloudflare Tunnel).
* **Đồng bộ cập nhật theo khóa nghiệp vụ.** Đồng bộ lại cùng một chẩn đoán **trong cùng ngày**
  sẽ cập nhật bản ghi cũ thay vì tạo bản mới — đánh đổi để bảo đảm idempotent (mục B.4).
* **Đồng nhất bệnh nhân dựa hoàn toàn vào định danh khai báo.** Không có CCCD/BHYT thì hệ
  thống chỉ quy được hồ sơ trong phạm vi một bệnh viện. Hệ thống **không** đối sánh xác suất
  theo họ tên/ngày sinh — đó là bài toán riêng, cần dữ liệu thật và thẩm định lâm sàng.
* **Chưa có `Encounter`, `Practitioner`, `AuditEvent`** và chưa phân biệt chẩn đoán chính /
  kèm theo ở tầng FHIR — xem kế hoạch `docs/T1.md`. Khóa API gắn ở mức ứng dụng nên đường
  liên thông mới thêm sau này (T1.3, T1.5) tự động được bảo vệ, không phải sửa tầng xác thực.
* **Mật khẩu PostgreSQL `hapi/hapi`** trong `docker-compose.yml` chỉ dành cho demo cục bộ.

---

## J. Hướng phát triển

**Khối NLP**

1. Fine-tune lại SBERT với **negative-pair mining** trên chính các cặp đối lập, để mô hình tự
   học ranh giới thay vì dựa vào luật hậu xử lý.
2. Mở rộng tập đánh giá lên **500+ ca** lấy từ bệnh án thật, có bác sĩ gán nhãn độc lập.
3. Đưa bối cảnh bệnh nhân (tuổi, giới, khoa phòng) vào tầng truy hồi chứ không chỉ tầng phạt.

**Khối liên thông**

4. Sinh **nhiều FHIR Condition** cho câu chẩn đoán chứa nhiều bệnh trên toàn bộ các đường vào.
5. Bổ sung `Encounter`, `Practitioner`, phân biệt chẩn đoán chính/kèm theo (`docs/T1.md` T1.3).
6. **`AuditEvent`** cho mỗi lần đọc/ghi bệnh sử (T1.4b); khóa API theo cơ sở đã xong (T1.4a),
   triển khai thật thay bằng OAuth2 client credentials — chỉ đụng `backend/auth.py`.
7. Ánh xạ hệ định danh theo **VN Core IG** và đường vào theo **XML QĐ 130/QĐ-BYT** (T1.2, T1.5).

---

## K. Tài liệu chi tiết

| Tài liệu                               | Nội dung                                                                                                                                  |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `docs/cac-file-nlp-va-lien-thong.md`   | Bản đồ**từng file mã nguồn** chia theo hai khối — dành cho người viết báo cáo                                          |
| `docs/luong-xu-ly-chan-doan.md`        | Truy vết đường đi của**một câu chẩn đoán** qua từng hàm                                                                 |
| `docs/chi-tiet-ky-thuat-7-file-loi.md` | Chi tiết kỹ thuật 7 file lõi                                                                                                           |
| `docs/thaydoi.md`                      | Nhật ký thay đổi, kèm số đo trước/sau mỗi lần sửa                                                                              |
| `docs/T0.md`                           | Củng cố an ninh trục EMR —**đã thực hiện**                                                                                   |
| `docs/T1.md`                           | **Kế hoạch** đưa khối liên thông khớp khung pháp lý VN: TT 13/2025/TT-BYT, QĐ 130/QĐ-BYT, VN Core IG, Luật 91/2025/QH15 |
| `Cong_thuc_toan_hoc_NLP_ICD10.docx`    | Công thức toán học của tầng tái xếp hạng và hiệu chuẩn                                                                         |
