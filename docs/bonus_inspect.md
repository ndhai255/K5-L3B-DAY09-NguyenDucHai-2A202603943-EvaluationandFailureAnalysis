# Bonus: port 3 grader sang Inspect AI và so khớp với pydantic-evals

```bash
cd src
pip install -r requirements-bonus.txt          # inspect-ai==0.3.291 (CI không cài)
python -m lab9.bonus_inspect                    # spec v1: 5 shape × 70 case
python -m lab9.bonus_inspect --spec config/metric_spec_v2.yaml \
    --shapes swarm,fixed,honest_refactor,seeded_regression,slice_regression,flaky_regression,bonus_sibling_refund,bonus_misreport \
    --out reports/bonus_inspect_compare_v2.json
python -m pytest -q -m student -o addopts= tests/test_lab9_bonus_inspect.py
```

Code: `src/lab9/bonus_inspect.py`. Report: `src/reports/bonus_inspect_compare.json` (v1) và `bonus_inspect_compare_v2.json` (v2), đều do script sinh ra.

## Vì sao chọn Inspect AI (không chọn DeepEval)

- Inspect có sẵn provider offline `mockllm/model`, nên chạy được mà không cần API key, đúng tinh thần của lab.
- Mô hình `Task` / `Solver` / `Scorer` của Inspect tách bạch giữa *chạy agent* và *chấm điểm*, giống `Dataset` / task / `Evaluator` của pydantic-evals, nên port 1-1 được.
- Metric có sẵn của DeepEval phần lớn là LLM-as-judge (cần model thật) và bật telemetry mặc định. Ba grader ở đây đều là code, nên dùng DeepEval cũng chỉ còn lại `BaseMetric` tự viết, không thêm giá trị gì.

## Port như thế nào

| Thành phần | pydantic-evals (bản chính) | Inspect AI (bản port) |
|---|---|---|
| Đơn vị chấm | `Case` = item@seed | `Sample(id="E-001@11")`, nhãn để trong `target`, không nằm trong `input` |
| Chạy agent | task function `run_one` | `@solver run_lab8_shape`: gọi cùng `lab9.eval.make_task` (chạy shape Lab 8 trên SQLite tạm) |
| Model | MockLLM của Lab 8 bên trong shape | Inspect bắt buộc có tên model nên dùng `mockllm/model`, nhưng không bao giờ gọi tới (tiền không do model quyết) |
| `approval_bypass` | `ApprovalBypass` | `@scorer approval_bypass`, metric tự viết `total()` (đếm vi phạm, không lấy trung bình) |
| `refund_amount_correct` | `RefundAmountCorrect` | `@scorer refund_amount_correct`, metric `mean()` |
| `reply_amount_matches_ledger` (FM-08) | `ReplyAmountMatchesLedger` | `@scorer reply_amount_matches_ledger`, metric `mean()` |

Ba scorer được **viết lại từ luật chấm** (PRD mục 4), không import từ `lab9/graders.py`; regex lấy số tiền cũng viết lại. Nếu bản port chỉ gọi lại grader cũ thì kết quả khớp là đương nhiên và không chứng minh được gì.

**Probe để phủ đủ hai phía.** Không variant công khai nào ở v1 làm `approval_bypass` bắn, và chỉ có `seeded_regression` làm grader FM-08 bắn. Vì vậy script đăng ký lúc chạy hai shape thăm dò: `bonus_sibling_refund` (fixed + `sibling_refund`, FM-07) và `bonus_misreport` (fixed + `misreport`, FM-08). Không sửa `lab9/variants.py` hay `lab8/`.

## Kết quả

Spec v1 (eval set `68e19b98b86d`, 14 item × 5 seed):

| Shape | Metric | pydantic-evals | Inspect | Khớp từng trial | Số trial bị chấm lỗi |
|---|---|---|---|---|---|
| swarm | approval_bypass / refund_amount_correct / reply | 0 / 0.9857 / 1.0 | 0 / 0.9857 / 1.0 | 70/70 ×3 | 0 / 1 / 0 |
| fixed | (cùng 3 metric) | 0 / 1.0 / 1.0 | 0 / 1.0 / 1.0 | 70/70 ×3 | 0 / 0 / 0 |
| seeded_regression | | 0 / 0.8571 / 0.7857 | 0 / 0.8571 / 0.7857 | 70/70 ×3 | 0 / 10 / 15 |
| bonus_sibling_refund | | **20** / 0.7143 / 1.0 | **20** / 0.7143 / 1.0 | 70/70 ×3 | 20 / 20 / 0 |
| bonus_misreport | | 0 / 1.0 / **0.8571** | 0 / 1.0 / **0.8571** | 70/70 ×3 | 0 / 0 / 10 |

**Khớp 1050/1050 lượt chấm (v1)**. Trên spec v2 (28 item × 5 seed, 8 shape, gồm `honest_refactor`, `slice_regression`, `flaky_regression`): **khớp 3360/3360**. Mỗi grader đều có cả trường hợp PASS lẫn FAIL, nên sự trùng khớp có ý nghĩa.

**Đối chứng âm.** Cố tình làm hỏng regex của bản port (chỉ nhận "... đồng", bỏ sót "35.000đ") thì so sánh báo `agreement 200/210` và 10 dòng `MISMATCH E-008/E-014 ... pydantic-evals=False inspect=True`, exit 1. Bộ so sánh không phải lúc nào cũng báo khớp. Đối chứng này được giữ lại thành test `test_comparison_is_not_vacuous`.

## Nhận xét khi port

- **Định dạng số tiền là chỗ dễ lệch nhất**: "35.000đ", "35000 đồng", "35,000 VND". Hai bản regex viết độc lập vẫn ra cùng kết quả trên mọi reply của agent, nhưng đối chứng âm cho thấy chỉ cần lệch một đơn vị tiền tệ là grader FM-08 im lặng. Đây là lý do tiền phải chấm bằng code **và** code đó cần có test.
- **Metric an toàn không lấy trung bình**: `mean()` của Inspect sẽ biến 20 vụ bypass thành 0.29. Phải tự viết metric `total()` để giữ nghĩa "đếm vi phạm, phải bằng 0".
- **Seed tường minh thay vì `epochs`**: Inspect có `epochs` để chạy lặp, nhưng seed của MockLLM phải cố định để so được từng cặp (item, seed). Vì vậy mỗi seed là một `Sample` riêng, giống `Case` của pydantic-evals.
- **Log của Inspect được đọc lười** từ `log_dir`: phải đọc kết quả trước khi xoá thư mục tạm (lỗi đầu tiên gặp phải khi port).
- Giới hạn: hai harness dùng chung phần chạy agent (`make_task`), nên phép so này kiểm **grader**, không kiểm runner. Runner đã được `ci_check` kiểm riêng (baseline khớp lần chạy của CI).
