import os

import pytest

from dynamix_manager.workbench.store import Store


def test_drafts_account_and_visibility_isolated(tmp_path):
    s = Store(tmp_path / "state")
    s.save_draft("a", 1, "internal", {"text": "secret"})
    assert s.draft("a", 1, "internal")["text"] == "secret"
    assert s.draft("b", 1, "internal") is None
    assert s.draft("a", 1, "public") is None
    assert os.stat(s.path).st_mode & 0o777 == 0o600
    assert os.stat(s.path.parent).st_mode & 0o777 == 0o700


def test_intent_duplicate_and_restart(tmp_path):
    s = Store(tmp_path / "state")
    i = s.begin("a", 1, "p", {"text": "message"})
    with pytest.raises(ValueError):
        s.begin("a", 1, "p2", {})
    s = Store(tmp_path / "state")
    assert s.blocking("a", 1)["status"] == "uncertain"
    s.finish(i, "acknowledged", {})
    assert s.blocking("a", 1) is None
