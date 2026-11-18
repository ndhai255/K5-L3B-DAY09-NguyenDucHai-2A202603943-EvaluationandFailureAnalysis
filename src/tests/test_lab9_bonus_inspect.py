"""Bonus: the Inspect AI port of 3 graders agrees with pydantic-evals trial by trial.
Skipped when inspect-ai is not installed (CI installs only requirements-lab9.txt)."""
import pytest

pytestmark = pytest.mark.student
pytest.importorskip("inspect_ai")


@pytest.fixture(scope="module")
def setup():
    from lab8.shapes import SHAPES
    from lab9 import bonus_inspect, variants
    from lab9.eval import load_spec
    from lab9.variant_agent import make_shape
    from pathlib import Path

    variants.register()
    for name, bugs in bonus_inspect.PROBES.items():
        SHAPES.setdefault(name, make_shape(name, bugs))
    return bonus_inspect, load_spec(Path("config/metric_spec.yaml"))


@pytest.mark.parametrize("shape", ["seeded_regression", "bonus_sibling_refund", "bonus_misreport"])
def test_inspect_port_agrees_with_pydantic_evals(setup, shape):
    bi, spec = setup
    res = bi.compare(shape, spec, None)
    assert res["mismatches"] == []
    for m, v in res["metrics"].items():
        assert v["agree"] == v["of"] == 70, m


def test_comparison_is_not_vacuous(setup, monkeypatch):
    """A deliberately broken port (regex misses '35.000đ') must produce mismatches."""
    import re
    bi, spec = setup
    monkeypatch.setattr(bi, "_MONEY", re.compile(r"(\d+)\s*đồng"))
    res = bi.compare("bonus_misreport", spec, None)
    assert len(res["mismatches"]) == 10
