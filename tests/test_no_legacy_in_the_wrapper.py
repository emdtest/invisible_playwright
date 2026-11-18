"""What was taken out of the wrapper does not come back.

Each entry below was a second way to reach something that has one place now: a
module that only re-exported the core, a name that only forwarded to it, a switch
for a path that no longer exists. They are asserted by name, because "it still
imports" is exactly how they survived: nothing broke while they sat there, so
nothing ever asked for them to go.

The import paths of the core are the ones to use: `invisible_core.<module>`.
"""
from __future__ import annotations

import importlib
import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.unit

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "invisible_playwright"

#: Modules of this package that only aliased, or re-exported, a module of the core.
_GONE_MODULES = (
    "_geo", "_headless", "_proxy", "_webgl_personas", "config", "constants",
    "download", "prefs", "_fpforge", "_reaper",
)


@pytest.mark.parametrize("name", _GONE_MODULES)
def test_a_module_that_only_forwarded_to_the_core_is_not_back(name):
    assert importlib.util.find_spec("invisible_playwright." + name) is None, (
        "invisible_playwright.%s exists again: the code is in the core "
        "(`_reaper` is `invisible_core.process`)" % name)
    assert not (_SRC / (name + ".py")).exists()
    assert not (_SRC / name).exists()


def test_no_module_of_the_package_hands_its_name_to_another_module():
    """The shape of every one of them: `sys.modules[__name__] = <the core's module>`."""
    offenders = [str(p.relative_to(_SRC)) for p in sorted(_SRC.rglob("*.py"))
                 if "_pw" not in p.relative_to(_SRC).parts
                 and "sys.modules[__name__]" in p.read_text(encoding="utf-8")]
    assert not offenders, offenders


def test_the_cursor_module_has_one_generator_and_two_engines():
    from invisible_playwright import _cursor

    for name in ("ENGINE_BINARY", "MIN_EVENT_INTERVAL_MS", "_build_profile",
                 "_MOTION_FACTORY_NAMES", "motion_available"):
        assert not hasattr(_cursor, name), "_cursor.%s is back" % name
    assert sorted(n for n in dir(_cursor) if n.startswith("ENGINE_") and n != "ENGINE_ENV") == [
        "ENGINE_OFF", "ENGINE_PYTHON"]
    assert "MIN_EVENT_INTERVAL_MS" not in _cursor.__all__
    assert "ENGINE_BINARY" not in _cursor.__all__


def test_the_generator_is_imported_directly_and_has_no_fallback():
    """The core is pinned with `==`: an install without the generator fails at import."""
    import ast

    from invisible_playwright import _cursor

    source = pathlib.Path(_cursor.__file__).read_text(encoding="utf-8")
    assert "_MOTION_IMPORT_ERROR" not in source
    assert "no-motion" not in source
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Try):
            text = ast.get_source_segment(source, node) or ""
            assert "invisible_core" not in text, "the import of the generator is guarded again"
    for name in ("MotionProfile", "new_motion"):
        assert name not in source
    import invisible_core.juggler as core_juggler
    assert _cursor._motion_mod is core_juggler
    assert _cursor._motion_factory() is core_juggler.CursorMotion


@pytest.mark.parametrize("value", ["binary", "juggler", "none", "false", "0"])
def test_the_engine_variable_takes_no_alias(monkeypatch, value):
    from invisible_playwright import _cursor

    monkeypatch.setenv(_cursor.ENGINE_ENV, value)
    with pytest.raises(ValueError, match="python"):
        _cursor.resolve_cursor_engine(True)


def test_the_session_module_does_not_know_a_third_engine():
    from invisible_playwright import _session

    assert not hasattr(_session, "ENGINE_BINARY")


def test_the_transport_module_has_no_driver_switch():
    from invisible_playwright._juggler import transport

    for name in ("CHOICE_ENV", "DRIVER", "JUGGLER", "chosen"):
        assert not hasattr(transport, name), "transport.%s is back" % name


def test_the_package_root_does_not_re_export_what_the_core_dropped():
    import invisible_playwright as root

    assert not hasattr(root, "resolve_session_timezone")
    assert "resolve_session_timezone" not in root.__all__


def test_no_script_reads_the_driver_tree_switch():
    scripts = pathlib.Path(__file__).resolve().parents[1] / "scripts"
    text = (scripts / "capture_protocol.py").read_text(encoding="utf-8")
    assert "INVPW_TRANSPORT" not in text
    assert "INVPW_DRIVER_TREE" not in text


def test_the_wrapper_names_the_core_by_the_names_it_has():
    """The three names the wrapper takes from the core under their final address."""
    core = importlib.import_module("invisible_core")
    assert callable(core.ensure_geoip_mmdb)
    assert callable(importlib.import_module("invisible_core.download").cache_dir_for_seal)
    assert callable(importlib.import_module("invisible_core.process").guard_for)
