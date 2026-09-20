import pytest


@pytest.fixture(autouse=True)
def isolated_application_cache(monkeypatch):
    """Tenant application discovery is cached per process; keep tests independent."""
    import dynamix_manager.plugin as plugin
    monkeypatch.setattr(plugin, "_APPLICATIONS", {}, raising=False)
