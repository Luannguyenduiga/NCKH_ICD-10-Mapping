# Luồng xử lý một câu chẩn đoán trong SMIG

Tài liệu này truy vết **toàn bộ đường đi của một câu chẩn đoán**, từ lúc bác sĩ gõ
vào ô nhập cho tới lúc hệ thống trả về danh sách mã ICD-10 kèm độ tin cậy — nêu rõ
từng file, từng hàm, từng dòng.

Đối tượng đọc: người cần hiểu bên trong hệ thống để viết báo cáo khoa học, bảo vệ
đề tài, hoặc sửa/mở rộng mã nguồn.

> Ký hiệu `file.py:123` = file, dòng số 123.

---

## 0. Bản đồ tổng quát

```
        ┌──────────────────────────────────────────────────┐
        │  CLIENT — cùng gọi một endpoint duy nhất      │
        │   • frontend/app.js:119        (web demo)        │
        │   • hospital_his/server.py:312 (HIS Python)      │
        │   • GatewayService.java:42     (HIS VNPT, Java)  │
        └──────────────────────┬───────────────────────────┘
                               │  POST /api/standardize
                               │  { "query": "...", "top_k": 4 }
                               ▼
        ┌──────────────────────────────────────────────────┐
        │  backend/main.py:216  standardize_diagnosis()    │
        │  Chỉ điều phối, gọi đúng 3 hàm của engine        │
        └──────────────────────┬───────────────────────────┘
                               │
     ┌─────────────────────────┼──────────────────────────┐
     ▼                         ▼                          ▼
 expand_query()      extract_entities_regex()      query_composite()
 (câu chuẩn hóa)      (bôi màu thuật ngữ)        (KẾT QUẢ MÃ ICD-10)
 nlp_engine.py:571     nlp_engine.py:776           nlp_engine.py:1083
                                                          │
                    ┌─────────────────────────────────────┤
                    ▼                                     ▼
              query()  ── tầng 2+3+4              _candidate_fragments()
           nlp_engine.py:842                      nlp_engine.py:1019
        SBERT → rerank → hiệu chuẩn              (cắt câu thành các vế)
                                                          │
                                       ┌──────────────────┴─────────────┐
                                       ▼                                ▼
                              luật dao găm/sao †/*          _separate_diagnoses()
                              _is_valid_pair:1031             nlp_engine.py:1214
                                → combination                  → diagnoses[]
                               │
                               ▼
        ┌──────────────────────────────────────────────────┐
        │  backend/main.py:234  gắn verification_status    │
        │  theo CONFIDENCE_POLICY → trả JSON               │
        └──────────────────────────────────────────────────┘
```

Bốn tầng kiến trúc (như mô tả ở docstring [`nlp/nlp_engine.py:1-15`](../nlp/nlp_engine.py#L1-L15)):

| Tầng | Tên            | Hàm chính                              |
| ----- | --------------- | ---------------------------------------- |
| 1     | Chuẩn hóa     | `expand_query`                         |
| 2     | Truy hồi       | `query` (phần cosine similarity)      |
| 3     | Tái xếp hạng | `query` (vòng lặp cộng/trừ điểm) |
| 4     | Hiệu chuẩn    | `_calibrate`                           |

Tầng bọc ngoài (`query_composite`) xử lý **chẩn đoán kép** và **nhiều bệnh trong một câu**.

---

## 1. Điểm vào — ba client, một endpoint

| Client                          | File                                                                                                             | Dòng |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ----- |
| Giao diện web của Gateway     | [`frontend/app.js`](../frontend/app.js#L119)                                                                    | 119   |
| HIS mô phỏng (Python/FastAPI) | [`hospital_his/server.py`](../hospital_his/server.py#L312)                                                      | 312   |
| HIS VNPT (Java/Spring)          | [`GatewayService.java`](../VNPT_HIS/backend/src/main/java/com/vnpt/his/backend/service/GatewayService.java#L42) | 42    |

Tất cả gửi:

```json
POST /api/standardize
{ "query": "Bệnh nhân bị ĐTĐ typ 2", "top_k": 4 }
```

Ràng buộc đầu vào ở [`backend/main.py:82-84`](../backend/main.py#L82-L84):
`query` dài 1–2000 ký tự, `top_k` trong khoảng 1–10 (mặc định 4).

### Handler

[`backend/main.py:216-247`](../backend/main.py#L216-L247) — `standardize_diagnosis()`.
Handler này **không chứa logic NLP nào**, chỉ gọi ba hàm:

```python
normalized_query = engine.expand_query(request.query)          # tầng 1
entities         = engine.extract_entities_regex(request.query) # NER
composite        = engine.query_composite(request.query, top_k) # tầng 2-4 + luật ghép
```

Trước đó [`main.py:170-176`](../backend/main.py#L170-L176) `_require_engine()` chặn
request nếu mô hình chưa nạp xong (trả HTTP 503).

---

## 2. Tầng 1 — Chuẩn hóa câu (`expand_query`)

[`nlp/nlp_engine.py:571-611`](../nlp/nlp_engine.py#L571-L611)

Chạy tuần tự 5 bước. Toàn bộ từ điển luật nằm ở [`nlp/clinical_rules.py`](../nlp/clinical_rules.py).

### Bước 1.1 — `normalize_text` ([:548](../nlp/nlp_engine.py#L548))

```python
text = unicodedata.normalize("NFC", text)     # dựng lại ký tự tổ hợp
text = text.lower().strip()
text = re.sub(r"[^\w\s\-\/\.]", " ", text)    # bỏ ký tự đặc biệt
text = re.sub(r"\s+", " ", text)
for pattern, replacement in _ABBR_RE:          # ABBREVIATIONS
    text = pattern.sub(replacement, text)
```

**Vì sao phải NFC trước tiên?** Danh mục của Bộ Y tế có 197 mã lưu ở dạng NFD, tức
chữ "không" được lưu thành `k h o + U+0302 + n g` (dấu mũ là một ký tự riêng). Dấu
tổ hợp không thuộc lớp `\w`, nên dòng `re.sub` phía dưới sẽ thay nó bằng dấu cách,
cắt "không" thành `"kho ng"` — mất token, mất điểm khớp trọn cụm, mất luôn điểm
thưởng mã "không đặc hiệu". Bệnh án dán từ macOS hoặc từ một số HIS cũng hay ở
dạng NFD.

> ⚠️ **Cảnh báo bảo trì.** `normalize_text` được dùng cho **cả** văn bản tham chiếu
> khi sinh embedding. Sửa hàm này hoặc sửa `ABBREVIATIONS` sẽ làm khóa cache đổi →
> phải mã hóa lại ~47.000 thuật ngữ (15–40 phút CPU). Viết tắt mới phải thêm vào
> `QUERY_ABBREVIATIONS` (chỉ áp cho câu truy vấn) — xem [`clinical_rules.py:47-49`](../nlp/clinical_rules.py#L47-L49).

### Bước 1.2 — Bóc tiền tố / hậu tố hành văn ([:590-597](../nlp/nlp_engine.py#L590-L597))

`NOISE_PREFIXES` ([`clinical_rules.py:97-109`](../nlp/clinical_rules.py#L97-L109)) —
"chẩn đoán:", "bệnh nhân bị", "theo dõi", "hiện tại"…
Được áp **lặp tối đa 4 vòng** vì các tiền tố hay xếp chồng nhau:

```
"Hiện tại bệnh nhân được chẩn đoán: đái tháo đường tuýp 2"
 └─vòng 1─┘└──────vòng 2──────┘└─vòng 3─┘
```

`NOISE_SUFFIXES` ([`clinical_rules.py:113-121`](../nlp/clinical_rules.py#L113-L121)) —
"đang điều trị…", "hẹn tái khám…", "ra viện…". Đây là thông tin hành chính, không
giúp xác định mã bệnh nhưng lại kéo lệch vector ngữ nghĩa của cả câu.

### Bước 1.3 — `QUERY_ABBREVIATIONS` ([:598-599](../nlp/nlp_engine.py#L598-L599))

Bộ viết tắt mở rộng, ~44 mục ([`clinical_rules.py:49-92`](../nlp/clinical_rules.py#L49-L92)):
`t2dm` → "đái tháo đường tuýp 2", `stc` → "suy thận cấp", `tiểu đường` → "đái tháo đường"…

### Bước 1.4 — `restore_diacritics` ([:613-646](../nlp/nlp_engine.py#L613-L646))

Bác sĩ thường gõ nhanh không dấu: `"nhoi mau co tim cap"`. SBERT coi chuỗi không dấu
là từ hoàn toàn khác nên trả kết quả ngẫu nhiên (chuỗi trên từng cho ra *"Nhịp nhanh
kịch phát"*).

Hàm này quét **n-gram dài nhất trước** (tối đa 12 từ) trên từ điển `_ascii_to_norm`
đã dựng sẵn từ 47k thuật ngữ danh mục, và **chỉ thay những cụm vốn không có dấu** —
nên văn bản gõ đúng chính tả không bị đụng tới:

```python
if phrase != strip_diacritics(phrase):   # cụm đã có dấu → bỏ qua
    continue
```

Từ điển này chỉ nhận cụm **từ 2 từ trở lên** ([:477](../nlp/nlp_engine.py#L477)):
cụm một từ quá dễ nhập nhằng khi bỏ dấu ("than" vừa là "thận" vừa là "than" trong
"bệnh than").

Sau bước này chạy lại `QUERY_ABBREVIATIONS` một lượt nữa ([:602-603](../nlp/nlp_engine.py#L602-L603)),
vì khôi phục dấu có thể làm lộ ra viết tắt mới.

### Bước 1.5 — `BRIDGE_TERMS` — cầu nối từ vựng ([:606-610](../nlp/nlp_engine.py#L606-L610))

Đây là **mấu chốt của cả hệ thống**. Bác sĩ viết "tuýp 2" nhưng danh mục Bộ Y tế ghi
"không phụ thuộc insuline" — hai cụm **không chung một từ nào**. Không bắc cầu thì
embedding kéo về nhóm E10 (tuýp 1).

Cụm chuẩn được **nối thêm**, không thay thế, để giữ ngữ cảnh gốc:

```python
bridges = [std for term, std in BRIDGE_TERMS if term in normalized and std not in normalized]
normalized = normalized + " " + " ".join(bridges)
```

13 cặp cầu nối ([`clinical_rules.py:137-153`](../nlp/clinical_rules.py#L137-L153)).
Cặp quan trọng nhất: `"ung thư" → "u ác"` — toàn bộ 553 mã chương C dùng "u ác",
**không mã nào ghi "ung thư"**.

### Kết quả tầng 1

```
"Bệnh nhân bị ĐTĐ typ 2"
  → "đái tháo đường tuýp 2 không phụ thuộc insuline"
```

Chuỗi này được trả về client ở trường `normalized_query` để hiển thị bước "Chuẩn hóa".

---

## 3. NER — Trích xuất thực thể (`extract_entities_regex`)

[`nlp/nlp_engine.py:776-819`](../nlp/nlp_engine.py#L776-L819)

Nhánh **song song** với nhánh mã hóa, dùng để bôi màu thuật ngữ y khoa trong nguyên
văn của bác sĩ. Gọi `expand_query(text, with_bridges=False)` — cố ý **tắt cầu nối**,
để chỉ bôi những cụm bác sĩ thực sự viết ra chứ không bôi cả cụm do hệ thống thêm vào.

Thuật toán: quét n-gram (dài nhất trước, tối đa 12 từ) trên hai bảng tra đã tiền tính
lúc khởi động ([`_build_lookup_tables:445`](../nlp/nlp_engine.py#L445)):

| Bảng                 | Vai trò                                                                 |
| --------------------- | ------------------------------------------------------------------------ |
| `_term_index`       | cụm chuẩn hóa → entry, ưu tiên thuật ngữ chính thức khi trùng |
| `_term_index_ascii` | bảng không dấu, phương án dự phòng                               |

Độ phức tạp **O(số từ × 12)** thay vì O(47.000) như bản trước — không còn phụ thuộc
kích thước danh mục. Sau đó giữ cụm dài nhất, loại các cụm chồng lấn ([:801-808](../nlp/nlp_engine.py#L801-L808)).

`_surface_form` ([:822](../nlp/nlp_engine.py#L822)) tìm lại đoạn văn bản **gốc** tương
ứng bằng cách so khớp không phân biệt dấu — vì chuỗi đã chuẩn hóa và chuỗi gốc có độ
dài khác nhau, không thể cắt theo chỉ số.

---

## 4. Tầng 2+3+4 — `query()`: trái tim của hệ thống

[`nlp/nlp_engine.py:842-947`](../nlp/nlp_engine.py#L842-L947)

### 4.1 Tầng 2 — Truy hồi ngữ nghĩa ([:848-859](../nlp/nlp_engine.py#L848-L859))

```python
query_embedding = self.model.encode(expanded, convert_to_tensor=True)
cos_scores = util.cos_sim(query_embedding, self.reference_embeddings)[0].cpu().numpy()

pool_size = min(CANDIDATE_POOL, len(cos_scores))          # CANDIDATE_POOL = 400
pool = set(np.argpartition(-cos_scores, pool_size - 1)[:pool_size].tolist())
for code in alias_boosts:                                  # mã trúng alias LUÔN được xét
    pool.update(self._code_to_indices.get(code, []))
```

* `self.reference_embeddings`: ~47.000 vector đã tiền tính, gồm tên tiếng Việt, tên
  tiếng Anh, synonym của 12.219 mã, cộng thêm các alias lâm sàng.
* `np.argpartition` lấy top-400 trong **O(n)** thay vì sắp xếp toàn bộ O(n log n).
* Mã trúng alias được **ép vào pool** kể cả khi cosine xếp ngoài 400 — đây là lưới an
  toàn cho trường hợp mô hình xếp sai hoàn toàn.

#### Cache embedding

Toàn bộ 47k vector được tính **một lần** rồi lưu ra đĩa ([`_prepare_embeddings:340`](../nlp/nlp_engine.py#L340)).
Khóa cache = *(định danh mô hình + vân tay trọng số, hash nội dung danh mục)*
([`_cache_key:259`](../nlp/nlp_engine.py#L259)).

Ba lớp bảo vệ chống nạp nhầm cache:

1. **Vân tay trọng số** ([`_model_fingerprint:187`](../nlp/nlp_engine.py#L187)) — băm
   kích thước + 1 MB đầu + 1 MB cuối của file trọng số. Không băm cả 540 MB để không
   thêm vài giây vào mỗi lần khởi động; huấn luyện lại luôn làm đổi các byte này.
2. **Hash nội dung danh mục** — thay cho số lượng bản ghi, để không nạp nhầm khi danh
   mục đổi nội dung mà giữ nguyên số dòng.
3. **Kiểm chứng bằng mẫu** ([`_cache_matches_model:231`](../nlp/nlp_engine.py#L231)) —
   mã hóa lại 3 mục từ mẫu rồi so cosine với vector trong cache; cùng mô hình thì phải
   ≈ 1.0 (ngưỡng 0.999).

> Lỗi mà lớp 1 khắc phục rất tinh vi: bản trước chỉ lấy **tên thư mục** mô hình làm
> khóa, nên fine-tune lại vào cùng thư mục vẫn nạp vector của checkpoint cũ. Truy vấn
> trùng khít mục từ chỉ đạt cosine 0.77 thay vì 1.0, kéo độ tin cậy xuống ~86%.

### 4.2 Tầng 3 — Tái xếp hạng ([:861-901](../nlp/nlp_engine.py#L861-L901))

Đây là **lý do hệ thống phân biệt được E10/E11 và I10/I15** — những cặp mà cosine
thuần túy xếp gần như ngang nhau (chênh lệch chỉ ~0,02).

Với mỗi ứng viên trong pool:

```python
score = float(cos_scores[idx])                    # điểm nền: cosine similarity
score += W_LEXICAL * self._lexical_f1(...)        # + trùng lặp từ vựng
score += W_EXACT   nếu khớp trọn cụm              # + khớp nguyên văn
score += alias_boosts[code]                       # + alias lâm sàng
score += polarity_delta                           # ± trục đối lập
score += spec_delta                               # ± mức chi tiết
score += chapter_delta                            # − cổng chương
```

Chi tiết sáu thành phần:

| # | Thành phần          | Hàm                                                   | Hệ số                                | Ý nghĩa                                                                                         |
| - | --------------------- | ------------------------------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 1 | Trùng lặp từ vựng | [`_lexical_f1:750`](../nlp/nlp_engine.py#L750)        | `W_LEXICAL = 0.12`                   | F1 trên tập token nội dung (đã bỏ`STOPWORDS`). Bù cho điểm yếu của embedding thuần. |
| 2 | Khớp trọn cụm      | inline[:872](../nlp/nlp_engine.py#L872)                 | `W_EXACT = 0.10`                     | Tên mã (≥5 ký tự) nằm trọn trong câu truy vấn.                                           |
| 3 | Alias lâm sàng      | [`_alias_targets:721`](../nlp/nlp_engine.py#L721)     | 0.15 / 0.10                            | Alias đã kiểm chứng thủ công.                                                               |
| 4 | Trục đối lập      | [`_polarity_delta:668`](../nlp/nlp_engine.py#L668)    | thưởng 0.03–0.05 / phạt 0.10–0.18 | Nhãn truy vấn ≠ nhãn mã → trừ điểm.                                                      |
| 5 | Mức chi tiết        | [`_specificity_delta:685`](../nlp/nlp_engine.py#L685) | ±0.06 / −0.04                        | Chọn giữa mã`.9` (không đặc hiệu) và `.8` (khác).                                    |
| 6 | Cổng chương        | [`_chapter_delta:711`](../nlp/nlp_engine.py#L711)     | −0.12 đến −0.22                    | Chặn chương O, S/T, V–Y, Z khi thiếu ngữ cảnh.                                             |

Các hệ số được đặt **đủ lớn để một vi phạm ngữ nghĩa lật được thứ hạng**, vì chênh
lệch cosine giữa các mã ICD-10 lân cận thường chỉ 0,005–0,03 ([:47-54](../nlp/nlp_engine.py#L47-L54)).

#### 4.2a Chi tiết: alias có tính "độ phủ"

[`_alias_targets:721-747`](../nlp/nlp_engine.py#L721-L747) so khớp hai vòng: có dấu
(hệ số 1.0) rồi không dấu (hệ số 0.85 — vì bỏ dấu tăng nguy cơ khớp nhầm).

Điểm thưởng được nhân với **độ phủ**:

```python
coverage = min(1.0, len(self._content_tokens(phrase)) / query_tokens)
```

Alias chỉ phủ một phần câu là bằng chứng yếu hơn: alias `"hen phế quản"` xuất hiện
trong câu `"hen phế quản không dị ứng"` không được phép lấn át mã **J45.1** vốn mô tả
đúng cả vế "không dị ứng".

#### 4.2b Chi tiết: trục đối lập

5 trục ([`clinical_rules.py:162-217`](../nlp/clinical_rules.py#L162-L217)):

| Trục                  | Phạm vi kích hoạt                | Hai nhãn               | Phạt |
| ---------------------- | ----------------------------------- | ----------------------- | ----- |
| `diabetes_type`      | đái tháo đường, insuline      | type2 / type1           | 0.18  |
| `hypertension_cause` | huyết áp                          | secondary / primary     | 0.16  |
| `complication`       | biến chứng, hôn mê, nhiễm toan | without / with          | 0.14  |
| `acuity`             | cấp, mãn/mạn                     | chronic / acute         | 0.10  |
| `allergy`            | dị ứng                            | non_allergic / allergic | 0.10  |

Hai điểm kỹ thuật:

* **Thứ tự nhãn quan trọng**: biến thể phủ định ("không phụ thuộc") phải đặt **trước**
  biến thể khẳng định ("phụ thuộc"), vì chuỗi khẳng định là chuỗi con của chuỗi phủ
  định. Nhãn khớp đầu tiên thắng ([`_polarity_labels:656`](../nlp/nlp_engine.py#L656)).
* **Phạm vi `complication` phải gồm tên từng biến chứng cụ thể**: danh mục ghi
  "(Có hôn mê)", "(Có nhiễm toan ceton)" chứ không ghi chữ "biến chứng".

Nhãn của từng mã ICD-10 được **tiền tính lúc khởi động** ([:490-493](../nlp/nlp_engine.py#L490-L493))
từ tên chính thức VI + EN, nên lúc chạy chỉ là tra bảng.

#### 4.2c Chi tiết: mức chi tiết của mã

[`_specificity_delta:685-708`](../nlp/nlp_engine.py#L685-L708)

Bài toán: trong mỗi khối ICD-10 luôn có một mã `.9` ("không đặc hiệu") và một mã `.8`
("khác"). Chọn sai thì mã vẫn "gần đúng" nhưng vẫn là sai.

Điều kiện then chốt:

```python
query_is_unqualified = q_tokens <= entry_tokens   # câu KHÔNG mang thông tin nào ngoài tên bệnh
```

| Tình huống                                           | Kết quả                                                                                    |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Câu không nêu thể + mã "không đặc hiệu"       | **+0.06** — "bệnh Crohn" → K50.9 ✔                                                 |
| Câu**có** nêu thể + mã "không đặc hiệu" | **−0.06** — "viêm kết mạc dị ứng" không được ra H10.9 (mất vế "dị ứng") |
| Câu không nêu thể + mã "khác"                    | **−0.04** — "bệnh Crohn" không được ra K50.8                                    |

#### 4.2d Chi tiết: cổng chương

[`_chapter_delta:711-719`](../nlp/nlp_engine.py#L711-L719) —
tránh trường hợp *"đái tháo đường tuýp 2"* bị gán **O24.0** (ĐTĐ ở thai phụ), vốn có
cosine rất cao vì tên mã chứa nguyên cụm "đái tháo đường". Chương O chỉ hợp lệ khi
câu có từ "thai", "sản", "chuyển dạ"… ([`clinical_rules.py:263-289`](../nlp/clinical_rules.py#L263-L289)).

#### 4.2e Gom theo mã

Một mã ICD-10 có nhiều entry (tên VI, tên EN, các synonym, các alias). Vòng lặp giữ
**entry có điểm cao nhất** cho mỗi mã ([:893-901](../nlp/nlp_engine.py#L893-L901)),
đồng thời lưu lại `notes` — chính là trường `explanation` mà giao diện hiển thị dưới
dạng "lý do hệ thống chọn mã này".

### 4.3 Tầng 4 — Hiệu chuẩn độ tin cậy ([:903-947](../nlp/nlp_engine.py#L903-L947))

**Vì sao không lấy thẳng điểm tái xếp hạng làm độ tin cậy?** Vì điểm đó có cộng
thưởng nên dễ vượt trần → mọi kết quả sẽ hiện 100%, làm mất tác dụng của ngưỡng duyệt.

Thay vào đó kết hợp **hai thành phần độc lập**:

```python
relative = softmax(rerank_scores)[i]                       # mức áp đảo so với ứng viên khác
absolute = (raw_cosine - 0.40) / (0.90 - 0.40), kẹp [0,1]  # mức khớp thật với danh mục
confidence = (0.65 * relative + 0.35 * absolute) * 100
```

Một mã thắng áp đảo nhưng ngữ nghĩa xa vẫn bị hạ điểm, và ngược lại.

Tham số ([:63-68](../nlp/nlp_engine.py#L63-L68)):
`W_RELATIVE=0.65`, `W_ABSOLUTE=0.35`, `SOFTMAX_TEMPERATURE=0.06`, `SOFTMAX_SCOPE=20`,
`SIM_FLOOR=0.40`, `SIM_CEIL=0.90`.

#### Gộp khối 3 ký tự ([:913-929](../nlp/nlp_engine.py#L913-L929))

**E11** và **E11.9** là cùng một chẩn đoán ở hai mức chi tiết. Nếu để chúng chia đôi
xác suất softmax thì độ tin cậy bị hạ oan. Giải pháp: cộng dồn xác suất theo khối
3 ký tự, và **mã tốt nhất trong khối nhận toàn bộ khối lượng xác suất của khối**:

```python
if block in claimed_blocks:
    relative = probs[rank]                    # mã thứ hai cùng khối: chỉ phần của nó
else:
    relative = block_mass.get(block, probs[rank])   # mã đầu tiên: nhận cả khối
    claimed_blocks.add(block)
```

### 4.4 Bản ghi kết quả

Mỗi phần tử trả về ([:933-946](../nlp/nlp_engine.py#L933-L946)):

| Trường                       | Ý nghĩa                                                                |
| ------------------------------ | ------------------------------------------------------------------------ |
| `code`                       | mã ICD-10 sạch, đã gỡ †/*/‡ — dùng cho FHIR                     |
| `code_no_dot`                | dạng liền (E11.9 → E119) — dùng cho HIS/báo cáo                   |
| `raw_code`                   | mã gốc trong danh mục, còn giữ dấu † hoặc *                      |
| `confidence`                 | % sau hiệu chuẩn                                                       |
| `confidence_band`            | `high` / `medium` / `low`                                          |
| `similarity_score`           | cosine thô (tầng 2)                                                    |
| `rerank_score`               | điểm sau tái xếp hạng (tầng 3)                                     |
| `matched_by`, `match_type` | entry nào đã khớp, loại gì                                         |
| `explanation`                | danh sách lý do — giá trị lớn nhất cho tính giải thích được |

> **Về `sanitize_icd10_code`** ([:110](../nlp/nlp_engine.py#L110)): danh mục Bộ Y tế
> dùng † để đánh dấu mã bệnh nguyên. Ký tự này **không thuộc mã ICD-10** và sẽ bị máy chủ FHIR từ chối, nên phải gỡ trước khi đưa vào tài nguyên liên thông.

---

## 5. Tầng bọc ngoài — `query_composite()`

[`nlp/nlp_engine.py:1083-1212`](../nlp/nlp_engine.py#L1083-L1212)

Đây là hàm mà backend thực sự gọi. Nó giải quyết hai bài toán mà `query()` một mình
không xử lý được.

### 5.1 Bước A — Truy vấn cả câu

```python
predictions = self.query(user_query, top_k=top_k)
```

### 5.2 Bước B — Cắt câu thành các vế

[`_candidate_fragments:1019-1029`](../nlp/nlp_engine.py#L1019-L1029) chạy hai lớp cắt:

**Lớp 1 — cắt theo dấu câu** ([`split_clinical_fragments:952`](../nlp/nlp_engine.py#L952)):

```python
_FRAGMENT_SPLIT_RE = r"[,;/+]|\bkèm theo\b|\bkèm\b|\bcó biến chứng\b|\bbiến chứng\b|\bgây\b|\bdẫn đến\b"
```

> **KHÔNG tách theo chữ "và"** ([:75-76](../nlp/nlp_engine.py#L75-L76)): "rễ **và** đám
> rối thần kinh" là một cụm giải phẫu, tách ra sẽ vỡ nghĩa.

Vế ngắn hơn 6 ký tự hoặc dưới 2 từ bị loại ("cấp", "(P)"…). Tối đa `MAX_FRAGMENTS = 6` vế.

**Lớp 2 — cắt theo tên bệnh trong danh mục** ([`_term_fragments:987`](../nlp/nlp_engine.py#L987)):

Cần thiết vì bác sĩ hay viết hai bệnh liền nhau không có dấu phân cách:

```
"đái tháo đường tuýp 2 tăng huyết áp"   ← một vế duy nhất theo lớp 1
```

Mã hóa cả cụm thành một vector thì E11 và I10 chia nhau xác suất, không mã nào đạt
ngưỡng duyệt. Lớp 2 dùng chính NER để tách, nên chỉ nhận cụm **thực sự có trong danh
mục ICD-10** — phần chữ không phải chẩn đoán ("đã điều trị 3 ngày") tự bị bỏ qua.

Ba bộ lọc chống tách nhầm:

| Bộ lọc            | Dòng                                         | Chặn trường hợp                                                                                                                                                                                                     |
| ------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_CODE_LIKE_RE`   | [:97](../nlp/nlp_engine.py#L97)                | Danh mục sinh synonym dạng mã rút gọn ("a988") — khớp NER nhưng không phải tên bệnh                                                                                                                         |
| `_is_group_label` | [:970](../nlp/nlp_engine.py#L970)              | D16.6 tên chỉ là "Cột sống"; nghĩa "u lành của xương" nằm ở**nhóm cha**. Không lọc thì "cột sống" trong "thoát vị đĩa đệm cột sống" bị nhận thành một chẩn đoán u xương riêng |
| Trùng khối ICD-10 | [:1012-1015](../nlp/nlp_engine.py#L1012-L1015) | Hai thuật ngữ cùng khối là hai cách gọi một bệnh, không phải hai chẩn đoán                                                                                                                                |

Trả `[]` khi vế chỉ chứa một bệnh, để phía gọi **giữ nguyên vế gốc** — vế đầy đủ mang
nhiều thông tin lâm sàng hơn nên cho mã chi tiết hơn tên bệnh trần.

### 5.3 Bước C — Gộp pool ứng viên ([:1097-1111](../nlp/nlp_engine.py#L1097-L1111))

Nếu có ≥2 vế, chạy `query()` **riêng cho từng vế** (`FRAGMENT_TOP_K = 5`), rồi gộp
kết quả cả câu + kết quả từng vế thành một pool, giữ bản ghi có độ tin cậy **cao nhất**.
`origin[code]` ghi nhớ mã đó đến từ vế nào — dùng để sinh lời giải thích.

### 5.4 Bước D — Luật dao găm/sao †/* (ưu tiên 1)

#### Bối cảnh y khoa

ICD-10 mã hóa một số chẩn đoán bằng **một cặp mã**:

* mã **†** (dagger) — bệnh nguyên
* mã **\*** (asterisk) — biểu hiện

Danh mục Bộ Y tế ghi sẵn mã đối tác ngay trong tên mã, nên **không cần bảng đối chiếu
chép tay**:

```
M51.1†  "... có kèm tổn thương của rễ tủy sống (G55.1*)"
G55.1*  "Chèn ép rễ và đám rối thần kinh trong bệnh đĩa đệm (M50-M51†)"
```

#### Dựng bảng liên kết lúc khởi động

[`_build_dagger_links:500-537`](../nlp/nlp_engine.py#L500-L537) quét regex
[`_CODE_REF_RE:72`](../nlp/nlp_engine.py#L72) trên tên mọi mã, dựng bốn cấu trúc:

| Cấu trúc           | Nội dung                                                                                                                                                                                |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_link_exact`      | tham chiếu**đích danh** (197 mã) — đủ chặt để **suy ra** cặp. Liên kết **hai chiều**, vì mã * thường không nhắc lại từng mã † và ngược lại |
| `_link_range`      | tham chiếu dạng**dải** ("M50-M51†", 86 mã) — chỉ khoanh vùng khối hợp lệ nên chỉ dùng để **kiểm tra tính hợp lệ**, không dùng để chọn mã           |
| `_marked_dagger`   | tập mã có dấu †                                                                                                                                                                     |
| `_marked_asterisk` | tập mã có dấu *                                                                                                                                                                      |

#### Dò cặp ([:1113-1131](../nlp/nlp_engine.py#L1113-L1131))

Duyệt mọi cặp (bệnh nguyên, biểu hiện) trong pool, kiểm bằng
[`_is_valid_pair:1031`](../nlp/nlp_engine.py#L1031), chọn cặp có tổng độ tin cậy cao nhất.

Trường hợp đặc biệt: **câu một vế vẫn có thể ra mã †**. Bản thân mã † là chẩn đoán
**chưa đủ** theo ICD-10, bắt buộc phải kèm mã * biểu hiện, nên hệ thống tự bổ sung nốt
vế còn thiếu ([:1121-1131](../nlp/nlp_engine.py#L1121-L1131)).

#### Đổi sang mã † anh em ([`_prefer_dagger_sibling:1045`](../nlp/nlp_engine.py#L1045))

Đây **là định nghĩa của mã †, không phải luật chỉnh tay**: khi biểu hiện đã được xác
nhận (G55.1* — chèn ép rễ), thì trong khối M51 phải chọn **M51.1†** "có kèm tổn thương
rễ tủy sống" chứ không phải M51.2 "đặc hiệu khác".

#### Cách tính độ tin cậy của cặp ([:1168-1181](../nlp/nlp_engine.py#L1168-L1181))

```python
record["confidence"] = min(pool[etiology]["confidence"], pool[manifestation]["confidence"])
```

Lấy **min**, và **KHÔNG cộng** hai vế lại. Hai lý do:

1. Một chẩn đoán ghép chỉ chắc chắn bằng vế yếu nhất của nó.
2. Hai vế đến từ **cùng một câu** nên không phải hai bằng chứng độc lập — cộng lại là
   đếm trùng. Cái được "cộng" là **bằng chứng**: một cặp †/* hợp lệ mới là lý do để
   nâng hạng mã bệnh nguyên.

### 5.5 Bước E — Nhiều bệnh độc lập (ưu tiên 2)

[`_separate_diagnoses:1214-1249`](../nlp/nlp_engine.py#L1214-L1249)

> **Thứ tự D → E là bắt buộc** ([:1132-1137](../nlp/nlp_engine.py#L1132-L1137)). Ở ca
> *"Thoát vị đĩa đệm cột sống, chèn ép rễ thần kinh"*, vế sau **cũng** khớp mã riêng
> rất mạnh (G55.1). Nếu xét tách trước thì một chẩn đoán ghép sẽ bị **xé thành hai
> bệnh không có thật**.

Chấm điểm từng vế trong **pool ứng viên riêng của nó**, nên độ tin cậy không bị chia đôi:

| Câu                               | Cách cũ (mã hóa cả câu) | Cách hiện tại (theo vế)        |
| ---------------------------------- | ----------------------------- | ---------------------------------- |
| "sỏi bàng quang, suy thận cấp" | N21.0 = 61%, N17.9 = 38%      | **N21.0 = 91%, N17.9 = 99%** |

Cách cũ còn **sinh mã ma**: "bàng quang" ở vế đầu trộn với "cấp" ở vế sau đẩy N30.0
"Viêm bàng quang cấp" lên hạng hai dù không ai chẩn đoán viêm bàng quang.

Hai điều kiện chấp nhận:

1. `confidence ≥ MULTI_DIAGNOSIS_MIN_CONF = 60.0` ([:88](../nlp/nlp_engine.py#L88)).
   Ngưỡng đặt **cao** vì hậu quả của việc tách nhầm (sinh thêm một bệnh không có thật
   trong hồ sơ) nặng hơn việc bỏ sót; vế mô tả bổ sung cho bệnh chính thường chỉ đạt
   30–50% khi tra riêng.
2. Không trùng khối ICD-10 — "suy thận cấp, vô niệu" đều rơi vào N17, là một bệnh.

Phải có **≥2 chẩn đoán** đạt chuẩn thì mới trả về; không thì trả `[]` để phía gọi giữ
nguyên kết quả cả câu.

[`_flatten_diagnoses:1251`](../nlp/nlp_engine.py#L1251) dựng danh sách phẳng (giữ hợp
đồng cũ với phía gọi): mã chính của từng chẩn đoán lên đầu **theo đúng thứ tự xuất
hiện trong câu**, rồi mới tới ứng viên hạng sau. Danh sách chỉ lấy từ kết quả **từng
vế**, KHÔNG trộn lại kết quả cả câu — vì đó chính là nguồn sinh mã ma.

### 5.6 Ba dạng kết quả trả về

| Trường hợp           | `predictions`            | `combination`                            | `diagnoses`                                                                          |
| ----------------------- | -------------------------- | ------------------------------------------ | -------------------------------------------------------------------------------------- |
| Chẩn đoán kép †/*  | cặp mã lên đầu        | có`display`, `members`, `rationale` | **1 mục** (cặp †/* là **một** chẩn đoán diễn đạt bằng hai mã) |
| Nhiều bệnh độc lập | danh sách phẳng đan xen | `null`                                   | **n mục**, mỗi mục giữ độ tin cậy riêng                                  |
| Một bệnh (đa số)    | top-k của cả câu        | `null`                                   | 1 mục                                                                                 |

---

## 6. Quay lại backend — Chính sách an toàn lâm sàng

[`backend/main.py:228-247`](../backend/main.py#L228-L247)

```python
for pred in predictions + [p for d in diagnoses for p in d["predictions"]]:
    status = verification_status_for(pred["confidence"])
    pred["suggested_verification_status"] = status
    pred["requires_review"] = status != "confirmed"
```

Duyệt **cả hai** danh sách vì ứng viên hạng thấp của từng vế chỉ nằm trong `diagnoses`,
mà Pydantic bắt buộc mọi bản ghi phải có đủ trường ([:231-233](../backend/main.py#L231-L233)).

[`verification_status_for:450`](../nlp/clinical_rules.py#L450) áp `CONFIDENCE_POLICY`:

| Độ tin cậy | `Condition.verificationStatus` | Hành vi                                                                          |
| ------------- | -------------------------------- | --------------------------------------------------------------------------------- |
| ≥ 85%        | `confirmed`                    | Liên thông tự động                                                           |
| 60 – 85%     | `provisional`                  | Dừng, chờ bác sĩ duyệt                                                       |
| 40 – 60%     | `unconfirmed`                  | Dừng, chờ bác sĩ duyệt                                                       |
| < 40%         | —                               | Từ chối sinh FHIR (HTTP 422,[`main.py:259-266`](../backend/main.py#L259-L266)) |

**Nguyên tắc:** kết quả của mô hình **không** được tự động ghi nhận là chẩn đoán đã
xác nhận. Đây là điểm cần nhấn khi bảo vệ đề tài — hệ thống được thiết kế theo mô hình
"máy gợi ý, bác sĩ quyết định".

---

## 7. Cấu trúc JSON trả về

Định nghĩa ở [`backend/main.py:142-150`](../backend/main.py#L142-L150).

```jsonc
{
  "query": "Bệnh nhân bị ĐTĐ typ 2",
  "normalized_query": "đái tháo đường tuýp 2 không phụ thuộc insuline",
  "entities": [
    { "text": "ĐTĐ typ 2", "normalized": "đái tháo đường tuýp 2",
      "code": "E11.9", "code_no_dot": "E119", "type": "clinical_alias" }
  ],
  "predictions": [
    { "code": "E11.9", "code_no_dot": "E119", "raw_code": "E11.9",
      "name_vi": "Đái tháo đường không phụ thuộc insuline, không có biến chứng",
      "name_en": "...",
      "confidence": 94.31, "confidence_band": "high",
      "similarity_score": 0.8123, "rerank_score": 1.0245,
      "matched_by": "đái tháo đường tuýp 2", "match_type": "clinical_alias",
      "explanation": [
        "khớp alias lâm sàng đã kiểm chứng",
        "chẩn đoán không nêu thể bệnh -> ưu tiên mã không đặc hiệu"
      ],
      "suggested_verification_status": "confirmed",
      "requires_review": false }
  ],
  "diagnoses": [ { "fragment": "...", "predictions": [ ... ] } ],
  "combination": null,
  "latency_ms": 58.7
}
```

---

## 8. Ba ví dụ truy vết đầy đủ

### Ví dụ 1 — Câu một bệnh, có viết tắt và tiền tố hành văn

**Đầu vào:** `"Hiện tại bệnh nhân được chẩn đoán: ĐTĐ typ 2"`

| Bước                    | Kết quả                                                                                                                                                                                                                                                                                             |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `normalize_text`        | `hiện tại bệnh nhân được chẩn đoán: đái tháo đường tuýp 2` (ABBREVIATIONS: `đtđ`, `typ 2`)                                                                                                                                                                                   |
| Bóc tiền tố vòng 1–3 | `đái tháo đường tuýp 2`                                                                                                                                                                                                                                                                      |
| `restore_diacritics`    | không đổi (đã có dấu)                                                                                                                                                                                                                                                                          |
| `BRIDGE_TERMS`          | `đái tháo đường tuýp 2 không phụ thuộc insuline`                                                                                                                                                                                                                                          |
| Tầng 2                   | cosine kéo về cả cụm E10 lẫn E11                                                                                                                                                                                                                                                                 |
| Tầng 3                   | trục`diabetes_type`: truy vấn = `type2`; E10.x = `type1` → **−0.18**; E11.x = `type2` → **+0.04**. Alias `"đái tháo đường tuýp 2"` → **+0.15** cho E11.9. Mức chi tiết: câu không nêu biến chứng → **+0.06** cho mã "không có biến chứng" |
| Tầng 4                   | E11 và E11.9 gộp khối → E11.9 nhận cả khối lượng xác suất                                                                                                                                                                                                                                  |
| **Kết quả**       | **E11.9**, `confidence_band = high` → `confirmed`                                                                                                                                                                                                                                          |

### Ví dụ 2 — Chẩn đoán kép †/*

**Đầu vào:** `"Thoát vị đĩa đệm cột sống, chèn ép rễ thần kinh"`

| Bước                     | Kết quả                                                                                                             |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Cắt lớp 1 (dấu phẩy)   | `["Thoát vị đĩa đệm cột sống", "chèn ép rễ thần kinh"]`                                                 |
| Cắt lớp 2                | vế 1 chứa "cột sống" → nhưng`_is_group_label` **loại** (D16.6 là nhãn nhóm cha) → giữ nguyên vế |
| `query()` cả câu       | G55.1* bị đẩy xuống**hạng 5** (vế phụ bị vế chính lấn át)                                           |
| `query()` vế 2 riêng   | **G55.1\* đứng đầu, 67%**                                                                                   |
| Dò cặp †/*              | G55.1 ∈`_marked_asterisk`; M51.x nằm trong dải `M50-M51†` → **cặp hợp lệ**                          |
| `_prefer_dagger_sibling` | đổi M51.2 →**M51.1†** (mã cùng khối, trỏ đích danh G55.1)                                             |
| Độ tin cậy M51.1†      | `min(conf(M51.2), conf(G55.1))`                                                                                     |
| **Kết quả**        | `combination.display = "M51.1† G55.1*"`, `diagnoses` có **1 mục**                                        |

### Ví dụ 3 — Nhiều bệnh độc lập

**Đầu vào:** `"sỏi bàng quang, suy thận cấp"`

| Bước                  | Kết quả                                                        |
| ----------------------- | ---------------------------------------------------------------- |
| Cắt lớp 1             | `["sỏi bàng quang", "suy thận cấp"]`                       |
| `query()` từng vế   | N21.0 =**91%**, N17.9 = **99%**                      |
| Dò cặp †/*           | không có cặp hợp lệ → chuyển sang ưu tiên 2             |
| `_separate_diagnoses` | cả hai ≥ 60%, khác khối (N21 vs N17) →**chấp nhận** |
| **Kết quả**     | `diagnoses` có **2 mục**, `combination = null`       |

---

## 9. Tra cứu nhanh — hằng số và ngưỡng

### Trong [`nlp/nlp_engine.py`](../nlp/nlp_engine.py)

| Hằng số                               | Giá trị   | Dòng                                | Vai trò                               |
| --------------------------------------- | ----------- | ------------------------------------ | -------------------------------------- |
| `W_LEXICAL`                           | 0.12        | [50](../nlp/nlp_engine.py#L50)        | thưởng trùng lặp từ vựng         |
| `W_EXACT`                             | 0.10        | [51](../nlp/nlp_engine.py#L51)        | thưởng khớp trọn cụm              |
| `ALIAS_BONUS_PRIMARY` / `SECONDARY` | 0.15 / 0.10 | [52-53](../nlp/nlp_engine.py#L52-L53) | thưởng alias                         |
| `CANDIDATE_POOL`                      | 400         | [54](../nlp/nlp_engine.py#L54)        | số ứng viên vào tái xếp hạng    |
| `W_RELATIVE` / `W_ABSOLUTE`         | 0.65 / 0.35 | [63-64](../nlp/nlp_engine.py#L63-L64) | trọng số hiệu chuẩn                |
| `SOFTMAX_TEMPERATURE`                 | 0.06        | [65](../nlp/nlp_engine.py#L65)        | độ "sắc" của softmax               |
| `SOFTMAX_SCOPE`                       | 20          | [66](../nlp/nlp_engine.py#L66)        | số mã đưa vào softmax             |
| `SIM_FLOOR` / `SIM_CEIL`            | 0.40 / 0.90 | [67-68](../nlp/nlp_engine.py#L67-L68) | dải quy đổi cosine → [0,1]         |
| `MAX_FRAGMENTS`                       | 6           | [81](../nlp/nlp_engine.py#L81)        | số vế tối đa                       |
| `FRAGMENT_TOP_K`                      | 5           | [82](../nlp/nlp_engine.py#L82)        | ứng viên mỗi vế                    |
| `MIN_TERM_FRAGMENT_LEN`               | 5           | [84](../nlp/nlp_engine.py#L84)        | độ dài tối thiểu một cụm bệnh  |
| `MULTI_DIAGNOSIS_MIN_CONF`            | 60.0        | [88](../nlp/nlp_engine.py#L88)        | ngưỡng tách thành bệnh độc lập |
| `_MAX_NER_NGRAM`                      | 12          | [94](../nlp/nlp_engine.py#L94)        | độ dài n-gram tối đa khi NER      |

### Trong [`nlp/clinical_rules.py`](../nlp/clinical_rules.py)

| Hằng số                           | Giá trị    | Dòng                                        |
| ----------------------------------- | ------------ | -------------------------------------------- |
| `UNSPECIFIED_BONUS` / `PENALTY` | 0.06 / 0.06  | [232-233](../nlp/clinical_rules.py#L232-L233) |
| `OTHER_PENALTY`                   | 0.04         | [234](../nlp/clinical_rules.py#L234)          |
| `CONFIDENCE_POLICY`               | 85 / 60 / 40 | [434-438](../nlp/clinical_rules.py#L434-L438) |
| Phạt trục đối lập              | 0.10 – 0.18 | [162-217](../nlp/clinical_rules.py#L162-L217) |
| Phạt cổng chương                | 0.12 – 0.22 | [263-289](../nlp/clinical_rules.py#L263-L289) |

---

## 10. Muốn sửa gì thì sửa ở đâu

| Muốn làm                                                                   | Sửa ở                                                                                            |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Thêm viết tắt mới (**an toàn**, không mất cache)                | `QUERY_ABBREVIATIONS` — [`clinical_rules.py:49`](../nlp/clinical_rules.py#L49)                 |
| Thêm viết tắt vào cả embedding (**tốn 15–40 phút tính lại**) | `ABBREVIATIONS` — [`clinical_rules.py:31`](../nlp/clinical_rules.py#L31)                       |
| Thêm alias bệnh → mã                                                     | `DIRECT_ALIASES` — [`clinical_rules.py:299`](../nlp/clinical_rules.py#L299)                    |
| Bác sĩ dùng từ khác danh mục                                           | `BRIDGE_TERMS` — [`clinical_rules.py:137`](../nlp/clinical_rules.py#L137)                      |
| Thêm cặp đối lập ngữ nghĩa mới                                       | `POLARITY_AXES` — [`clinical_rules.py:162`](../nlp/clinical_rules.py#L162)                     |
| Chặn thêm một chương ICD-10                                             | `CHAPTER_GATES` — [`clinical_rules.py:263`](../nlp/clinical_rules.py#L263)                     |
| Bỏ tiền tố / hậu tố hành văn mới                                     | `NOISE_PREFIXES` / `NOISE_SUFFIXES` — [`clinical_rules.py:97`](../nlp/clinical_rules.py#L97) |
| Đổi ngưỡng duyệt của bác sĩ                                          | `CONFIDENCE_POLICY` — [`clinical_rules.py:434`](../nlp/clinical_rules.py#L434)                 |
| Đổi ký hiệu tách vế                                                    | `_FRAGMENT_SPLIT_RE` — [`nlp_engine.py:77`](../nlp/nlp_engine.py#L77)                          |
| Sửa dữ liệu sai của file danh mục                                       | `DATA_FIXES` — [`clinical_rules.py:422`](../nlp/clinical_rules.py#L422)                        |

---

## 11. Cách tự kiểm chứng luồng

### Chạy engine độc lập, không cần server

`nlp_engine.py` có sẵn khối `__main__` ([:1281-1294](../nlp/nlp_engine.py#L1281-L1294)):

```powershell
.venv\Scripts\python -m nlp.nlp_engine
```

In ra câu gốc → câu đã chuẩn hóa → top-4 mã kèm độ tin cậy, cho 4 câu mẫu.

### Kiểm thử chức năng

```powershell
.venv\Scripts\python -m pytest nlp/test_nlp.py -v
```

Các test bám sát tài liệu này: [`test_nlp.py:178`](../nlp/test_nlp.py#L178) và
[`:201`](../nlp/test_nlp.py#L201) kiểm luật †/*, [`:209`](../nlp/test_nlp.py#L209)
kiểm tách nhiều bệnh, [`:149`](../nlp/test_nlp.py#L149) kiểm gõ không dấu.

### Đo định lượng

```powershell
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
```

### Xem trực tiếp trong giao diện

Mở [http://127.0.0.1:8000](http://127.0.0.1:8000), gõ câu chẩn đoán. Giao diện hiển thị đúng ba bước của
tài liệu này: **Chuẩn hóa** (bước 2) → **NER** (bước 3) → **Ánh xạ ICD-10** kèm
trường `explanation` (bước 4–5).

---

## 12. Bốn quyết định thiết kế đáng nhấn khi bảo vệ đề tài

1. **Không dùng embedding thuần.** Chênh lệch cosine giữa "phụ thuộc insuline" và
   "không phụ thuộc insuline" chỉ ~0,02 — nhỏ hơn nhiễu. Tầng tái xếp hạng theo tri
   thức lâm sàng là thứ tạo ra khả năng phân biệt.
2. **Độ tin cậy được hiệu chuẩn, không phải điểm thô.** Kết hợp mức áp đảo tương đối
   (softmax) với mức khớp tuyệt đối (cosine thô), để ngưỡng duyệt của bác sĩ có ý nghĩa
   thực sự thay vì mọi kết quả đều hiện ~100%.
3. **Thứ tự xét †/* trước rồi mới xét nhiều bệnh.** Đảo thứ tự sẽ xé một chẩn đoán ghép
   thành hai bệnh không có thật trong hồ sơ — sai lầm nặng hơn bỏ sót.
4. **Mọi luật đều có lời giải thích trả ra `explanation`.** Hệ thống y tế không được là
   hộp đen: bác sĩ phải thấy được **vì sao** máy đề xuất mã đó thì mới có căn cứ duyệt
   hay bác bỏ.

---

## Phụ lục — Bảng tra file

| File                                                                                                                      | Vai trò trong luồng                                                          |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| [`frontend/app.js`](../frontend/app.js)                                                                                  | Client web, gọi`/api/standardize`                                           |
| [`hospital_his/server.py`](../hospital_his/server.py)                                                                    | Client HIS mô phỏng                                                          |
| [`VNPT_HIS/.../GatewayService.java`](../VNPT_HIS/backend/src/main/java/com/vnpt/his/backend/service/GatewayService.java) | Client HIS VNPT                                                                |
| [`backend/main.py`](../backend/main.py)                                                                                  | Endpoint, mô hình dữ liệu, chính sách độ tin cậy                      |
| [`backend/fhir_helper.py`](../backend/fhir_helper.py)                                                                    | Sinh FHIR Condition (bước**sau** khi bác sĩ chọn mã)               |
| [`nlp/nlp_engine.py`](../nlp/nlp_engine.py)                                                                              | **Toàn bộ 4 tầng xử lý + luật ghép mã**                          |
| [`nlp/clinical_rules.py`](../nlp/clinical_rules.py)                                                                      | Tri thức lâm sàng: viết tắt, cầu nối, trục đối lập, alias, ngưỡng |
| [`nlp/data/icd10_db.json`](../nlp/data/icd10_db.json)                                                                    | Danh mục ICD-10 (12.219 mã)                                                  |
| `nlp/data/embeddings_*.npy`                                                                                             | Cache ~47.000 vector, sinh tự động                                          |
| [`ChangeJson.py`](../ChangeJson.py)                                                                                      | Chuyển danh mục Excel Bộ Y tế → JSON                                      |
