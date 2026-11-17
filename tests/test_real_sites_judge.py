"""The judge of the real-sites round (scripts/judge_real_sites.py): it is red where stock Firefox gets through a page and the published wrapper does not, and only there.

The judge is a pure function of the cells the two browsers wrote. A cell is *good* when the page loaded, reached ``networkidle``, answered 2xx, showed its marker
and the canary page after it was quiet. The rule, per site, on the runs available for that site (the first pass, plus two more runs of each browser for the
cells that differ):

* RED: stock is good in every run and ours is not good in at least two of three;
* NO INFORMATION: stock is not good in every run (a blocked, fallen or changed site): never red, ours is reported as data;
* the job is red too when a site was not visited by both browsers, or when fewer cells than ``min_informative`` carry information ("il giro non ha misurato").

The six cases of the design come first, each on known-bad input; the rest are the helpers around them. No time is read or asserted anywhere here.
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("judge_real_sites_under_test", ROOT / "scripts" / "judge_real_sites.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass reads its own module
    spec.loader.exec_module(module)
    return module


judge_module = _load()
judge = judge_module.judge

GOOD = {"load": "ok", "networkidle": "ok", "status": "2xx", "marker": True, "canary": "ok"}
SITE = {"id": "site-a", "url": "https://a.example/", "marker": "#main", "class": "calma"}
OTHER = {"id": "site-b", "url": "https://b.example/", "marker": "#main", "class": "giornale"}


def cell(**over):
    return {**GOOD, **over}


def runs_of(site_id, *cells):
    """One run per cell: a run is the cells of one pass, keyed by site."""
    return [{site_id: c} for c in cells]


def row(result, site_id):
    return next(r for r in result.table if r["id"] == site_id)


# --- the six cases of the design --------------------------------------------------------------------------------------------------------------
def test_stock_good_three_of_three_and_ours_good_once_is_red():
    stock = runs_of("site-a", cell(), cell(), cell())
    ours = runs_of("site-a", cell(), cell(load="timeout"), cell(status="4xx"))
    result = judge(stock, ours, [SITE], 0)
    assert row(result, "site-a")["state"] == "red"
    assert result.verdict == "red" and result.exit_code == 1
    assert result.red == 1


def test_stock_good_three_of_three_and_ours_good_twice_is_green():
    stock = runs_of("site-a", cell(), cell(), cell())
    ours = runs_of("site-a", cell(), cell(), cell(networkidle="timeout"))
    result = judge(stock, ours, [SITE], 0)
    assert row(result, "site-a")["state"] == "green"
    assert result.verdict == "green" and result.exit_code == 0
    assert result.red == 0 and result.informative == 1


@pytest.mark.parametrize("stock_cells", [
    (cell(status="4xx"), cell(status="4xx"), cell(status="4xx")),
    (cell(), cell(), cell(marker=False)),
    (cell(load="timeout"), cell(), cell()),
    (cell(canary="no"), cell(canary="no"), cell(canary="no")),
], ids=["blocked in all runs", "bad in the last run", "bad in the first run", "canary of stock fails"])
def test_a_stock_that_is_not_good_gives_no_information_and_is_never_red(stock_cells):
    ours = runs_of("site-a", cell(load="error:NS_ERROR_NET_RESET"), cell(load="timeout"), cell(status="5xx"))
    result = judge(runs_of("site-a", *stock_cells), ours, [SITE], 0)
    assert row(result, "site-a")["state"] == "no_information"
    assert result.red == 0 and result.informative == 0
    assert result.verdict == "green" and result.exit_code == 0


def test_the_outcome_of_ours_is_reported_where_stock_gives_no_information():
    stock = runs_of("site-a", cell(status="4xx"), cell(status="4xx"), cell(status="4xx"))
    ours = runs_of("site-a", cell(), cell(), cell())
    result = judge(stock, ours, [SITE], 0)
    assert row(result, "site-a")["state"] == "no_information"
    assert row(result, "site-a")["ours"]["good"] == 3 and row(result, "site-a")["stock"]["good"] == 0
    assert result.ours_good_stock_not == 1
    assert result.verdict == "green"


def test_a_site_missing_from_one_of_the_two_passes_is_red():
    stock = [{"site-a": cell(), "site-b": cell()}]
    ours = [{"site-a": cell()}]
    result = judge(stock, ours, [SITE, OTHER], 0)
    assert row(result, "site-b")["state"] == "missing"
    assert result.verdict == "red" and result.exit_code == 1
    assert any("site-b" in p and "ours" in p for p in result.problems), result.problems
    # the same site missing from the pass of stock is just as red, and a pass that is not there at all misses every site
    flipped = judge(ours, stock, [SITE, OTHER], 0)
    assert flipped.verdict == "red" and any("site-b" in p and "stock" in p for p in flipped.problems)
    nothing = judge([], stock, [SITE, OTHER], 0)
    assert nothing.verdict == "red" and len(nothing.problems) >= 2


def test_informative_cells_below_the_minimum_make_the_round_red_with_its_reason():
    stock = [{"site-a": cell(), "site-b": cell(status="4xx")}]
    ours = [{"site-a": cell(), "site-b": cell()}]
    result = judge(stock, ours, [SITE, OTHER], 2)
    assert result.informative == 1
    assert result.verdict == "red" and result.exit_code == 1
    assert any("il giro non ha misurato" in p for p in result.problems), result.problems
    enough = judge(stock, ours, [SITE, OTHER], 1)
    assert enough.verdict == "green" and enough.problems == []


def test_a_canary_that_fails_only_for_ours_with_the_real_page_good_is_red():
    stock = runs_of("site-a", cell(), cell(), cell())
    ours = runs_of("site-a", cell(canary="no"), cell(canary="no"), cell(canary="no"))
    for r in ours:
        # the real page was good in every other respect
        assert {k: v for k, v in r["site-a"].items() if k != "canary"} == {k: v for k, v in GOOD.items() if k != "canary"}
    result = judge(stock, ours, [SITE], 0)
    assert row(result, "site-a")["state"] == "red"
    assert "canary" in row(result, "site-a")["ours"]["why"]
    assert result.verdict == "red" and result.exit_code == 1


# --- what "3 of 3" means, and the numbers -----------------------------------------------------------------------------------------------------
def test_three_of_three_means_the_runs_available_for_that_site():
    # a cell that did not differ in the first pass has one run each: good and good is green, and stock good with ours good is not asked again
    result = judge([{"site-a": cell()}], [{"site-a": cell()}], [SITE], 1)
    assert row(result, "site-a")["state"] == "green" and row(result, "site-a")["stock"]["runs"] == 1
    # one run of each where ours is bad: all the runs of ours are bad, so it is red (the round always supplies three for such a cell)
    red = judge([{"site-a": cell()}], [{"site-a": cell(marker=False)}], [SITE], 0)
    assert row(red, "site-a")["state"] == "red"
    # the later runs hold only the differing cells: a site absent from them is not a missing site
    stock = [{"site-a": cell(), "site-b": cell()}, {"site-a": cell()}, {"site-a": cell()}]
    ours = [{"site-a": cell(), "site-b": cell()}, {"site-a": cell()}, {"site-a": cell()}]
    result = judge(stock, ours, [SITE, OTHER], 2)
    assert result.verdict == "green" and row(result, "site-b")["stock"]["runs"] == 1 and row(result, "site-a")["stock"]["runs"] == 3


def test_the_judge_does_not_touch_what_it_is_given():
    stock = runs_of("site-a", cell(), cell(), cell())
    ours = runs_of("site-a", cell(load="timeout"), cell(load="timeout"), cell())
    before = copy.deepcopy((stock, ours, [SITE]))
    judge(stock, ours, [SITE], 0)
    assert (stock, ours, [SITE]) == before


def test_the_three_numbers_of_the_summary():
    sites = [SITE, OTHER, {"id": "site-c", "url": "https://c.example/", "marker": "h1", "class": "negozio"}]
    stock = [{"site-a": cell(), "site-b": cell(), "site-c": cell(status="4xx")}, {"site-a": cell()}, {"site-a": cell()}]
    ours = [{"site-a": cell(load="timeout"), "site-b": cell(), "site-c": cell()}, {"site-a": cell(status="5xx")}, {"site-a": cell(marker=False)}]
    result = judge(stock, ours, sites, 0)
    assert (result.informative, result.red, result.ours_good_stock_not) == (2, 1, 1)
    text = judge_module.render_markdown(result)
    assert "site-a" in text and "site-b" in text and "site-c" in text
    assert "informative cells: 2" in text and "red cells: 1" in text and "ours good, stock not: 1" in text
    assert "| red |" in text and "| no information |" in text


def test_a_cell_is_good_only_when_all_five_outcomes_are():
    assert judge_module.is_good(cell())
    for field, bad in [("load", "timeout"), ("load", "error:NS_ERROR_NET_RESET"), ("networkidle", "timeout"), ("status", "3xx"), ("status", "none"),
                       ("marker", False), ("canary", "no")]:
        assert not judge_module.is_good(cell(**{field: bad})), (field, bad)
    assert not judge_module.is_good({}) and not judge_module.is_good(None)


def test_the_cells_that_differ_are_the_ones_that_get_two_more_runs():
    sites = [SITE, OTHER, {"id": "site-c", "url": "https://c.example/", "marker": "h1", "class": "negozio"}]
    stock = {"site-a": cell(), "site-b": cell(), "site-c": cell(status="4xx")}
    ours = {"site-a": cell(), "site-b": cell(load="timeout"), "site-c": cell()}
    assert judge_module.differing_sites(stock, ours, sites) == ["site-b", "site-c"]
    assert judge_module.differing_sites(stock, stock, sites) == []


# --- the admission of a list ------------------------------------------------------------------------------------------------------------------
def _site(site_id, klass="calma"):
    return {"id": site_id, "url": "https://%s.example/" % site_id, "marker": "#main", "class": klass}


def _passes(per_site, n=5):
    """``per_site``: id -> a cell, or a function of the pass number (to make an unstable site). Five passes of stock."""
    return [{sid: (c(i) if callable(c) else c) for sid, c in per_site.items()} for i in range(n)]


def _decisions(report):
    return {d["id"]: (d["decision"], d["reason"]) for d in report["decisions"]}


def test_admission_keeps_the_good_drops_the_blocked_and_the_unstable_and_says_why():
    sites = [_site("good"), _site("blocked", "giornale"), _site("unstable"), _site("shield", "protetta"), _site("shield-unstable", "protetta")]
    runs = _passes({"good": cell(), "blocked": cell(status="4xx"), "unstable": lambda i: cell(load="timeout") if i == 3 else cell(),
                    "shield": cell(status="4xx"), "shield-unstable": lambda i: cell(status="4xx") if i == 0 else cell()})
    report = judge_module.admission({"linux": runs, "windows": [dict(r) for r in runs]}, sites, expected_runs=5)
    decided = _decisions(report)
    assert decided["good"][0] == "keep" and "good 5 of 5" in decided["good"][1]
    assert decided["blocked"][0] == "drop" and "blocked 5 of 5" in decided["blocked"][1]
    assert decided["unstable"][0] == "drop" and "not the same 5 of 5" in decided["unstable"][1]
    assert decided["shield"][0] == "keep" and "as data" in decided["shield"][1]
    assert decided["shield-unstable"][0] == "drop", "an unstable site is always dropped, whatever its class"
    assert report["kept"] == ["good", "shield"]
    assert report["linux"]["same"] == 3 and report["linux"]["good"] == 1
    text = judge_module.render_admission(report)
    assert "| blocked | giornale |" in text and "| drop |" in text and "| keep |" in text and "not the same 5 of 5" in text


def test_admission_blocked_on_one_system_only_is_blocked_there():
    sites = [_site("a"), _site("b", "protetta")]
    linux = _passes({"a": cell(), "b": cell()})
    windows = _passes({"a": cell(status="4xx"), "b": cell(status="4xx")})
    decided = _decisions(judge_module.admission({"linux": linux, "windows": windows}, sites, expected_runs=5))
    assert decided["a"][0] == "drop" and "windows" in decided["a"][1]
    assert decided["b"][0] == "keep"


def test_admission_keeps_at_most_four_protected_and_twenty_in_all():
    sites = [_site("p%d" % i, "protetta") for i in range(6)] + [_site("c%02d" % i) for i in range(22)]
    runs = _passes({s["id"]: (cell(status="4xx") if s["id"] in ("p0", "p1") else cell()) for s in sites})
    report = judge_module.admission({"linux": runs}, sites, expected_runs=5)
    decided = _decisions(report)
    protected = [i for i in decided if i.startswith("p") and decided[i][0] == "keep"]
    assert sorted(protected) == ["p2", "p3", "p4", "p5"], "within the four, the good ones of the class come before the ones kept as data"
    assert decided["p0"][0] == "drop" and "4" in decided["p0"][1]
    assert len(report["kept"]) == 20
    assert [i for i in report["kept"] if i.startswith("c")] == ["c%02d" % i for i in range(16)], "the others fill the rest in the order of the list"
    assert decided["c16"][0] == "drop" and "20" in decided["c16"][1]
    assert report["linux"]["kept_good"] == 20 and report["min_informative"] == 17
    # a class with fewer good sites than four keeps the blocked ones as data, and they do not count
    blocked_first = judge_module.admission({"linux": _passes({s["id"]: (cell(status="4xx") if s["id"] in ("p0", "p1") else cell()) for s in sites[:2]})},
                                           sites[:2], expected_runs=5)
    assert blocked_first["kept"] == ["p0", "p1"] and blocked_first["linux"]["kept_good"] == 0


def test_admission_min_informative_is_the_good_five_of_five_minus_three():
    sites = [_site("s%d" % i) for i in range(8)]
    big = judge_module.admission({"linux": _passes({s["id"]: cell() for s in sites})}, sites, expected_runs=5)
    assert big["linux"]["good"] == 8 and big["linux"]["min_informative"] == 5 and big["min_informative"] == 5
    assert "min_informative" in judge_module.render_admission(big)
    few = judge_module.admission({"linux": _passes({"s0": cell()})}, sites[:1], expected_runs=5)
    assert few["min_informative"] == 0, "never below 0"


def test_admission_refuses_a_run_that_is_missing():
    report = judge_module.admission({"linux": [{"site-a": cell()}] * 4}, [SITE], expected_runs=5)
    assert report["problems"] and report["linux"]["same"] == 0
    assert _decisions(report)["site-a"][0] == "drop"


def test_what_a_pass_adds_to_diagnose_a_marker_does_not_enter_the_judgement():
    diagnosed = cell(marker=False, diagnosis={"status": 403, "title": "Blocked", "ids": ["a", "b"]})
    # the judge: the extra field changes nothing, a good cell stays good and a diagnosed one is the same as one without
    assert judge_module.is_good(cell(diagnosis={"status": 200, "title": "x", "ids": []}))
    stock = runs_of("site-a", cell(), cell(), cell())
    with_diagnosis = judge(stock, runs_of("site-a", diagnosed, diagnosed, diagnosed), [SITE], 0)
    without = judge(stock, runs_of("site-a", cell(marker=False), cell(marker=False), cell(marker=False)), [SITE], 0)
    assert (with_diagnosis.verdict, with_diagnosis.table[0]["state"], with_diagnosis.table[0]["ours"]["why"]) ==            (without.verdict, without.table[0]["state"], without.table[0]["ours"]["why"])
    # the admission: five passes that differ only in the diagnosis are the same outcome
    runs = [{"site-a": cell(marker=False, diagnosis={"status": 403, "title": "t%d" % i, "ids": [str(i)]})} for i in range(5)]
    assert _decisions(judge_module.admission({"linux": runs}, [_site("site-a", "protetta")], expected_runs=5))["site-a"][0] == "keep"


# --- the files ------------------------------------------------------------------------------------------------------------------------------
def test_the_cli_reads_the_cells_and_writes_the_summary_and_the_exit_code(tmp_path):
    sites_file = tmp_path / "sites.json"
    sites_file.write_text(json.dumps({"min_informative": 1, "sites": [SITE]}), encoding="utf-8")

    def write(name, cells):
        path = tmp_path / name
        path.write_text(json.dumps({"browser": name.split("-")[0], "cells": cells}), encoding="utf-8")
        return str(path)

    stock = [write("stock-%d.json" % n, {"site-a": cell()}) for n in (1, 2, 3)]
    bad = [write("ours-%d.json" % n, {"site-a": cell(load="timeout")}) for n in (1, 2, 3)]
    summary = tmp_path / "summary.md"
    code = judge_module.main(["judge", "--sites", str(sites_file), "--stock", *stock, "--ours", *bad, "--summary", str(summary)])
    assert code == 1 and "site-a" in summary.read_text(encoding="utf-8")
    good = [write("ours-%d.json" % n, {"site-a": cell()}) for n in (1, 2, 3)]
    assert judge_module.main(["judge", "--sites", str(sites_file), "--stock", *stock, "--ours", *good, "--summary", str(summary)]) == 0


def test_the_sites_file_has_its_schema_and_every_site_a_class():
    data = json.loads((ROOT / "tests" / "realsites" / "sites.json").read_text(encoding="utf-8"))
    assert isinstance(data["min_informative"], int) and data["min_informative"] >= 0
    assert isinstance(data["sites"], list) and set(data["classes"]) == {"calma", "giornale", "negozio", "applicazione", "protetta"}
    for field in ("id", "url", "marker", "class"):
        assert data["fields"][field].strip()
    assert judge_module.validate_sites(data["sites"]) == []
    problems = judge_module.validate_sites([{"id": "x", "url": "https://x.example/", "marker": "a", "class": "other"},
                                            {"id": "x", "url": "https://y.example/", "marker": "", "class": "calma"}])
    assert len(problems) >= 3, problems
