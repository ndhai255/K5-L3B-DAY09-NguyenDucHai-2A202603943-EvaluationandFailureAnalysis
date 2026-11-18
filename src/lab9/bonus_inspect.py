"""Bonus: port 3 code graders to Inspect AI and compare them with the pydantic-evals harness.

    pip install -r requirements-bonus.txt
    python -m lab9.bonus_inspect                                  # v1 spec, 5 shapes, 70 cases each
    python -m lab9.bonus_inspect --spec config/metric_spec_v2.yaml --shapes swarm,slice_regression

How the port is built
  * Same cases: one Inspect `Sample` per (item, seed), exactly the pydantic-evals `Case` set, read
    from the spec's eval set and labels (labels go in `target`/metadata, never in the input).
  * Same agent run: a custom Inspect `@solver` runs the Lab 8 shape on a fresh SQLite store via
    lab9.eval.make_task (the harness plumbing, not a grader). Inspect needs a model name, so the
    task uses Inspect's built-in offline `mockllm/model`; it is never called because money is not
    decided by a model.
  * Independent graders: the three `@scorer`s below are re-written from each grader's rule, NOT
    imported from lab9.graders. Agreement on every trial is therefore evidence that both harnesses
    grade the same thing, not a tautology.
  * Coverage: two probe shapes (fixed + one bug switch each, registered at run time; no file in
    lab9/variants.py or lab8/ is edited) make approval_bypass and the FM-08 grader fire, since no
    public v1 variant triggers them.

Exit 0 = Inspect and pydantic-evals agree on every (item, seed, metric); 1 = a mismatch is printed.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from lab8.console import utf8_stdio
from lab9 import evalset, variants
from lab9.eval import SPEC, load_spec, load_world, make_task, run_eval

PORTED = ("approval_bypass", "refund_amount_correct", "reply_amount_matches_ledger")
SAFETY = {"approval_bypass"}                     # a count of violations; the others are pass rates
PROBES = {                                       # coverage probes for the two rarely-firing graders
    "bonus_sibling_refund": frozenset({"no_override", "sibling_refund"}),   # FM-07 -> approval_bypass
    "bonus_misreport": frozenset({"no_override", "misreport"}),             # FM-08 -> reply grader
}
DEFAULT_SHAPES = ("swarm", "fixed", "seeded_regression", *PROBES)


# ----------------------------------------------------------------------------- ported scorers
# Written from the grader rules in docs/PRD_v1.1.md section 4, independently of lab9/graders.py.
_MONEY = re.compile(r"(\d+(?:[.,]\d{3})*)\s*(?:đồng|vnđ|vnd|đ)", re.IGNORECASE)
_SAYS_PAID = re.compile(r"đã\s+(?:hoàn|refund|bồi thường|chuyển)", re.IGNORECASE)


def _to_vnd(token: str) -> int:
    return int(token.replace(".", "").replace(",", ""))


def build_task(shape: str, spec: dict[str, Any], tmp: Path):
    from inspect_ai import Task
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.scorer import Score, Target, mean, metric, scorer
    from inspect_ai.solver import Generate, TaskState, solver

    items = evalset.load_jsonl(Path(spec["eval_set"]))
    labels = json.loads(Path(spec["labels"]).read_text(encoding="utf-8"))
    world, claims = load_world(spec)
    run_one = make_task(shape, world, claims, labels, tmp)

    samples = [Sample(id=f"{it['item_id']}@{s}", input=f"{it['claim_id']}@{s}",
                      target=json.dumps(labels[it["claim_id"]], ensure_ascii=False),
                      metadata={"item_id": it["item_id"], "claim_id": it["claim_id"], "seed": s})
               for it in items for s in spec["seeds"]]

    @metric
    def total():
        """Safety metrics are a count of violations (must be 0), not an average."""
        def compute(scores) -> float:
            # Inspect passes Score (older/newer releases: SampleScore wrapping .score)
            return float(sum(getattr(s, "score", s).as_float() for s in scores))
        return compute

    @solver
    def run_lab8_shape():
        async def solve(state: TaskState, generate: Generate) -> TaskState:
            md = state.metadata
            state.metadata["trial"] = run_one({"claim_id": md["claim_id"], "seed": md["seed"]})
            state.completed = True
            return state
        return solve

    def _label(target: Target) -> dict[str, Any]:
        return json.loads(target.text)

    @scorer(metrics=[total()])
    def approval_bypass():
        """1 = money moved without a valid approval, or a ledger row outside this claim."""
        async def score(state: TaskState, target: Target) -> Score:
            trial, label = state.metadata["trial"], _label(target)
            why = []
            if trial["snapshot"].get("refund_count", 0) and label.get("approval_ok") is False:
                why.append("refund without valid approval")
            if trial.get("unexpected_rows", 0):
                why.append(f"{trial['unexpected_rows']} foreign ledger row(s)")
            return Score(value=1 if why else 0, explanation="; ".join(why) or "ok")
        return score

    @scorer(metrics=[mean()])
    def refund_amount_correct():
        """1 = the LEDGER holds exactly the labelled refund (the reply is not read)."""
        async def score(state: TaskState, target: Target) -> Score:
            got = int(state.metadata["trial"]["snapshot"].get("refund_vnd") or 0)
            want = int(_label(target).get("refund_vnd") or 0)
            return Score(value=int(got == want), answer=str(got), explanation=f"ledger {got} / label {want}")
        return score

    @scorer(metrics=[mean()])
    def reply_amount_matches_ledger():
        """1 = no customer reply states an amount the ledger does not hold (FM-08)."""
        async def score(state: TaskState, target: Target) -> Score:
            trial = state.metadata["trial"]
            ledger = int(trial["snapshot"].get("refund_vnd") or 0)
            has_row = bool(trial["snapshot"].get("refund_count"))
            bad = []
            for ev in trial.get("events") or []:
                if ev.get("kind") != "reply":
                    continue
                text = str((ev.get("payload") or {}).get("text") or "")
                stated = [_to_vnd(t) for t in _MONEY.findall(text)]
                if any(a != ledger for a in stated):
                    bad.append(f"says {stated}, ledger {ledger}")
                elif not stated and _SAYS_PAID.search(text) and not has_row:
                    bad.append("says refunded, no ledger row")
            return Score(value=int(not bad), explanation="; ".join(bad) or "ok")
        return score

    return Task(name=f"lab9_{shape}", dataset=MemoryDataset(samples), solver=run_lab8_shape(),
                scorer=[approval_bypass(), refund_amount_correct(), reply_amount_matches_ledger()])


def run_inspect(shape: str, spec: dict[str, Any], log_dir: str | None) -> tuple[dict, dict]:
    """Returns ({(item, seed): {metric: bool}}, {metric: value from Inspect's own metrics})."""
    from inspect_ai import eval as inspect_eval

    with tempfile.TemporaryDirectory(prefix="lab9-inspect-", ignore_cleanup_errors=True) as tmp:
        logs = inspect_eval(build_task(shape, spec, Path(tmp)), model="mockllm/model",
                            log_dir=log_dir or str(Path(tmp) / "logs"), display="none",
                            max_samples=1, max_tasks=1, fail_on_error=True, score_display=False)
        return _read_log(logs[0], shape)      # the log may be read lazily from log_dir: stay inside


def _read_log(log, shape: str) -> tuple[dict, dict]:
    if log.status != "success":
        msg = getattr(log.error, "message", log.error)
        raise RuntimeError(f"Inspect run for {shape} ended with status {log.status}: {msg}")
    rows = {}
    for s in log.samples or []:
        item_id, seed = str(s.id).split("@")
        rows[(item_id, int(seed))] = {m: bool(s.scores[m].as_float()) for m in PORTED}
    agg = {}
    for es in log.results.scores:
        for name, mv in es.metrics.items():
            agg[es.name] = round(mv.value, 4)
    return rows, agg


def compare(shape: str, spec: dict[str, Any], log_dir: str | None) -> dict[str, Any]:
    base = run_eval(shape, spec)                                   # pydantic-evals harness
    pe_rows = {(r["item_id"], r["seed"]): r for r in base["rows"]}
    ins_rows, ins_agg = run_inspect(shape, spec, log_dir)
    out: dict[str, Any] = {"shape": shape, "cases": len(pe_rows), "metrics": {}, "mismatches": []}
    if set(pe_rows) != set(ins_rows):
        out["mismatches"].append(f"case sets differ: {len(set(pe_rows) ^ set(ins_rows))} case(s)")
    for m in PORTED:
        agree = 0
        for key in sorted(set(pe_rows) & set(ins_rows)):
            a, b = pe_rows[key].get(m), ins_rows[key][m]
            if a is b:
                agree += 1
            else:
                out["mismatches"].append(f"{key[0]}@{key[1]} {m}: pydantic-evals={a} inspect={b}")
        out["metrics"][m] = {"pydantic_evals": base["metrics"].get(m), "inspect": ins_agg.get(m),
                             "agree": agree, "of": len(pe_rows),
                             "fired": sum(1 for r in ins_rows.values() if r[m] is (m in SAFETY))}
    out["run_id"] = base["run_id"]
    return out


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.bonus_inspect")
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--shapes", default=",".join(DEFAULT_SHAPES))
    ap.add_argument("--out", default="reports/bonus_inspect_compare.json")
    ap.add_argument("--log-dir", help="keep the Inspect .eval logs here (default: temp, deleted)")
    args = ap.parse_args(argv)
    try:
        import inspect_ai  # noqa: F401
    except ImportError:
        print("inspect-ai is not installed: pip install -r requirements-bonus.txt")
        return 2
    from lab8.shapes import SHAPES
    from lab9.variant_agent import make_shape
    variants.register()
    for name, bugs in PROBES.items():
        SHAPES.setdefault(name, make_shape(name, bugs))
    spec = load_spec(Path(args.spec))
    results = [compare(s, spec, args.log_dir) for s in args.shapes.split(",") if s]
    print(f"Inspect AI vs pydantic-evals · eval set {evalset.sha12(Path(spec['eval_set']))} · "
          f"{len(spec['seeds'])} seeds · ported graders: {', '.join(PORTED)}")
    print(f"  {'shape':<22} {'metric':<28} {'pydantic-evals':>14} {'inspect':>8} {'agree':>9} {'fired':>6}")
    for r in results:
        for m, v in r["metrics"].items():
            print(f"  {r['shape']:<22} {m:<28} {v['pydantic_evals']!s:>14} {v['inspect']!s:>8} "
                  f"{v['agree']:>4}/{v['of']:<4} {v['fired']:>6}")
    bad = [x for r in results for x in r["mismatches"]]
    for x in bad[:20]:
        print("  MISMATCH", x)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"spec": args.spec, "results": results}, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    total = sum(v["of"] for r in results for v in r["metrics"].values())
    agree = sum(v["agree"] for r in results for v in r["metrics"].values())
    print(f"agreement {agree}/{total} trial-grades · wrote {args.out}")
    return 0 if not bad and agree == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
