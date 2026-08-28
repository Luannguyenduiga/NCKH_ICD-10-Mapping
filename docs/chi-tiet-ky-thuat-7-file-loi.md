# Chi tiết kỹ thuật bảy file lõi của SMIG

Tài liệu này mô tả **từng file một** trong bảy file mang toàn bộ thuật toán NLP và cơ
chế liên thông của đề tài — 4 647 dòng mã, không tính giao diện, không tính HIS thứ hai,
không tính kiểm thử.

Mỗi file được trình bày theo cùng một khung: **nhận dạng → bài toán → kiến trúc → chi
tiết từng hàm → quyết định thiết kế → giới hạn**. Phần "quyết định thiết kế" là phần
dùng được trực tiếp cho báo cáo khoa học: nó nói *vì sao* làm như vậy, kèm số đo.

> Tài liệu bổ trợ: `docs/cac-file-nlp-va-lien-thong.md` cho bản đồ toàn bộ 16 file;
> `docs/luong-xu-ly-chan-doan.md` truy vết đường đi của một câu chẩn đoán qua từng hàm.

## Mục lục

| # | File | Khối | Dòng |
| --- | --- | --- | --- |
| [1](#1-nlpnlp_enginepy) | `nlp/nlp_engine.py` | NLP | 1 360 |
| [2](#2-nlpclinical_rulespy) | `nlp/clinical_rules.py` | NLP | 502 |
| [3](#3-nlpevaluatepy) | `nlp/evaluate.py` | NLP | 189 |
| [4](#4-changejsonpy) | `ChangeJson.py` | NLP (dữ liệu) | 564 |
| [5](#5-backendmainpy) | `backend/main.py` | Liên thông | 689 |
| [6](#6-backendfhir_helperpy) | `backend/fhir_helper.py` | Liên thông | 349 |
| [7](#7-hospital_hisserverpy) | `hospital_his/server.py` | Liên thông | 994 |

Ranh giới hai khối nằm ở đúng một điểm: **khối NLP kết thúc khi trả ra danh sách mã
ICD-10 kèm độ tin cậy; khối liên thông bắt đầu từ đó.**

---

# 1. `nlp/nlp_engine.py`

## 1.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 1 360 |
| Lớp chính | `NLPEngine` |
| Phụ thuộc ngoài | `sentence-transformers`, `torch`, `numpy` |
| Phụ thuộc trong | `nlp/clinical_rules.py` (18 hằng số + 1 hàm) |
| Dữ liệu đọc | `nlp/data/icd10_db.json`, `nlp/my_medical_nlp_model/`, cache `.npy` |
| Được gọi bởi | `backend/main.py`, `nlp/evaluate.py`, `nlp/test_nlp.py` |
| Hàm đối ngoại | `query()`, `query_composite()`, `expand_query()`, `extract_entities_regex()` |

## 1.2 Bài toán

Ánh xạ một câu chẩn đoán tiếng Việt viết tự do sang mã ICD-10 trong danh mục 12 137 mã
của Bộ Y tế.

Cách tiếp cận hiển nhiên — mã hóa câu và toàn bộ tên bệnh bằng SBERT rồi lấy cosine cao
nhất — **không hoạt động**, vì mô hình embedding đo *độ tương đồng ngữ nghĩa*, mà mã
ICD-10 lại được phân biệt bằng những *cặp đối lập*. Với truy vấn `"đái tháo đường tuýp 2"`,
sáu kết quả đầu của cosine thuần túy:

```
E10.3  0.6102   ĐTĐ phụ thuộc insuline (biến chứng mắt)   <- típ 1, SAI
E10.5  0.5932   ĐTĐ phụ thuộc insuline                     <- típ 1, SAI
E10.4  0.5882   ...                                        <- típ 1, SAI
E11.3  0.5785   ĐTĐ không phụ thuộc insuline               <- típ 2
```

Nguyên nhân kép:

1. Khoảng cách cosine giữa `"phụ thuộc insuline"` và `"không phụ thuộc insuline"` chỉ
   khoảng **0,02** — chữ "không" gần như không làm dịch chuyển vector.
2. Danh mục Bộ Y tế **không dùng chữ "tuýp 2"** ở bất kỳ mã nào; thuật ngữ tương ứng là
   "không phụ thuộc insuline". Hai cụm không chung một từ nào.

## 1.3 Kiến trúc — truy hồi lai bốn tầng

```
câu chẩn đoán thô
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│ Tầng 1 — CHUẨN HÓA          expand_query()               │
│   NFC → hạ chữ → bóc tiền tố ×4 → bóc hậu tố             │
│   → giải viết tắt → khôi phục dấu → nối cầu nối từ vựng  │
└─────────────────────────────────────────────────────────┘
      │  câu đã mở rộng
      ▼
┌─────────────────────────────────────────────────────────┐
│ Tầng 2 — TRUY HỒI            query()                     │
│   encode(câu) → cosine với ~47k vector tham chiếu        │
│   → argpartition lấy 400 ứng viên + ép thêm mã trúng alias│
└─────────────────────────────────────────────────────────┘
      │  pool ứng viên
      ▼
┌─────────────────────────────────────────────────────────┐
│ Tầng 3 — TÁI XẾP HẠNG                                    │
│   S = cos + 0.12·F1_lex + 0.10·khớp_trọn + alias         │
│         ± cực_tính ± mức_chi_tiết − cổng_chương          │
│   → gộp về mã tốt nhất cho mỗi mã ICD-10, sắp xếp        │
└─────────────────────────────────────────────────────────┘
      │  danh sách đã xếp hạng
      ▼
┌─────────────────────────────────────────────────────────┐
│ Tầng 4 — HIỆU CHUẨN          _calibrate()                │
│   softmax(τ=0.06) trên top-20 → gộp khối 3 ký tự         │
│   conf = 0.65·tương_đối + 0.35·tuyệt_đối                 │
│   + sàn theo độ phủ, có chốt cực tính                    │
└─────────────────────────────────────────────────────────┘
      │
      ▼  [{code, confidence, explanation, ...}]
```

**Tầng 3 là đóng góp chính của đề tài.** Nếu bỏ tầng này, hệ thống trở lại đúng bảng kết
quả sai ở mục 1.2.

## 1.4 Khởi tạo — `__init__` (dòng 139–178)

Thứ tự bốn bước khởi tạo là **bắt buộc**, ghi rõ ở dòng 169–172:

```python
self._prepare_embeddings()        # 1. cache embedding của danh mục GỐC
self._apply_data_fixes()          # 2. vá dữ liệu sai, chỉ ở tầng hiển thị/luật
self._prepare_alias_embeddings()  # 3. embedding alias (nhỏ, vài giây)
self._build_lookup_tables()       # 4. mọi bảng tra dùng lúc chạy
```

Lý do: embedding của danh mục phải dựng từ dữ liệu **gốc**. Nếu vá dữ liệu trước, nội
dung danh mục đổi → khóa cache đổi → hệ thống phải mã hóa lại ~47 000 vector (15–40 phút
trên CPU) chỉ vì sửa một dòng tên tiếng Anh. Bản vá vì vậy được đưa vào tập alias — vốn
nhỏ và tính trong vài giây — để vẫn có mặt trong không gian embedding.

### Cơ chế cache embedding

Đây là phần kỹ thuật nặng nhất của file, vì nó quyết định hệ thống khởi động trong 5 giây
hay 40 phút, **và** quyết định kết quả có đúng hay không.

**`_cache_key()` (dòng 266–292)** sinh khóa từ hai thành phần:

```python
model_key = tên_thư_mục_mô_hình + "_" + vân_tay_trọng_số
db_hash   = sha1(mọi code + name_vi + name_en + synonyms)[:12]
→ embeddings_{model_key}_{db_hash}.npy
```

- Dùng **tên thư mục** thay vì đường dẫn tuyệt đối → chép dự án sang máy khác vẫn dùng
  lại được cache.
- Dùng **hash nội dung** danh mục thay vì số lượng bản ghi → đổi nội dung mà giữ nguyên
  số dòng cũng làm cache tự vô hiệu.

**`_model_fingerprint()` (dòng 194–236)** — băm SHA-1 của `kích thước + 1 MB đầu + 1 MB
cuối` các file trọng số (`model.safetensors`, `pytorch_model.bin`, `config.json`…), lấy
12 ký tự hex.

Lý do tồn tại, ghi ở docstring dòng 197–202: bản trước chỉ lấy **tên** thư mục làm khóa.
Khi fine-tune lại vào cùng thư mục, hệ thống vẫn nạp vector của checkpoint cũ. Hậu quả đo
được: truy vấn trùng khít mục từ (`"đậu khỉ"` → `"Đậu khỉ"`) chỉ đạt cosine **0,77** thay
vì 1,0, kéo độ tin cậy xuống ~86% và làm lệch thứ hạng. Không có lỗi nào được ném ra.

Chỉ băm 2 MB thay vì toàn bộ ~540 MB để không thêm vài giây vào mỗi lần khởi động; huấn
luyện lại luôn làm đổi các byte này.

**`_cache_matches_model()` (dòng 238–264)** — lưới an toàn cuối cùng. Mã hóa lại 3 mục từ
mẫu (đầu, giữa, cuối) rồi so cosine với vector tương ứng trong cache; cùng mô hình thì
phải ≥ **0,999**. Bắt được các trường hợp vân tay không phủ: cache chép tay, đổi tên file.

**`_migrate_legacy_cache()` (dòng 310–345)** — nhận diện cache của phiên bản cũ rồi *đổi
tên* sang khóa mới thay vì tính lại. Chốt quan trọng ở dòng 337: trùng danh sách mục từ
**không** có nghĩa là cùng mô hình — mọi checkpoint đều sinh ra đúng ngần ấy vector. Thiếu
bước gọi `_cache_matches_model` ở đây thì cache của mô hình cũ bị đổi tên sang khóa mới và
âm thầm làm sai toàn bộ điểm cosine.

### Văn bản tham chiếu

`_build_reference_entries()` (dòng 294–308) sinh ~47 000 mục từ cho 12 137 mã:

| Loại | Nguồn | Ghi chú |
| --- | --- | --- |
| `official_vi` | `name_vi` | 1 mục / mã |
| `official_en` | `name_en` | có ở phần lớn mã |
| `synonym` | `synonyms[]` | biến thể không dấu, mã rút gọn, bỏ ngoặc, "u ác"→"ung thư" |
| `clinical_alias` | `DIRECT_ALIASES` | cache riêng, dòng 394–450 |

### Bảng tra tiền tính — `_build_lookup_tables` (dòng 452–505)

| Bảng | Kiểu | Dùng để |
| --- | --- | --- |
| `_entry_norm` | `List[str]` | văn bản đã chuẩn hóa của từng mục |
| `_entry_tokens` | `List[frozenset]` | token nội dung, cho `_lexical_f1` |
| `_term_index` | `Dict[str, entry]` | NER: cụm chuẩn hóa → mục từ |
| `_term_index_ascii` | `Dict[str, entry]` | NER dự phòng khi gõ không dấu |
| `_ascii_to_norm` | `Dict[str, str]` | khôi phục dấu: `"hen phe quan"` → `"hen phế quản"` |
| `_code_to_indices` | `Dict[str, List[int]]` | ép ứng viên alias vào vòng tái xếp hạng |
| `_code_polarity` | `Dict[str, Dict]` | nhãn trục đối lập của từng mã |
| `_link_exact`, `_link_range` | `Dict` | liên kết dagger/asterisk |

Docstring dòng 455–457 ghi rõ lý do: phiên bản trước chuẩn hóa và sắp xếp lại toàn bộ
~47k thuật ngữ trên **mỗi** request để trích xuất thực thể. Ở đây làm đúng một lần lúc
khởi tạo, đưa NER từ `O(số thuật ngữ)` xuống `O(số từ × độ dài n-gram)`.

Từ điển khôi phục dấu có **thứ tự ưu tiên** (dòng 469, `_TYPE_RANK`): `clinical_alias` 3 >
`official_vi` 2 > `synonym` 1 > `official_en` 0. Và chỉ nhận cụm từ **2 từ trở lên**, vì
cụm một từ quá nhập nhằng khi bỏ dấu — `"than"` vừa là `"thận"` vừa là `"than"` trong
`"bệnh than"`.

### Liên kết dagger/asterisk — `_build_dagger_links` (dòng 507–544)

ICD-10 mã hóa chẩn đoán kép bằng một **cặp**: mã † cho bệnh nguyên, mã * cho biểu hiện.
Danh mục Bộ Y tế ghi sẵn mã đối tác ngay trong tên bệnh, nên không cần bảng đối chiếu chép
tay:

```
M51.1†  "... có kèm tổn thương của rễ tủy sống (G55.1*)"
G55.1*  "Chèn ép rễ và đám rối thần kinh trong bệnh đĩa đệm (M50-M51†)"
```

Biểu thức chính quy `_CODE_REF_RE` (dòng 79–81) bóc hai dạng tham chiếu:

| Dạng | Số mã | Dùng để |
| --- | --- | --- |
| Đích danh `(G55.1*)` | 197 | **suy ra cặp** — đủ chặt để chọn mã |
| Dải `(M50-M51†)` | 86 | **chỉ kiểm tra hợp lệ** — chỉ khoanh vùng khối |

Liên kết được dựng **hai chiều** (dòng 543–544) vì mã * thường không nhắc lại từng mã †
và ngược lại.

## 1.5 Tầng 1 — Chuẩn hóa

### `normalize_text()` (dòng 555–576)

Dùng cho **cả** văn bản tham chiếu lúc sinh embedding, nên mọi thay đổi ở đây làm cache
mất nhất quán. Viết tắt bổ sung dành riêng cho truy vấn phải đặt ở `QUERY_ABBREVIATIONS`.

Thứ tự bắt buộc, và dòng đầu là điểm đáng nêu trong báo cáo:

```python
text = unicodedata.normalize("NFC", text)   # <- PHẢI trước
text = text.lower().strip()
text = re.sub(r"[^\w\s\-\/\.]", " ", text)  # <- dòng phá hoại nếu NFD
```

Danh mục Bộ Y tế có **197 mã lưu ở dạng NFD**: chữ `"không"` được lưu là `k h o U+0302 n g`
— dấu mũ là một ký tự tổ hợp riêng. Ký tự tổ hợp không thuộc lớp `\w` nên dòng `re.sub`
thay nó bằng dấu cách, cắt `"không"` thành `"kho ng"`. Mất token → mất điểm khớp trọn cụm
→ mất luôn điểm thưởng mã "không đặc hiệu". Bệnh án dán từ macOS hay HIS cũng hay ở dạng
NFD. Đây là lỗi từng làm mã A99 tụt từ hạng 1 xuống hạng 3.

### `expand_query()` (dòng 578–618)

Chuẩn hóa dành riêng cho câu của bác sĩ, gồm bảy bước:

```
normalize_text
  → bóc NOISE_PREFIXES, LẶP tối đa 4 vòng cho tới khi ổn định
  → bóc NOISE_SUFFIXES
  → giải QUERY_ABBREVIATIONS
  → restore_diacritics
  → giải QUERY_ABBREVIATIONS lần 2   (khôi phục dấu có thể làm lộ viết tắt mới)
  → nối BRIDGE_TERMS   (chỉ khi with_bridges=True)
```

Bóc **lặp** là cần thiết vì tiền tố hành văn xếp chồng nhau: `"Hiện tại | bệnh nhân được
chẩn đoán: | ..."` — quét một lượt duy nhất sẽ bỏ sót.

Tham số `with_bridges=False` dùng cho trích xuất thực thể, để chỉ bôi màu những cụm bác sĩ
**thực sự viết ra**, không bôi cả cụm do hệ thống nối thêm.

Bước nối cầu nối (dòng 613–617) là mấu chốt của cả tầng: cụm chuẩn được **nối thêm**, không
thay thế, để giữ nguyên ngữ cảnh gốc.

### `restore_diacritics()` (dòng 620–653)

Quét n-gram **dài nhất trước** (từ 12 từ xuống 2 từ), và chỉ thay thế những cụm vốn *không*
có dấu:

```python
if phrase != strip_diacritics(phrase):
    continue   # cụm đã có dấu -> bác sĩ gõ đúng, không đụng tới
```

Nhờ chốt này, văn bản gõ đúng chính tả không bị đụng. Không có bước này, chuỗi
`"nhoi mau co tim cap"` cho ra `"Nhịp nhanh kịch phát"` — SBERT coi chuỗi không dấu là từ
hoàn toàn khác.

## 1.6 Tầng 2+3 — `query()` (dòng 898–1012)

### Chọn pool ứng viên (dòng 911–915)

```python
pool = set(np.argpartition(-cos_scores, pool_size - 1)[:pool_size])   # top-400
for code in alias_boosts:
    pool.update(self._code_to_indices.get(code, []))                  # ép alias vào
```

Dùng `argpartition` thay vì `argsort`: `O(n)` thay vì `O(n log n)` trên 47k phần tử. Mã
trúng alias **luôn** được xét kể cả khi cosine xếp ngoài top-400 — nếu không, alias đã
kiểm chứng vẫn có thể bị loại trước khi được cộng điểm.

### Công thức tái xếp hạng

Với mỗi mục từ tham chiếu `e` của mã `c`:

```
S(q, e) = cos(v_q, v_e)
        + 0.12 · F1_lex(q, e)
        + 0.10 · 1[ text_e ⊆ q  ∧  |text_e| ≥ 5 ]
        + bonus_alias(c)                      ∈ {0, 0.10, 0.15} × hệ_số × độ_phủ
        + Δ_polarity(q, c)                    ∈ [−0.20, +0.05]
        + Δ_specificity(q, e)                 ∈ {−0.06, −0.04, 0, +0.06}
        + Δ_chapter(q, c)                     ∈ {−0.22, −0.20, −0.18, −0.12, 0}
```

trong đó `F1_lex` là F1 trên tập token nội dung (dòng 782–791):

```
P = |T_q ∩ T_e| / |T_e|      R = |T_q ∩ T_e| / |T_q|      F1 = 2PR/(P+R)
```

**Vì sao các hệ số ở mức 0,1–0,2.** Ghi ở dòng 48–49: chênh lệch cosine giữa các mã ICD-10
lân cận thường chỉ **0,005–0,03**. Hệ số phải đủ lớn để một vi phạm ngữ nghĩa lật được thứ
hạng, nhưng không lớn tới mức nuốt hẳn tín hiệu ngữ nghĩa.

Mỗi mã ICD-10 chỉ giữ **một** bản ghi — bản có điểm cao nhất trong số các mục từ của nó
(dòng 954–963), kèm `raw` (cosine gốc), `coverage`, và danh sách `notes` giải thích.

### Ba luật tri thức

**`_polarity_delta()` (dòng 675–689)** — so nhãn trục đối lập giữa truy vấn và mã ứng viên.
Khớp thì cộng `bonus`, xung đột thì trừ `penalty`, và ghi lý do vào `notes`:
`"xung đột diabetes_type: chẩn đoán 'type2' vs mã 'type1'"`.

**`_specificity_delta()` (dòng 717–741)** — điều chỉnh theo mức chi tiết:

| Tên mã | Truy vấn không nêu thể bệnh | Truy vấn có nêu thể bệnh |
| --- | --- | --- |
| chứa "không đặc hiệu" | **+0,06** | **−0,06** |
| chứa "khác" | **−0,04** | 0 |

Điều kiện "không nêu thể bệnh" được cài bằng `q_tokens ⊆ entry_tokens` — câu chẩn đoán
không mang thông tin nào ngoài chính tên bệnh của mã đó. Ví dụ: `"bệnh Crohn"` phải ra
K50.9 "không đặc hiệu" chứ không phải K50.8 "Bệnh Crohn khác"; ngược lại `"viêm kết mạc
dị ứng"` không được ra H10.9 vì mã đó đánh rơi vế "dị ứng".

**`_chapter_delta()` (dòng 743–752)** — chặn chương chỉ hợp lệ trong ngữ cảnh chuyên biệt.
Không có nó, `"đái tháo đường tuýp 2"` bị gán O24.0 (ĐTĐ ở thai phụ).

**`_alias_targets()` (dòng 754–780)** — so khớp hai vòng, có dấu (hệ số 1,0) rồi không dấu
(hệ số 0,85, thấp hơn vì bỏ dấu tăng nguy cơ khớp nhầm). Điểm thưởng nhân với **độ phủ**:

```python
coverage = min(1.0, len(tokens(alias)) / len(tokens(query)))
```

Lý do ở dòng 768–770: alias chỉ phủ một phần câu là bằng chứng yếu hơn. `"hen phế quản"`
trong `"hen phế quản không dị ứng"` không được phép lấn át J45.1 vốn mô tả đúng cả vế
"không dị ứng".

## 1.7 Tầng 4 — Hiệu chuẩn

### Softmax có gộp khối (dòng 967–991)

```
p_i = exp((S_i − S_max) / 0.06) / Σ_{j≤20} exp((S_j − S_max) / 0.06)

P_block(b) = Σ_{i : block(i) = b} p_i          # block = 3 ký tự đầu, "E11.9" → "E11"
```

Mã tốt nhất trong mỗi khối nhận **toàn bộ khối lượng xác suất của khối đó**; các mã sau
trong cùng khối chỉ nhận `p_i` riêng. E11 và E11.9 là cùng một chẩn đoán ở hai mức chi
tiết — để chúng chia đôi xác suất là hạ độ tin cậy một cách oan uổng.

### `_calibrate()` (dòng 798–827)

```
absolute = clip( (sim − 0.40) / (0.90 − 0.40), 0, 1 )
conf     = 0.65 · relative + 0.35 · absolute
if coverage ≥ 1.0 ∧ sim ≥ 0.90:  conf = max(conf, 0.88)
conf%    = round(conf × 100, 2)
```

Hai thành phần đo hai thứ **độc lập nhau**:

- `relative` — mức **áp đảo** so với ứng viên khác.
- `absolute` — mức **khớp thật** với danh mục.

Một mã thắng áp đảo nhưng ngữ nghĩa xa vẫn bị hạ điểm, và ngược lại. Nếu lấy thẳng điểm
tái xếp hạng làm độ tin cậy thì điểm đó có cộng thưởng nên dễ vượt trần, mọi kết quả hiện
100% và ngưỡng duyệt mất tác dụng hoàn toàn.

**Vì sao cần sàn theo độ phủ.** Docstring dòng 804–808 nêu ca cụ thể: `"bệnh van hai lá do
thấp"` khớp I05 *"Bệnh lý van hai lá do thấp"* với cosine **0,991** — gần như trùng nghĩa
hoàn toàn — nhưng chỉ được **56,26%** vì các mã van tim khác (I08, I34) chia mất xác suất.
Bác sĩ gõ đúng tên bệnh mà máy báo "cần duyệt lại" là phản trực giác. Gộp khối 3 ký tự đã
xử lý anh em cùng mã cha, nhưng không xử lý được khối lân cận về mặt lâm sàng.

Sàn cũng chặn được **chiều ngược lại**: `"viêm kết mạc dị ứng"` khớp H10.9 *"Viêm kết mạc,
không đặc hiệu"* ở cosine 0,953 nhưng thiếu hẳn hai từ "dị ứng" — đúng phần mang nghĩa
phân biệt. Độ phủ chưa trọn thì không được hưởng sàn.

### `_polarity_compatible()` — chốt cho sàn (dòng 691–715)

Độ phủ đếm theo **túi từ** nên mù với hai kiểu sai nguy hiểm, cả hai đều từng lọt lên mức
"tự động liên thông":

| Kiểu sai | Ví dụ đo được |
| --- | --- |
| Mã khẳng định thêm | Câu `"phình động mạch chủ bụng"` (không nói vỡ hay không) khớp I71.3 *"Phình động mạch chủ bụng, **vỡ**"*. Đủ từ, nhưng mã tự thêm một tình trạng cấp cứu bệnh án không hề ghi |
| Phủ định gắn sai chỗ | Câu `"viêm mũi không dị ứng"` và tên mã J30.4 *"Viêm mũi dị ứng, không phân loại"* dùng chung đúng bấy nhiêu từ, chỉ khác chỗ đặt chữ "không" — mà nghĩa thì ngược hẳn |

Nguyên tắc cài đặt: **trục nào mã khẳng định thì câu phải khẳng định y hệt. Câu im lặng ở
trục đó cũng không đủ điều kiện — im lặng không phải là đồng ý.**

## 1.8 NER theo luật — `extract_entities_regex()` (dòng 832–875)

Quét n-gram trên bảng tra đã tiền tính, độ dài từ 12 từ giảm dần về 1; tại mỗi vị trí, cụm
dài nhất khớp được thì dừng (`break`, dòng 855). Sau đó sắp theo `(start − end, start)` để
**cụm dài đứng trước**, rồi loại các cụm chồng lấn ngắn hơn.

`_surface_form()` (dòng 877–893) tìm đoạn văn bản **gốc** tương ứng với cụm đã chuẩn hóa,
so khớp không phân biệt dấu. Bản trước cắt `text[start:end]` bằng chỉ số tính trên chuỗi
*đã* chuẩn hóa — chỉ đúng khi hai chuỗi tình cờ dài bằng nhau.

## 1.9 Nhiều bệnh trong một câu — `query_composite()` (dòng 1149–1278)

Đây là phần logic phức tạp nhất file. Thứ tự xét là **bắt buộc**:

```
1. query(cả câu)                        → predictions
2. _candidate_fragments(cả câu)         → các vế
3. query(từng vế) nếu có ≥ 2 vế         → gộp vào pool
4. Thử ghép cặp †/*                     ← XÉT TRƯỚC
5. Nếu không ghép được: _separate_diagnoses  ← xét sau
6. Nếu vẫn không: trả kết quả cả câu
```

**Vì sao †/* phải xét trước** (ghi ở dòng 1199–1203): ở ca `"Thoát vị đĩa đệm cột sống,
chèn ép rễ thần kinh"`, vế sau **cũng** khớp một mã riêng rất mạnh (G55.1). Nếu xét tách
trước thì một chẩn đoán ghép bị xé thành hai bệnh không có thật trong hồ sơ.

### Tách vế — hai tầng

**`split_clinical_fragments()` (dòng 1017–1033)** cắt theo dấu câu và liên từ lâm sàng:

```python
_FRAGMENT_SPLIT_RE = r"[,;/+]|kèm theo|kèm|có biến chứng|biến chứng|gây|dẫn đến"
```

**Cố ý không tách theo `"và"`** (ghi ở dòng 82–83): `"rễ và đám rối thần kinh"` là một
cụm, tách ra sẽ vỡ nghĩa. Vế ngắn hơn 6 ký tự hoặc dưới 2 từ bị bỏ.

**`_term_fragments()` (dòng 1053–1083)** xử lý trường hợp dấu câu bỏ sót: `"đái tháo đường
tuýp 2 tăng huyết áp"` là **một** vế duy nhất; mã hóa cả cụm thành một vector thì E11 và
I10 chia nhau xác suất và không mã nào đạt ngưỡng duyệt. Hàm này chỉ nhận cụm **thực sự có
trong danh mục ICD-10**, nên phần chữ không phải chẩn đoán (`"đã điều trị 3 ngày"`) tự bị
bỏ qua.

Ba bộ lọc chống tách nhầm:

| Bộ lọc | Chặn cái gì |
| --- | --- |
| `_CODE_LIKE_RE` (dòng 104) | Synonym dạng mã rút gọn (`"a988"`) khớp NER như thuật ngữ nhưng không phải tên bệnh |
| `_is_group_label()` (dòng 1035–1051) | D16.6 tên chỉ là *"Cột sống"*, nghĩa "u lành của xương" nằm ở **nhóm cha**. Không lọc thì `"cột sống"` trong `"thoát vị đĩa đệm cột sống"` thành chẩn đoán u xương riêng |
| Gộp theo khối 3 ký tự | Hai thuật ngữ cùng khối là hai cách gọi một bệnh, không phải hai chẩn đoán |

Trả `[]` khi vế chỉ chứa một bệnh, để phía gọi giữ nguyên vế gốc — vế đầy đủ mang nhiều
thông tin lâm sàng hơn nên cho mã chi tiết hơn tên bệnh trần.

### Ghép cặp †/* (dòng 1179–1266)

`_is_valid_pair()` (dòng 1097–1109) kiểm ba điều kiện: hai mã khác nhau, mã biểu hiện phải
có dấu `*`, và có liên kết đích danh **hoặc** nằm trong dải hợp lệ.

`_prefer_dagger_sibling()` (dòng 1111–1125) đổi mã bệnh nguyên sang mã anh em cùng khối có
dấu † trỏ đích danh mã biểu hiện. Đây là **định nghĩa của mã †**, không phải luật chỉnh
tay: khi biểu hiện đã xác nhận (G55.1* — chèn ép rễ), thì trong khối M51 phải chọn M51.1†
*"có kèm tổn thương rễ tủy sống"* chứ không phải M51.2 *"đặc hiệu khác"*.

**Quy tắc cộng điểm — điểm đáng nêu trong báo cáo** (dòng 1156–1158 và 1236–1241):

> Độ tin cậy của hai mã **không** cộng vào nhau. Chúng cùng đến từ một câu nên không phải
> hai bằng chứng độc lập — cộng lại là đếm trùng. Mã † thay thế lấy **min** của hai vế:
> một chẩn đoán ghép chỉ chắc chắn bằng vế yếu nhất của nó.

### Tách thành nhiều bệnh — `_separate_diagnoses()` (dòng 1280–1315)

Điều kiện nhận một vế làm chẩn đoán độc lập:

1. Có ≥ 2 vế.
2. Mã tốt nhất của vế đạt ≥ **60%** (`MULTI_DIAGNOSIS_MIN_CONF`).
3. Khối 3 ký tự chưa bị vế nào khác chiếm.
4. Kết quả cuối phải có ≥ 2 chẩn đoán, nếu không trả `[]`.

Ngưỡng 60% đặt cao có chủ đích (dòng 92–94): **hậu quả của việc tách nhầm — sinh thêm một
bệnh không có thật trong hồ sơ — nặng hơn việc bỏ sót**. Vế mô tả bổ sung cho bệnh chính
thường chỉ đạt 30–50% khi tra riêng.

Lợi ích đo được (dòng 1287–1291): `"sỏi bàng quang, suy thận cấp"` cho N21.0 và N17.9 giữ
nguyên mức **91%** và **99%**, thay vì tụt xuống **61%** và **38%** như khi mã hóa cả câu
thành một vector. Cách cũ còn sinh **mã ma**: `"bàng quang"` ở vế đầu trộn với `"cấp"` ở vế
sau đẩy N30.0 *"Viêm bàng quang cấp"* lên hạng hai dù không ai chẩn đoán viêm bàng quang.

## 1.10 Bảng hằng số

| Hằng số | Giá trị | Ý nghĩa |
| --- | --- | --- |
| `W_LEXICAL` | 0,12 | thưởng trùng lặp từ vựng |
| `W_EXACT` | 0,10 | thưởng khớp trọn cụm (≥ 5 ký tự) |
| `ALIAS_BONUS_PRIMARY` / `SECONDARY` | 0,15 / 0,10 | alias mã ưu tiên / mã phụ |
| `CANDIDATE_POOL` | 400 | ứng viên vào tái xếp hạng |
| `W_RELATIVE` / `W_ABSOLUTE` | 0,65 / 0,35 | trọng số hiệu chuẩn |
| `SOFTMAX_TEMPERATURE` | 0,06 | nhiệt độ softmax |
| `SOFTMAX_SCOPE` | 20 | số mã vào chuẩn hóa softmax |
| `SIM_FLOOR` / `SIM_CEIL` | 0,40 / 0,90 | dải quy đổi độ tương đồng tuyệt đối |
| `COVERAGE_FLOOR_SIM` / `_CONF` | 0,90 / 0,88 | ngưỡng và giá trị sàn theo độ phủ |
| `MAX_FRAGMENTS` | 6 | số vế tối đa, chặn chi phí encode |
| `FRAGMENT_TOP_K` | 5 | ứng viên xét cho mỗi vế |
| `MIN_TERM_FRAGMENT_LEN` | 5 | độ dài tối thiểu của một cụm bệnh |
| `MULTI_DIAGNOSIS_MIN_CONF` | 60,0 | ngưỡng nhận một vế là bệnh độc lập |

## 1.11 Giới hạn

- **Cache phụ thuộc mô hình và danh mục.** Đổi một trong hai phải mã hóa lại ~47 000
  thuật ngữ (15–40 phút trên CPU). Hệ thống có in cảnh báo trước khi chạy (dòng 378–382).
- **`ABBREVIATIONS` đóng băng.** Sửa bộ này buộc tính lại toàn bộ cache; viết tắt mới phải
  vào `QUERY_ABBREVIATIONS`.
- **Ràng buộc tuổi/giới chưa được dùng.** `ChangeJson.py` ghi `age_constraint`,
  `sex_constraint`, `can_be_primary` vào danh mục, nhưng `NLPEngine` không đọc — xem
  mục [4.7](#47-giới-hạn).
- **`_polarity_labels` khớp chuỗi con thô.** Nhãn khớp đầu tiên thắng, nên thứ tự trong
  `POLARITY_AXES` là một phần của tính đúng đắn, không phải sở thích trình bày.

---

# 2. `nlp/clinical_rules.py`

## 2.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 502 |
| Phụ thuộc | **không có** — thuần dữ liệu Python + 2 hàm |
| Được gọi bởi | `nlp/nlp_engine.py`, `backend/main.py`, `nlp/evaluate.py` |
| Xuất ra | 18 hằng số + `confidence_band()` + `verification_status_for()` |

## 2.2 Vì sao tách thành file riêng

Tri thức lâm sàng thay đổi theo chuyên môn; thuật toán thay đổi theo kỹ thuật. Tách hai
thứ ra để **sửa tri thức mà không đụng thuật toán** — bác sĩ bổ sung một alias không cần
đọc hiểu softmax, và ngược lại.

## 2.3 Bảy nhóm dữ liệu

### (1) Viết tắt — hai bộ tách biệt

| Bộ | Dòng | Phạm vi áp dụng | Sửa được không |
| --- | --- | --- | --- |
| `ABBREVIATIONS` | 31–45 | **Cả** truy vấn lẫn văn bản tham chiếu | **Không** — sửa là phải tính lại toàn bộ cache embedding |
| `QUERY_ABBREVIATIONS` | 49–92 | **Chỉ** câu truy vấn | Có — an toàn tuyệt đối |

Sự phân đôi này là một quyết định kiến trúc, không phải ngẫu nhiên: nó tạo ra một chỗ để
thêm tri thức mà chi phí bằng không.

`ABBREVIATIONS` gồm 14 mục lõi (`đtđ`, `tha`, `nmct`, `hpq`, `copd`, `gerd`, `ckd`, và các
biến thể `type 2`/`typ ii`/`t2` → `tuýp 2`).

`QUERY_ABBREVIATIONS` gồm 44 mục, chia ba loại:
- **Viết tắt lâm sàng**: `nmn`, `tbmmn`, `đqn`, `stm`, `stc`, `vpq`, `vgb`, `hctht`…
- **Chuẩn hóa từ đồng nghĩa**: `cao huyết áp`/`huyết áp cao` → `tăng huyết áp`;
  `tiểu đường` → `đái tháo đường`; `mạn` → `mãn tính`; `nguyên phát`/`tiên phát` → `vô căn`.
- **Từ hành chính**: `bn`, `cđ`, `td`, `gđ`, `bc`.

### (2) Tiền tố và hậu tố nhiễu (dòng 97–121)

`NOISE_PREFIXES` — 11 mẫu, áp dụng **lặp** tới khi ổn định. Đáng chú ý mẫu dòng 103:

```python
r"^bn (nam|nữ)?\s*\d*\s*(tuổi|t)?\s*[,\-]?\s*"
```

bắt được cả `"BN nam 54 tuổi,"` — cách viết cực kỳ phổ biến trong bệnh án Việt Nam.

`NOISE_SUFFIXES` — 7 mẫu, bóc thông tin điều trị/hành chính ở đuôi (`"đang điều trị..."`,
`"hẹn tái khám..."`, `"chuyển tuyến..."`). Chúng không giúp xác định mã bệnh nhưng **kéo
lệch vector ngữ nghĩa của cả câu**.

### (3) `STOPWORDS` (dòng 124–129)

38 từ, chỉ dùng khi tính điểm trùng lặp từ vựng (`_lexical_f1`), **không** dùng khi tính
embedding. Đây là điểm khác biệt so với NLP cổ điển: bỏ stopword trước khi encode sẽ làm
mất ngữ pháp mà SBERT dựa vào.

### (4) `BRIDGE_TERMS` — cầu nối từ vựng (dòng 137–153)

13 cặp `(cụm bác sĩ dùng, cụm chuẩn trong danh mục)`. Cụm chuẩn được **nối thêm**, không
thay thế.

Cặp quan trọng nhất, kèm số liệu ở dòng 141–142:

```python
("ung thư", "u ác"),
```

> Toàn bộ chương C (**553 mã**) dùng "u ác"; **không mã nào** ghi "ung thư". Đây là khoảng
> cách từ vựng lớn nhất giữa lời bác sĩ và danh mục Bộ Y tế.

Các cặp còn lại: `tuýp 2`→`không phụ thuộc insuline`, `tuýp 1`→`phụ thuộc insuline`,
`đường huyết`/`đường máu`→`glucose máu`, `u xơ tử cung`→`u cơ trơn tử cung`,
`nhiễm khuẩn huyết`→`nhiễm trùng huyết`, `viêm bể thận`→`viêm mô kẽ ống thận`,
`nghiện rượu`→`hội chứng nghiện`.

### (5) `POLARITY_AXES` — sáu trục đối lập (dòng 162–239)

| Trục | Phạm vi kích hoạt | Nhãn | Phạt | Thưởng |
| --- | --- | --- | --- | --- |
| `diabetes_type` | đái tháo đường, insulin | type2 / type1 | **0,18** | 0,04 |
| `hypertension_cause` | huyết áp | secondary / primary | 0,16 | 0,05 |
| `complication` | biến chứng, hôn mê, nhiễm toan | without / with | 0,14 | 0,04 |
| `acuity` | cấp, mãn, mạn | chronic / acute | 0,10 | 0,03 |
| `allergy` | dị ứng | non_allergic / allergic | 0,10 | 0,03 |
| `rupture` | phình, vỡ | unruptured / ruptured | **0,20** | 0,04 |

Ba điểm kỹ thuật:

**(a) Thứ tự nhãn là một phần của tính đúng đắn.** Nhãn khớp đầu tiên thắng, mà chuỗi
khẳng định là **chuỗi con** của chuỗi phủ định. Phải đặt `"không phụ thuộc insulin"` trước
`"phụ thuộc insulin"`, `"không vỡ"` trước `"vỡ"`. Đảo thứ tự là luật chạy ngược.

**(b) `scope` của trục `complication` phải bao gồm tên từng biến chứng cụ thể** (dòng
184–185): danh mục ghi *"(Có hôn mê)"*, *"(Có nhiễm toan ceton)"* chứ **không** ghi chữ
"biến chứng". Đây là một trong bốn khiếm khuyết phát hiện qua tập kiểm tra độc lập.

**(c) Trục `rupture` phạt nặng nhất (0,20)** vì phình động mạch **vỡ** là cấp cứu ngoại
khoa còn **chưa vỡ** là theo dõi định kỳ — cùng vị trí giải phẫu, hai hướng xử trí khác
hẳn nhau. Ca đo được ghi ngay trong comment dòng 222–226.

### (6) Mức chi tiết và nhãn nhóm cha (dòng 248–276)

```python
UNSPECIFIED_MARKERS = ["không đặc hiệu", "không phân loại", "không xác định",
                       "không rõ", "unspecified", "nos"]
OTHER_MARKERS       = ["khác", "other"]

UNSPECIFIED_BONUS   = 0.06    # bác sĩ không nêu thể bệnh -> mã .9 là đúng
UNSPECIFIED_PENALTY = 0.06    # bác sĩ có nêu thể bệnh   -> mã .9 làm mất thông tin
OTHER_PENALTY       = 0.04    # mã .8 chỉ dùng khi thể bệnh đã nêu mà không có mã riêng
```

`NEOPLASM_TERMS` (dòng 272–276) — 18 mẫu regex nhận diện từ khóa khối u. Dùng cho
`_is_group_label`: dấu hiệu là **tên mã không chứa từ khóa khối u nào trong khi tên nhóm
cha thì có**, tức nghĩa bệnh chỉ tồn tại ở nhóm cha. Toàn danh mục chỉ **27/889** mã chương
II rơi vào diện này.

### (7) `CHAPTER_GATES` — cổng chương (dòng 285–311)

| Chương | Nội dung | Từ khóa bắt buộc (trích) | Phạt |
| --- | --- | --- | --- |
| O | Thai sản | thai, sản, chuyển dạ, sau đẻ, hậu sản, sảy thai… | 0,20 |
| S, T | Chấn thương, ngộ độc | chấn thương, gãy, bỏng, ngộ độc, trật khớp, dị vật… | 0,18 |
| V–Y | Nguyên nhân ngoại sinh | tai nạn, ngã, đuối nước, hỏa hoạn, điện giật… | **0,22** |
| Z | Yếu tố sức khỏe | khám, tiêm chủng, sàng lọc, tư vấn, tái khám… | 0,12 |

### (8) `DIRECT_ALIASES` (dòng 321–436)

Khoảng **130 alias** đã kiểm chứng, chia 8 nhóm chuyên khoa (nội tiết, tim mạch, hô hấp,
tiêu hóa, thận tiết niệu, cơ xương khớp/thần kinh, nhiễm khuẩn/khác).

Mỗi alias hoạt động ở **hai** tầng cùng lúc: được mã hóa embedding bổ sung (tầng 2) **và**
cộng điểm trực tiếp cho mã đích (tầng 3). Mã đầu tiên trong danh sách là mã ưu tiên
(+0,15), các mã sau là phụ (+0,10).

Đây là "từ đồng nghĩa tiếng Việt" **thật sự** của hệ thống — khác với các `synonyms` do
`ChangeJson.py` sinh tự động, vốn chỉ là biến thể hình thức của chính tên bệnh.

### (9) `DATA_FIXES` (dòng 444–448)

Hiện có đúng **một** mục: file Excel nguồn ghi nhầm tên tiếng Anh của I10 thành *"Other
rheumatic heart diseases"* (vốn là của I09.8). Tên đúng theo WHO: *"Essential (primary)
hypertension"*.

Vá ở tầng ứng dụng thay vì sửa danh mục để **không phải sinh lại toàn bộ embedding cache**.

## 2.4 Chính sách an toàn lâm sàng

### `CONFIDENCE_POLICY` (dòng 456–460)

```python
{"auto_confirm": 85.0, "provisional": 60.0, "reject": 40.0}
```

### `confidence_band()` (dòng 463–469)

`high` ≥ 85 > `medium` ≥ 60 > `low`. Dùng chung cho backend và giao diện để hai nơi không
tự định nghĩa ngưỡng riêng rồi lệch nhau.

### `verification_status_for()` (dòng 472–502) — hàm quan trọng nhất file

| Độ tin cậy | `clinician_confirmed=False` | `=True` |
| --- | --- | --- |
| ≥ 85% | `provisional` | `confirmed` |
| 60–85% | `differential` | `confirmed` |
| < 60% | `unconfirmed` | `confirmed` |

**Máy không bao giờ được tự gán `confirmed`.** Lập luận đầy đủ nằm ở docstring dòng
476–494, và đây là đoạn nên trích nguyên văn vào báo cáo:

> Trong đặc tả HL7 FHIR, `confirmed` nghĩa là chẩn đoán **đã được xác nhận** — hàm ý có
> người đủ thẩm quyền đứng sau, chứ không phải thuật toán tự chấm mình 85 điểm.
>
> Bản trước gán `confirmed` ở mọi ca ≥ 85%. Đo trên tập kiểm tra độc lập thì trong nhóm đó
> **vẫn còn ca sai**, cao nhất là `"viêm kết mạc dị ứng"` ra H10.9 *"không đặc hiệu"* với
> **96,78%**. Những bản ghi ấy lên trục mang nhãn "đã xác nhận", bệnh viện khác đọc về
> không có cách nào biết chưa ai duyệt. Đó là **sai lệch thông tin y tế do chính khâu gắn
> nhãn tạo ra**, không phải do mô hình đoán sai — mô hình đoán sai là chuyện bình thường
> và chấp nhận được, miễn là bản ghi nói đúng sự thật về độ chắc chắn của nó.

Ba giá trị máy được phép dùng, theo đúng nghĩa FHIR: `provisional` (sơ bộ, đủ vững để làm
việc tiếp), `differential` (một trong nhiều khả năng), `unconfirmed` (chưa đủ căn cứ).

---

# 3. `nlp/evaluate.py`

## 3.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 189 |
| Kiểu | Script CLI (`python -m nlp.evaluate`) |
| Phụ thuộc | `nlp/nlp_engine.py`, `nlp/clinical_rules.py` |
| Dữ liệu | `nlp/data/eval_set.json` (110 ca), `eval_holdout.json` (51 ca) |

## 3.2 Định dạng một ca đánh giá

```json
{ "query": "đái tháo đường tuýp 2", "expected": ["E11", "E11.9"], "tag": "plain" }
```

`expected` là **danh sách** vì nhiều mức chi tiết có thể cùng đúng (E11 và E11.9). `tag`
phân nhóm để phân tích lỗi theo đặc thù đầu vào.

## 3.3 Bốn hàm

### `rank_of_hit()` (dòng 37–43)

Trả vị trí 1-based của mã đúng đầu tiên, 0 nếu trượt. So khớp sau khi `sanitize_icd10_code`
và `.upper()` ở **cả hai phía** — nhãn viết `"e11.9"` hay `"E11.9†"` đều khớp.

### `evaluate()` (dòng 46–106)

Chạy toàn bộ tập, đo bốn nhóm chỉ số:

```
Top-k = (số ca có 1 ≤ rank ≤ k) / tổng số ca        với k ∈ {1, 3, 5}

MRR   = (1/N) · Σ 1/rank_i        (rank_i = 0 → cộng 0)
```

MRR đo **cả thứ hạng** chứ không chỉ trúng/trượt: mã đúng ở hạng 2 (0,5 điểm) tốt hơn hạng
5 (0,2 điểm), trong khi Top-5 coi hai ca đó như nhau.

Độ trễ đo bằng `time.perf_counter()` quanh **riêng** lời gọi `engine.query()` — không tính
thời gian nạp mô hình. Báo cáo trung bình, p50, p95.

Hai bảng phân rã:

- `per_tag` — Top-1/3/5 theo từng nhóm đầu vào. Đây là bảng dùng để **tìm khiếm khuyết hệ
  thống**: nhóm nào yếu bất thường thì ở đó có một luật còn thiếu.
- `per_band` — tỷ lệ Top-1 đúng trong từng mức tin cậy (`high`/`medium`/`low`). Đây là phép
  kiểm **chính sách ngưỡng**: nếu nhóm `high` mà độ chính xác thấp thì ngưỡng 85% đang đặt
  sai, không phải mô hình yếu.

`errors` giữ đủ ngữ cảnh của mọi ca không đạt Top-1: câu hỏi, mã kỳ vọng, thứ hạng thực
tế, và top-3 thực nhận kèm độ tin cậy.

### `validate_dataset()` (dòng 109–117)

Cảnh báo các mã kỳ vọng **không tồn tại** trong danh mục. Chốt chặn chống một lỗi rất khó
thấy: **test sai vì nhãn sai**. Nếu nhãn ghi một mã không có trong danh mục, ca đó vĩnh
viễn tính là trượt và người đọc kết luận nhầm rằng mô hình yếu.

### `print_report()` (dòng 120–154)

In bảng cố định cột. In kèm ngưỡng của `CONFIDENCE_POLICY` ngay dưới bảng hiệu chuẩn để
người đọc đối chiếu được ngay.

## 3.4 Giao diện dòng lệnh (dòng 157–186)

```powershell
.venv\Scripts\python -m nlp.evaluate
.venv\Scripts\python -m nlp.evaluate --dataset nlp/data/eval_holdout.json --show-errors
.venv\Scripts\python -m nlp.evaluate --json ket_qua.json
```

Dòng 164–168 ép `stdout` sang UTF-8 trên Windows — không có bước này, mọi tên bệnh tiếng
Việt in ra console đều lỗi.

## 3.5 Lưu ý bắt buộc khi trích dẫn số liệu

Tập phát triển (`eval_set.json`, 110 ca) **đã được dùng để hiệu chỉnh** luật, alias và
trọng số. Con số 100% trên đó **không** phản ánh năng lực tổng quát hóa — nó chỉ chứng tỏ
các luật đã được cài đúng.

Con số đưa vào báo cáo khoa học phải là kết quả trên **tập kiểm tra độc lập**
(`eval_holdout.json`, 51 ca): **Top-1 70,6% / Top-3 88,2% / Top-5 92,2% / MRR 0,796**.

Và ngay cả tập này cũng **không còn hoàn toàn "sạch"**: lần đo đầu cho Top-1 62,7%, sau
khi phân tích lỗi và khắc phục bốn khiếm khuyết hệ thống thì đạt 70,6%. Vì đã nhìn vào lỗi
của tập này nên lần báo cáo tiếp theo cần soạn một tập độc lập mới.

---

# 4. `ChangeJson.py`

## 4.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 564 |
| Kiểu | Script CLI chạy **ngoại tuyến**, không phải một phần của runtime |
| Phụ thuộc | `pandas`, `openpyxl` |
| Đầu vào | `dataICD10.xlsx` — 14 sheet của Bộ Y tế |
| Đầu ra | `nlp/data/icd10_db.json` — 12 137 mã |

## 4.2 Bài toán

File Excel danh mục ICD-10 của Bộ Y tế có 14 sheet, mỗi sheet một bố cục. Bản trước chỉ
đọc sheet đầu tiên (`ICD10`, 12 219 dòng) và bỏ qua 13 sheet còn lại.

Riêng sheet `E - ICD10 Mã bệnh chính` chứa **13 026 mã**, trong đó hơn **bốn nghìn** mã —
phần lớn là **nhánh mở rộng năm ký tự đặc thù Việt Nam** (B37.00, C02.10) — không hề có
trong danh mục sinh ra. Đây đúng là nhóm mã Thông tư 06/2026/TT-BYT quy định phải mã hóa
được.

Các sheet phụ lục còn mang ràng buộc lâm sàng bị bỏ trắng: A1 ghép cặp †/*, A2 đánh dấu mã
không được làm bệnh chính, A3.x giới hạn tuổi, A4.x giới hạn giới tính.

## 4.3 Bản đồ sheet

| Hằng số | Sheet | Vai trò |
| --- | --- | --- |
| `SHEET_MASTER` | `ICD10` | Danh mục gốc — sheet **duy nhất** có đủ chương/nhóm |
| `SHEETS_CODES` | `E - ICD10 Mã bệnh chính`, `E1 - Không ghép DRG`, `A2 …` | Bổ sung mã |
| `SHEETS_AGE` | A3.1 → A3.10 (7 sheet) | Ràng buộc tuổi |
| `SHEETS_SEX` | A4.1, A4.2 | Ràng buộc giới tính |
| `SHEET_DAGGER` | `A1` | Cặp bệnh nguyên † ↔ biểu hiện * |
| `SHEET_NO_PRIMARY` | `A2 …` | Mã không được làm bệnh chính |

## 4.4 Luồng xử lý (dòng 525–560)

```
load_master()          # sheet ICD10, có chương/nhóm
   → merge_code_sheets()   # 3 sheet phụ lục, nạp 4.000+ mã bị bỏ sót
   → merge_supplement()    # danh mục vá thủ công (JSON riêng)
   → apply_constraints()   # CHẠY SAU CÙNG
   → sắp xếp theo mã → ghi JSON
```

`apply_constraints()` chạy **sau cùng** có chủ đích (dòng 544): để mã nạp từ phụ lục và
danh mục vá cũng được gắn ràng buộc.

## 4.5 Các hàm tiện ích

### `nfc()` (dòng 95–105) — quan trọng nhất

```python
return unicodedata.normalize("NFC", str(text))
```

File Excel nguồn **trộn hai cách mã hóa**: phần lớn dạng dựng sẵn (NFC) nhưng **197 dòng**
ở dạng tổ hợp (NFD). Chuẩn hóa ngay từ khâu nhập liệu để lỗi "kho ng" (xem mục 1.5) không
quay lại. Hàm này được gọi ở **mọi** chỗ đọc chuỗi từ Excel.

### `to_dotted()` (dòng 124–137)

Sheet phụ lục lưu mã dạng liền (`A066`, `B3700`), sheet chính lưu dạng có chấm (`A06.6`).
Quy tắc: ba ký tự đầu là danh mục, phần còn lại là danh mục con.

```
A066  → A06.6        B3700 → B37.00        I10 → I10 (giữ nguyên)
```

### `read_sheet()` (dòng 245–255) + `find_code_column()` (dòng 234–242)

Bố cục các sheet phụ lục **không đồng nhất**: dòng tiêu đề nằm ở vị trí khác nhau, tên cột
khác nhau. Hai hàm này dò tự động:

- Thử `header` ở dòng 2, 1, 0, 3 (thứ tự theo tần suất thực tế).
- Chọn cột chứa **nhiều mã ICD hợp lệ nhất** làm cột mã — không dựa vào tên cột.

Đây là cách xử lý bền với dữ liệu đầu vào không có chuẩn: thay vì ghi cứng tên cột cho 14
sheet rồi hỏng khi Bộ Y tế phát hành bản mới, hệ thống *đo* xem cột nào chứa mã.

### `build_synonyms()` (dòng 179–210)

Sinh 4 loại biến thể tìm kiếm:

| Loại | Ví dụ |
| --- | --- |
| Không dấu | `"viêm phổi"` → `"viem phoi"` |
| Mã rút gọn | `A06.6` → `"a066"` |
| Bỏ chú thích ngoặc | `"Bệnh X (Chưa có biến chứng)"` → `"bệnh x"` |
| Thuật ngữ thông dụng | `"u ác của phổi"` → `"ung thư của phổi"` (+ dạng không dấu) |

Docstring dòng 182–185 nêu vấn đề của bản trước: chỉ tạo `[name_vi.lower()]` — tức bản sao
viết thường của chính tên bệnh, **không thêm một chút thông tin nào** nhưng nhân ba số
vector phải mã hóa.

### `CLINICAL_TERM_VARIANTS` + `build_term_variants()` (dòng 159–176)

Đây là bản đối chiếu ở tầng **dữ liệu** của `BRIDGE_TERMS` ở tầng **truy vấn**. Số liệu ghi
ở comment dòng 145–157:

> Danh mục Bộ Y tế đặt tên khối u ác theo lối văn bản: **426 mã** mang cụm "u ác", **40 mã**
> "u ác tính". Trong khi đó chỉ **81 mã** có chữ "ung thư", và chúng dồn vào vài nhóm hẹp —
> C22 (gan), C46 (Kaposi), D00 (tại chỗ).
>
> Hậu quả đo được trên tập kiểm tra độc lập: `"ung thư phổi"`, `"ung thư dạ dày"`,
> `"ung thư đại tràng"` đều trả về **C22.0 "Ung thư biểu mô tế bào gan"**, vì đó là một
> trong số ít tên có sẵn chữ "ung thư" để bám vào. Ba mã đúng C34, C16, C18 lại mang tên
> "U ác của..." nên không khớp được từ nào.

Hai chốt kỹ thuật:
- **Thứ tự trong danh sách có ý nghĩa**: `"u ác tính"` phải đứng trước `"u ác"`, nếu không
  luật ngắn cắt trước và sinh ra `"ung thư tính"`.
- **Dừng ngay sau cụm khớp đầu tiên** (`return`, dòng 175): để chạy hết danh sách thì
  `"u ác tính của da"` vừa sinh `"ung thư của da"` (đúng) vừa sinh `"ung thư tính của da"`
  (rác).

### `add_alias()` (dòng 213–231)

Hai dòng cùng mã sau khi gỡ † thường là cặp dagger/asterisk — bệnh nguyên và biểu hiện —
**mang tên khác nhau**. Bản trước `continue` thẳng, mất luôn từ khóa tìm kiếm của dòng sau.
Ở đây tên của dòng trùng được nhập vào `synonyms` kèm cả dạng không dấu.

### `inherit_meta()` (dòng 308–321)

Sheet phụ lục chỉ có mã và tên bệnh, không có cột chương. Mã con luôn cùng chương với mã
cha ba ký tự (B37.00 cùng chương với B37) nên suy ra được — bỏ trống sẽ làm hỏng phần cổng
chương của `NLPEngine`.

## 4.6 Chính sách xử lý mã thiếu tên — quyết định đáng nêu

Sheet `E` liệt kê **4 242 mã mà cột tên bệnh bỏ trắng** ở cả hai file Excel nguồn, trong đó
**4 066 mã** thuộc nhánh mở rộng năm ký tự.

Script **cố ý không nạp** những mã này, và **cố ý không mượn tên của mã cha**. Lý do ở
comment dòng 76–91:

> Mã không có tên thì không embedding được. Cũng không được mượn tên của mã cha: B37.00 đến
> B37.09 sẽ cùng mang tên *"Viêm miệng do candida"*, vừa không phân biệt được với nhau vừa
> cạnh tranh với chính B37.0 hợp lệ — **kéo độ chính xác xuống thay vì lên**.

Thay vào đó có `nlp/data/icd10_supplement.json` — nguồn **duy nhất** được phép thêm mã
ngoài Excel, mỗi mục **bắt buộc ghi `source`** để về sau kiểm chứng được lấy tên từ đâu.

File này **hiện đang để rỗng, có lý do** — và đây là một ví dụ tốt về kỷ luật dữ liệu để
đưa vào báo cáo:

> Mã 3 ký tự duy nhất thiếu tên là A91, thoạt nhìn thì đáng bổ sung *"Sốt xuất huyết
> Dengue"*. Nhưng danh mục **đã có sẵn A97** mang đúng tên đó, kèm A97.0/A97.1/A97.2 theo
> phân loại mức độ mới của WHO — tức Việt Nam dùng A97 cho mặt bệnh này, còn A91 chỉ là vết
> tích trong phụ lục. Thêm A91 vào sẽ tạo hai mã **trùng tên hoàn toàn**, xẻ đôi điểm khớp
> và làm hỏng cả hai. Tra được tên thật của mã nào thì thêm mã đó, đừng suy đoán.

## 4.7 Giới hạn

- **Bốn trường ràng buộc được ghi ra nhưng chưa ai đọc.** `apply_constraints()` gắn
  `age_constraint` (1 337 mã), `sex_constraint` (869 mã), `can_be_primary` (853 mã),
  `asterisk_codes` (374 cặp) vào `meta`, nhưng `NLPEngine` **không đọc** trường nào trong
  bốn trường đó — nó tự dựng liên kết †/* bằng cách bóc regex từ **tên bệnh**
  (`_build_dagger_links`), và không hề lọc theo tuổi/giới.

  Nghĩa là câu *"Gateway loại được ứng viên sai về mặt lâm sàng"* ở docstring dòng 17–18
  hiện **chưa đúng với mã nguồn**. Dữ liệu đã sẵn sàng, phần dùng nó thì chưa viết. Khi
  viết báo cáo, hoặc mô tả đây là *dữ liệu đã chuẩn bị cho hướng phát triển*, hoặc cài
  thêm bộ lọc — đó là một luật ngắn, cộng vào `query()` bên cạnh `_chapter_delta`.

- **Chạy lại script đổi khóa cache embedding.** Lần khởi động kế tiếp phải mã hóa lại toàn
  bộ danh mục (15–40 phút trên CPU). Script có in cảnh báo (dòng 559–560) và có cờ
  `--dry-run` để xem số liệu trước khi ghi thật.

---

# 5. `backend/main.py`

## 5.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 689 |
| Khung | FastAPI + Uvicorn, cổng 8000 |
| Phụ thuộc trong | `backend/fhir_helper.py`, `nlp/nlp_engine.py`, `nlp/clinical_rules.py` |
| Endpoint | 8 |
| Vai trò | **Điều phối** — nối khối NLP với khối liên thông, không tự cài thuật toán nào |

## 5.2 Cấu hình (dòng 44–73)

| Biến môi trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `SMIG_FHIR_SERVER_URL` | `http://127.0.0.1:8090/fhir` | Địa chỉ EMR Cloud |
| `SMIG_FHIR_TIMEOUT` | `8` | Thời gian chờ gọi FHIR (giây) |
| `SMIG_FACILITY_CODE` | `BV-DEMO-01` | **Mã cơ sở của bản Gateway này** |
| `SMIG_FACILITY_NAME` | `Bệnh viện Demo SMIG` | Tên cơ sở |
| `SMIG_ALLOW_CLIENT_FACILITY` | `0` (tắt) | Cho HIS tự khai mã cơ sở |
| `SMIG_ALLOWED_ORIGINS` | 4 origin localhost | Danh sách CORS |

CORS dùng **danh sách nguồn cụ thể**, không dùng `"*"`. Comment dòng 100–101: `"*"` kèm
`allow_credentials=True` là cấu hình **không hợp lệ** theo đặc tả CORS và bị trình duyệt từ
chối.

## 5.3 Vòng đời — `lifespan` (dòng 79–90)

Nạp `NLPEngine` **một lần** khi khởi động (mô hình chiếm vài GB RAM, nạp mỗi request là bất
khả thi). Bắt ngoại lệ rộng và **giữ server sống**:

```python
except Exception as exc:
    engine_error = str(exc)
    nlp_engine = None
```

Lý do: nếu để server chết thì người vận hành chỉ thấy "không kết nối được", không biết vì
sao. Giữ sống thì `/health` báo `status: degraded` kèm `engine_error` cụ thể, và mọi
endpoint NLP trả **503** với thông báo rõ ràng qua `_require_engine()` (dòng 216–222).

## 5.4 Hai cơ chế cốt lõi

### `resolve_facility()` (dòng 225–254)

Chốt mã cơ sở dùng cho một yêu cầu.

| Trường hợp | Kết quả |
| --- | --- |
| Bên gọi không khai | Lấy cấu hình Gateway — đường đi của mọi HIS cũ, hành vi không đổi |
| Khai đúng mã của Gateway | Hợp lệ |
| Khai **mã khác**, cờ **tắt** | **403** |
| Khai mã khác, cờ **bật** | Chấp nhận mã đã khai |

Hai quyết định thiết kế:

**(a) Mặc định tắt là mặc định đúng cho triển khai thật.** Mã cơ sở là **danh tính** của
bên ghi hồ sơ, phải do phía máy chủ xác lập (ở đây là biến môi trường; thực tế là chứng
thư/khóa API). Để bên gọi tự khai thì bệnh viện B khai mình là bệnh viện A được ngay.

**(b) Từ chối thẳng chứ không âm thầm lấy mã của Gateway** (dòng 236–239). Sai cấu hình mà
vẫn chạy thì hồ sơ của bệnh viện B nằm trên trục dưới tên bệnh viện A, và **không ai phát
hiện ra**. Lỗi 403 to tiếng tốt hơn dữ liệu sai lặng lẽ.

Trạng thái cờ được phơi ra `/health` (`allow_client_facility`) để nhìn là biết Gateway đang
ở chế độ thử nghiệm.

### `stable_condition_key()` (dòng 257–284)

```
{mã cơ sở}-{định danh bệnh nhân}-{mã ICD-10}-{ngày khám}
```

Mỗi thành phần chặn **một** kiểu trộn dữ liệu:

| Thành phần | Thiếu nó thì |
| --- | --- |
| Mã cơ sở | Chẩn đoán của BV B ghi đè lên BV A khi hai nơi trùng mã bệnh án |
| Định danh bệnh nhân | (khóa vô nghĩa) — ưu tiên CCCD/BHYT, xem `patient_match_key` |
| Mã ICD-10 | Hai bệnh khác nhau của cùng người đè lên nhau |
| Ngày khám | Lần tái khám đè lên chẩn đoán cũ, **mất lịch sử điều trị** |

Khóa đi vào `Condition.identifier` và là thứ conditional update tìm theo → đồng bộ lại cùng
một chẩn đoán trong cùng ngày vẫn cho đúng **một** tài nguyên.

## 5.5 Tám endpoint

| Method + đường dẫn | Khối | Việc làm |
| --- | --- | --- |
| `GET /health` | — | Trạng thái mô hình, chính sách tin cậy, mã cơ sở, cờ đa cơ sở |
| `POST /api/standardize` | NLP | Chuẩn hóa câu → danh sách mã ICD-10 |
| `POST /api/fhir/condition` | Liên thông | Sinh FHIR Condition từ một mã |
| `POST /api/fhir/sync` | Liên thông | Đẩy Organization + Patient + Condition lên EMR |
| `GET /api/fhir/sync` | Liên thông | Đọc Condition mới nhất trên trục |
| `GET /api/fhir/condition/{id}` | Liên thông | Đọc lại một Condition để đối chiếu |
| `DELETE /api/fhir/condition/{id}` | Liên thông | Gỡ một Condition (bác sĩ sửa mã) |
| `DELETE /api/fhir/sync` | Liên thông | Dọn dữ liệu demo |

### `POST /api/standardize` (dòng 342–377)

Gọi ba hàm của engine rồi bọc kết quả:

```python
normalized_query = engine.expand_query(request.query)
entities         = engine.extract_entities_regex(request.query)
composite        = engine.query_composite(request.query, top_k=request.top_k)
```

Sau đó gắn hai trường vào **mọi** bản ghi dự đoán (dòng 360–367):

```python
pred["suggested_verification_status"] = verification_status_for(pred["confidence"])
pred["requires_review"] = confidence_band(pred["confidence"]) != "high"
```

Vòng lặp duyệt **cả** `predictions` phẳng **lẫn** `diagnoses[].predictions` — ứng viên hạng
thấp của từng vế chỉ nằm ở nhánh thứ hai, mà Pydantic bắt buộc mọi bản ghi có đủ trường.

**Điểm tinh tế đáng nêu** (comment dòng 362–366): `requires_review` suy từ **mức tin cậy**,
không suy từ **trạng thái FHIR**. Hai việc này vốn khác nhau — `requires_review` quyết định
có được tự động đẩy lên trục hay không, còn `verificationStatus` là bản ghi **tự khai** độ
chắc chắn của nó. Từ khi máy thôi tự gán `confirmed`, nếu vẫn so với chuỗi đó thì mọi ca
đều thành "cần duyệt" và luồng tự động liên thông **chết hẳn**.

### `POST /api/fhir/condition` (dòng 380–427)

Hai chốt chặn trước khi sinh tài nguyên:

1. **Định dạng mã** — `is_valid_icd10()` → **422** nếu sai.
2. **Ngưỡng tin cậy** — dưới `reject` (40%) → **422**, kèm thông báo *"Cần bác sĩ chọn lại
   mã ICD-10"*.

Chốt thứ hai là chính sách an toàn lâm sàng ở tầng API: **không sinh tài nguyên FHIR cho
một suy đoán dưới 40%**, kể cả khi bên gọi yêu cầu.

Điểm tinh tế ở dòng 401–405: khóa Condition bám theo **định danh đã chọn** (CCCD/BHYT/mã
bệnh án), không bám theo mã bệnh án thô. Nếu không, cùng một người sẽ có hai khóa khác nhau
tùy lần đó HIS có gửi kèm CCCD hay không.

### `POST /api/fhir/sync` (dòng 445–590) — endpoint phức tạp nhất

Chấp nhận hai dạng thân yêu cầu: `{...Condition...}` hoặc
`{"condition": {...}, "patient": {...}}`.

**Bốn bước kiểm tra đầu vào:**

1. `resourceType == "Condition"` → nếu không, 422.
2. Có `identifier` hệ `SYSTEM_CONDITION_KEY` → nếu không, 422 kèm hướng dẫn gọi
   `/api/fhir/condition` trước.
3. Có `subject.identifier` (đủ system + value) → nếu không, 422.
4. **Đối chiếu định danh** (dòng 494–506) — nếu thân yêu cầu kèm khối `patient` mà định
   danh suy ra được **mâu thuẫn** với `Condition.subject.identifier` thì **từ chối thẳng**.

Bước 4 là quyết định thiết kế đáng nêu: *"chọn nhầm sẽ tạo Patient mới và để Condition mồ
côi"*. Một nguồn sự thật duy nhất cho định danh bệnh nhân, mâu thuẫn thì báo lỗi chứ không
âm thầm chọn một bên.

**Thứ tự đồng bộ bắt buộc — Organization → Patient → Condition** (dòng 508–509): HAPI bật
`enforce_referential_integrity_on_write` nên tài nguyên được trỏ tới phải tồn tại trước.

**Conditional update** cho cả ba (dòng 294–297):

```
PUT /{Type}?identifier={system}|{value}
```

| Số bản khớp | Hành vi máy chủ |
| --- | --- |
| 0 | Tạo mới, **server tự cấp id** |
| 1 | Cập nhật đúng bản đó |
| > 1 | Trả **412**, không sửa gì |

Nhờ vậy thao tác **idempotent** mà client **không tự đặt id**. Comment dòng 174–177 của
`fhir_helper.py` giải thích vì sao điều này quan trọng về mặt an ninh: client tự đặt id
cộng với `PUT` theo id là một **lỗ ghi đè** — biết mã bệnh án và mã ICD-10 là dựng được id
của người khác.

**Ghép tham chiếu thật sau khi có id** (dòng 551–560): thay `subject.reference` bằng
`Patient/{id thật}` và `extension[source-facility].valueReference.reference` bằng
`Organization/{id thật}`, **giữ nguyên** phần `identifier` để tài nguyên vẫn tự mô tả được
nó nói về ai và do đâu ghi.

`_resource_id_from()` (dòng 300–316) đọc id do máy chủ cấp: ưu tiên thân phản hồi, dự phòng
bằng header `Location`/`Content-Location` — vì máy chủ FHIR **được phép** trả thân rỗng.

### `DELETE /api/fhir/condition/{id}` (dòng 610–642)

Chi tiết đáng nêu ở dòng 628–630:

```python
if response.status_code not in (404, 410):
    response.raise_for_status()
```

Bản ghi đã không còn trên trục thì mục tiêu **coi như đã đạt**. Thao tác gỡ phải lặp lại
được mà không báo lỗi, vì HIS có thể gọi lại sau khi mất mạng. Đây là tính **idempotent**
ở chiều xóa.

## 5.6 Giới hạn

- **Không có xác thực.** Mọi endpoint đều mở. Ba dịch vụ chỉ nghe trên `127.0.0.1` nên rủi
  ro giới hạn ở máy demo, nhưng triển khai thật bắt buộc OAuth2 / SMART on FHIR.
- **Không sinh `Encounter`.** Chỉ có Condition, Patient, Organization.
- **Namespace định danh là của riêng đề tài**, chưa ánh xạ sang NamingSystem của VN Core.

---

# 6. `backend/fhir_helper.py`

## 6.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 349 |
| Phụ thuộc | **không có** — chỉ thư viện chuẩn Python |
| Được gọi bởi | `backend/main.py`, `tests/` |
| Vai trò | Tầng chuẩn HL7 FHIR R4 — định nghĩa hệ định danh và dựng tài nguyên |

Không phụ thuộc gì là một tính chất có chủ đích: file này thuần túy là ánh xạ dữ liệu →
JSON FHIR, kiểm thử được mà không cần mô hình NLP, không cần mạng, không cần Docker.

## 6.2 Hệ định danh (dòng 20–55)

```python
SMIG_NAMESPACE = "https://smig.nckh.vn/fhir"
```

| Hằng số | Giá trị | Ý nghĩa |
| --- | --- | --- |
| `ICD10_SYSTEM` | `http://hl7.org/fhir/sid/icd-10` | Hệ mã **quốc tế chuẩn** |
| `SYSTEM_CCCD` | `.../identifier/cccd` | Số căn cước — toàn quốc |
| `SYSTEM_BHYT` | `.../identifier/bhyt` | Thẻ BHYT — toàn quốc |
| `mrn_system(code)` | `.../identifier/mrn/{mã cơ sở}` | Mã bệnh án — **một cơ sở** |
| `SYSTEM_FACILITY` | `.../identifier/co-so-kcb` | Mã cơ sở KCB |
| `SYSTEM_CONDITION_KEY` | `.../condition-key` | Khóa nghiệp vụ của Condition |
| `EXT_CONFIDENCE` | `.../StructureDefinition/nlp-confidence-score` | **Độ tin cậy mô hình** |
| `EXT_ENGINE` | `.../StructureDefinition/nlp-engine` | **Phiên bản mô hình** |
| `EXT_SOURCE_FACILITY` | `.../StructureDefinition/source-facility` | Cơ sở đã ghi chẩn đoán |

**Vì sao dùng namespace riêng cho extension** (comment dòng 18–19): theo quy tắc đặt tên
của HL7, URL của extension phải trỏ về **tổ chức định nghĩa** nó. Dùng `hl7.org` cho
extension tự định nghĩa là sai quy tắc.

### `mrn_system()` (dòng 38–47) — chi tiết quan trọng

```python
return f"{SMIG_NAMESPACE}/identifier/mrn/{to_fhir_id(code) if code else 'khong-ro-co-so'}"
```

Khi thiếu mã cơ sở, hàm lùi về nhãn **cố định** `khong-ro-co-so` chứ **không** để
`to_fhir_id` sinh chuỗi ngẫu nhiên. Lý do: system đổi mỗi lần gọi sẽ làm conditional update
không bao giờ khớp lại bản ghi cũ — **mỗi lần đồng bộ đẻ thêm một bệnh nhân mới**.

## 6.3 `patient_match_key()` (dòng 278–302) — cơ chế đồng nhất bệnh nhân

Trả `(system, value)` theo thứ tự ưu tiên **phản ánh phạm vi hiệu lực** của từng loại định
danh:

| Ưu tiên | Định danh | Phạm vi | Vì sao ở vị trí này |
| --- | --- | --- | --- |
| 1 | Số CCCD | Toàn quốc | Cùng một người khám hai nơi vẫn quy về **một** hồ sơ |
| 2 | Thẻ BHYT | Toàn quốc | Dùng khi chưa có CCCD |
| 3 | Mã bệnh án **+ mã cơ sở** | Một bệnh viện | Phương án cuối; thiếu mã cơ sở thì `"BN001"` của hai viện bị trộn thành một người |

Đây là câu trả lời cho câu hỏi khó nhất của liên thông đa cơ sở: **"hai hồ sơ này có phải
cùng một người không?"** Bản đầu dùng mã bệnh án làm khóa và mắc đúng lỗi kinh điển — thực
nghiệm tái hiện được: sau hai lần đồng bộ, trên trục chỉ còn **một** bệnh nhân mang họ tên,
giới tính và ngày sinh của **người thứ hai**.

Vai trò của `identifier.system` ở đây đúng như đặc tả FHIR giao cho nó: chỉ ra **ai** cấp
định danh đó.

## 6.4 Bốn hàm dựng tài nguyên

### `build_fhir_condition_resource()` (dòng 110–226)

Nhận 14 tham số, trả `dict` JSON của Condition R4.

**Kiểm tra đầu vào** (dòng 148–155): mã sau khi làm sạch không rỗng; `clinical_status` và
`verification_status` phải nằm trong tập hợp lệ của R4 — sai thì `ValueError`, được
`main.py` chuyển thành 422.

**Cấu trúc tài nguyên sinh ra:**

```jsonc
{
  "resourceType": "Condition",
  "clinicalStatus":     { "coding": [{ "system": "…/condition-clinical",   "code": "active" }] },
  "verificationStatus": { "coding": [{ "system": "…/condition-ver-status", "code": "provisional" }] },
  "category":           [{ "coding": [{ "code": "encounter-diagnosis" }] }],
  "code": {
    "coding": [{ "system": "http://hl7.org/fhir/sid/icd-10", "code": "E11.9", "display": "…" }],
    "text": "<nguyên văn bác sĩ nhập>"          // ← giữ nguyên lời bác sĩ
  },
  "subject": {
    "identifier": { "system": "…/cccd", "value": "079…" },   // tham chiếu LOGIC
    "display": "<họ tên>"
  },
  "recordedDate": "2026-08-16T…Z",
  "identifier": [{ "system": "…/condition-key", "value": "BV-A-001-079…-E11-9-2026-08-16" }],
  "meta": { "tag": [{ "system": "…/co-so-kcb", "code": "BV-A-001", "display": "…" }] },
  "extension": [
    { "url": "…/nlp-confidence-score", "valueDecimal": 0.934 },
    { "url": "…/nlp-engine",           "valueString": "my_medical_nlp_model" },
    { "url": "…/source-facility",      "valueReference": { … } }
  ]
}
```

Bốn quyết định thiết kế:

**(a) `code.text` giữ nguyên văn bác sĩ nhập.** Mã là bản dịch máy; nguyên văn là bằng
chứng. Bên nhận đọc được cả hai.

**(b) Không đặt `"id"`** (comment dòng 174–177). Client tự đặt id + `PUT` theo id = lỗ ghi
đè. Định danh nghiệp vụ nằm ở `identifier`, và đồng bộ đi qua conditional update nên **vẫn**
idempotent.

**(c) Tham chiếu logic theo identifier.** Lúc dựng tài nguyên chưa biết id mà EMR sẽ cấp.
Khâu đồng bộ bổ sung `reference` trỏ tới id thật và **giữ nguyên** `identifier` — nhờ vậy
chỉ cần đọc Condition là đủ biết nó nói về ai, không phải tin vào một khối dữ liệu gửi kèm
bên ngoài.

**(d) Cơ sở ghi nhận nằm ở hai chỗ bổ trợ nhau:**

| Nơi | Truy vấn được | Đọc được |
| --- | --- | --- |
| `meta.tag` | `GET /Condition?_tag={system}\|{mã cơ sở}` | — |
| `extension[source-facility]` | — | có `display` là tên bệnh viện, nhìn JSON là thấy |

Dùng extension cho cơ sở vì **R4 không cho `Condition.recorder` trỏ tới `Organization`**
(comment dòng 50–52).

### `build_fhir_patient_resource()` (dòng 305–349)

Bệnh nhân mang **nhiều** định danh cùng lúc, xếp theo thứ tự:

```
[CCCD nếu có]  →  [mã bệnh án + assigner = mã cơ sở]  →  [BHYT nếu có]
```

Giới tính ánh xạ qua `GENDER_MAP` (dòng 63–67) nhận cả `"nam"`/`"male"`/`"m"` và
`"nữ"`/`"nu"`/`"female"`/`"f"`. Ngày sinh chỉ nhận đúng dạng `YYYY-MM-DD`, sai thì **bỏ
trường** chứ không sinh giá trị rác.

Docstring dòng 316–318 ghi lỗi bản trước: bỏ qua giới tính và ngày sinh **dù HIS đã có**,
khiến hồ sơ liên thông lên EMR bị mất dữ liệu hành chính.

### `build_fhir_organization_resource()` (dòng 229–243)

Đơn giản nhất: khóa theo mã cơ sở, `type` = `prov` (Healthcare Provider).

## 6.5 Ba hàm đọc ngược

| Hàm | Dòng | Đọc gì | Chi tiết |
| --- | --- | --- | --- |
| `facility_of()` | 246–258 | Cơ sở đã ghi chẩn đoán | Thử `meta.tag` trước, rồi `extension` |
| `subject_identifier_of()` | 261–265 | `(system, value)` của bệnh nhân | Trả `None` nếu thiếu một trong hai |
| `condition_key_of()` | 268–275 | Khóa nghiệp vụ | **Bỏ qua identifier hệ khác** |

`condition_key_of` lọc theo `system` chứ không lấy `identifier[0]`: tài nguyên đọc về từ
trục có thể mang thêm identifier do hệ thống khác gắn vào.

## 6.6 Ba hàm tiện ích

**`sanitize_icd10_code()` (dòng 78–80)** — gỡ `†`, `*`, `‡`. Danh mục Bộ Y tế dùng `†` để
đánh dấu mã bệnh nguyên trong hệ dagger/asterisk. Ký tự này **không thuộc mã ICD-10** và
sẽ bị máy chủ FHIR **từ chối**.

**`is_valid_icd10()` (dòng 83–85)** — `[A-Z]\d{2}(\.\d{1,2})?`, chấp nhận cả nhánh mở rộng
5 ký tự của Việt Nam.

**`to_fhir_id()` (dòng 88–97)** — FHIR R4 chỉ cho `[A-Za-z0-9.-]`, tối đa 64 ký tự. Ký tự
lạ → `-`, gộp dấu gạch liên tiếp, cắt hai đầu. Rỗng thì sinh `unknown-{8 hex}`.

## 6.7 Đóng góp riêng của đề tài

Hai extension `EXT_CONFIDENCE` và `EXT_ENGINE` làm cho **độ tin cậy của mô hình đi kèm bản
ghi lên trục**. Bên nhận biết được mã này do máy suy ra ở mức chắc chắn nào và bằng phiên
bản mô hình nào — điều mà một `Condition` tiêu chuẩn **không** mang theo.

Đây là chỗ đáng chính thức hóa thành một profile FHIR riêng: VN Core có 52 profile nhưng
không có cái nào cho *"chẩn đoán do AI sinh, kèm độ tin cậy, chờ người duyệt"*.

---

# 7. `hospital_his/server.py`

## 7.1 Nhận dạng

| Thuộc tính | Giá trị |
| --- | --- |
| Số dòng | 994 |
| Khung | FastAPI + SQLite, cổng 8085 |
| Vai trò | HIS mô phỏng — **bên gửi** trong kịch bản liên thông |
| Phụ thuộc | Chỉ gọi Gateway qua HTTP; **không** import gì từ `backend/` hay `nlp/` |

Việc không import gì từ hai thư mục kia là có chủ đích: nó chứng minh HIS là một hệ thống
**độc lập**, đúng như một phần mềm bệnh viện thật.

## 7.2 Lược đồ cơ sở dữ liệu (dòng 119–185)

**Bảng `patients`** — hồ sơ hành chính + chẩn đoán chính:

```
id (PK), name, gender, birth_date, clinical_note,
icd10_code, icd10_display, fhir_condition_id, confidence_score,
verification_status, sync_status, sync_time,
citizen_id, insurance_card          ← thêm bằng migration
```

**Bảng `patient_conditions`** — danh sách chẩn đoán đầy đủ:

```
PRIMARY KEY (patient_id, icd10_code)
+ icd10_display, fragment, confidence_score, verification_status,
  fhir_condition_id, status, message, sync_time, clinical_status
```

**Vì sao hai bảng.** Một dòng chẩn đoán có thể chứa nhiều bệnh (`"sỏi bàng quang, suy thận
cấp"`), mỗi bệnh là một FHIR Condition riêng. Các cột `icd10_*` trên bảng `patients` chỉ
giữ được **một** mã, nên chúng được giữ lại làm **chẩn đoán chính** (tương thích phần hiển
thị cũ), còn danh sách đầy đủ nằm ở bảng thứ hai.

**Migration tại chỗ** (dòng 163–181): đọc `PRAGMA table_info` rồi `ALTER TABLE ADD COLUMN`
cho ba cột thêm sau (`clinical_status`, `citizen_id`, `insurance_card`). Cơ sở dữ liệu tạo
từ phiên bản trước vẫn dùng được, không mất dữ liệu.

**`db_cursor()` (dòng 101–116)** — context manager luôn đóng kết nối, kể cả khi ném ngoại
lệ. Bản trước gọi `conn.close()` thủ công ở từng nhánh nên `HTTPException` ném giữa chừng
làm kết nối bị bỏ ngỏ.

## 7.3 Cấu hình đa bệnh viện (dòng 37–56)

```python
DB_PATH = os.getenv("SMIG_HIS_DB") or ".../his_db.sqlite"
FACILITY_CODE = os.getenv("SMIG_FACILITY_CODE", "BV-DEMO-01")
```

Comment dòng 39–42 nêu một cạm bẫy demo: chạy hai bản HIS trên cùng một máy để trình diễn
liên thông thì **mỗi bản phải có tệp SQLite riêng**. Dùng chung một tệp thì hai "bệnh viện"
nhìn thấy y nguyên danh sách bệnh nhân của nhau — hỏng hẳn kịch bản.

**`_to_fhir_id()` (dòng 60–71)** sao chép **nguyên xi** logic của
`fhir_helper.to_fhir_id()`. Đây là trùng lặp **có chủ đích** (HIS không được import từ
Gateway), và docstring giải thích hậu quả nếu hai bên lệch nhau: với mã sạch như
`"BV-A-001"` thì vẫn trùng, nhưng mã có dấu gạch dưới hay khoảng trắng sẽ cho hai chuỗi
khác nhau — và **tra cứu bệnh nhân trên EMR im lặng trả về rỗng chứ không báo lỗi gì**.

## 7.4 Tra cứu bệnh nhân — `GET /api/patients` (dòng 244–401)

Hai chế độ:

**(a) Không có `search_id`** → trả toàn bộ bệnh nhân nội viện, kèm danh sách chẩn đoán, cờ
`is_local = True`.

**(b) Có `search_id`** → tra hai tầng:

```
1. SQLite cục bộ (theo mã bệnh án)
      ↓ không thấy
2. EMR Cloud — thử LẦN LƯỢT ba loại định danh:
      FHIR_MRN_SYSTEM  →  FHIR_CCCD_SYSTEM  →  FHIR_BHYT_SYSTEM
```

**Đây là cơ chế làm cho kịch bản chuyển tuyến chạy được.** Comment dòng 267–270:

> Mã bệnh án chỉ có nghĩa trong **nội bộ** viện này, nên với bệnh nhân chuyển từ nơi khác
> tới thì nó luôn trượt — bác sĩ chỉ có CCCD hoặc thẻ BHYT trong tay. Hỏi mỗi namespace mã
> bệnh án là ca chuyển tuyến **không bao giờ tra ra bệnh sử, đúng vào lúc cần bệnh sử nhất**.

Hồ sơ đọc từ trục mang `is_local = False`. Giao diện dựa vào cờ này để hiện dạng **chỉ đọc**
thay vì gọi API sửa rồi nhận 404 — hồ sơ chỉ đang *đọc* từ trục, chưa tiếp nhận vào viện
này nên không có bản ghi cục bộ để sửa.

Mỗi chẩn đoán đọc về mang thêm ba trường suy ra từ `meta.tag` (dòng 353–362):
`facility_code`, `facility_name`, và `la_ngoai_vien` — cờ để giao diện hiện huy hiệu tên
bệnh viện khác và khóa nút sửa.

## 7.5 Luồng liên thông — `_sync_patient_record()` (dòng 648–773)

```
1. Đọc hồ sơ từ SQLite
2. _fetch_diagnoses()      → gọi Gateway /api/standardize
3. VỚI TỪNG chẩn đoán:
      requires_review?  → "Needs Review", KHÔNG đẩy
      ngược lại         → _push_condition()  → "Synced" / "Failed"
4. Tổng hợp trạng thái, lưu SQLite, dựng thông báo
```

### `_fetch_diagnoses()` (dòng 529–583)

Đọc `diagnoses` từ Gateway; nếu Gateway là bản cũ chưa có trường này thì lùi về
`predictions` và coi cả câu là một chẩn đoán. **Tương thích ngược có chủ đích.**

Khử trùng mã (dòng 563–566): hai vế cho ra cùng một mã thì chỉ liên thông một lần — EMR
không nên nhận hai Condition trùng mã cho cùng một bệnh nhân.

### Chốt chặn an toàn lâm sàng xét cho **từng** mã (dòng 686–698)

Đây là điểm nghiệp vụ quan trọng nhất của file:

> Mã dưới ngưỡng dừng lại chờ bác sĩ duyệt, **nhưng không chặn** những mã đã đủ tin cậy
> trong cùng câu.

Ví dụ `"ĐTĐ tuýp 2 kèm tăng huyết áp"` cho E11.9 (93,4%) và I10 (71,5%): E11.9 lên trục
ngay, I10 dừng chờ duyệt. Nếu chặn cả câu thì bệnh nhân mất luôn chẩn đoán đã chắc chắn.

### Tổng hợp trạng thái (dòng 718–757)

| Điều kiện | Trạng thái hồ sơ |
| --- | --- |
| Có mã `Failed` | `Failed` |
| Không lỗi nhưng có mã chờ duyệt | `Needs Review` |
| Tất cả đã đẩy | `Synced` |

**Trường hợp đặc biệt** (dòng 722–728): không mã nào đi được **vì lỗi hạ tầng** → ném
502/503. Mục đích là để giao diện phân biệt được **"hệ thống hỏng"** với **"chờ bác sĩ
duyệt"** — hai tình huống cần hai hành động khác hẳn nhau.

### `_push_condition()` (dòng 586–645)

Gọi hai endpoint Gateway liên tiếp: `/api/fhir/condition` rồi `/api/fhir/sync`.

Điểm tinh tế ở dòng 608–611: `verification_status` **chỉ** được đặt khi bác sĩ tự quyết.
Luồng tự động để **trống** cho Gateway suy ra:

> Gửi lại giá trị HIS đang giữ nghe thì tương đương, nhưng nếu Gateway là bản cũ chưa trả
> `suggested_verification_status`, HIS sẽ gửi `"unconfirmed"` và **ghi đè mất kết luận của
> chính Gateway**.

Trả về **id do EMR Cloud xác nhận**, không phải id tự đoán (dòng 644–645) → truy vết được
thật.

## 7.6 Sửa chẩn đoán — `PUT /conditions/{icd10_code}` (dòng 829–928)

Bài toán: mô hình NLP suy sai mã. Trước đây hồ sơ chỉ **thêm** được chẩn đoán, nên mã sai
nằm lại vĩnh viễn trong bệnh án **và trên trục**; bác sĩ chỉ còn cách xóa cả hồ sơ rồi nhập
lại từ đầu.

**Thứ tự thao tác là bắt buộc** (docstring dòng 838–841):

```
1. Đẩy bản ghi ĐÚNG lên trục          ← trước
2. Gỡ bản mang mã SAI                 ← sau
```

Vì mã ICD-10 nằm trong khóa nghiệp vụ, đổi mã **không** cập nhật được bản cũ mà sinh tài
nguyên mới. Làm ngược thứ tự thì **có lúc hồ sơ trên trục không còn chẩn đoán nào**.

**Chốt chống tự xóa mất bản ghi mới** (dòng 880–885):

```python
warning = (_retire_on_emr(old_condition_id)
           if old_condition_id and old_condition_id != new_condition_id else None)
```

Sửa mà **giữ nguyên mã** trong cùng ngày khám thì khóa nghiệp vụ không đổi, conditional
update trả lại **đúng tài nguyên vừa cập nhật** — gỡ nó đi là xóa mất bản ghi mới.

**Bác sĩ tự chọn mã → 100% và `confirmed`** (comment dòng 859–862):

> Con số này không còn là điểm tin cậy của mô hình nữa mà là **quyết định chuyên môn**. Ghi
> 100% và `confirmed` để phần sau không đem nó ra so với ngưỡng tự động rồi bắt duyệt lại
> chính người vừa duyệt. Đây cũng là cách bác sĩ **giải phóng** một mã đang "Chờ duyệt".

**Giữ nguyên vị trí trong danh sách** (dòng 888–908): xóa dòng cũ rồi chèn lại với **đúng
`rowid` vừa xóa**. Khóa chính là `(patient_id, icd10_code)` nên đổi mã là đổi khóa, cập
nhật tại chỗ sẽ để lại dòng mang mã cũ. Không giữ `rowid` thì chẩn đoán vừa sửa **nhảy
xuống cuối danh sách ngay dưới tay bác sĩ**.

## 7.7 Hai hàm bảo toàn tính nhất quán

### `_retire_on_emr()` (dòng 467–483) — cố ý không ném ngoại lệ

```python
except requests.RequestException as exc:
    return f"Bệnh án cục bộ đã cập nhật, nhưng KHÔNG gỡ được bản ghi cũ {condition_id}…"
```

Lý do: bản ghi mới **đã lên trục rồi** mới tới lượt gỡ bản cũ. Hỏng ở bước này mà dựng
ngược cả thao tác thì bệnh án cục bộ và trục lại lệch nhau **thêm**. Thay vào đó lỗi được
trả ngược lên giao diện dưới dạng `warning` để bác sĩ biết còn bản ghi thừa.

Đây là một quyết định về **mô hình nhất quán**: chấp nhận trạng thái không hoàn hảo nhưng
**hiển thị nó ra**, thay vì cố khôi phục nguyên tử trên một hệ phân tán không hỗ trợ giao
dịch.

### `_refresh_primary_diagnosis()` (dòng 486–526)

Tính lại chẩn đoán chính và trạng thái liên thông từ bảng `patient_conditions`.

**Trạng thái xấu nhất thắng** (dòng 511–517): một mã hỏng là cả hồ sơ chưa liên thông trọn
vẹn. Báo `"Đã liên thông"` khi còn mã chờ duyệt là **che mất việc bác sĩ phải làm**.

Bảng rỗng → xóa hết trường chẩn đoán chính và đặt lại `sync_status = 'Unsynced'`. Không có
bước này thì bệnh án còn trỏ vào mã vừa bị bỏ đi.

## 7.8 Bảng endpoint

| Method + đường dẫn | Việc làm |
| --- | --- |
| `GET /api/config` | Cấu hình cho giao diện (địa chỉ Gateway, mã cơ sở) |
| `GET /api/patients` | Danh sách nội viện, hoặc tra một hồ sơ (cục bộ → EMR) |
| `POST /api/patients` | Tạo hồ sơ mới |
| `DELETE /api/patients/{id}` | Xóa hồ sơ + dọn bảng chẩn đoán |
| `POST /api/sync/{id}` | Liên thông lại toàn bộ hồ sơ (**thay thế** chẩn đoán cũ) |
| `POST /api/patients/{id}/diagnosis` | Chẩn đoán thêm (**giữ nguyên** chẩn đoán cũ) |
| `PUT /api/patients/{id}/conditions/{code}` | Sửa một chẩn đoán đã ghi nhận |
| `DELETE /api/patients/{id}/conditions/{code}` | Gỡ một chẩn đoán khỏi hồ sơ **và** khỏi trục |
| `POST /api/reset` | Cài lại cơ sở dữ liệu |

Khác biệt giữa `POST /api/sync` và `POST /diagnosis` nằm ở cờ `replace_existing` truyền
vào `_save_conditions()` (dòng 436–464) — đây là hai luồng nghiệp vụ khác nhau: **đồng bộ
lại** một hồ sơ đã sửa phải bỏ đi mã không còn đúng; **tái khám phát hiện thêm bệnh** thì
bệnh cũ vẫn phải còn.

`DELETE /conditions/{code}` có ghi chú nghiệp vụ đáng nêu (dòng 936–938):

> Bệnh đã điều trị xong thì nên **sửa trạng thái** sang `resolved` qua `PUT` chứ đừng xóa:
> xóa là mất lịch sử điều trị, còn `resolved` giữ lại bệnh sử mà vẫn báo đúng là bệnh không
> còn hoạt động.

## 7.9 Giới hạn và điểm cần dọn

- **`FALLBACK_AUTO_CONFIRM = 80.0` (dòng 82) lệch với `auto_confirm = 85.0`** của
  `clinical_rules.py`. Về mặt **hành vi** thì không sai — quyết định thật do Gateway trả về
  qua `requires_review`, giá trị 80 chỉ là dự phòng. Nhưng nó bị **in vào thông báo** cho
  người dùng (dòng 693–694, 750): *"chưa đạt ngưỡng tự động (80.0%)"* — trong khi ngưỡng
  thật là 85%. Người dùng đọc được một con số không đúng.

- **Độ tin cậy đọc từ EMR bị ghi cứng thành 100%** (dòng 366–367). Khi tra hồ sơ từ trục,
  mọi Condition đều được gán `confidence_score = 100.0` và `verification_status =
  "confirmed"` bất kể tài nguyên thật mang gì. HIS **không đọc** extension
  `nlp-confidence-score` — tức chính đóng góp riêng của đề tài (mục 6.7) đang không được
  phía nhận sử dụng. Đây là chỗ đáng sửa trước khi bảo vệ, vì nó là một hàng ba dòng.

- **Mệnh đề `except sqlite3.Error` bị lặp** (dòng 398–401): khối thứ hai không bao giờ chạy
  tới. Vô hại nhưng nên xóa.

- **Không có xác thực.** Bất kỳ ai gọi được cổng 8085 đều đọc ghi được toàn bộ bệnh án.

---

# Phụ lục — Bảng tra nhanh

## Đường đi đầy đủ của một ca khám

```
[1] Bác sĩ gõ: "bn bị đtđ tuýp 2 kèm cao huyết áp"
        │                                     hospital_his/frontend/app.js
        ▼
[2] HIS gửi POST /api/standardize             hospital_his/server.py:538
        │
        ▼
[3] NLPEngine.query_composite()               nlp/nlp_engine.py:1149
    ├─ Tầng 1: expand_query()                 nlp/clinical_rules.py
    ├─ Tầng 2: cosine trên 12.137 mã          nlp/data/embeddings_*.npy
    ├─ Tầng 3: tái xếp hạng theo luật lâm sàng
    └─ Tầng 4: _calibrate()
        │
        ▼  → 2 chẩn đoán: E11.9 (93,4%) và I10 (71,5%)
        │
════════╪══════ RANH GIỚI HAI KHỐI ══════════════════════════════
        │
[4] Xét ngưỡng cho TỪNG mã                    hospital_his/server.py:686
    ├─ E11.9 ≥ ngưỡng → liên thông tiếp
    └─ I10  < ngưỡng → DỪNG, chờ bác sĩ duyệt
        │
        ▼
[5] POST /api/fhir/condition                  backend/main.py:380
    └─ build_fhir_condition_resource()        backend/fhir_helper.py:110
        │
        ▼
[6] POST /api/fhir/sync                       backend/main.py:445
    └─ conditional update: Organization → Patient → Condition
        │
        ▼
[7] EMR Cloud (HAPI FHIR R4)                  docker-compose.yml
        │
        ▼
[8] Bệnh viện khác tra bằng CCCD → thấy chẩn đoán này
```

## Chín quyết định thiết kế nên nêu trong báo cáo

| # | Quyết định | File | Vì sao |
| --- | --- | --- | --- |
| 1 | Tái xếp hạng bằng luật lâm sàng, không chỉ cosine | `nlp_engine.py` | Cosine không phân biệt được E10/E11, I10/I15 |
| 2 | Cầu nối từ vựng "ung thư" → "u ác" | `clinical_rules.py` | 553 mã chương C dùng "u ác", 0 mã dùng "ung thư" |
| 3 | Hiệu chuẩn hai thành phần + sàn theo độ phủ | `nlp_engine.py` | Cosine 0,991 mà chỉ được 56% là phản trực giác |
| 4 | Máy không bao giờ tự gán `confirmed` | `clinical_rules.py` | Ca sai vẫn tồn tại ở mức ≥85% (96,78%) |
| 5 | Chuẩn hóa NFC trước khi lọc ký tự | `nlp_engine.py`, `ChangeJson.py` | 197 mã lưu dạng NFD, "không" bị cắt thành "kho ng" |
| 6 | Không mượn tên mã cha cho mã thiếu tên | `ChangeJson.py` | 4 066 mã sẽ trùng tên, kéo độ chính xác **xuống** |
| 7 | Khóa đồng nhất bệnh nhân theo phạm vi định danh | `fhir_helper.py` | "BN001" của hai viện là hai người khác nhau |
| 8 | Conditional update, client không đặt id | `main.py` | Id đoán được + `PUT` theo id = lỗ ghi đè |
| 9 | Chốt an toàn xét cho **từng** mã, không cho cả câu | `server.py` | Chặn cả câu là mất luôn chẩn đoán đã chắc chắn |

## Bốn việc nên dọn trước khi bảo vệ

| Việc | File | Mức |
| --- | --- | --- |
| HIS đọc `EXT_CONFIDENCE` thay vì ghi cứng 100% | `server.py:366` | **Nên sửa** — đây là đóng góp riêng của đề tài |
| Thống nhất `FALLBACK_AUTO_CONFIRM` với `CONFIDENCE_POLICY` | `server.py:82` | Nên sửa — số in ra cho người dùng đang sai |
| Dùng `age_constraint` / `sex_constraint` trong xếp hạng | `nlp_engine.py` | Tùy chọn — hoặc mô tả là hướng phát triển |
| Xóa `except sqlite3.Error` lặp | `server.py:400` | Nhỏ |
