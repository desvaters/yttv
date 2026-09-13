"""The backend registry: lazy import, unknown names, missing extras."""

from __future__ import annotations

import builtins
import sys

import pytest

from yttv import backends
from yttv.backends import BackendUnavailable, get_launcher


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(backends._LAUNCHERS)
    backends._LAUNCHERS.clear()
    yield
    backends._LAUNCHERS.clear()
    backends._LAUNCHERS.update(saved)


def test_none_and_unknown_names_give_no_launcher() -> None:
    assert get_launcher(None) is None
    assert get_launcher("toaster") is None


def test_known_backend_is_imported_on_first_use(monkeypatch) -> None:
    pytest.importorskip("pyatv")
    monkeypatch.delitem(sys.modules, "yttv.backends.appletv", raising=False)
    launcher = get_launcher("appletv")
    assert launcher is not None and hasattr(launcher, "launch")
    assert get_launcher("appletv") is launcher, "registered once, reused"


def test_missing_dependency_is_reported_with_install_hint(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "yttv.backends.appletv", raising=False)
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pyatv" or name.startswith("pyatv."):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(BackendUnavailable, match=r"yttv\[appletv\]"):
        get_launcher("appletv")
