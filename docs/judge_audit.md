# Judge audit

Chạy: `python -m lab9.judge --csv <file> --audit 5` (backend replay, verdict của judge lưu sẵn trong CSV; FAIL là lớp dương).

| Tiêu chí | n | κ | Precision | Recall | Đạt FLOORS (κ≥0.6, P≥0.8, R≥0.7)? | Quyết định |
|---|---|---|---|---|---|---|
| tone_ok | 100 | 0.757 | 0.857 | 0.800 | **Đạt** (tp 24, fp 4, fn 6, tn 66) | **dùng** (tín hiệu tham khảo, không chặn tiền) |
| helpful | 100 | 0.375 | 1.000 | 0.300 | **Không đạt** κ và R (tp 9, fp 0, fn 21, tn 70) | **loại** |

Tỉ lệ fail hiệu chỉnh Rogan-Gladen: tone quan sát 0.28 → 0.30; helpful quan sát 0.09 → 0.30. Judge helpful báo chỉ 9% reply có vấn đề trong khi tỉ lệ thật khoảng 30%: nó **đếm thiếu hơn 3 lần**.

## ≥5 case audit thủ công

| # | Case | Judge nói | Người nói | Ai đúng? | Ghi chú / sửa rubric |
|---|---|---|---|---|---|
| 1 | H-008 "Em đã hoàn tiền cho anh rồi nha, anh check giúp em." | PASS | FAIL | Người | Xưng "em", "nha" quá suồng sã với khách khiếu nại. Rubric thêm: *xưng hô phải là "LogiX"/"anh/chị"; "em/bạn/nha/nhé" ⇒ FAIL* |
| 2 | H-017 "Dạ, bạn ơi LogiX đã hoàn 25.000đ ... rồi nha." | PASS | FAIL | Người | Cùng lỗi xưng hô; judge coi "thân thiện" là lịch sự. Thêm 2 ví dụ few-shot FAIL có "bạn ơi" |
| 3 | H-029 "Dạ, vui lòng chờ. ... không cần nhắn thêm." | PASS | FAIL | Người | Judge chỉ thấy "Dạ, vui lòng"; vế sau là giọng ra lệnh. Rubric: *chấm cả câu, một vế ra lệnh/gạt khách ⇒ FAIL* |
| 4 | H-042 "Dạ, đơn O-808 đã hoàn 120.000đ. Tiền về trong 3–5 ngày ạ." | FAIL | PASS | Người | Judge phạt vì thiếu xin lỗi. Rubric: *xin lỗi không bắt buộc khi đã giải quyết xong; ngắn mà đủ ý là PASS* |
| 5 | H-051 "Dạ, LogiX đã refund 90.000đ ..." | FAIL | PASS | Người | Style guide CS LogiX cho phép từ "refund". Ghi vào rubric danh sách từ tiếng Anh được phép |
| 6 | H-063 "... đã hoàn 20.000đ và sẽ hoàn thêm cho anh/chị ạ." | PASS | FAIL | Người | Hứa ngoài kết quả hệ thống: đây là lỗi **nội dung/tiền**, không phải giọng. Chuyển sang grader code (so reply với ledger, FM-08), không bắt judge tone gánh |
| 7 | H-070 "... cho đơn O-909 ạ 😂😂" | PASS | FAIL | Người | Rubric: *emoji trong ngữ cảnh khiếu nại ⇒ FAIL* |
| 8 | H-081 "Dạ, đơn O-107 chưa đủ điều kiện hoàn ..." | FAIL | PASS | **Judge** | Ghi chú CSV: người đọc nhầm ticket. Sửa nhãn người (không sửa rubric); tính lại κ sau khi sửa |
| 9 | H-088 "Dạ, dạ, LogiX đã hoàn 40.000đ ..." | FAIL | PASS | Không rõ | Lặp "Dạ" do lỗi gõ. Cần quy ước: lỗi gõ nhẹ không đổi giọng ⇒ PASS; đưa vào rubric để hai bên nhất quán |
| 10 | H-094 "Da, LogiX da hoan 40.000đ ..." | PASS | FAIL | Người | Mất dấu tiếng Việt. Rubric: *thiếu dấu ⇒ FAIL* |
| 11 | HL-015 "Dạ, LogiX đã hoàn 55.000đ cho đơn O-112 ạ." (ledger 40.000đ) | PASS | FAIL | Người | Judge helpful không có ledger nên không thể biết số sai. 18/21 FN của helpful là "sai tiền" |
| 12 | HL-016 xin lỗi 2 lần + "đã hoàn 55.000đ" (ledger 35.000đ) | PASS | FAIL | Người | Câu dài, lịch sự che số sai: judge thưởng độ dài/giọng. Đúng loại lỗi FM-08 |
| 13 | HL-047 "Ok bạn, đơn O-515 xử lý rồi nhé." | PASS | FAIL | Người | Sai xưng hô và không nói kết quả cụ thể (3/21 FN) |

## Kết luận
- **helpful bị loại.** κ = 0.375 < 0.6 và R = 0.30 < 0.7: trong 30 reply người chấm FAIL, judge chỉ bắt được 9. Ma trận nhầm lẫn (fn 21, fp 0) cho thấy judge gần như không bao giờ nói FAIL; P = 1.0 chỉ vì nó hiếm khi lên tiếng. 18/21 lần bỏ sót là **sai số tiền** (reply nói 55.000đ, ledger 40.000đ). Judge không đọc ledger nên về nguyên tắc không chấm được tiền; dùng nó làm gate sẽ cho qua đúng loại lỗi đắt nhất (FM-08). Thay bằng grader code `reply_amount_matches_ledger` (blocking, min 1.0).
- **tone_ok được dùng** (đạt cả ba sàn), nhưng chỉ làm tín hiệu chất lượng giọng văn, không chặn release về tiền/approval. Sửa rubric theo bảng trên (xưng hô, emoji, thiếu dấu, xin lỗi không bắt buộc, từ tiếng Anh được phép) rồi calibrate lại.
- Rủi ro còn lại: judge chỉ được kiểm trên dữ liệu replay đã công khai (100 dòng/tiêu chí); cần nhãn người mới để có held-out thật. Một nhãn người sai (H-081) cho thấy chính nhãn người cũng cần kiểm chéo (2 người chấm, đo κ giữa người với người).
