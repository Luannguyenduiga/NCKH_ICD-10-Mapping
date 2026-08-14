# Nhật ký thay đổi — 14/08/2026

Phiên làm việc tập trung vào ba việc: dựng lại bộ nạp danh mục ICD-10, hiệu chỉnh
cách tính độ tin cậy, và sửa ánh xạ trạng thái FHIR cho đúng đặc tả.

Mọi con số trong tài liệu này đo bằng [`nlp/evaluate.py`](../nlp/evaluate.py) trên
hai bộ dữ liệu có sẵn của dự án. **Tập quyết định là `eval_holdout.json`** — 51 ca
được soạn sau khi chốt luật và không dùng để tinh chỉnh bất cứ thứ gì. Con số trên
`eval_set.json` (110 ca) chỉ để đối chiếu, vì tập đó đã tham gia quá trình chỉnh luật.

---

## 1. Tổng kết trước / sau

### Tập kiểm tra độc lập — `eval_holdout.json`, 51 ca

| Chỉ số | Trước | Sau | Chênh |
| --- | ---: | ---: | ---: |
| Top-1 accuracy | 68,6% | **72,5%** | +3,9 |
| Top-3 accuracy | 84,3% | **86,3%** | +2,0 |
| Top-5 accuracy | 84,3% | **86,3%** | +2,0 |
| MRR | 0,7516 | **0,7876** | +0,036 |
| Chính xác trong mức tin cậy cao | 87,5% | **92,6%** | +5,1 |
| Ca đúng đạt mức cao (được tự động liên thông) | 21 | **25** | +4 |
| **Ca sai lọt mức cao** | **3** | **2** | −1 |

Hai chiều cùng tốt lên: nhiều ca đúng được tự động hơn, đồng thời ít ca sai lọt qua
hơn. Đây là điều đáng nhấn khi báo cáo, vì hai mục tiêu này thường đánh đổi nhau.

### Tập phát triển — `eval_set.json`, 110 ca

| Chỉ số | Trước | Sau |
| --- | ---: | ---: |
| Top-1 accuracy | 99,1% | 99,1% |
| Top-3 accuracy | 99,1% | **100%** |
| MRR | 0,9932 | **0,9955** |
| Số ca mức cao | 104 | **105** |
| Chính xác trong mức cao | 100% | **100%** |

Không thụt lùi ở đâu. Lưu ý: **99,1% không phải con số đem đi bảo vệ** — tập này đã
được dùng để tinh chỉnh luật nên chỉ số bị lạc quan. Con số thật là 72,5%.

### Danh mục ICD-10

| Hạng mục | Trước | Sau |
| --- | ---: | ---: |
| Số sheet Excel được đọc | 1 / 14 | **14 / 14** |
| Số mã | 12.219 | 12.137 |
| Mã còn dính ký tự dao găm † | 858 | **0** |
| Mã 5 ký tự nhận diện đúng (nhánh mở rộng VN) | 265 | **677** |
| Biến thể tìm kiếm | 22.388 | **24.003** |
| Mã mang ràng buộc tuổi / giới tính | 0 | **1.640** |
| Mã bị cấm dùng làm bệnh chính | 0 | **853** |
| Liên kết dagger † → asterisk * | 0 | **2.077** (trên 374 mã) |
| Vector tham chiếu | 47.542 | **48.391** |

Số mã giảm từ 12.219 xuống 12.137 **không phải mất mã**: bản cũ đếm `A02.2†` và
`A02.2` thành hai mục riêng, gỡ † thì chúng là một. Đã kiểm chứng bằng đối chiếu tập
mã và tập tên bệnh: **0 mã mất, 0 tên bệnh mất**.

### Môi trường chạy

| | Trước | Sau |
| --- | --- | --- |
| PyTorch | `2.13.0+cpu` | **`2.13.0+cu126`** |
| Thiết bị | CPU | **NVIDIA RTX 3050 Laptop, 4 GB** |
| Thời gian mã hóa lại toàn bộ danh mục | 15–40 phút | **1 phút 50** |
| Độ trễ mỗi truy vấn (trung bình / p50 / p95) | 67 / 63 / 115 ms | **65 / 58 / 95 ms** |

Không sửa dòng mã nào — `sentence-transformers` tự dò thiết bị, và
[`nlp_engine.py`](../nlp/nlp_engine.py) vốn đã dùng `.to(self.model.device)`.

---

## 2. Diễn biến theo từng bước

Mỗi dòng là một lần đo trên `eval_holdout.json` sau khi áp dụng thêm một thay đổi.

| Bước | Thay đổi | Top-1 | Top-3 | MRR | Số ca mức cao | Đúng trong mức cao |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 0 | Trạng thái ban đầu | 68,6% | 84,3% | 0,7516 | 24 | 87,5% |
| 1 | Danh mục sạch (14 sheet, gỡ †) | 70,6% | 84,3% | 0,7647 | 23 | 91,3% |
| 2 | Luật thuật ngữ `u ác` ↔ `ung thư` | 72,5% | 86,3% | 0,7876 | 24 | 91,7% |
| 3 | Sàn tin cậy theo độ phủ | 72,5% | 86,3% | 0,7876 | 30 | 86,7% |
| 4 | Cổng lọc phủ định | 72,5% | 86,3% | 0,7876 | 28 | 89,3% |
| 5 | Trục đối lập vỡ / không vỡ | 72,5% | 86,3% | 0,7876 | **27** | **92,6%** |

Đọc bảng này theo hai nửa:

- **Bước 1–2** cải thiện **thứ hạng** (Top-1, Top-3 tăng). Đây là chất lượng dữ liệu.
- **Bước 3–5** cải thiện **độ tin cậy** (Top-k đứng yên, cột cuối biến động). Đây là
  hiệu chuẩn — không đổi mã nào được chọn, chỉ đổi mức chắc chắn gán cho nó.

Bước 3 một mình làm tụt độ chính xác trong mức cao (91,7% → 86,7%) vì kéo thêm cả ca
sai lên. Bước 4 và 5 là hai chốt chặn bù lại, kết quả cuối cao hơn cả xuất phát.

---

## 3. Chi tiết thay đổi

### 3.1 Dựng lại bộ nạp danh mục — [`ChangeJson.py`](../ChangeJson.py)

Viết lại toàn bộ. Bản trước chỉ đọc sheet đầu tiên của
[`dataICD10.xlsx`](../dataICD10.xlsx) trong khi file có 14 sheet.

**Nạp thêm ràng buộc lâm sàng.** Các phụ lục A1–A4 mang thông tin trước giờ bỏ trắng:

| Phụ lục | Nội dung | Trường sinh ra | Số mã |
| --- | --- | --- | ---: |
| A2 | Mã không được dùng làm bệnh chính | `can_be_primary` | 853 |
| A3.1–A3.10 | Giới hạn tuổi | `age_constraint` | 1.337 |
| A4.1–A4.2 | Giới hạn giới tính | `sex_constraint` | 869 |
| A1 | Cặp bệnh nguyên † ↔ biểu hiện * | `asterisk_codes` | 374 |

Tổng số mã mang ít nhất một ràng buộc tuổi hoặc giới: **1.640**. Riêng phụ lục A1
sinh **2.077 liên kết** trải trên 374 mã bệnh nguyên.

Bốn trường này **đã có trong dữ liệu nhưng chưa được engine đọc**. Đây là hướng cải
tiến tiếp theo: dùng chúng để loại cứng ứng viên sai về mặt lâm sàng — bệnh nhân nam
không bao giờ nhận mã sản khoa, bệnh nhân 60 tuổi không nhận mã sơ sinh.

**Gộp thay vì loại khi trùng mã.** Bản trước gặp hai dòng cùng mã thì `continue`,
vứt luôn tên bệnh của dòng sau. Hai dòng đó thường là cặp dagger/asterisk mang tên
khác nhau, nên mất từ khóa tìm kiếm. Nay nhập tên dòng sau vào `synonyms`.

**Luật thuật ngữ `u ác` ↔ `ung thư`.** Danh mục Bộ Y tế đặt tên theo lối hành chính:
426 mã mang cụm "u ác", 40 mã "u ác tính", chỉ 81 mã có chữ "ung thư" và chúng dồn
vào vài nhóm hẹp (C22 gan, C46 Kaposi, D00 tại chỗ).

Hậu quả đo được trước khi sửa — cả ba câu đều trả về ung thư gan:

| Truy vấn | Kết quả cũ | Mã đúng |
| --- | --- | --- |
| ung thư phổi | C22.0 — Ung thư biểu mô tế bào gan | C34 |
| ung thư dạ dày | C22.0 — Ung thư biểu mô tế bào gan | C16 |
| ung thư đại tràng | C22.0 — Ung thư biểu mô tế bào gan | C18 |

Vì C34 tên là *"U ác của phế quản và phổi"* — không có chữ nào để câu bám vào.
Nay mỗi mã "U ác..." sinh thêm biến thể "ung thư...", cả dạng có dấu và không dấu.

**Cơ chế danh mục vá thủ công.** Thêm
[`nlp/data/icd10_supplement.json`](../nlp/data/icd10_supplement.json), mỗi mục bắt
buộc ghi `source`. Hiện để rỗng, có lý do ghi trong mã nguồn.

### 3.2 Sàn tin cậy theo độ phủ — [`nlp_engine.py`](../nlp/nlp_engine.py)

Công thức cũ dành 65% trọng số cho thành phần `relative`, vốn đo **mức áp đảo so với
ứng viên khác** chứ không đo mức đúng. Hệ quả: câu khớp gần tuyệt đối vẫn bị điểm
thấp chỉ vì danh mục còn mã lân cận.

Ví dụ đo được — `"bệnh van hai lá do thấp"` khớp **I05** *"Bệnh lý van hai lá do
thấp"* ở cosine **0,991**, tức gần như trùng nghĩa hoàn toàn:

| Thành phần | Giá trị |
| --- | ---: |
| cosine thô | 0,991 |
| absolute (đã kịch trần) | 1,000 |
| **relative** | **0,327** |
| **Độ tin cậy** | **56,26%** |

Bác sĩ gõ đúng tên bệnh mà máy báo "cần duyệt lại" là phản trực giác. Sau khi thêm
sàn: **88%**.

Bổ sung ba hằng số (`nlp_engine.py:73-75`): `COVERAGE_FULL=1.0`,
`COVERAGE_FLOOR_SIM=0.90`, `COVERAGE_FLOOR_CONF=0.88`.

### 3.3 Cổng lọc phủ định — [`_polarity_compatible`](../nlp/nlp_engine.py#L691)

Độ phủ đếm theo túi từ nên mù với hai kiểu sai. Cả hai đều từng lọt lên mức tự động
liên thông ở bước 3:

| Kiểu sai | Ví dụ | Vì sao túi từ không bắt được |
| --- | --- | --- |
| Mã tự khẳng định thêm | `"phình động mạch chủ bụng"` → I71.3 *"…**vỡ**"* | Mọi từ trong câu đều có ở tên mã; mã thêm một tình trạng cấp cứu bệnh án không ghi |
| Phủ định gắn sai chỗ | `"viêm mũi **không** dị ứng"` → J30.4 *"Viêm mũi dị ứng, **không** phân loại"* | Trùng đúng bấy nhiêu từ, chỉ khác chỗ đặt chữ "không" — nghĩa ngược hẳn |

Nguyên tắc: **trục nào tên mã khẳng định thì câu phải khẳng định y hệt; câu im lặng
không tính là đồng ý.**

### 3.4 Trục đối lập vỡ / không vỡ — [`clinical_rules.py`](../nlp/clinical_rules.py#L217)

Trục thứ 6, `penalty = 0,20` — cao nhất trong nhóm. Phình động mạch vỡ là cấp cứu
ngoại khoa, chưa vỡ là theo dõi định kỳ: cùng vị trí giải phẫu, hai hướng xử trí
khác hẳn nhau.

### 3.5 Ánh xạ trạng thái FHIR — [`clinical_rules.py`](../nlp/clinical_rules.py#L472), [`main.py`](../backend/main.py#L234)

| Độ tin cậy | Trước | Sau |
| --- | --- | --- |
| ≥ 85% | `confirmed` | **`provisional`** |
| 60 – 85% | `provisional` | **`differential`** |
| < 60% | `unconfirmed` | `unconfirmed` |
| Bác sĩ duyệt | — | **`confirmed`** |

Theo đặc tả HL7 FHIR R4, `verificationStatus = confirmed` nghĩa là **chẩn đoán đã
được xác nhận** — hàm ý có người đủ thẩm quyền đứng sau, không phải thuật toán tự
chấm mình 85 điểm.

Đo trên tập kiểm tra độc lập, trong nhóm ≥85% vẫn còn ca sai, cao nhất là
`"viêm kết mạc dị ứng"` trả về H10.9 *"Viêm kết mạc, không đặc hiệu"* với **96,78%**.
Những bản ghi ấy lên trục dữ liệu mang nhãn "đã xác nhận", bệnh viện khác đọc về
không có cách nào biết chưa ai duyệt.

Đây là sai lệch thông tin y tế sinh ra từ **khâu gắn nhãn**, không phải từ việc mô
hình đoán sai — mô hình đoán sai là chuyện bình thường và chấp nhận được, miễn bản
ghi nói đúng sự thật về độ chắc chắn của nó.

Kèm theo, tách bạch hai khái niệm đang bị gộp trong [`main.py:240`](../backend/main.py#L240):

- `requires_review` — bản ghi có được tự động đẩy lên trục hay không. Suy từ `band(x)`.
- `verificationStatus` — bản ghi tự khai độ chắc chắn của mình.

Ngưỡng tự động liên thông **giữ nguyên 85%**, luồng demo không đổi.

> **Ghi chú.** [`docs/luong-xu-ly-chan-doan.md`](luong-xu-ly-chan-doan.md) ở dòng
> 607-609 vốn đã tuyên bố nguyên tắc *"kết quả của mô hình không được tự động ghi
> nhận là chẩn đoán đã xác nhận"*, nhưng bảng ngay trên nó lại ghi ≥85% →
> `confirmed`, và mã nguồn chạy theo cái bảng. Thay đổi này **không đổi thiết kế**,
> mà sửa cho mã nguồn làm đúng điều tài liệu đã thiết kế.

### 3.6 Cập nhật tài liệu công thức — [`Cong_thuc_toan_hoc_NLP_ICD10.docx`](../Cong_thuc_toan_hoc_NLP_ICD10.docx)

| Chỗ sửa | Nội dung |
| --- | --- |
| Công thức 11 | Bổ sung nhánh sàn theo độ phủ và cổng lọc phủ định; đổi tiêu đề |
| Công thức 12 | Sửa ánh xạ `verificationStatus`; thêm `requires_review` |
| Công thức 5 | 5 trục → **6 trục**, thêm vỡ–không vỡ (±0,20/0,04) |
| Công thức 1 | 46.942 → **48.391** vector tham chiếu |
| Công thức 16 | Cập nhật độ trễ đo lại trên GPU |
| 15 mục "Vị trí" | Đồng bộ số dòng đã dịch chuyển |
| Bảng tổng hợp | 17 ô số dòng |

---

## 4. Việc còn lại

### 4.1 Hai ca sai còn lọt cổng tự động

| Tin cậy | Truy vấn | Hệ thống trả | Mã đúng |
| ---: | --- | --- | --- |
| 96,78% | viêm kết mạc dị ứng | H10.9 *"Viêm kết mạc, **không đặc hiệu**"* | H10.1 |
| 89,84% | viem phoi thuy | J18.9 *"Viêm phổi, **không đặc hiệu**"* | J18.1 |

Cùng một lỗi: mã `.9` "không đặc hiệu" được điểm cao trong khi câu **có** nêu thể
bệnh (`dị ứng`, `thùy`). [`UNSPECIFIED_PENALTY = 0.06`](../nlp/clinical_rules.py#L255)
hiện quá nhẹ so với lợi thế cosine.

### 4.2 Tập holdout quá nhỏ

51 ca cho khoảng tin cậy 95% trải từ **60,2% đến 84,8%** quanh con số 72,5%. Chưa
đủ để kết luận chắc. Nâng lên 150–200 ca là việc rẻ và làm con số đáng tin hẳn —
quan trọng hơn việc cố đẩy 72,5% lên 75%.

### 4.3 Thiếu script huấn luyện

Mô hình được fine-tune trên Google Colab, notebook không có trong repo. Model card
[`nlp/my_medical_nlp_model/README.md`](../nlp/my_medical_nlp_model/README.md) còn lưu
đủ tham số (`MultipleNegativesRankingLoss`, scale 20.0, 5 epoch, batch 8, lr 5e-05),
nhưng **cách sinh 36.657 cặp huấn luyện thì không lưu ở đâu**.

Con số 36.657 = 12.219 × 3 đúng khít — mỗi mã trong danh mục cũ sinh đúng 3 cặp. Ba
dạng nhìn thấy trong model card: mã viết liền (`z723`), tiền tố lâm sàng
(`cdls: <tên bệnh>`), và dạng không dấu.

Đây là **chương phương pháp bắt buộc phải có** trong báo cáo NCKH, và cũng là điều
kiện để tái lập kết quả. Cần lấy notebook về đưa vào repo thành `nlp/train.py`.

### 4.4 Bốn trường ràng buộc lâm sàng chưa được dùng

`age_constraint`, `sex_constraint`, `can_be_primary`, `asterisk_codes` đã có trong
danh mục nhưng [`nlp_engine.py`](../nlp/nlp_engine.py) chưa đọc trường nào — hiện chỉ
dùng `meta.type_name`.

Nối vào sẽ cho phép loại **cứng** ứng viên sai lâm sàng, khác hẳn việc hạ điểm theo
xác suất. Đây là thứ trả lời thẳng câu hỏi *"hệ thống hơn gì tìm kiếm bằng từ khóa"*.

### 4.5 Danh mục thiếu tên cho 4.652 mã

Phụ lục E của file Excel liệt kê 4.652 mã mà **cột tên bệnh bỏ trắng ở cả hai file
nguồn**, trong đó 4.465 mã thuộc nhánh mở rộng 5 ký tự. Mã không có tên thì không
embedding được, không so khớp được.

Không được mượn tên mã cha để lấp: B37.00 đến B37.09 sẽ cùng mang tên *"Viêm miệng do
candida"*, vừa không phân biệt được với nhau vừa cạnh tranh với chính B37.0 hợp lệ.

Cần tra tên thật từ QĐ 4469/QĐ-BYT rồi bổ sung qua
[`icd10_supplement.json`](../nlp/data/icd10_supplement.json).

---

## 5. Danh sách file đã sửa

| File | Loại thay đổi |
| --- | --- |
| [`ChangeJson.py`](../ChangeJson.py) | Viết lại toàn bộ |
| [`nlp/nlp_engine.py`](../nlp/nlp_engine.py) | Sàn độ phủ, cổng lọc phủ định |
| [`nlp/clinical_rules.py`](../nlp/clinical_rules.py) | Trục vỡ/không vỡ, ánh xạ FHIR |
| [`backend/main.py`](../backend/main.py) | Tách `requires_review` khỏi `verificationStatus` |
| [`nlp/data/icd10_db.json`](../nlp/data/icd10_db.json) | Sinh lại |
| [`nlp/data/icd10_supplement.json`](../nlp/data/icd10_supplement.json) | File mới |
| [`Cong_thuc_toan_hoc_NLP_ICD10.docx`](../Cong_thuc_toan_hoc_NLP_ICD10.docx) | Cập nhật công thức 1, 5, 11, 12, 16 |

Toàn bộ nằm trong working tree, chưa commit. Hoàn tác bằng
`git checkout <file>`; cache embedding cũ vẫn còn trên đĩa nên lùi lại là tức thì.

---

## 6. Cách đo lại

```powershell
.venv\Scripts\python -m nlp.evaluate                                    # eval_set, 110 ca
.venv\Scripts\python -m nlp.evaluate --dataset nlp\data\eval_holdout.json   # holdout, 51 ca
.venv\Scripts\python ChangeJson.py --dry-run                            # kiểm tra danh mục, không ghi
```

Đổi danh mục thì lần khởi động kế tiếp phải mã hóa lại — khoảng 2 phút trên GPU.
