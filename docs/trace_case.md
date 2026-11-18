# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-014 --seed 11
python -m lab9.trace_case --shape fixed --claim C-014 --seed 11   # đối chứng
```

Output thật (rút gọn payload):

```
== seeded_regression C-014@11  lab8 grader: FAIL (proposal_mismatch)
   1 handoff            {... "task": "check_applicable_policy" ...}
   2 investigator       {"lookup_order": {"visible": true, "order": {"order_id": "O-515", "status": "delivered_late", ...}}}
   3 quote              {"amount_vnd": 150000, "reason": "entitled", "clause_ids": ["SHIP-LATE-03", "DMG-04"], ...}
   4 refund_history     {"refund_count": 1, "refunded_vnd": 30000}
   5 proposal           {"amount_vnd": 120000}
   6 reply              {"text": "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515."}
-- ledger vs label
  refund_vnd     ledger=90000    label=120000   <-- differs
  refund_count   ledger=1        label=1
  status         result=resolved label=resolved
```

## Case: `seeded_regression C-014@11`

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-014@11`, report `E-seeded_regression-20261118-68e1` (item E-014, fail cả 5 seed) |
| Triệu chứng | Lab 8 grader `proposal_mismatch`; `refund_amount_correct` FAIL (ledger 90.000 ≠ nhãn 120.000); `reply_amount_matches_ledger` FAIL (reply nói 120.000đ, ledger 90.000đ). Status `resolved` khớp nhãn nên nhìn status thì tưởng đúng |
| Bước sai đầu tiên | Event 5 `proposal {"amount_vnd": 120000}`: agent lấy quote 150.000 trừ 30.000 đã hoàn rồi mới gửi host |
| Vì sao là bước *đầu tiên* | Event 2 đọc đúng đơn O-515. Event 3 quote 150.000 đúng nhãn `proposal_amount`. Event 4 chỉ là đọc lịch sử, đọc thì chưa sai. Sai bắt đầu khi agent tự dùng số đó để tính tiền (`model.may_compute_money: false`); host theo `cap_per_order` trừ thêm 30.000 lần nữa → 90.000 |
| Failure mode | **FM-05** pre_deducted (kéo theo FM-08: reply nói số agent tự tính, không phải số ledger) |
| Đối chứng | `fixed C-014@11`: event 3 quote 150.000, **không có** event `refund_history`/`proposal`, gửi nguyên 150.000 cho host → ledger 120.000, reply "đã hoàn 120.000đ", lab8 PASS |
| Regression test | `src/tests/test_lab9_trace_case.py`: `test_trace_case_c014_first_wrong_step_is_pre_deduction` assert event ngay trước `proposal` là `refund_history`, proposal = 120.000, ledger = 90.000, và `RefundAmountCorrect`, `ReplyAmountMatchesLedger`, `ProposalCorrect` đều False; `test_trace_case_c014_fixed_is_clean` assert cả ba True trên `fixed`. Đã chạy: fail đúng chỗ trên variant lỗi, pass trên `fixed` (`13 passed` với `-k "lab9 and not advanced"`) |
| Chưa chắc chắn | Trace không ghi lời gọi `issue_refund` và cách host trừ (chỉ suy ra từ ledger 90.000 và `docs/business_rules.md` của Day 8). MockLLM tất định nên không biết model thật có tự trừ thường xuyên không; case chỉ chứng minh grader bắt được khi lỗi xảy ra |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
