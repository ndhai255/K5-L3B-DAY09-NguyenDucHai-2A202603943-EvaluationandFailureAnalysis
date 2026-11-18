"""Regression test from docs/trace_case.md: seeded_regression C-014@11 (FM-05 pre-deduction).

First wrong step: event 5 `proposal` = 120.000 (the agent subtracted the 30.000 already refunded
from the 150.000 quote). The host subtracts it again, so the ledger holds 90.000 while the reply
says 120.000. The test runs the shape on a temp store (no team DB) and must:
  - fail RefundAmountCorrect and ReplyAmountMatchesLedger on seeded_regression,
  - pass both on fixed (the same claim@seed, honest path).
"""
import pytest

pytestmark = pytest.mark.student


def _grade(shape: str, claim: str = "C-014", seed: int = 11) -> dict:
    from pydantic_evals.evaluators import EvaluatorContext

    from lab9 import graders
    from lab9.trace_case import investigate

    case = investigate(shape, claim, seed)
    ctx = EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=case["label"],
                           output=case["trial"], duration=0.0, _span_tree=None, attributes={}, metrics={})
    return {"trial": case["trial"],
            "refund": graders.RefundAmountCorrect().evaluate(ctx).value,
            "reply": graders.ReplyAmountMatchesLedger().evaluate(ctx).value,
            "proposal": graders.ProposalCorrect().evaluate(ctx).value}


def test_trace_case_c014_first_wrong_step_is_pre_deduction():
    bad = _grade("seeded_regression")
    kinds = [e["kind"] for e in bad["trial"]["events"]]
    step = kinds.index("proposal")                       # 0-based; event 5 in trace_case output
    assert kinds[step - 1] == "refund_history"           # read prior refund, then deducted it
    assert bad["trial"]["events"][step]["payload"]["amount_vnd"] == 120000
    assert bad["trial"]["snapshot"]["refund_vnd"] == 90000
    assert (bad["refund"], bad["reply"], bad["proposal"]) == (False, False, False)


def test_trace_case_c014_fixed_is_clean():
    ok = _grade("fixed")
    assert "proposal" not in [e["kind"] for e in ok["trial"]["events"]]
    assert ok["trial"]["snapshot"]["refund_vnd"] == 120000
    assert (ok["refund"], ok["reply"], ok["proposal"]) == (True, True, True)
