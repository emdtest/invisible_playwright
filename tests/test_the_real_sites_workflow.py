"""The wiring of .github/workflows/real-sites.yml that its rules rest on, read from the file (no browser, no network).

* the version of the post-publication round is the tag of the publish run (``head_branch`` without its ``v``), and a run that is not of a ``v*`` tag is red;
  the other events ask the index;
* the token of the workflow is in the steps that call ``gh`` (the plan and the admission report) and nowhere else: a step that opens real sites never sees it;
* a pull request that touches the list runs the admission AND the round: the round does not wait on the admission and is not skipped by it.
"""
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEXT = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "real-sites.yml").read_text(encoding="utf-8")
HEAD, JOBS = TEXT.split("\njobs:\n", 1)


def _code(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _jobs():
    marks = list(re.finditer(r"^  ([a-z-]+):\s*$", JOBS, re.M))
    return {m.group(1): JOBS[m.start():(marks[i + 1].start() if i + 1 < len(marks) else len(JOBS))] for i, m in enumerate(marks)}


def _steps():
    return re.split(r"(?m)^      - ", JOBS)[1:]


def test_the_version_of_the_round_after_a_publication_is_the_tag_of_the_publish_run():
    plan = _code(_jobs()["plan"])
    assert "github.event.workflow_run.head_branch" in plan
    assert "v*)" in plan and "${HEAD_BRANCH#v}" in plan, "the tag without its v is the version"
    assert re.search(r"v\*\)[^;]*;;\s*\*\)[^;]*exit 1", plan, re.S), "a run that is not of a v* tag is red"
    assert "pypi.org/pypi/invisible-playwright/json" in plan, "the other events read the index"


def test_the_token_is_in_the_steps_that_call_gh_and_nowhere_else():
    assert not re.search(r"GITHUB_TOKEN|GH_TOKEN|github\.token", _code(HEAD)), "no token at the level of the workflow"
    jobs = _jobs()
    for name in ("round", "admit"):
        assert not re.search(r"GITHUB_TOKEN|GH_TOKEN|github\.token|secrets\.", _code(jobs[name])), "%s opens real sites and sees no token" % name
    callers = [s for s in _steps() if re.search(r"\bgh (api|run) ", _code(s))]
    assert len(callers) == 2, "the plan and the admission report call gh"
    for step in callers:
        assert "GH_TOKEN: ${{ github.token }}" in step


def test_a_pull_request_that_touches_the_list_runs_the_admission_and_the_round():
    jobs = _jobs()
    assert "admission: ${{ steps.plan.outputs.admission }}" in jobs["plan"]
    assert re.search(r"(?m)^    if: needs\.plan\.outputs\.admission == 'true'$", jobs["admit"])
    assert not re.search(r"(?m)^    if:.*admission", jobs["round"]), "the round is not skipped by the admission"
    assert "needs: plan" in jobs["round"] and "outputs.mode" not in _code(jobs["round"])
