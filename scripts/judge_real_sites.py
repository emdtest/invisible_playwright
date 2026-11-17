#!/usr/bin/env python3
"""The judge of the real-sites round (stdlib only).

``scripts/run_real_sites.py`` visits the list of ``tests/realsites/sites.json`` with ONE browser and writes the cells of that pass. This file reads the cells of
the two browsers (stock Firefox driven by stock Playwright, and the published wrapper: "ours") and says whether the wrapper got through where stock did.

A cell is what one visit of one site gave:

    {"load": "ok" | "timeout" | "error:<type>", "networkidle": "ok" | "timeout", "status": "2xx" | "3xx" | "4xx" | "5xx" | "none",
     "marker": true | false, "canary": "ok" | "no"}

It is GOOD when the load is ok, networkidle is ok, the status is 2xx, the marker is there and the canary page, visited right after, reached networkidle.

The rule, per site, over the runs that are available for that site:

* RED: stock is good in all three runs and ours is not good in at least two of the three;
* INCOMPLETE: a cell that differs in the first pass (one browser good, the other not) must have three runs per browser; without them it is not a red cell but a
  problem, "giro incompleto" (the job is red, the round's fault);
* NO INFORMATION: stock is not good in every run (a site that blocks, has fallen or has changed). Never red. The outcome of ours is still reported, as data;
* the job is RED when a cell is red or incomplete, when a site of the list was not visited by both browsers, when the list has no sites, when ``min_informative``
  is below 1, or when fewer cells than ``min_informative`` carry information ("il giro non ha misurato": the round is broken, not the wrapper; an empty round is
  never green).

THE RUNS. ``judge(stock_runs, ours_runs, sites, min_informative)`` takes lists of runs; a run is the dict ``{site id: cell}`` of one pass. The workflow supplies, per browser, the first pass over the whole list (``<browser>-1.json``) and, only for the
sites whose first-pass cells differ (``differing``: one browser good and the other not), two more passes of those sites alone, in the order stock, ours, stock,
ours (``<browser>-2.json``, ``<browser>-3.json``). A site is "visited" when it is in the FIRST pass of each browser; a site absent from a later pass is only a
site that did not differ. A cell that did not differ has one run each: good and good is green, and bad and bad is no information.

The function is pure (no file, no clock, no network): ``main`` reads the files and writes the summary.

    judge_real_sites.py judge     --sites tests/realsites/sites.json --stock FILE... --ours FILE... [--summary summary.md] [--result result.json]
    judge_real_sites.py differing --sites tests/realsites/sites.json --stock FILE --ours FILE            (ids of the cells that differ, one comma-joined line)
    judge_real_sites.py admit     --sites tests/realsites/sites.json --os LABEL FILE... [--os LABEL FILE...] [--summary admission.md]
        exit 1 when the file holds a site the admission would drop, or its min_informative is 0 or above the computed one; 0 for an empty list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: What a good cell is, field by field.
GOOD = {"load": "ok", "networkidle": "ok", "status": "2xx", "marker": True, "canary": "ok"}
FIELDS = tuple(GOOD)
CLASSES = ("calma", "giornale", "negozio", "applicazione", "protetta")
#: A cell that differs in the first pass has this many runs of each browser; without them the round is incomplete (its fault, not a red cell).
RUNS_OF_A_DIFFERING_CELL = 3
#: Red: stock good in every one of its three runs and ours not good in at least this many of its three.
BAD_RUNS_FOR_RED = 2
#: The admission keeps at most this many sites, of which at most MAX_PROTECTED are of the class PROTECTED (kept as data when stock is blocked there).
MAX_KEPT = 20
MAX_PROTECTED = 4
PROTECTED = "protetta"
NOT_MEASURED = "il giro non ha misurato"
STATE_LABEL = {"red": "red", "green": "green", "no_information": "no information", "missing": "missing", "incomplete": "incomplete"}
SITE_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


@dataclass
class Judgement:
    verdict: str                       # "green" or "red": one per job
    exit_code: int                     # 0 or 1
    table: list                        # one row per site of the list, in its order
    informative: int                   # cells with information (stock good in every run)
    red: int                           # red cells
    ours_good_stock_not: int           # cells where ours is good in every run and stock is not (data, not a verdict)
    problems: list = field(default_factory=list)


def is_good(cell) -> bool:
    return isinstance(cell, dict) and all(cell.get(name) == want for name, want in GOOD.items())


def _why(cells) -> str:
    """What was not good, named once per field and value, over the runs: ``load=timeout, status=4xx``; empty when every run was good."""
    seen = []
    for cell in cells:
        if is_good(cell):
            continue
        if not isinstance(cell, dict):
            notes = ["cell=unreadable"]
        else:
            notes = ["%s=%s" % (name, str(cell.get(name)).lower()) for name, want in GOOD.items() if cell.get(name) != want]
        seen += [note for note in notes if note not in seen]
    return ", ".join(seen)


def _side(cells) -> dict:
    return {"runs": len(cells), "good": sum(1 for c in cells if is_good(c)), "why": _why(cells)}


def _cells(runs, site_id) -> list:
    return [run[site_id] for run in runs if isinstance(run, dict) and site_id in run]


def _visited(runs, site_id) -> bool:
    return bool(runs) and isinstance(runs[0], dict) and site_id in runs[0]


def judge(stock_runs, ours_runs, sites, min_informative) -> Judgement:
    """The verdict, the table and the exit code of one job. Pure: it reads only its arguments and changes none of them."""
    if not isinstance(min_informative, int) or isinstance(min_informative, bool):
        raise ValueError("min_informative is an integer, not %r" % (min_informative,))
    table, problems = [], []
    informative = red = ours_good_stock_not = 0
    for site in sites:
        site_id = site["id"]
        base = {"id": site_id, "class": site.get("class", "")}
        missing = [name for name, runs in (("stock", stock_runs), ("ours", ours_runs)) if not _visited(runs, site_id)]
        stock, ours = _side(_cells(stock_runs, site_id)), _side(_cells(ours_runs, site_id))
        if missing:
            problems += ["%s was not visited by %s" % (site_id, name) for name in missing]
            table.append({**base, "state": "missing", "stock": stock, "ours": ours})
            continue
        differs = is_good(stock_runs[0][site_id]) != is_good(ours_runs[0][site_id])
        if differs and min(stock["runs"], ours["runs"]) < RUNS_OF_A_DIFFERING_CELL:
            problems.append("giro incompleto: %s differs in the first pass and has %d runs of stock and %d of ours, %d each are needed"
                            % (site_id, stock["runs"], ours["runs"], RUNS_OF_A_DIFFERING_CELL))
            table.append({**base, "state": "incomplete", "stock": stock, "ours": ours})
            continue
        if stock["good"] != stock["runs"]:
            state = "no_information"
            if ours["good"] == ours["runs"]:
                ours_good_stock_not += 1
        else:
            informative += 1
            not_good = ours["runs"] - ours["good"]
            if not_good >= BAD_RUNS_FOR_RED:
                state = "red"
                red += 1
                problems.append("%s is red: stock was good in %d of %d runs and ours in %d of %d (%s)"
                                % (site_id, stock["good"], stock["runs"], ours["good"], ours["runs"], ours["why"] or "no reason read"))
            else:
                state = "green"
        table.append({**base, "state": state, "stock": stock, "ours": ours})
    if not sites:
        problems.append("%s: the list has no sites" % NOT_MEASURED)
    elif min_informative < 1:
        problems.append("%s: min_informative is %d, the minimum of a list that measures is 1" % (NOT_MEASURED, min_informative))
    elif informative < min_informative:
        problems.append("%s: %d cells with information, the minimum is %d" % (NOT_MEASURED, informative, min_informative))
    code = 1 if problems else 0
    return Judgement("red" if code else "green", code, table, informative, red, ours_good_stock_not, problems)


def differing_sites(stock_first, ours_first, sites) -> list:
    """The ids, in the order of the list, whose first-pass cells differ in being good: the ones that get two more runs of each browser."""
    return [site["id"] for site in sites
            if site["id"] in stock_first and site["id"] in ours_first and is_good(stock_first[site["id"]]) != is_good(ours_first[site["id"]])]


def admission(runs_by_os, sites, expected_runs=5) -> dict:
    """The admission of a list: stock alone, ``expected_runs`` passes per system. Per site and system the outcome is "good" (the same good cell in every pass),
    "blocked" (the same cell in every pass, not good) or "unstable" (not the same in every pass, or a pass missing). The decision per site, over the systems:

    * unstable on any system: DROP, whatever its class;
    * blocked on any system: KEEP only if its class is ``protetta`` (kept as data, at most ``MAX_PROTECTED`` of that class, the good ones first), else DROP;
    * good on every system: KEEP, in the order of the list, while the list has fewer than ``MAX_KEPT`` sites.

    ``min_informative`` is the number of the kept sites that are good five of five, minus 3 (not below 0); the lowest of the systems is the one to write.
    Only the five fields of a cell are compared: what a pass adds to diagnose a marker (``diagnosis``) is not part of the outcome."""
    report = {"problems": []}
    outcome = {}
    for label, runs in runs_by_os.items():
        if len(runs) != expected_runs:
            report["problems"].append("%s: %d passes of stock, %d expected" % (label, len(runs), expected_runs))
        rows = []
        for site in sites:
            cells = _cells(runs, site["id"])
            full = len(runs) == expected_runs and len(cells) == expected_runs and all(isinstance(c, dict) for c in cells)
            same = full and all([c.get(n) for n in FIELDS] == [cells[0].get(n) for n in FIELDS] for c in cells)
            rows.append({"id": site["id"], "class": site.get("class", ""), "same": bool(same), "good": bool(same and is_good(cells[0])),
                         "why": (_why(cells[:1]) or "good") if same else "differs between passes, or missing"})
            outcome[(label, site["id"])] = "unstable" if not same else ("good" if is_good(cells[0]) else "blocked")
        report[label] = {"rows": rows, "same": sum(1 for r in rows if r["same"]), "good": sum(1 for r in rows if r["good"])}
    labels = list(runs_by_os)
    decisions = {}
    protected, plain = [], []
    for index, site in enumerate(sites):
        seen = {label: outcome[(label, site["id"])] for label in labels}
        unstable = [label for label, o in seen.items() if o == "unstable"]
        blocked = [label for label, o in seen.items() if o == "blocked"]
        if unstable:
            decisions[site["id"]] = ("drop", "stock outcome not the same 5 of 5 on %s" % ", ".join(unstable))
        elif site.get("class") == PROTECTED:
            protected.append((bool(blocked), index, site))
        elif blocked:
            decisions[site["id"]] = ("drop", "stock blocked 5 of 5 on %s and the class is not %s" % (", ".join(blocked), PROTECTED))
        else:
            plain.append(site)
    for rank, (blocked, _index, site) in enumerate(sorted(protected, key=lambda t: (t[0], t[1]))):
        if rank < MAX_PROTECTED:
            decisions[site["id"]] = ("keep", "stock blocked 5 of 5, kept as data (%s)" % PROTECTED if blocked else "good 5 of 5 (%s)" % PROTECTED)
        else:
            decisions[site["id"]] = ("drop", "more than %d sites of the class %s" % (MAX_PROTECTED, PROTECTED))
    room = MAX_KEPT - sum(1 for d, _r in decisions.values() if d == "keep")
    for rank, site in enumerate(plain):
        decisions[site["id"]] = ("keep", "good 5 of 5") if rank < room else ("drop", "more than %d sites kept" % MAX_KEPT)
    report["decisions"] = [{"id": s["id"], "class": s.get("class", ""), "decision": decisions[s["id"]][0], "reason": decisions[s["id"]][1],
                            "outcome": {label: outcome[(label, s["id"])] for label in labels}} for s in sites]
    kept = {d["id"] for d in report["decisions"] if d["decision"] == "keep"}
    report["kept"] = [s["id"] for s in sites if s["id"] in kept]
    lows = []
    for label in labels:
        report[label]["kept_good"] = sum(1 for r in report[label]["rows"] if r["good"] and r["id"] in kept)
        report[label]["min_informative"] = max(report[label]["kept_good"] - 3, 0)
        lows.append(report[label]["min_informative"])
    report["min_informative"] = min(lows) if lows else 0
    return report


def admission_problems(report, sites, min_informative) -> list:
    """What makes the admission red: a site of the file that the admission would drop, a pass that is missing, a ``min_informative`` of 0 or above the one the
    admission computes (lower is fine, from 1). An empty list is not asked anything: there is nothing to admit."""
    if not sites:
        return []
    out = list(report["problems"])
    out += ["%s: the admission would drop it (%s)" % (d["id"], d["reason"]) for d in report["decisions"] if d["decision"] == "drop"]
    if min_informative < 1:
        out.append("min_informative is %d: write %d at most, and at least 1" % (min_informative, report["min_informative"]))
    elif min_informative > report["min_informative"]:
        out.append("min_informative is %d, above the %d the admission computes" % (min_informative, report["min_informative"]))
    return out


def validate_sites(sites) -> list:
    """The problems of a list: every site has an id (unique, lowercase letters, digits and hyphens), an https url, a marker and one of the five classes."""
    if not isinstance(sites, list):
        return ["the sites are not a list"]
    out, seen = [], set()
    for i, site in enumerate(sites):
        if not isinstance(site, dict):
            out.append("site %d is not an object" % i)
            continue
        site_id = site.get("id")
        if not isinstance(site_id, str) or not SITE_ID.match(site_id):
            out.append("site %d: the id %r is not lowercase letters, digits and hyphens" % (i, site_id))
        elif site_id in seen:
            out.append("site %d: the id %r is used twice" % (i, site_id))
        seen.add(site_id)
        for name in ("url", "marker"):
            if not isinstance(site.get(name), str) or not site[name].strip():
                out.append("%s: no %s" % (site_id, name))
        if isinstance(site.get("url"), str) and site["url"].strip() and not site["url"].startswith("https://"):
            out.append("%s: the url is not https" % site_id)
        if site.get("class") not in CLASSES:
            out.append("%s: the class %r is not one of %s" % (site_id, site.get("class"), ", ".join(CLASSES)))
    return out


# ------------------------------------------------------------------------------------------------------------------------------ the summary
def _side_text(side) -> str:
    if side["runs"] == 0:
        return "not visited"
    text = "%d/%d good" % (side["good"], side["runs"])
    return text + (" (%s)" % side["why"]) if side["why"] else text


def render_markdown(result: Judgement) -> str:
    lines = ["## Real sites: ours against stock", "",
             "**Verdict: %s**" % result.verdict, "",
             "- informative cells: %d" % result.informative,
             "- red cells: %d" % result.red,
             "- ours good, stock not: %d (data, not a verdict)" % result.ours_good_stock_not, ""]
    if result.problems:
        lines += ["### Problems", ""] + ["- " + p for p in result.problems] + [""]
    lines += ["| site | class | stock | ours | state |", "|---|---|---|---|---|"]
    for r in result.table:
        lines.append("| %s | %s | %s | %s | %s |" % (r["id"], r["class"], _side_text(r["stock"]), _side_text(r["ours"]), STATE_LABEL[r["state"]]))
    return "\n".join(lines) + "\n"


def render_admission(report: dict, verdict_problems=()) -> str:
    labels = [k for k in report if k not in ("problems", "min_informative", "decisions", "kept")]
    lines = ["## Admission of the list: stock, same outcome in every pass", "",
             "- sites kept: %d of %d" % (len(report.get("kept", [])), len(report.get("decisions", []))),
             "- min_informative to write in sites.json (kept sites good 5 of 5, minus 3, the lowest of the systems): **%d**" % report["min_informative"], ""]
    for label in labels:
        entry = report[label]
        lines.append("- %s: same outcome in every pass %d, good in every pass %d, kept and good %d, min_informative %d"
                     % (label, entry["same"], entry["good"], entry["kept_good"], entry["min_informative"]))
    lines += ["", "| site | class | " + " | ".join(labels) + " | decision | reason |", "|---|---|" + "---|" * len(labels) + "---|---|"]
    for d in report.get("decisions", []):
        lines.append("| %s | %s | %s | %s | %s |" % (d["id"], d["class"], " | ".join(d["outcome"].get(label, "-") for label in labels), d["decision"], d["reason"]))
    if labels:
        lines += ["", "What stock showed where it was not good, per system (the cells of the passes also hold the status, the title and the first ids of the page where the marker was not found):", ""]
        for label in labels:
            for r in report[label]["rows"]:
                if r["same"] and not r["good"]:
                    lines.append("- %s, %s: %s" % (label, r["id"], r["why"]))
    if report["problems"]:
        lines += ["", "### Problems", ""] + ["- " + p for p in report["problems"]]
    lines += ["", "**Verdict: %s**" % ("red" if verdict_problems else "green")]
    lines += ["- " + p for p in verdict_problems]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------------------------------------ the files
def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_sites(path):
    data = _read_json(path)
    sites = data.get("sites", [])
    problems = validate_sites(sites)
    if problems:
        raise SystemExit("the list of sites is not valid:\n  " + "\n  ".join(problems))
    return data, sites


def _cells_of(path) -> dict:
    data = _read_json(path)
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, dict):
        raise SystemExit("%s holds no cells" % path)
    return cells


def _write(path, text):
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_judge = sub.add_parser("judge")
    p_judge.add_argument("--sites", required=True)
    p_judge.add_argument("--stock", nargs="+", required=True, help="the files of the passes of stock, the first pass first")
    p_judge.add_argument("--ours", nargs="+", required=True, help="the files of the passes of ours, the first pass first")
    p_judge.add_argument("--summary")
    p_judge.add_argument("--result")
    p_diff = sub.add_parser("differing")
    p_diff.add_argument("--sites", required=True)
    p_diff.add_argument("--stock", required=True)
    p_diff.add_argument("--ours", required=True)
    p_admit = sub.add_parser("admit")
    p_admit.add_argument("--sites", required=True)
    p_admit.add_argument("--os", nargs="+", action="append", required=True, metavar="LABEL FILE", help="a system label, then its passes of stock")
    p_admit.add_argument("--expected-runs", type=int, default=5)
    p_admit.add_argument("--summary")
    args = parser.parse_args(argv)

    data, sites = _read_sites(args.sites)
    if args.command == "differing":
        print(",".join(differing_sites(_cells_of(args.stock), _cells_of(args.ours), sites)))
        return 0
    if args.command == "admit":
        report = admission({group[0]: [_cells_of(f) for f in group[1:]] for group in args.os}, sites, args.expected_runs)
        problems = admission_problems(report, sites, data.get("min_informative", 0))
        text = render_admission(report, problems)
        if args.summary:
            _write(args.summary, text)
        print(text)
        return 1 if problems else 0
    result = judge([_cells_of(f) for f in args.stock], [_cells_of(f) for f in args.ours], sites, data.get("min_informative", 0))
    text = render_markdown(result)
    if args.summary:
        _write(args.summary, text)
    if args.result:
        _write(args.result, json.dumps({"verdict": result.verdict, "exit_code": result.exit_code, "informative": result.informative, "red": result.red,
                                        "ours_good_stock_not": result.ours_good_stock_not, "problems": result.problems, "table": result.table}, indent=2) + "\n")
    print(text)
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
