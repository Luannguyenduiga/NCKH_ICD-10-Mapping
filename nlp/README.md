# Cải tiến khối NLP — nhật ký & kết quả

Tài liệu này ghi lại phần việc cải tiến module NLP, phạm vi **chỉ phần AI**
(model/tín hiệu học máy) — không sửa `clinical_rules.py`, để tách bạch với
phần luật lâm sàng do người khác trong nhóm phụ trách. Toàn bộ số liệu đo
bằng `nlp/bench.py` (công cụ mới, xem mục cuối) trên `nlp/data/eval_holdout.json`
(51 ca, tập kiểm tra độc lập) và `nlp/data/eval_set.json` (110 ca, tập phát triển).

## Tóm tắt kết quả

| | Baseline (code gốc, model thật) | + Tín hiệu NLI |
| --- | --- | --- |
| Top-1 (holdout) | 72,5% | **78,4%** |
| Top-3 (holdout) | 86,3% | 84,3% |
| Top-5 (holdout) | 86,3% | 86,3% |
| MRR (holdout) | 0,788 | **0,815** |
| ECE (holdout, càng thấp càng tốt) | 0,116 | **0,096** |
| Top-1 (eval_set, 110 ca) | 99,1% | 98,2% |
| Độ trễ trung bình | 39 ms | 79 ms |

**Baseline ở đây là code hiện tại của nhóm, chạy nguyên trạng với đúng model
đã fine-tune (`nlp/my_medical_nlp_model/`), không sửa gì cả** — dùng để so
sánh công bằng, không lẫn với bất kỳ thay đổi nào của tôi.

## Đã làm gì

### 1. Xây bộ đo lường (`nlp/bench.py`)

Mở rộng `nlp/evaluate.py` sẵn có (chỉ đo Top-k/MRR/độ trễ) với:
- **Watchlist** — theo dõi đích danh vài ca đã biết là khó, để không bị con số
  trung bình che mất việc một ca cụ thể vừa được sửa hay vừa bị hỏng.
- **Khoảng tin cậy Wilson 95%** cho Top-1 — vì `eval_holdout.json` chỉ có 51
  ca, một con số điểm dễ gây hiểu lầm là chính xác hơn thực tế.
- **Expected Calibration Error (ECE)** — đo độ hiệu chuẩn định lượng, mịn hơn
  3 dải tin cậy hiện có.
- **So sánh hai lần chạy** — tự động phát hiện hồi quy theo từng tag, không
  chỉ nhìn con số tổng.

Dùng công cụ này cho MỌI lần đo trong tài liệu này, để đảm bảo so sánh nhất
quán giữa các lần thử nghiệm.

### 2. Thử tích hợp NLI (Natural Language Inference) làm tín hiệu tái xếp hạng bổ sung

**Ý tưởng**: `POLARITY_AXES` hiện tại chỉ bắt được 5 trục đối lập đã liệt kê
tay (típ tiểu đường, nguyên nhân THA, biến chứng, cấp/mạn, dị ứng). Một mô
hình NLI đa ngôn ngữ đã huấn luyện sẵn (zero-shot, không cần dữ liệu lâm sàng
mới) có thể tổng quát hóa việc phát hiện mâu thuẫn ngữ nghĩa ra ngoài 5 trục
đó.

**Cách tích hợp** (`nlp/nli_reranker.py`): sau khi tầng luật đã tái xếp hạng
xong, lấy top-K ứng viên đưa qua model NLI (`mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`),
hỏi "câu chẩn đoán và tên mã này có mâu thuẫn không", dùng điểm *contradiction*
để trừ điểm (không dùng entailment để cộng điểm — kém tin cậy hơn hẳn, xem
phần hạn chế bên dưới). **Mặc định BẬT** (`use_nli=True`) — số đo ở trên là
số thật khi gọi `NLPEngine()` không truyền gì thêm, kể cả trong
`backend/main.py` (Gateway). Cấu hình hiện tại (`top_k=4`, trọng số 0,20) là
kết quả sau khi dò nhiều bộ tham số và 2 model NLI khác nhau. Truyền
`use_nli=False` để tắt (đổi lại độ trễ thấp hơn ~2 lần, mất phần cải thiện
Top-1/MRR/ECE).

## Phát hiện quan trọng nhất — giới hạn của NLI zero-shot cho tiếng Việt y khoa

Đo được lặp lại **3 lần**, ở model/tham số khác nhau, cùng một hiện tượng:
mã ICD-10 **đúng** đôi khi bị model NLI chấm *contradiction* rất cao một cách
không giải thích được, trong khi mã **sai** lại được điểm thấp chỉ vì trùng
đúng một vài chữ với câu hỏi (không phải vì đúng về ý nghĩa lâm sàng). Ví dụ
đã ghi lại:

| Câu hỏi | Mã đúng bị chấm sai | Mã sai được lợi vì trùng chữ |
| --- | --- | --- |
| "viêm mũi không dị ứng" | J31.0 (contradiction 0,976) | J30.3 "dị ứng khác" (0,495 — **thật sự** mâu thuẫn nhưng điểm thấp nhất) |
| "chấn thương sọ não kín" | S06 (contradiction 0,881) | S02.10 "vỡ **kín**" (0,042 — trùng chữ "kín") |

Đây **không phải lỗi tham số** — đã thử 2 model, nhiều `top_k`/trọng số, hiện
tượng vẫn lặp lại (chỉ đổi ca nào bị ảnh hưởng). Kết luận: NLI đa ngôn ngữ
zero-shot có ích ở phần lớn trường hợp (Top-1/MRR/ECE tổng thể đều tăng) nhưng
**không đáng tin tuyệt đối** cho từng ca riêng lẻ — luôn còn 1 ca đổi chỗ dù
đổi cấu hình nào. Nên coi đây là hạn chế đã biết, không phải lỗi cần vá tiếp
bằng cách dò tham số nhiều hơn (rủi ro overfit vào đúng 51 ca của tập holdout).

## Cách dùng

```python
from nlp.nlp_engine import NLPEngine

engine = NLPEngine()                    # mặc định: NLI đã bật (top_k=4, trọng số 0,20)
engine = NLPEngine(use_nli=False)       # tắt NLI - dùng thuần luật, độ trễ thấp hơn
```

Đo lại / so sánh:
```powershell
.venv\Scripts\python -m nlp.bench --label baseline --no-nli --save runs\baseline.json
.venv\Scripts\python -m nlp.bench --compare runs\baseline.json
```

Tham số NLI có thể chỉnh qua `NLPEngine(nli_weight=..., nli_top_k=..., nli_template=..., nli_contradiction_floor=...)`
hoặc cờ dòng lệnh tương ứng trong `bench.py` (`--nli-weight`, `--nli-top-k`, `--nli-template`, `--nli-floor`).

## Lưu ý khi đọc số liệu

- **`backend/main.py` gọi `NLPEngine()` không tham số** → Gateway thật giờ tự
  động tải thêm model NLI (~280M tham số) lúc khởi động, và mỗi request
  `/api/standardize` chậm hơn ~2 lần (từ ~40ms lên ~80-90ms trên GPU, thêm
  bộ nhớ GPU ~550MB). Nếu cần độ trễ thấp nhất hoặc chạy trên máy không có
  GPU, sửa dòng đó thành `NLPEngine(use_nli=False)`.
- Tập holdout chỉ 51 ca — khoảng tin cậy 95% cho Top-1 rộng khoảng ±12-13
  điểm phần trăm (xem `bench.py` in kèm CI). Chênh lệch vài điểm % giữa các
  cấu hình có thể nằm trong nhiễu thống kê, không nên diễn giải quá mức.

## File liên quan

| File | Vai trò |
| --- | --- |
| `nlp/nli_reranker.py` | Module NLI — mô hình, cách diễn đạt câu hỏi, công thức tính điểm |
| `nlp/bench.py` | Bộ đo lường dùng cho mọi so sánh trong tài liệu này |
| `nlp/nlp_engine.py` | Điểm tích hợp NLI vào `NLPEngine.query()` (tham số `use_nli`, mặc định **bật**) |
| `runs/*.json` | Kết quả đo đã lưu (baseline, cấu hình NLI cuối cùng) |
