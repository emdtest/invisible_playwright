"""The wiring of .github/workflows/real-sites.yml that its rules rest on, read from the file (no browser, no network).

* the version of the post-publication round is the tag of the publish run (``head_branch`` without its ``v``), and a run that is not of a ``v*`` tag is red;
  the other events ask the index;
* the token of the workflow is in three named steps (the plan, the admission report, the install and fetch of the round) and nowhere else: a step that opens real
  sites never sees it, and the install and fetch step launches no browser;
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


TOKEN = re.compile(r"GITHUB_TOKEN|GH_TOKEN|github\.token|secrets\.")
STEPS_WITH_THE_TOKEN = ["id: plan", "name: The two clean venvs", "name: Which sites are the same five of five, and the minimum to write"]


def _first_line(step):
    return step.splitlines()[0].strip()


def test_the_token_is_in_exactly_three_named_steps_and_in_none_that_opens_real_sites():
    assert not TOKEN.search(_code(HEAD)), "no token at the level of the workflow"
    for name, job in _jobs().items():
        assert not TOKEN.search(_code(job.split("    steps:", 1)[0])), "%s has a token at the level of the job" % name
    steps = _steps()
    assert [_first_line(s) for s in steps if TOKEN.search(_code(s))] == STEPS_WITH_THE_TOKEN
    for step in steps:
        if "run_real_sites.py" in step:
            assert not TOKEN.search(_code(step)), "a step that runs the passes sees the token: " + _first_line(step)
    # the step of the install and the fetch launches no browser, and the steps that call gh carry the token
    install = next(s for s in steps if _first_line(s) == "name: The two clean venvs")
    assert "invisible_playwright fetch" in install and "run_real_sites.py" not in install and "xvfb-run" not in install
    for step in steps:
        if re.search(r"\bgh (api|run) ", _code(step)):
            assert "GH_TOKEN: ${{ github.token }}" in step
    assert "GH_TOKEN: ${{ github.token }}" in install


def test_a_pull_request_that_touches_the_list_runs_the_admission_and_the_round():
    jobs = _jobs()
    assert "admission: ${{ steps.plan.outputs.admission }}" in jobs["plan"]
    assert re.search(r"(?m)^    if: needs\.plan\.outputs\.admission == 'true'$", jobs["admit"])
    assert not re.search(r"(?m)^    if:.*admission", jobs["round"]), "the round is not skipped by the admission"
    assert "needs: plan" in jobs["round"] and "outputs.mode" not in _code(jobs["round"])
