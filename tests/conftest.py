"""Offline tests must not contact real accounts or use the user's OS vault."""
import sys
from types import SimpleNamespace
import urllib.request
import pytest


@pytest.fixture(autouse=True)
def isolated_external_services(monkeypatch, request):
    if request.node.path.name == "test_live_mnemosyne_contract.py":
        return
    def unavailable(*args, **kwargs):
        raise RuntimeError("OS vault disabled for synthetic tests")
    monkeypatch.setitem(sys.modules, "keyring", SimpleNamespace(get_password=unavailable, set_password=unavailable))
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network forbidden in offline tests")
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
