"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS

SAFETY_COUNTS = ("secret_leak", "approval_bypass")   # aggregated as a count, not a mean


def _is_bad_number(v) -> bool:
    return isinstance(v, float) and (math.isnan(v) or math.isinf(v))


def _graded_ids(spec: dict) -> list[str]:
    """Spec metrics that a grader scores row by row (the rest are aggregates like pass_5)."""
    return [m["id"] for m in spec.get("metrics") or [] if m["id"] in GRADERS]


def _pairs(rep: dict) -> list[tuple[str, int]]:
    return [(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []]


def new_failures(pr: dict, base: dict, metric: str, safety: bool = False) -> int:
    """Rows (same item, same seed) that were good on the baseline and are bad on the PR.
    Averages hide a bug that fails 3 rows on seed 33 when the baseline failed 3 other rows on
    seed 55; a paired row-level count does not. Valid only after integrity_check has proven the
    (item, seed) sets are identical. With MockLLM a re-run of the same rows changes nothing
    (A/A noise 0), so the allowed count is 0; with a real LLM, set it from repeated A/A runs."""
    good = (lambda v: v is False) if safety else (lambda v: v is True)
    was = {(r["item_id"], r["seed"]): r.get(metric) for r in base.get("rows") or []}
    return sum(1 for r in pr.get("rows") or []
               if good(was.get((r["item_id"], r["seed"]))) and not good(r.get(metric)))


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Same eval-set version => every threshold may only get stricter. Returns problems."""
    out: list[str] = []
    same_version = (spec.get("eval_set_version") == base_spec.get("eval_set_version")
                    or spec.get("eval_set") == base_spec.get("eval_set"))
    if not same_version:
        return out                          # new eval-set version: new baseline, thresholds restart
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        out.append(f"ratchet: eval_set_version renamed {base_spec.get('eval_set_version')!r} -> "
                   f"{spec.get('eval_set_version')!r} on the same eval set {spec.get('eval_set')}")
    for key in ("seeds", "bootstrap", "pass_k", "eval_set", "labels", "adversarial_pack"):
        if key in base_spec and spec.get(key) != base_spec.get(key):
            out.append(f"ratchet: spec[{key!r}] changed {base_spec.get(key)!r} -> {spec.get(key)!r}")

    def compare(kind: str, old: dict, new: dict | None) -> None:
        if new is None:
            out.append(f"ratchet: {kind} removed")
            return
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"ratchet: {kind} no longer blocking")
        o_th = old.get("threshold", old) if kind.startswith("metric") else old
        n_th = new.get("threshold", new) if kind.startswith("metric") else new
        for k in ("max", "max_increase", "max_new_failures"):
            if k in o_th and (k not in n_th or n_th[k] > o_th[k]):
                out.append(f"ratchet: {kind} {k} loosened {o_th[k]} -> {n_th.get(k)}")
        if "min" in o_th and ("min" not in n_th or n_th["min"] < o_th["min"]):
            out.append(f"ratchet: {kind} min loosened {o_th['min']} -> {n_th.get('min')}")
        if o_th.get("no_significant_drop") and not n_th.get("no_significant_drop"):
            out.append(f"ratchet: {kind} dropped no_significant_drop")

    new_metrics = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        compare(f"metric {m['id']}", m, new_metrics.get(m["id"]))
    key = lambda sl: (sl.get("name"), sl.get("metric", "pass_rate"))
    new_slices = {key(sl): sl for sl in spec.get("slices") or []}
    for sl in base_spec.get("slices") or []:
        compare(f"slice {sl.get('name')}/{sl.get('metric', 'pass_rate')}", sl, new_slices.get(key(sl)))
    return out


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows = pr.get("rows") or []
    man, bman = pr.get("manifest") or {}, base.get("manifest") or {}
    graded = _graded_ids(spec)

    # 1. metric values that compare False against every threshold
    for k, v in (pr.get("metrics") or {}).items():
        if _is_bad_number(v):
            problems.append(f"metric {k} is {v} (NaN/inf slips past every threshold)")

    # 2. row count and manifest: every (item, seed) exactly once
    seeds, items = man.get("seeds") or [], man.get("items")
    if not isinstance(items, int) or len(rows) != items * len(seeds):
        problems.append(f"{len(rows)} rows != {items} items x {len(seeds)} seeds")
    if seeds != bman.get("seeds") or items != bman.get("items"):
        problems.append(f"manifest {items} items / seeds {seeds} != baseline "
                        f"{bman.get('items')} items / seeds {bman.get('seeds')}")
    pairs = _pairs(pr)
    dup = sorted({p for p in pairs if pairs.count(p) > 1}, key=str)
    if dup:
        problems.append(f"{len(dup)} duplicated (item, seed) row(s), first {dup[0]}")

    # 3. same (item, seed) set as the baseline: no dropped items, no cherry-picked seeds
    have, want = set(pairs), set(_pairs(base))
    if have != want:
        problems.append(f"(item, seed) set differs from baseline: {len(want - have)} missing, "
                        f"{len(have - want)} extra")

    # 4. graders that crashed or rows whose graded value is not a real verdict
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s), first: "
                        f"{str(pr['evaluator_failures'][0])[:80]}")
    for m in graded:
        bad = [r for r in rows if not isinstance(r.get(m), bool)]
        if bad:
            problems.append(f"{len(bad)} row(s) with no verdict for {m} (None/NaN/missing drops out of the mean)")

    # 5. headline metrics must be what the rows say (a hand-edited number is not evidence)
    if rows and not any(p.startswith(f"{len(rows)} rows") for p in problems):
        ids = [m for m in graded if all(isinstance(r.get(m), bool) for r in rows)]
        safe = [{**r, "cost": float(r.get("cost") or 0.0)} for r in rows]
        again = aggregate(safe, ids, k_values=spec.get("pass_k", []))
        has_cost = all("cost" in r for r in rows)
        for k, v in (pr.get("metrics") or {}).items():
            if k not in again or (k == "cost_per_success" and not has_cost) or _is_bad_number(v):
                continue
            w = again[k]
            if (v is None) != (w is None) or (v is not None and abs(float(v) - float(w)) > 1e-6):
                problems.append(f"metric {k}={v} but the rows give {w} (edited report)")

    # 6. ratchet: thresholds only get stricter on the same eval-set version
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)

    # 7. slice tags come from the label, so they must match the baseline item by item
    base_tags: dict[str, set] = {}
    for r in base.get("rows") or []:
        base_tags.setdefault(r.get("item_id"), set(r.get("slices") or []))
    moved = sorted({r.get("item_id") for r in rows if r.get("item_id") in base_tags
                    and set(r.get("slices") or []) != base_tags[r.get("item_id")]}, key=str)
    if moved:
        problems.append(f"slice tags differ from baseline on {len(moved)} item(s), first {moved[0]}")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl.get("metric", "pass_rate")
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"             # a slice with no items cannot vouch for anything
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            if sl.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027),
                                                       confidence=bs.get("confidence", 0.95))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}  (n={len(a)} items)")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if "max_new_failures" in th and val is not None:
            n_new = new_failures(pr, base, mid, safety=m.get("kind") == "safety")
            note = f"{note} new fails {n_new}".strip()
            if n_new > th["max_new_failures"]:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
