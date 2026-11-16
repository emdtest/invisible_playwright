"""A test that needs a driver bundle tree, which only a machine that has one can give it.

``test_the_injected_sources_have_balanced_quotes`` reads ``coreBundle.js`` of the tree ``INVPW_DRIVER_TREE`` names (a git worktree, or a fresh
upstream bundle being considered for extraction); the driver was removed from this checkout on 2026-08-28, so with no variable it skips. A skip is red
in the whole unit suite of the candidate, so the test lives in its own file: ``tests/test_fork.py`` holds the checks of the fork that run on every
machine, and this one is not in the rows of ``ci/required-suites.json``. It is a file of the environment of ``tests/test_the_unit_suite_is_accounted_for.py``.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from test_fork import _unprotected_quote


def _injected_sources():
    """The strings the bundle injects into the page, one per physical line.

    They are declared as ``sourceN = '...'`` with SINGLE quotes and with newlines
    written as two characters, so each one occupies a single line of the file.
    """
    # ⛔ THE BUNDLE MAY NOT BE HERE ANY MORE, and that is not a broken tree:
    # the driver was removed on 2026-08-28. The defect this scans for lives in
    # a SINGLE-QUOTED JavaScript string inside that bundle, so with the bundle
    # gone the class is gone with it - our `injected.js` is a plain file, where
    # an apostrophe in a comment is just an apostrophe.
    #
    # It is kept reachable rather than deleted because the check still applies
    # to any bundle somebody points at: `INVPW_DRIVER_TREE` from a git worktree,
    # or a fresh upstream one being considered for extraction.
    import os

    tree = os.environ.get("INVPW_DRIVER_TREE")
    root = (pathlib.Path(tree) if tree
            else pathlib.Path(__file__).resolve().parent.parent)
    bundle = (root / "src" / "invisible_playwright" / "_driver" / "package"
              / "lib" / "coreBundle.js")
    if not bundle.is_file():
        return
    text = bundle.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*(source\d*) = '", line)
        if m:
            yield number, m.group(1), line


def test_the_injected_sources_have_balanced_quotes():
    """An apostrophe in a comment closes the string and breaks the whole bundle.

    Happened on 2026-08-24: an Italian comment added inside ``source4``
    contained ``piu'`` and ``cioe'``. The string closed right there, the file
    became invalid JavaScript and the driver stopped starting at all.

    The defect is not visible to the eye - those lines are hundreds of
    thousands of characters long - and no test that only imports the Python
    package sees it. No Node required: it holds even where the runtime has not
    been downloaded yet.
    """
    found = list(_injected_sources())
    if not found:
        # ⛔ NOT AN ASSERTION FAILURE. With no bundle reachable there is
        # nothing of this shape to check, and the defect class went with it -
        # see `_injected_sources`. Saying "no injected source found" as a
        # failure would turn a deliberate deletion into a permanent red.
        pytest.skip("no driver bundle reachable: nothing of this shape exists "
                    "to check. Point INVPW_DRIVER_TREE at a worktree to run "
                    "it against one.")
    for number, name, line in found:
        rest = _unprotected_quote(line)
        assert rest is None, (
            "coreBundle.js:%d, %s: the injected string closes too early, "
            "what remains after it is %r - almost always an apostrophe inside a "
            "comment" % (number, name, rest))
