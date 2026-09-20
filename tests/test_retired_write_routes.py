import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from dynamix_manager.hosted import HostedSettings, create_app
from dynamix_manager.hosted_vault import CredentialVault
from dynamix_manager.personal_auth_store import OAuthStore


@pytest.mark.parametrize('path', ['/writes/review', '/writes/open', '/writes/save'])
@pytest.mark.parametrize('method', ['GET', 'POST', 'PUT', 'DELETE'])
def test_old_review_routes_are_inert_without_reading_request_body(tmp_path, path, method):
    settings = HostedSettings('https://connector.test', 'https://connector.test',
                              'https://connector.test/unused')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())

    class Provider:
        routes = []
        store = OAuthStore(vault)

        def guard(self, app):
            return app

    app = create_app(settings, vault, auth_provider=Provider())

    def forbidden(*args, **kwargs):
        raise AssertionError('Retired routes must not call the write service')

    app.write_service.open_review = forbidden
    app.write_service.commit = forbidden
    with TestClient(app, base_url=settings.public_url) as client:
        response = client.request(method, path, content='privatecapabilitycanary')
    assert response.status_code == 410
    assert 'cannot submit' in response.text
    assert 'privatecapabilitycanary' not in response.text
    assert 'set-cookie' not in response.headers
    assert 'no-store' in response.headers['cache-control'].split(', ')
