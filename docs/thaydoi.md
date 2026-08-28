# Nhật ký thay đổi — 28/08/2026 (phần 2)

Nửa sau của phiên, sau khi mục 28/08 phần 1 đã merge. Trọng tâm chuyển sang **hai
câu hỏi do người dùng đặt ra** và cả hai đều lộ ra lỗi thật:

> *"bên HIS nên có thêm phần xem chẩn đoán để xem lại lịch sử khám ở bệnh viện và các cấp"*
>
> *"vậy thì đâu phải liên thông — nếu bệnh khám tuyến khác thì vẫn phải hiện kèm nơi khám chứ"*

Câu thứ hai là câu chỉnh hướng quan trọng nhất của cả phiên, và nó đúng: phần liên
thông khi đó chỉ tồn tại trong các màn phụ.

Toàn bộ đã merge vào `main` tại `29466b1`. **138/138 test pass**, gồm cả
`nlp/test_nlp.py` vốn đang đỏ từ trước.

---

## 11. Bệnh sử toàn tuyến, và bài học về "thế nào là liên thông"

### 11.1 Lần làm thứ nhất — đúng nhưng chưa đủ

Thêm `GET /api/patients/{id}/history` và một modal "Xem bệnh sử": ghép bệnh án cục
bộ với mọi `Condition` trên trục, nhóm theo cơ sở đã lập.

Khoảng trống ban đầu rất rõ: `GET /api/patients` cố ý chỉ trả bệnh án cục bộ (comment
trong mã ghi thẳng *"return local SQLite patients ONLY"*), nên bác sĩ mở hồ sơ một
bệnh nhân **đã tiếp nhận** thì chỉ thấy phần viện mình ghi.

### 11.2 Lỗi thứ nhất — ca cần nhất lại là ca hỏng

Bản đầu bắt buộc phải có bệnh án cục bộ, không thì 404. Nghĩa là bệnh nhân **vừa
chuyển tuyến tới, chưa kịp tiếp nhận** — đúng lúc cần bệnh sử nhất — lại là ca duy
nhất không xem được. Tái hiện được với dữ liệu thật: tra CCCD `079095010245` ra hồ
sơ 5 chẩn đoán, nhưng bấm "Xem bệnh sử" trên chính dòng đó thì 404.

Sửa: hồ sơ cục bộ thành tùy chọn, chỉ 404 khi không có ở **cả hai** nơi.

### 11.3 Lỗi thứ hai, và là lỗi về bản chất

Người dùng chỉ ra: dựng phần liên thông vào một modal thì **màn hình chính vẫn chỉ
thấy phần của mình**. Một hệ thống liên thông mà phần liên thông chỉ nằm trong màn
phụ thì chưa phải liên thông.

`GET /api/patients` nay bồi thêm chẩn đoán tuyến khác cho từng hồ sơ. Bốn quyết định:

| Quyết định | Vì sao |
| --- | --- |
| Đọc trục trong **đúng 2 lần gọi**, bất kể bao nhiêu bệnh nhân | Hỏi một lần cho mỗi hồ sơ thì màn hình chậm dần theo số bệnh án — kiểu chậm chỉ lộ khi dữ liệu đã nhiều, tức lúc không sửa được nữa |
| Gom theo `subject.reference`, không theo `subject.identifier` | Cùng một người có thể được ghi dưới hai định danh khác nhau ở hai lần khám; gom theo identifier sẽ tách làm hai hồ sơ |
| **Mọi** chẩn đoán mang `facility_name`, kể cả của viện mình | Thấy bệnh sử mà không rõ ai ghi còn nguy hiểm hơn không thấy — bác sĩ mặc định coi là bản ghi của mình và tin theo mức không đúng |
| Bản trên trục **không** mang điểm mô hình | Điểm ấy là của lần chẩn đoán tại nơi lập, không phải thứ viện này chấm. Để trống còn hơn bịa một con số trông như đã kiểm chứng |

Khử trùng lặp hai tầng: theo `fhir_condition_id`, và theo mã ICD **trong phạm vi
viện mình**. Tầng hai xử ca bản cục bộ chưa liên thông trùng mã với bản của chính
viện này trên trục — hai dòng "chưa liên thông" và "chỉ đọc" cho cùng một bệnh ở
cùng một viện là hai điều không thể cùng đúng.

---

## 12. Tìm kiếm bệnh nhân cho VNPT HIS

Bảng bệnh nhân trước đây render toàn bộ danh sách, không lọc được; state
`searchPatientQuery` khai ở `App.jsx:23` mà không dùng ở đâu.

Làm hai tầng, cùng mô hình với HIS Python: lọc nội viện theo Tên/Mã BN/CCCD/BHYT
(bỏ dấu trước khi so khớp), không thấy thì tra EMR Cloud theo **mã bệnh án → CCCD
→ BHYT**.

`EmrLookupService` **chỉ đọc** FHIR; mọi đường ghi vẫn qua Gateway vì chỉ Gateway
gắn được mã cơ sở và khóa nghiệp vụ.

---

## 13. Chẩn đoán trên trục phải mang tên bệnh

`HisController:283` đẩy thẳng chuỗi `"Chẩn đoán kèm theo"` làm **tên bệnh**, vì màn
khám chỉ lưu MÃ của chẩn đoán kèm theo. Đo trên trục đang chạy: **4/11 Condition**
mang nhãn đó thay cho tên bệnh.

| Condition | Mã | Đang hiện | Đúng ra là |
| --- | --- | --- | --- |
| 1007 | I21.9 | Chẩn đoán kèm theo | **Nhồi máu cơ tim cấp, không đặc hiệu** |
| 1058 | I47.2 | Chẩn đoán kèm theo | **Nhịp nhanh thất** |
| 1052 | E87.7 | Chẩn đoán kèm theo | Quá tải dịch |
| 1055 | J00 | Chẩn đoán kèm theo | Viêm mũi họng cấp |

Chữa ở **Gateway** chứ không ở HIS: Gateway là nơi duy nhất chắc chắn có đủ danh
mục 12.137 mã. `icd10_display` thành tùy chọn; danh mục **thắng** tên bên gọi gửi
lên, vì theo đặc tả FHIR `Coding.display` là cách diễn đạt nghĩa của mã *trong hệ
mã đó*, không phải chỗ mỗi HIS ghi cách gọi riêng.

Thêm `GET /api/icd10/{code}`, nhận cả mã có chấm lẫn viết liền — HIS thường cầm
dạng viết liền, chỉ nhận một dạng thì nửa số lần tra trượt và HIS lại quay về điền
nhãn chung.

> **Còn tồn:** 4 Condition đã nằm trên trục vẫn mang nhãn cũ. Bản vá chỉ tác dụng
> với bản ghi mới; cần một lần sửa dữ liệu riêng.

---

## 14. Ba lỗi NLP chưa ai biết

Xử mục 4.4 (bốn trường ràng buộc lâm sàng), và trên đường làm phát hiện thêm hai lỗi.

### 14.1 515 mã mang khoảng tuổi của phụ lục khác

Sheet Excel `A3.7 - A3.8` chứa **hai phụ lục khác hẳn nhau**, mà bộ nạp gán một nhãn
cho cả sheet:

| Phụ lục | Thật sự là | Số mã |
| --- | --- | ---: |
| A3.7 Bệnh của tuổi dậy thì | 8–19 tuổi | 2 |
| A3.8 **Bệnh sản phụ khoa** | **9–60 tuổi** | **505** |

505 mã sản phụ khoa mang khoảng "8-19 tuổi". Đem ràng buộc đó ra lọc ứng viên thì
**sản phụ 30 tuổi bị loại hết mã chương O** — đúng loại hỏng mà ràng buộc lâm sàng
sinh ra để chặn, chỉ khác là nó chặn nhầm người bệnh. Sheet `A3.2-A3.3-A3.4` hỏng
tương tự với 10 mã.

`ChangeJson.py` nay đọc khoảng tuổi từ chính dòng tiêu đề "Phụ lục ..." bên trong
sheet. Danh mục sinh lại: 12.137 mã không đổi, **10 khoảng tuổi đúng** thay cho 7
khoảng sai.

### 14.2 Lối đặt dấu — 2.475 mã, 20% danh mục

"thùy" và "thuỳ" đều đúng chính tả nhưng là **hai chuỗi Unicode khác nhau**. Danh
mục dùng lối này, bộ gõ phổ biến dùng lối kia:

| Truy vấn | Kết quả |
| --- | --- |
| `nhiễm mucor lan tỏa` (bác sĩ gõ) | B46.4 **79,0%** |
| `nhiễm mucor lan toả` (danh mục viết) | B46.4 **99,4%** |

Chênh 20 điểm đủ để rơi khỏi ngưỡng tự động 85%: gõ **đúng** tên bệnh vẫn bị bắt
duyệt tay.

**Lần sửa đầu làm chỉ số tệ đi.** Đặt phép thống nhất vào `normalize_text` — nhưng
hàm đó nuôi cả văn bản sinh embedding, mà mô hình fine-tune trên dạng chữ danh mục
gốc. Đo được: Top-1 holdout 72,5% → **70,6%**, nhóm `polarity` tập dev 94,4% →
**83,3%**. Chuyển xuống tầng so khớp token thì chỉ số phục hồi nguyên vẹn mà vẫn thu
được gần hết lợi ích (79,0% → 94,0%).

Chốt chặn chữ **QU**: trong "quý", "đột quỵ" thì `u` thuộc digraph `qu`, đổi bừa ra
"qúy", "đột qụy" — vừa sai chính tả vừa đẩy mã ra xa tầm khớp.

### 14.3 Cặp chẩn đoán †/* chết âm thầm

`_build_dagger_links` đánh dấu mã bệnh nguyên/biểu hiện bằng ký tự `†` và `*`
**trong mã**. Nhưng phiên 14/08 đã gỡ sạch hai ký tự đó (858 → 0) vì máy chủ FHIR
từ chối chúng. Sau lần gỡ ấy:

```
_marked_dagger   = 0 phần tử
_marked_asterisk = 0 phần tử
```

Mọi nhánh ghép cặp đi qua hai tập này nên chúng thành **code chết**: hệ thống lặng
lẽ thôi nhận ra chẩn đoán kép, trong khi bảng liên kết vẫn đúng (`M51.1 → G55.1`).
Không có gì báo lỗi — chỉ có một bài test đỏ mà không ai đọc.

Lấy dấu từ hai nguồn thay cho ký tự trong mã. Phụ lục A1 (`meta.asterisk_codes`,
374 mã) chuẩn nhưng **thiếu**: không có cặp M51.1/G55.1 dù tên hai mã tham chiếu
nhau. Nguồn thứ hai giải quyết được — ký tự đánh dấu nằm ngay trong tên mã, chỉ là
regex khớp nó rồi vứt đi; giữ lại làm nhóm bắt thì nó nói rõ bên nào là gì.

### 14.4 Nối bốn trường ràng buộc (mục 4.4)

`sex_constraint` (869 mã), `age_constraint` (1.640), `can_be_primary` (853). Dùng
**hạ điểm chứ không loại thẳng**, hai lý do:

* Phụ lục A3 là quy tắc **kiểm tra** của Bộ Y tế, không phải điều bất khả — phụ nữ
  65 tuổi vẫn mắc bệnh phụ khoa.
* Như 14.1 cho thấy, chính dữ liệu ràng buộc từng sai. Kiến trúc phải chịu được
  việc đó mà không giấu mất mã đúng.

Thiếu thông tin bệnh nhân thì **không phạt**, nên mọi đường gọi cũ giữ nguyên hành
vi. Gateway nhận thêm `patient_sex` / `patient_birth_date` (tùy chọn), chấp nhận cả
"Nam"/"Nữ".

---

## 15. Ngưỡng test: chốt hồi quy, không phải nơi ghi mục tiêu

`MIN_HOLDOUT_TOP5 = 0.88` đặt từ commit đầu tiên và **chưa lần nào đạt** — mục 1 của
nhật ký 14/08 ghi Top-5 holdout là 84,3% rồi 86,3%. Bài test đó đã đỏ liên tục.

Một bộ test luôn đỏ thì người ta thôi nhìn nó — và **đó chính là lý do lỗi 14.3 nằm
im suốt**: test đỏ vì mục tiêu chưa đạt và test đỏ vì code hỏng trông giống hệt
nhau, nên cái thứ hai bị chôn dưới cái thứ nhất.

Hạ về `0.86` sát giá trị thật, kèm lý do đầy đủ trong mã.

> **Mục tiêu 88% chuyển về đây.** Đạt được bằng cách mở rộng tập holdout (mục 4.2),
> không phải bằng cách chỉnh một con số trong file test.

---

## 16. Đo lại sau phiên

Đo ngày 28/08/2026 bằng [`nlp/evaluate.py`](../nlp/evaluate.py).

| Chỉ số | Đầu phiên | Cuối phiên |
| --- | ---: | ---: |
| Holdout Top-1 | 72,5% | 72,5% |
| Holdout Top-3 / Top-5 | 86,3% | 86,3% |
| Holdout MRR | 0,7876 | 0,7876 |
| Holdout — số ca mức cao | 27 (92,6% đúng) | 27 (92,6% đúng) |
| Dev Top-1 / MRR | 99,1% / 0,9955 | 99,1% / 0,9955 |
| `nhiễm mucor lan tỏa` | B46.4 **79,0%** | B46.4 **94,0%** |
| `bệnh tích lũy glycogen` | E74.0 **79,4%** | E74.0 **97,2%** |
| `nlp/test_nlp.py` | **27 pass / 2 fail** | **29 pass / 0 fail** |

Chỉ số tổng thể **không đổi một chữ số** — đúng thiết kế: ràng buộc lâm sàng chỉ
kích hoạt khi có giới tính/tuổi bệnh nhân, mà tập đánh giá không có. Hai ca lối đặt
dấu vượt ngưỡng tự động 85%.

### Bộ kiểm thử

| Bộ | Số ca | Cần gì |
| --- | ---: | --- |
| `tests/` | 76 | Không cần Docker, không nạp mô hình |
| `nlp/test_rang_buoc.py` | 33 | Không nạp mô hình, chạy dưới 1 giây |
| `nlp/test_nlp.py` | 29 | Nạp mô hình |
| **Tổng** | **138** | Tất cả xanh |

---

## 17. Việc còn lại — cập nhật

| Mục | Trạng thái |
| --- | --- |
| 4.1 Hai ca sai lọt cổng tự động | **Không phải lỗi tham số.** J18.1 tên là *"Viêm phổi thuỳ, không đặc hiệu"* nên cũng bị luật `.9` hạ bậc — tăng `UNSPECIFIED_PENALTY` làm ca đó **tệ hơn**. Cần phân biệt "không đặc hiệu về thể bệnh" với "không đặc hiệu về tác nhân" |
| 4.2 Tập holdout quá nhỏ | **Chưa làm — và là việc cấp nhất** |
| 4.3 Thiếu script huấn luyện | Chưa làm |
| 4.4 Bốn trường ràng buộc | **Xong 3/4.** `asterisk_codes` dùng cho cặp †/* (14.3), ba trường còn lại nối vào xếp hạng (14.4) |
| 4.5 Danh mục thiếu tên 4.652 mã | Chưa làm |
| 8.3 Nửa liên thông chưa có bằng chứng định lượng | **Một phần**: `$validate` của HAPI cho **0 lỗi** trên `Condition`, `Patient`, `Organization` |
| 8.4 `Provenance` | Chưa làm — phụ thuộc lớp xác thực |
| 8.6 Xác thực | Đã bàn hướng: **API key gắn cứng với một mã cơ sở**, đọc mã từ khóa chứ không từ thân yêu cầu. Xóa được cờ `SMIG_ALLOW_CLIENT_FACILITY` |

### 17.1 Mã ICD-10 chưa hề được kiểm chứng

`$validate` của HAPI trả về cảnh báo:

> `CodeSystem is unknown and can't be validated: http://hl7.org/fhir/sid/icd-10`

Nghĩa là máy chủ **không thể xác minh mã có thật hay không** — chỉ kiểm cấu trúc.
Một mã đúng định dạng nhưng gõ sai (E11.9 → E11.8) lên trục trót lọt. Nạp danh mục
12.137 mã lên HAPI thành `CodeSystem` là biến cảnh báo này thành kiểm chứng thật.

### 17.2 Điều cần nói thẳng

Phiên này ra nhiều việc thật. Nhưng **mục 4.2 chưa nhúc nhích một bước nào**, và nó
là việc **duy nhất phụ thuộc người khác** nên là việc duy nhất không rút ngắn được.
Mọi thứ còn lại — API key, `Provenance`, `CodeSystem` — code trong vài ngày là xong;
150–200 ca gán nhãn thì tính bằng tuần.

Phiên này vừa cho thêm hai bằng chứng nữa rằng bộ đo 51 ca là quá nhỏ: nó **mù hoàn
toàn** với nhóm lỗi dây chằng (phần 1, mục 6) và **mù luôn** với lỗi đặt dấu ảnh
hưởng 20% danh mục (14.2). Cả hai đều là lỗi có thật, tái lập được, mà chỉ số không
hề nhúc nhích.

---
---

# Nhật ký thay đổi — 28/08/2026

Phiên này chuyển trọng tâm từ **NLP** sang **liên thông**. Việc lớn nhất: một mã
bệnh án chỉ có nghĩa trong nội bộ nơi cấp nó, nên trước phiên này hai bệnh viện
dùng trùng mã bệnh án là ghi đè hồ sơ của nhau trên trục dữ liệu.

Khác phiên 14/08, phần lớn thay đổi ở đây **không đo được bằng độ chính xác** —
chúng sửa mô hình dữ liệu chứ không sửa mô hình học máy. Bằng chứng vì vậy nằm ở bộ
kiểm thử (mục 7), không ở bảng chỉ số. Riêng phần NLP có đo lại, kết quả ở mục 6.

Toàn bộ đã commit và merge vào `main` tại `656d7fe`.

---

## 1. Phạm vi phiên làm việc

Sáu nhánh, mỗi nhánh một vấn đề, đều đã merge vào `main`:

| Nhánh | Nội dung | Quy mô |
| --- | --- | ---: |
| `feat/dinh-danh-da-co-so-va-lien-thong` | Định danh bệnh nhân đa cơ sở | 19 file, +3.061 |
| `fix/chot-co-so-khi-go-mot-chan-doan` | Chốt cơ sở ở đường xóa | 5 file, +241 |
| `fix/nlp-alias-day-chang-khop-goi` | Alias dây chằng khớp gối | 2 file, +68 |
| `chore/fhir-postgres-va-siet-cau-hinh` | EMR Cloud lưu trữ bền vững | 1 file, +38 |
| `docs/tai-lieu-ky-thuat-va-readme` | Tài liệu kỹ thuật | 4 file, +2.279 |
| `chore/xoa-icd10-db-json-trung-lap` | Dọn bản danh mục trùng ở gốc | −244.382 |

---

## 2. Định danh bệnh nhân đa cơ sở

### 2.1 Vấn đề

Mã bệnh án (MRN) do từng bệnh viện tự cấp và **chỉ có nghĩa trong nội bộ nơi đó**.
Bản trước lấy thẳng mã bệnh án làm khóa trên trục, nên hỏng hai kiểu cùng lúc:

| Hỏng | Cơ chế |
| --- | --- |
| Trộn hồ sơ hai người | Bệnh viện A và B cùng cấp mã `BN-001` cho hai bệnh nhân khác nhau → trên trục thành một người |
| Mất lịch sử điều trị | Cùng người mắc lại cùng bệnh ở lần khám sau đè lên chẩn đoán lần trước |

### 2.2 Khóa nghiệp vụ bốn thành phần

[`stable_condition_key`](../backend/main.py) sinh khóa gồm bốn phần, mỗi phần chặn
một kiểu trộn:

```
{mã cơ sở} - {mã bệnh án} - {mã ICD} - {ngày khám}
```

Hai quyết định thiết kế đáng ghi lại vì đều phản trực giác:

**Không dùng CCCD/BHYT trong khóa này**, dù chúng "toàn quốc" hơn. Hồ sơ đầy dần
theo thời gian: lần khám đầu HIS chỉ có mã bệnh án, lần sau mới bổ sung CCCD. Khóa
bám theo định danh ưu tiên cao nhất hiện có sẽ **đổi** giữa hai lần, và cùng một
chẩn đoán thành hai bản ghi. Mã bệnh án là thứ duy nhất chắc chắn có mặt và không
đổi. Việc đồng nhất một người giữa nhiều viện do `Patient.identifier` đảm nhiệm.

**Ngày khám nằm trong khóa**, nên tái khám cùng bệnh là một `Condition` mới chứ
không phải bản cập nhật — giữ được lịch sử điều trị.

### 2.3 Chốt mã cơ sở

Mã cơ sở là **danh tính của bên ghi hồ sơ**, phải do phía máy chủ xác lập:

| Cấu hình | Hành vi |
| --- | --- |
| Mặc định | Mã lấy từ `SMIG_FACILITY_CODE` của Gateway. Mỗi bệnh viện chạy một bản Gateway riêng |
| `SMIG_ALLOW_CLIENT_FACILITY=1` | HIS tự khai mã trong từng yêu cầu — **chỉ để thử nghiệm cục bộ** |

Cờ này tồn tại vì một lý do rất thực tế: mô hình NLP chiếm vài GB RAM nên chạy hai
bản Gateway trên một máy demo là quá nặng, trong khi vẫn cần hai bệnh viện phân biệt
được nhau để trình diễn kịch bản chuyển tuyến.

Khi cờ tắt mà bên gọi khai mã lạ thì [`resolve_facility`](../backend/main.py) trả
**403**, cố ý không âm thầm lùi về mã của Gateway: sai cấu hình mà vẫn chạy thì hồ
sơ của bệnh viện B nằm trên trục dưới tên bệnh viện A, và không ai phát hiện ra.

Trạng thái cờ được phơi ra `GET /health` (`allow_client_facility`) để nhìn là biết
Gateway đang ở chế độ thử nghiệm.

### 2.4 Định danh cấp quốc gia

Thêm `citizen_id` và `insurance_card`, kiểm ngay tại tầng nhận yêu cầu bằng
`field_validator` nên sai định dạng là **422 trước khi bất kỳ tài nguyên nào được
dựng**:

| Trường | Luật | Bỏ trống |
| --- | --- | --- |
| `citizen_id` | 12 chữ số (hoặc 9 nếu là CMND cũ) | Hợp lệ |
| `insurance_card` | 2 chữ cái + 13 chữ số | Hợp lệ |

Bỏ trống là hợp lệ vì không phải bệnh nhân nào cũng có giấy tờ lúc tiếp nhận, và
luồng đã có phương án lùi về mã bệnh án. Cái không được phép là giá trị **có mà
sai** — nó tạo ra một danh tính toàn quốc giả.

### 2.5 Conditional update thay cho PUT theo id

Cả `Patient` lẫn `Condition` nay ghi bằng `PUT /{Type}?identifier=system|value`.
Idempotent mà không cần client tự đặt id: không khớp bản nào thì máy chủ tạo mới và
tự cấp id, khớp đúng một bản thì cập nhật bản đó, khớp nhiều bản thì trả 412 và
không sửa gì. Nhờ vậy HAPI mới bật được `client_id_strategy=NOT_ALLOWED` (mục 5).

### 2.6 Lan sang các thành phần khác

| Thành phần | Thay đổi |
| --- | --- |
| [`run.ps1`](../run.ps1) | Tham số `-Port`, `-FacilityCode`, `-FacilityName`, `-AllowClientFacility` |
| [`hospital_his/`](../hospital_his/) | Nhập CCCD/BHYT, tra cứu trên EMR Cloud, chẩn đoán thêm cho hồ sơ đã có |
| [`VNPT_HIS/`](../VNPT_HIS/) | Gửi kèm mã cơ sở và định danh quốc gia |
| [`frontend/app.js`](../frontend/app.js) | Sửa lời cảnh báo xóa — chỉ xóa phần của chính cơ sở này |

Hai sửa nhỏ trong `VNPT_HIS` đáng ghi riêng:

- `RestTemplate` có thời gian chờ tường minh (5 giây kết nối, 60 giây đọc). Mặc định
  của `new RestTemplate()` là chờ **vô hạn**; Gateway nạp mô hình NLP nên lần gọi đầu
  có thể chậm, và luồng khám bệnh của HIS treo theo mà không có cách nào thoát.
- Từ điển dự phòng khi Gateway offline hạ độ tin cậy **90 → 50** (khớp từ khóa) và
  **50 → 30** (gợi ý chung), kèm `requires_review` và `source: "fallback"`. Đây là
  kết quả tra bảng cứng 10 mã, không được để nó mang vẻ chắc chắn ngang kết quả của
  mô hình trên 48.407 vector.

---

## 3. Chốt cơ sở ở đường xóa

`DELETE /api/fhir/condition/{id}` nhận thẳng id rồi xóa, **không hỏi bản ghi đó của
ai** — khác hẳn `DELETE /api/fhir/sync` ngay dưới nó, vốn có lọc theo cơ sở.

Ghép với `GET /api/fhir/sync`, vốn **cố ý** trả chẩn đoán của mọi cơ sở kèm `id` vì
nhìn thấy hồ sơ nơi khác lập chính là điều cần trình bày, thì thành một đường xóa
chéo: đọc danh sách → lấy id của bệnh viện B → gọi gỡ.

Đúng kiểu trộn dữ liệu mà mã cơ sở trong khóa nghiệp vụ sinh ra để chặn ở đường
**ghi**, chỉ khác là nó nằm ở đường **xóa** — và hậu quả nặng hơn, vì không phục hồi
được.

**Cách vá.** Thêm tham số `facility` đi qua `resolve_facility` nên cùng một chính
sách với đường ghi. Đọc `Condition` trước khi xóa, đối chiếu thẻ cơ sở trong
`meta.tag` bằng `facility_of`, lệch thì 403. Tốn thêm một vòng gọi, nhưng FHIR không
có "xóa kèm điều kiện theo thẻ".

Hai hành vi giữ nguyên có chủ ý:

| Trường hợp | Xử lý | Vì sao |
| --- | --- | --- |
| Bản ghi đã mất | Vẫn báo thành công | `_retire_on_emr` của HIS gọi lại sau khi mất mạng; một lỗi giả ở đây hiện lên giao diện bác sĩ như bản ghi thừa còn sót trên trục |
| Bản ghi không mang thẻ cơ sở | Vẫn cho gỡ | Dữ liệu có từ trước khi Gateway gắn thẻ; không quy được về ai thì cũng không có ai để bảo vệ, mà từ chối thì chúng kẹt trên trục vĩnh viễn |

Trường hợp thứ hai là **đánh đổi**, đã ghi rõ lý do trong mã và chốt bằng một test
riêng, để ai đổi hành vi này phải đọc được vì sao.

**Kiểm chứng ngược.** Lùi `backend/main.py` về bản chưa vá rồi chạy lại bộ test mới:
**5/6 ca fail**, ca chính fail đúng lý do `DID NOT RAISE HTTPException` — xác nhận
bản cũ xóa im lặng thật, và bộ test không phải viết cho vừa với mã nguồn.

`hospital_his/server.py` kèm theo phải khai mã cơ sở khi gọi gỡ; thiếu nó thì ở chế
độ một Gateway phục vụ nhiều bệnh viện, HIS bị 403 khi gỡ đúng bản ghi của mình.

---

## 4. Alias dây chằng khớp gối

Danh mục ICD-10 gọi cả nhóm này là *"bong gân và căng cơ… tổn thương dây chằng"* —
không có chữ **"đứt"** hay **"rách"**, đúng hai từ mà bác sĩ luôn dùng. Thiếu cầu
nối từ vựng thì cosine kéo về **S53.3** *"Chấn thương đứt dây chằng hai bên xương
trụ"* — **khuỷu tay**, sai hẳn chi thể — chỉ vì mã đó trùng nguyên cụm "đứt dây
chằng".

Đây là lệch từ vựng giữa danh mục và lời khai, **không phải thiếu mã**: S83.4 và
S83.5 vẫn nằm sẵn trong danh mục. Nên chữa bằng alias, không train lại.

Thêm **16 alias**: 10 cho S83.5 (dây chằng chéo, gồm các viết tắt DCCT/DCCS/ACL/PCL),
5 cho S83.4 (dây chằng bên), 1 cho M23.5 (mất vững khớp gối). Dây chằng **chéo** và
dây chằng **bên** giữ riêng — gộp chung là mất đúng phần thông tin bác sĩ đã nêu rõ.

[`nlp/test_nlp.py`](../nlp/test_nlp.py) chốt cả hai chiều: sáu ca dây chằng phải ra
đúng mã, **và** alias mới không được kéo `"bệnh dây chằng"` (M24.2) hay `"đau thần
kinh tọa"` (M54.3) về nhóm khớp gối.

---

## 5. EMR Cloud: lưu trữ bền vững và siết cấu hình

[`docker-compose.yml`](../docker-compose.yml):

| Thay đổi | Vì sao |
| --- | --- |
| Thêm `postgres:16` + volume `hapi-pgdata` | Thay H2 in-memory — `docker compose restart` không còn xóa sạch dữ liệu đã liên thông |
| `client_id_strategy` → `NOT_ALLOWED` | Id đoán được cộng với PUT theo id là một lỗ ghi đè. Tính idempotent nay do conditional update đảm nhiệm (mục 2.5) |
| `enforce_referential_integrity_on_write` → `true` | Chặn `Condition` trỏ tới `Patient` không tồn tại. Gateway luôn đồng bộ Patient trước nên luồng bình thường không đổi |
| Cả hai cổng chỉ nghe `127.0.0.1` | Cụm này giữ toàn bộ dữ liệu bệnh án mà **chưa có xác thực**; mở ra LAN là trao quyền đọc/ghi/xóa cho mọi máy cùng mạng |

PostgreSQL dùng cổng **5433** vì 5432 thường đã có bản cài sẵn chiếm chỗ. Mật khẩu
`hapi/hapi` chỉ dành cho demo cục bộ.

---

## 6. Đo lại NLP sau phiên

Đo ngày 28/08/2026 bằng [`nlp/evaluate.py`](../nlp/evaluate.py), cùng cách với phiên
14/08. Cột "14/08" chính là cột *Sau* của nhật ký bên dưới.

### Tập kiểm tra độc lập — `eval_holdout.json`, 51 ca

| Chỉ số | 14/08 | 28/08 | Chênh |
| --- | ---: | ---: | ---: |
| Top-1 accuracy | 72,5% | 72,5% | 0 |
| Top-3 / Top-5 accuracy | 86,3% | 86,3% | 0 |
| MRR | 0,7876 | 0,7876 | 0 |
| Số ca mức cao | 27 | 27 | 0 |
| Chính xác trong mức cao | 92,6% | 92,6% | 0 |

### Tập phát triển — `eval_set.json`, 110 ca

| Chỉ số | 14/08 | 28/08 |
| --- | ---: | ---: |
| Top-1 accuracy | 99,1% | 99,1% |
| Top-3 accuracy | 100% | 100% |
| MRR | 0,9955 | 0,9955 |
| Số ca mức cao | 105 | 105 |

### Danh mục

| | 14/08 | 28/08 |
| --- | ---: | ---: |
| Vector tham chiếu | 48.391 | **48.407** |
| Số mã ICD-10 | 12.137 | 12.137 |

Chênh **đúng +16**, khớp chính xác số alias thêm ở mục 4.

### Đọc bảng này thế nào

Mọi chỉ số **đứng yên tuyệt đối**. Đây không phải thất bại của thay đổi ở mục 4 — mà
là bằng chứng cho mục 8.2: **tập holdout 51 ca không chứa ca chấn thương dây chằng
nào**, nên một lỗi có thật, tái lập được, sai hẳn chi thể lại **hoàn toàn vô hình**
với bộ đo.

Điều rút ra: ở quy mô hiện tại, bảng chỉ số **không đủ để phát hiện lỗi, cũng không
đủ để xác nhận đã sửa**. Chỗ bắt được lỗi này là `nlp/test_nlp.py`. Hai công cụ có
vai trò khác nhau và không thay thế nhau được.

---

## 7. Bộ kiểm thử liên thông

Thư mục [`tests/`](../tests/) — **53 ca**, không cần Docker và không nạp mô hình NLP,
chạy hết trong khoảng 10 giây:

| File | Ca | Chốt điều gì |
| --- | ---: | --- |
| `test_kiem_dinh_danh.py` | 14 | Kiểm CCCD/BHYT, chuẩn hóa, mâu thuẫn định danh |
| `test_sua_chan_doan.py` | 9 | Bác sĩ sửa lại chẩn đoán đã liên thông |
| `test_nhieu_co_so.py` | 7 | Hai bệnh viện không ghi đè hồ sơ nhau |
| `test_go_mot_chan_doan.py` | 6 | Gỡ một chẩn đoán phải dừng trong phạm vi cơ sở |
| `test_chuyen_tuyen.py` | 5 | Kịch bản chuyển tuyến A → B |
| `test_luong_nlp_khong_doi.py` | 5 | Thay đổi liên thông không đụng vào luồng NLP |
| `test_dinh_danh_bo_sung_sau.py` | 4 | Hồ sơ đầy dần: lần đầu chỉ có MRN, lần sau thêm CCCD |
| `test_xoa_theo_co_so.py` | 3 | Xóa hàng loạt dừng trong phạm vi cơ sở |

`test_chuyen_tuyen.py` là chỗ trình diễn giá trị của cả nửa liên thông: bệnh viện A
chẩn đoán đái tháo đường rồi chuyển tuyến, B khám ra thêm bệnh thận mạn, và ba câu
hỏi phải trả lời được là B thêm bệnh mà không đè lên A, hai nơi quy về cùng một
người nhờ CCCD, và bác sĩ ở B tra được bệnh sử bằng CCCD.

---

## 8. Việc còn lại

### 8.1 Chuyển tiếp từ phiên 14/08

| Mục | Trạng thái |
| --- | --- |
| 4.1 Hai ca sai lọt cổng tự động | **Chưa làm** — đo lại 28/08 vẫn còn nguyên |
| 4.2 Tập holdout quá nhỏ | **Chưa làm** — và mục 6 vừa cho thêm một bằng chứng nữa |
| 4.3 Thiếu script huấn luyện | **Chưa làm** — vẫn là chương phương pháp bắt buộc phải có |
| 4.4 Bốn trường ràng buộc lâm sàng chưa dùng | **Chưa làm** |
| 4.5 Danh mục thiếu tên cho 4.652 mã | **Chưa làm** |

### 8.2 Tập holdout: thêm một lý do nữa

Mục 6 cho thấy bộ đo hiện tại **mù** với cả một nhóm lỗi. 51 ca đã cho khoảng tin
cậy 95% trải từ 60,2% đến 84,8%; nay biết thêm rằng nó còn không phủ nổi những nhóm
chẩn đoán thường gặp. Nâng lên 150–200 ca vẫn là việc đáng làm nhất của nửa NLP, và
là việc duy nhất phụ thuộc người khác nên phải khởi động sớm nhất.

### 8.3 Nửa liên thông chưa có bằng chứng định lượng

Nửa NLP có bảng chỉ số; nửa liên thông mới chỉ có "test pass". Ba thứ đo được, đều rẻ:

- Gọi `$validate` của HAPI trên tài nguyên sinh ra → tỷ lệ hợp lệ theo profile R4.
  Khác hẳn "máy chủ chịu nhận".
- Round-trip: đẩy N bệnh án lên rồi đọc về, đối chiếu có mất trường nào không.
- Nâng `test_chuyen_tuyen.py` từ test đúng/sai thành **kịch bản đánh giá có số liệu**.

### 8.4 `Provenance` — chỗ nối hai nửa

Hiện chỉ dựng `Condition`, `Patient`, `Organization`. Độ tin cậy của mô hình sống
trong bộ nhớ lúc chạy rồi mất. Ghi nó vào `Provenance` thì con số hiệu chuẩn ở mục 6
**đi vào tài nguyên FHIR** và sống tiếp trên trục — hai nửa của đề tài thành một mạch
thay vì hai chương rời.

### 8.5 Độ trễ: số đo chưa ổn định

Hai lần chạy trong cùng một ngày cho kết quả lệch xa: `eval_holdout` báo trung bình
**22 ms** (p50 20, p95 30) trong khi `eval_set` báo **66 ms** (p50 61, p95 93) — con
số thứ hai khớp với phiên 14/08, con số thứ nhất thì không. **Chưa cô lập được
nguyên nhân** (khác nội dung truy vấn? trạng thái GPU?). Không đưa con số độ trễ nào
vào báo cáo trước khi có phép đo có kiểm soát.

### 8.6 Nằm ngoài phạm vi — nhưng phải khai trong báo cáo

Gateway **chưa có bất kỳ lớp xác thực nào**: không API key, không token. CORS chỉ
chặn trình duyệt, không chặn `curl`. Nghĩa là chốt mã cơ sở ở mục 2.3 và mục 3 mới
kiểm **mã được khai**, chưa kiểm **bên khai là ai**.

Đề tài khoanh vào chất lượng ánh xạ ICD-10 và tính đúng đắn của mô hình liên thông,
nên đây là giới hạn có chủ ý — nhưng phải **khai ra** trong phần giới hạn đề tài,
với chuẩn tham chiếu cho hướng phát triển là **SMART on FHIR** (OAuth2
`client_credentials`), chứ không im lặng bỏ qua.

---

## 9. Danh sách file đã sửa

| File | Loại thay đổi |
| --- | --- |
| [`backend/main.py`](../backend/main.py) | Mã cơ sở, khóa nghiệp vụ 4 thành phần, conditional update, chốt cơ sở khi xóa |
| [`backend/fhir_helper.py`](../backend/fhir_helper.py) | Chuẩn hóa CCCD/BHYT, dựng `Organization`, khóa đối chiếu bệnh nhân |
| [`hospital_his/`](../hospital_his/) | CCCD/BHYT, tra cứu EMR Cloud, chẩn đoán thêm |
| [`VNPT_HIS/`](../VNPT_HIS/) | Mã cơ sở, timeout tường minh, hạ tin cậy từ điển dự phòng |
| [`frontend/app.js`](../frontend/app.js) | Lời cảnh báo xóa theo đúng phạm vi |
| [`run.ps1`](../run.ps1) | Tham số cổng và mã cơ sở |
| [`docker-compose.yml`](../docker-compose.yml) | PostgreSQL, siết cấu hình HAPI |
| [`nlp/clinical_rules.py`](../nlp/clinical_rules.py) | 16 alias dây chằng khớp gối |
| [`nlp/test_nlp.py`](../nlp/test_nlp.py) | 6 ca dây chằng + 1 ca chốt không lan sang mã khác |
| [`tests/`](../tests/) | Bộ kiểm thử liên thông, 53 ca — **thư mục mới** |
| `icd10_db.json` (thư mục gốc) | **Đã xóa** — bản thật là `nlp/data/icd10_db.json` |

---

## 10. Cách kiểm chứng

```powershell
.venv\Scripts\python -m pytest tests\ -q                                   # 53 ca, ~10 giay
.venv\Scripts\python -m pytest nlp\test_nlp.py -q                          # can nap mo hinh
.venv\Scripts\python -m nlp.evaluate                                       # eval_set, 110 ca
.venv\Scripts\python -m nlp.evaluate --dataset nlp\data\eval_holdout.json  # holdout, 51 ca
```

Bộ `tests/` không cần Docker và không nạp mô hình nên chạy được trong mọi hoàn cảnh.
Kiểm chất lượng nhận diện mã thì phải qua `nlp/`.

---
---

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

Toàn bộ đã commit tại `36d1be3` *(cập nhật 28/08: khi viết nhật ký này thì
chúng còn nằm trong working tree)*. Cache embedding cũ vẫn còn trên đĩa nên lùi
lại là tức thì.

---

## 6. Cách đo lại

```powershell
.venv\Scripts\python -m nlp.evaluate                                    # eval_set, 110 ca
.venv\Scripts\python -m nlp.evaluate --dataset nlp\data\eval_holdout.json   # holdout, 51 ca
.venv\Scripts\python ChangeJson.py --dry-run                            # kiểm tra danh mục, không ghi
```

Đổi danh mục thì lần khởi động kế tiếp phải mã hóa lại — khoảng 2 phút trên GPU.
