import base64
import asyncio
import hashlib
import html
import re
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

UID = '00000000-0000-0000-0000-000000000001'
CALLBACK = 'https://chatgpt.test/callback'
VERIFIER = 'v' * 64


@pytest.fixture
def pilot(tmp_path):
    from dynamix_manager.hosted import HostedSettings, create_app
    from dynamix_manager.hosted_vault import CredentialVault
    from dynamix_manager.personal_auth import PersonalAuthProvider
    settings = HostedSettings('https://connector.test', 'https://connector.test',
                              'https://connector.test/unused')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    calls = []

    def login(username, password):
        calls.append((username, password))
        return UID if username == 'allowed' else '00000000-0000-0000-0000-000000000002', 'upstream-secret', int(time.time()) + 1800

    provider = PersonalAuthProvider(settings, vault, UID, [CALLBACK], login=login)
    with TestClient(create_app(settings, vault, auth_provider=provider),
                    base_url=settings.public_url) as http:
        yield http, provider, calls, vault


def register(http, callback=CALLBACK, scope='tdx.read'):
    return http.post('/register', json={'redirect_uris': [callback],
        'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'], **({'scope': scope} if scope is not None else {})})


def login_form(http, client, scope='tdx.read', *, page=None):
    response = http.get('/authorize', params={'client_id': client, 'response_type': 'code',
        'redirect_uri': CALLBACK, **({'scope': scope} if scope is not None else {}), 'state': 'original-state',
        'resource': 'https://connector.test/mcp', 'code_challenge_method': 'S256',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')})
    assert response.status_code == 200, response.text
    assert response.headers['referrer-policy'] == 'same-origin'
    assert CALLBACK in response.text
    if page is not None:
        page.append(response.text)
    return dict(re.findall(r'name="(transaction|csrf)" value="([^"]+)"', response.text))


def obtain_code(http, client, scope='tdx.read', *, remember=False):
    form = login_form(http, client, scope)
    data = {**form, 'username': 'allowed', 'password': 'private-password', 'consent': 'yes'}
    if remember:
        data['remember'] = 'yes'
    response = http.post('/personal/login', data=data,
                         headers={'Origin': 'https://connector.test'}, follow_redirects=False)
    assert response.status_code == 200, response.text
    assert 'Continue to ChatGPT' in response.text
    assert 'private-password' not in response.text
    assert '<form' not in response.text
    assert "form-action 'self'" in response.headers['content-security-policy']
    target = html.unescape(re.search(r'href="([^"]+)"', response.text)[1])
    assert target.startswith(CALLBACK + '?')
    query = parse_qs(urlsplit(target).query)
    assert query['state'] == ['original-state']
    return query['code'][0]


def exchange(http, client, code, **changes):
    return http.post('/token', data={'client_id': client, 'grant_type': 'authorization_code',
        'code': code, 'code_verifier': VERIFIER, 'redirect_uri': CALLBACK,
        'resource': 'https://connector.test/mcp', **changes})


def test_full_flow_rotation_replay_encryption(pilot):
    http, provider, calls, vault = pilot
    client = register(http).json()['client_id']
    code = obtain_code(http, client)
    tokens = exchange(http, client, code).json()
    assert 0 < tokens['expires_in'] <= 600
    assert exchange(http, client, code).status_code == 400
    headers = {'Authorization': 'Bearer ' + tokens['access_token'], 'Accept': 'application/json, text/event-stream'}
    assert http.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).status_code == 200
    form = {'grant_type': 'refresh_token', 'client_id': client, 'refresh_token': tokens['refresh_token'], 'resource': provider.settings.resource}
    rotated = http.post('/token', data=form).json()
    assert rotated['refresh_token'] != tokens['refresh_token']
    family = asyncio.run(provider.load_access_token(tokens['access_token'])).claims['tdx_grant_family']
    with provider.store.transaction() as db:
        family_before_replay = provider.store.get(db, 'family', family)
    assert http.post('/token', data=form).status_code == 400
    with provider.store.transaction() as db:
        family_after_replay = provider.store.get(db, 'family', family)
    assert {k: v for k, v in family_after_replay.items() if k != 'revoked'} == {
        k: v for k, v in family_before_replay.items() if k != 'revoked'}
    assert family_before_replay['revoked'] is False and family_after_replay['revoked'] is True
    headers['Authorization'] = 'Bearer ' + rotated['access_token']
    assert http.post('/mcp', headers=headers, json={}).status_code == 401
    raw = vault.path.read_bytes()
    for secret in ['private-password', 'upstream-secret', tokens['access_token'], tokens['refresh_token'], client]:
        assert secret.encode() not in raw


def test_redirect_resource_and_pkce(pilot):
    http, provider, _, _ = pilot
    assert register(http, 'https://evil.test/callback').status_code == 400
    client = register(http).json()['client_id']
    code = obtain_code(http, client)
    assert exchange(http, client, code, resource='https://evil.test/mcp').status_code == 400
    assert exchange(http, client, code, code_verifier='wrong').status_code == 400
    tokens = exchange(http, client, code).json()
    assert 'access_token' in tokens
    assert http.post('/token', data={'client_id': client, 'grant_type': 'refresh_token',
        'refresh_token': tokens['refresh_token'], 'resource': 'wrong'}).status_code == 400
    provider.redirect_uris = frozenset()
    assert register(http).status_code == 400


@pytest.mark.parametrize('change', [{'csrf': 'wrong'}, {'csrf': '\u00e9'}, {'username': 'wrong'}, {'consent': 'no'}])
def test_browser_failures_and_transaction_replay(pilot, change):
    http, _, _, _ = pilot
    client = register(http).json()['client_id']
    form = {**login_form(http, client), 'username': 'allowed', 'password': 'secret', 'consent': 'yes'}
    assert http.post('/personal/login', data={**form, **change}, headers={'Origin': 'https://connector.test'}).status_code == 400
    assert http.post('/personal/login', data=form, headers={'Origin': 'https://connector.test'}).status_code == 400


def test_origin_cookie_bounds_and_headers(pilot, caplog):
    http, _, calls, _ = pilot
    client = register(http).json()['client_id']
    form = {**login_form(http, client), 'username': 'allowed', 'password': 'secret', 'consent': 'yes'}
    response = http.post('/personal/login', data=form, headers={'Origin': 'https://evil.test'})
    assert response.status_code == 400
    assert calls == []
    assert 'personal_login_denied: origin' in caplog.text
    assert 'evil.test' not in caplog.text
    assert 'secret' not in caplog.text
    response = http.post('/personal/login', content=b'x' * 20000)
    assert response.status_code == 413
    response = http.get('/personal/login')
    # Native form POSTs under no-referrer send Origin: null and fail CSRF checks.
    # same-origin preserves the Origin while suppressing cross-site referrers.
    assert response.headers['referrer-policy'] == 'same-origin'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


def test_atomic_code_consumption_and_revocation(pilot):
    from concurrent.futures import ThreadPoolExecutor
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    code = obtain_code(http, client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: exchange(http, client, code), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 400]
    token = next(r.json() for r in responses if r.status_code == 200)
    assert http.post('/revoke', data={'client_id': client, 'token': token['access_token']}).status_code == 200
    assert asyncio.run(provider.load_access_token(token['access_token'])) is None
    assert http.post('/token', data={'client_id': client, 'grant_type': 'refresh_token',
        'refresh_token': token['refresh_token'], 'resource': provider.settings.resource}).status_code == 400


def test_expiry_persistence_and_rate_bound(pilot, monkeypatch):
    from dynamix_manager.personal_auth import PersonalAuthProvider
    http, provider, _, vault = pilot
    expiry = int(time.time()) + 90
    provider.login = lambda u, p: (UID, 'upstream-secret', expiry)
    client = register(http).json()['client_id']
    token = exchange(http, client, obtain_code(http, client)).json()
    assert 0 < token['expires_in'] <= 90
    restored = PersonalAuthProvider(provider.settings, vault, UID, [CALLBACK])
    assert asyncio.run(restored.get_client(client)).client_id == client
    assert asyncio.run(restored.load_access_token(token['access_token'])).subject == UID
    monkeypatch.setattr('dynamix_manager.personal_auth.time.time', lambda: expiry + 1)
    assert asyncio.run(restored.load_access_token(token['access_token'])) is None
    assert http.post('/token', data={'client_id': client, 'grant_type': 'refresh_token',
        'refresh_token': token['refresh_token'], 'resource': provider.settings.resource}).status_code == 400
    assert any(http.get('/personal/login').status_code == 429 for _ in range(35))


def test_missing_cookie_denied(pilot):
    http, _, calls, _ = pilot
    client = register(http).json()['client_id']
    form = login_form(http, client)
    cookie = next(iter(http.cookies.jar))
    assert cookie.secure and cookie._rest['HttpOnly'] is None and cookie._rest['SameSite'] == 'strict'
    http.cookies.clear()
    assert http.post('/personal/login', data={**form, 'username': 'allowed', 'password': 'secret', 'consent': 'yes'},
        headers={'Origin': 'https://connector.test'}).status_code == 400
    assert calls == []


@pytest.mark.parametrize('expiry', [None, '12345', float('inf'), True, 1])
def test_unknown_or_expired_upstream_denied(pilot, expiry):
    http, provider, _, _ = pilot
    provider.login = lambda u, p: (UID, 'upstream', expiry)
    client = register(http).json()['client_id']
    form = login_form(http, client)
    response = http.post('/personal/login', data={**form, 'username': 'allowed', 'password': 'secret', 'consent': 'yes'},
        headers={'Origin': 'https://connector.test'})
    assert response.status_code == 400
    assert 'secret' not in response.text


def test_tdx_adapter_uses_explicit_credentials_and_closes(pilot, monkeypatch):
    from unittest.mock import Mock
    import jwt
    http, provider, _, _ = pilot
    connection = Mock(auth_mode='user')
    connection.identity.return_value = UID
    expiry = int(time.time()) + 100
    token = jwt.encode({'exp': expiry}, 'test-key' * 8, algorithm='HS256')
    connection.client.authenticate.return_value = token
    factory = Mock(return_value=connection)
    monkeypatch.setattr('dynamix_manager.personal_auth.Connection', factory)
    assert provider._tdx_login('allowed', 'password') == (UID, token, expiry)
    assert factory.call_args.args[0] == {'TDX_BASE_URL': provider.settings.tdx_url,
        'TDX_APP_ID': provider.settings.tdx_client_id, 'WORKBENCH_PERSONAL_USERNAME': 'allowed',
        'WORKBENCH_PERSONAL_PASSWORD': 'password'}
    assert connection.client.password == ''
    connection.client.session.close.assert_called_once()
    connection.auth_mode = 'admin'
    connection.client.authenticate.reset_mock()
    with pytest.raises(ValueError):
        provider._tdx_login('admin', 'password')
    connection.client.authenticate.assert_not_called()


def test_authorize_rejects_missing_or_wrong_resource(pilot):
    http, _, _, _ = pilot
    client = register(http).json()['client_id']
    for resource in ['', 'https://evil.test/mcp']:
        response = http.get('/authorize', params={'client_id': client, 'response_type': 'code',
            'redirect_uri': CALLBACK, 'scope': 'tdx.read', 'state': 'state', 'resource': resource,
            'code_challenge_method': 'S256', 'code_challenge': 'a' * 43}, follow_redirects=False)
        assert '/personal/login' not in response.headers.get('location', '')


def test_empty_allowlist_environment_starts_closed(tmp_path, monkeypatch):
    from dynamix_manager.hosted import from_environment
    for key, value in {'TDX_HOSTED_AUTH_MODE': 'personal', 'TDX_HOSTED_PUBLIC_URL': 'https://connector.test',
        'TDX_HOSTED_VAULT_PATH': str(tmp_path / 'vault'), 'TDX_HOSTED_VAULT_KEY': Fernet.generate_key().decode(),
        'TDX_HOSTED_ALLOWED_UID': UID, 'TDX_HOSTED_REDIRECT_URIS': '[]'}.items():
        monkeypatch.setenv(key, value)
    with TestClient(from_environment(), base_url='https://connector.test') as http:
        assert http.get('/healthz').status_code == 200
        assert register(http).status_code == 400
        metadata = http.get('/.well-known/oauth-authorization-server').json()
        assert metadata['issuer'] == 'https://connector.test/'


def test_duplicate_parameters_and_non_form_login_are_rejected(pilot):
    http, _, calls, _ = pilot
    response = http.post('/personal/login', content='transaction=a&transaction=b',
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    assert response.status_code == 400
    assert http.post('/personal/login', json={'password': 'secret'}).status_code == 400
    assert calls == []


def test_confidential_revocation_still_requires_secret(pilot):
    http, _, _, _ = pilot
    client = http.post('/register', json={'redirect_uris': [CALLBACK],
        'token_endpoint_auth_method': 'client_secret_post', 'grant_types': ['authorization_code', 'refresh_token'],
        'response_types': ['code'], 'scope': 'tdx.read'}).json()
    code = obtain_code(http, client['client_id'])
    token = exchange(http, client['client_id'], code, client_secret=client['client_secret']).json()
    assert http.post('/revoke', data={'client_id': client['client_id'], 'token': token['access_token']}).status_code == 401
    assert http.post('/revoke', data={'client_id': client['client_id'], 'token': token['access_token'],
        'client_secret': client['client_secret']}).status_code == 200


def test_durable_state_capacity_and_expired_pruning(pilot, monkeypatch):
    http, provider, _, _ = pilot
    monkeypatch.setattr(provider.store, 'CAPACITY', {'client': 2, 'transaction': 2}, raising=False)
    first = register(http).json()['client_id']
    assert register(http).status_code == 201
    assert register(http).status_code == 400
    login_form(http, first)
    login_form(http, first)
    from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
    params = AuthorizationParams(state='state', scopes=['tdx.read'], code_challenge='a' * 43,
        redirect_uri=CALLBACK, redirect_uri_provided_explicitly=True, resource=provider.settings.resource)
    client = asyncio.run(provider.get_client(first))
    with pytest.raises(AuthorizeError):
        asyncio.run(provider.authorize(client, params))
    future = time.time() + 301
    monkeypatch.setattr('dynamix_manager.personal_auth.time.time', lambda: future)
    assert '/personal/login' in asyncio.run(provider.authorize(client, params))


def test_token_capacity_returns_protocol_error_and_preserves_code(pilot, monkeypatch):
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    code = obtain_code(http, client)
    monkeypatch.setattr(provider.store, 'CAPACITY', {**provider.store.CAPACITY, 'access': 0})
    assert exchange(http, client, code).status_code == 400
    provider.store.CAPACITY['access'] = 2048
    assert exchange(http, client, code).status_code == 200


def test_scope_discovery_defaults_and_registration_validation(pilot):
    http, _, _, _ = pilot
    resource = http.get('/.well-known/oauth-protected-resource/mcp').json()
    authorization = http.get('/.well-known/oauth-authorization-server').json()
    assert resource['scopes_supported'] == ['tdx.read', 'tdx.write']
    assert authorization['scopes_supported'] == ['tdx.read', 'tdx.write']
    assert register(http, scope=None).json()['scope'] == 'tdx.read'
    for scope in ('tdx.write', 'tdx.read unknown', 'unknown'):
        response = register(http, scope=scope)
        assert response.status_code == 400


def test_existing_read_client_can_request_explicit_write_consent_without_elevation(pilot):
    http, provider, _, _ = pilot
    client = register(http, scope='tdx.read').json()['client_id']
    read_token = exchange(http, client, obtain_code(http, client)).json()
    assert read_token['scope'] == 'tdx.read'
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.write_grant_binding(asyncio.run(provider.load_access_token(read_token['access_token'])))

    form = login_form(http, client, 'tdx.read tdx.write')
    response = http.post('/personal/login', data={**form, 'username': 'allowed',
        'password': 'private-password', 'consent': 'yes'},
        headers={'Origin': 'https://connector.test'}, follow_redirects=False)
    assert response.status_code == 200
    target = html.unescape(re.search(r'href="([^"]+)"', response.text)[1])
    code = parse_qs(urlsplit(target).query)['code'][0]
    write_token = exchange(http, client, code).json()
    assert set(write_token['scope'].split()) == {'tdx.read', 'tdx.write'}
    old_principal = asyncio.run(provider.load_access_token(read_token['access_token']))
    assert old_principal.scopes == ['tdx.read']
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.write_grant_binding(old_principal)


def test_write_login_uses_explicit_selected_scope_consent(pilot):
    http, _, _, _ = pilot
    client = register(http).json()['client_id']
    response = http.get('/authorize', params={'client_id': client, 'response_type': 'code',
        'redirect_uri': CALLBACK, 'scope': 'tdx.write tdx.read', 'state': 'original-state',
        'resource': 'https://connector.test/mcp', 'code_challenge_method': 'S256',
        'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')})
    assert response.status_code == 200
    assert 'tdx.read' in response.text and 'tdx.write' in response.text
    assert 'read and modify' in response.text.lower()
    assert 'Allow read-only access' not in response.text


def test_missing_authorization_scope_defaults_read_and_unknown_sets_are_denied(pilot):
    http, _, _, _ = pilot
    client = register(http).json()['client_id']
    assert 'read-only access' in http.get('/authorize', params={'client_id': client,
        'response_type': 'code', 'redirect_uri': CALLBACK, 'state': 'original-state',
        'resource': 'https://connector.test/mcp', 'code_challenge_method': 'S256',
        'code_challenge': 'a' * 43}).text
    for scope in ('tdx.write', 'tdx.read unknown', 'tdx.read tdx.write unknown'):
        response = http.get('/authorize', params={'client_id': client, 'response_type': 'code',
            'redirect_uri': CALLBACK, 'scope': scope, 'state': 'state',
            'resource': 'https://connector.test/mcp', 'code_challenge_method': 'S256',
            'code_challenge': 'a' * 43}, follow_redirects=False)
        assert '/personal/login' not in response.headers.get('location', '')


def test_read_refresh_cannot_elevate_and_write_refresh_preserves_authority(pilot):
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    read = exchange(http, client, obtain_code(http, client)).json()
    base = {'grant_type': 'refresh_token', 'client_id': client,
            'resource': provider.settings.resource}
    elevated = http.post('/token', data={**base, 'refresh_token': read['refresh_token'],
                                         'scope': 'tdx.read tdx.write'})
    assert elevated.status_code == 400
    assert elevated.json()['error'] == 'invalid_scope'

    write = exchange(http, client, obtain_code(http, client, 'tdx.read tdx.write')).json()
    rotated = http.post('/token', data={**base, 'refresh_token': write['refresh_token']}).json()
    assert set(rotated['scope'].split()) == {'tdx.read', 'tdx.write'}
    principal = asyncio.run(provider.load_access_token(rotated['access_token']))
    assert provider.write_grant_binding(principal)['scopes'] == ['tdx.read', 'tdx.write']


def test_narrow_refresh_access_cannot_write_or_re_elevate(pilot):
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    write = exchange(http, client, obtain_code(http, client, 'tdx.read tdx.write')).json()
    base = {'grant_type': 'refresh_token', 'client_id': client,
            'resource': provider.settings.resource}
    narrowed = http.post('/token', data={**base, 'refresh_token': write['refresh_token'],
                                         'scope': 'tdx.read'}).json()
    principal = asyncio.run(provider.load_access_token(narrowed['access_token']))
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.write_grant_binding(principal)
    assert http.post('/token', data={**base, 'refresh_token': narrowed['refresh_token'],
        'scope': 'tdx.read tdx.write'}).status_code == 400


def test_write_binding_rechecks_durable_family_and_all_values(pilot):
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    token = exchange(http, client, obtain_code(http, client, 'tdx.read tdx.write')).json()
    principal = asyncio.run(provider.load_access_token(token['access_token']))
    binding = provider.write_grant_binding(principal)
    assert set(binding) == {'subject', 'client_id', 'resource', 'family', 'scopes', 'grant_expiry'}
    assert binding['subject'] == UID and binding['client_id'] == client
    assert binding['resource'] == provider.settings.resource
    assert binding['scopes'] == ['tdx.read', 'tdx.write']
    for key, replacement in [('subject', str(UUID(int=2))), ('client_id', 'other'),
                             ('resource', 'https://other.test/mcp'), ('family', 'other'),
                             ('scopes', ['tdx.read']), ('grant_expiry', binding['grant_expiry'] + 1)]:
        with pytest.raises(RuntimeError, match='write authorization'):
            provider.validate_write_grant({**binding, key: replacement})
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.validate_write_grant({**binding, 'extra': 'value'})

    with provider.store.transaction() as db:
        before_revoke = provider.store.get(db, 'family', binding['family'])
    assert http.post('/revoke', data={'client_id': client,
        'token': token['access_token']}).status_code == 200
    with provider.store.transaction() as db:
        after_revoke = provider.store.get(db, 'family', binding['family'])
    assert {k: v for k, v in after_revoke.items() if k != 'revoked'} == {
        k: v for k, v in before_revoke.items() if k != 'revoked'}
    assert before_revoke['revoked'] is False and after_revoke['revoked'] is True
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.validate_write_grant(binding)


def test_fresh_login_does_not_extend_an_existing_write_family(pilot, monkeypatch):
    http, provider, _, _ = pilot
    now = int(time.time())
    old_expiry = now + 90
    provider.login = lambda u, p: (UID, 'first-upstream', old_expiry)
    client = register(http).json()['client_id']
    old_token = exchange(http, client,
                         obtain_code(http, client, 'tdx.read tdx.write')).json()
    old_binding = provider.write_grant_binding(
        asyncio.run(provider.load_access_token(old_token['access_token'])))
    assert old_binding['grant_expiry'] == old_expiry

    new_expiry = now + 1800
    provider.login = lambda u, p: (UID, 'replacement-upstream', new_expiry)
    exchange(http, client, obtain_code(http, client, 'tdx.read tdx.write'))
    assert provider.validate_write_grant(old_binding)['grant_expiry'] == old_expiry
    monkeypatch.setattr('dynamix_manager.personal_auth.time.time', lambda: old_expiry + 1)
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.validate_write_grant(old_binding)


def test_restored_provider_configuration_cannot_authorize_old_binding(pilot):
    from dynamix_manager.hosted import HostedSettings
    from dynamix_manager.personal_auth import PersonalAuthProvider
    http, provider, _, vault = pilot
    client = register(http).json()['client_id']
    token = exchange(http, client,
                     obtain_code(http, client, 'tdx.read tdx.write')).json()
    binding = provider.write_grant_binding(
        asyncio.run(provider.load_access_token(token['access_token'])))

    changed_subject = PersonalAuthProvider(provider.settings, vault, str(UUID(int=2)), [CALLBACK])
    changed_resource = PersonalAuthProvider(
        HostedSettings('https://other-connector.test', provider.settings.issuer,
                       provider.settings.jwks_url), vault, UID, [CALLBACK])
    changed_issuer = PersonalAuthProvider(
        HostedSettings(provider.settings.public_url, 'https://other-issuer.test',
                       provider.settings.jwks_url), vault, UID, [CALLBACK])
    for restored in (changed_subject, changed_resource, changed_issuer):
        with pytest.raises(RuntimeError, match='write authorization'):
            restored.validate_write_grant(binding)


def test_legacy_family_keeps_read_access_but_has_no_write_authority(pilot):
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    token = exchange(http, client, obtain_code(http, client, 'tdx.read tdx.write')).json()
    principal = asyncio.run(provider.load_access_token(token['access_token']))
    family = principal.claims['tdx_grant_family']
    with provider.store.transaction() as db:
        current = provider.store.get(db, 'family', family)
        provider.store.put(db, 'family', family, {'revoked': False,
            'expires_at': current['expires_at']})
    restored = asyncio.run(provider.load_access_token(token['access_token']))
    assert restored.subject == UID and 'tdx.read' in restored.scopes
    with pytest.raises(RuntimeError, match='write authorization'):
        provider.write_grant_binding(restored)


def test_write_binding_requires_server_loaded_trusted_token_context(pilot):
    from mcp.server.auth.provider import AccessToken
    http, provider, _, _ = pilot
    client = register(http).json()['client_id']
    token = exchange(http, client, obtain_code(http, client, 'tdx.read tdx.write')).json()
    loaded = asyncio.run(provider.load_access_token(token['access_token']))
    assert loaded.claims['iss'] == provider.settings.issuer
    assert isinstance(loaded.claims['tdx_grant_family'], str)
    for changes in [
        {'claims': {'iss': provider.settings.issuer}},
        {'claims': {'iss': 'https://evil.test', 'tdx_grant_family': loaded.claims['tdx_grant_family']}},
        {'subject': 'other'}, {'client_id': 'other'}, {'resource': 'https://other.test/mcp'},
        {'scopes': ['tdx.read']}, {'scopes': ['tdx.read', 'tdx.write', 'unknown']},
    ]:
        candidate = AccessToken(**{**loaded.model_dump(), **changes})
        with pytest.raises(RuntimeError, match='write authorization'):
            provider.write_grant_binding(candidate)


def family_record(provider, access_token):
    family = asyncio.run(provider.load_access_token(access_token)).claims['tdx_grant_family']
    with provider.store.transaction() as db:
        return provider.store.get(db, 'family', family)


def test_remember_login_stores_credentials_and_issues_long_lived_grant(pilot):
    http, provider, calls, vault = pilot
    client = register(http).json()['client_id']
    page = []
    login_form(http, client, page=page)
    assert 'name="remember"' in page[0] and 'checked' in page[0]
    assert 'encrypted' in page[0].lower()

    code = obtain_code(http, client, remember=True)
    stored = vault.get(provider.settings.issuer, UID)
    assert stored['username'] == 'allowed' and stored['password'] == 'private-password'
    assert b'private-password' not in vault.path.read_bytes()
    tokens = exchange(http, client, code).json()
    family = family_record(provider, tokens['access_token'])
    assert family['expires_at'] > time.time() + 80 * 86400
    assert family['expires_at'] <= time.time() + provider.GRANT_LIFETIME + 5


def test_login_without_remember_keeps_grant_bounded_by_tdx_expiry(pilot):
    http, provider, calls, vault = pilot
    client = register(http).json()['client_id']
    code = obtain_code(http, client)
    stored = vault.get(provider.settings.issuer, UID)
    assert stored.get('password') is None and stored.get('username') is None
    tokens = exchange(http, client, code).json()
    assert family_record(provider, tokens['access_token'])['expires_at'] <= time.time() + 1800


def test_personal_credential_renews_expiring_token_with_stored_password(pilot, monkeypatch):
    http, provider, calls, vault = pilot
    now = time.time()
    monkeypatch.setattr(provider, 'RENEW_MARGIN', 300)
    vault.put(provider.settings.issuer, UID, UID, 'fresh-token', now + 1200, username='allowed', password='private-password')
    assert provider.personal_credential(UID)['token'] == 'fresh-token'
    assert calls == []
    vault.update_token(provider.settings.issuer, UID, 'stale-token', now + 120)
    renewed = provider.personal_credential(UID)
    assert renewed['token'] == 'upstream-secret' and renewed['uid'] == UID
    assert calls == [('allowed', 'private-password')]
    assert vault.get(provider.settings.issuer, UID)['token'] == 'upstream-secret'
    assert provider.personal_credential(UID)['token'] == 'upstream-secret'
    assert len(calls) == 1


def test_personal_credential_without_password_or_with_failed_renewal_falls_back_safely(pilot, monkeypatch):
    http, provider, calls, vault = pilot
    now = time.time()
    clock = [now]
    monkeypatch.setattr('dynamix_manager.hosted_vault.time.time', lambda: clock[0])
    monkeypatch.setattr('dynamix_manager.personal_auth.time.time', lambda: clock[0])
    vault.put(provider.settings.issuer, UID, UID, 'old-token', now + 120)
    assert provider.personal_credential(UID)['token'] == 'old-token'
    assert calls == []
    clock[0] = now + 200
    with pytest.raises(RuntimeError, match='relinked'):
        provider.personal_credential(UID)

    clock[0] = now
    vault.put(provider.settings.issuer, UID, UID, 'old-token', now + 120, username='allowed', password='pw')

    def failing(username, password):
        raise RuntimeError('private upstream failure detail')

    provider.login = failing
    assert provider.personal_credential(UID)['token'] == 'old-token'
    clock[0] = now + 200
    with pytest.raises(RuntimeError) as error:
        provider.personal_credential(UID)
    assert 'private upstream' not in str(error.value)


def test_personal_credential_wipes_credentials_that_renew_as_another_identity(pilot):
    http, provider, calls, vault = pilot
    now = time.time()
    vault.put(provider.settings.issuer, UID, UID, 'stale-token', now + 120, username='other', password='pw')
    with pytest.raises(RuntimeError, match='relinked'):
        provider.personal_credential(UID)
    with pytest.raises(RuntimeError, match='linked'):
        vault.get(provider.settings.issuer, UID, allow_expired=True)


UID_B = '00000000-0000-0000-0000-00000000000b'


@pytest.fixture
def open_pilot(tmp_path):
    """Multi-user mode: no allowed UID; anyone TDX authenticates becomes their own subject."""
    from unittest.mock import Mock
    from dynamix_manager.hosted import HostedSettings, create_app
    from dynamix_manager.hosted_vault import CredentialVault
    from dynamix_manager.personal_auth import PersonalAuthProvider
    from dynamix_manager.plugin import Connection
    settings = HostedSettings('https://connector.test', 'https://connector.test', 'https://connector.test/unused')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    people = {'allowed': UID, 'bob': UID_B}
    tokens_seen = []

    def login(username, password):
        if username not in people:
            raise RuntimeError('bad credentials')
        return people[username], f'tdx-{username}', int(time.time()) + 86400

    def make_connection(values):
        tokens_seen.append(values['WORKBENCH_PERSONAL_TOKEN'])
        client = Mock()
        client.list_ticketing_applications.side_effect = lambda x: x
        client.fetch_applications.return_value = [{'AppID': 634, 'Name': 'InfoTech Tickets'}]
        uid = {'tdx-allowed': UID, 'tdx-bob': UID_B}[values['WORKBENCH_PERSONAL_TOKEN']]
        client.session.get.return_value.json.return_value = {'UID': uid}
        client.get_ticket.return_value = {'ID': 1, 'Title': 'for ' + uid}
        return Connection(values, client=client)

    provider = PersonalAuthProvider(settings, vault, None, [CALLBACK], login=login)
    with TestClient(create_app(settings, vault, auth_provider=provider, connection_factory=make_connection),
                    base_url=settings.public_url) as http:
        yield http, provider, vault, tokens_seen


def sign_in(http, client, username, scope='tdx.read tdx.write'):
    form = login_form(http, client, scope)
    response = http.post('/personal/login', data={**form, 'username': username, 'password': 'pw', 'consent': 'yes'},
                         headers={'Origin': 'https://connector.test'}, follow_redirects=False)
    assert response.status_code == 200, response.text
    target = html.unescape(re.search(r'href="([^"]+)"', response.text)[1])
    return exchange(http, client, parse_qs(urlsplit(target).query)['code'][0]).json()


def test_multi_user_mode_isolates_each_persons_credentials_and_grants(open_pilot):
    http, provider, vault, tokens_seen = open_pilot
    assert provider.allowed_uid is None
    client = register(http).json()['client_id']
    alice = sign_in(http, client, 'allowed')
    bob = sign_in(http, client, 'bob')
    assert asyncio.run(provider.load_access_token(alice['access_token'])).subject == UID
    assert asyncio.run(provider.load_access_token(bob['access_token'])).subject == UID_B
    assert vault.get(provider.settings.issuer, UID)['token'] == 'tdx-allowed'
    assert vault.get(provider.settings.issuer, UID_B)['token'] == 'tdx-bob'

    call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'get_ticket', 'arguments': {'ticket_id': 1}}}
    for tokens, uid, tdx in ((alice, UID, 'tdx-allowed'), (bob, UID_B, 'tdx-bob')):
        headers = {'Authorization': 'Bearer ' + tokens['access_token'], 'Accept': 'application/json, text/event-stream'}
        result = http.post('/mcp', json=call, headers=headers).json()['result']['structuredContent']
        assert result['detail']['Title'] == 'for ' + uid
        assert tokens_seen[-1] == tdx

    for tokens, uid in ((alice, UID), (bob, UID_B)):
        binding = provider.write_grant_binding(asyncio.run(provider.load_access_token(tokens['access_token'])))
        assert binding['subject'] == uid

    # An unknown TDX login is refused; nothing is linked for it.
    form = login_form(http, client)
    denied = http.post('/personal/login', data={**form, 'username': 'stranger', 'password': 'pw', 'consent': 'yes'},
                       headers={'Origin': 'https://connector.test'})
    assert denied.status_code == 400
    with pytest.raises(RuntimeError, match='linked'):
        vault.get(provider.settings.issuer, 'stranger')


def test_multi_user_credential_renewal_stays_bound_to_the_same_subject(open_pilot):
    http, provider, vault, _ = open_pilot
    now = time.time()
    vault.put(provider.settings.issuer, UID_B, UID_B, 'stale', now + 60, username='bob', password='pw')
    renewed = provider.personal_credential(UID_B)
    assert renewed['token'] == 'tdx-bob' and renewed['uid'] == UID_B
    # Credentials that renew as a different person are wiped, even without an allowlist.
    vault.put(provider.settings.issuer, UID, UID, 'stale', now + 60, username='bob', password='pw')
    with pytest.raises(RuntimeError, match='relinked'):
        provider.personal_credential(UID)


def test_keep_me_connected_defaults_off_in_production(pilot):
    http, provider, calls, vault = pilot
    client = register(http).json()['client_id']
    page = []
    login_form(http, client, page=page)
    assert 'name="remember"' in page[0]
    assert re.search(r'name="remember"[^>]*checked', page[0]) is None


def test_rotated_refresh_tokens_are_retained_only_for_a_short_replay_window(pilot):
    http, provider, calls, vault = pilot
    client = register(http).json()['client_id']
    code = obtain_code(http, client, remember=True)
    tokens = exchange(http, client, code).json()
    form = {'grant_type': 'refresh_token', 'client_id': client, 'refresh_token': tokens['refresh_token'],
            'resource': provider.settings.resource}
    rotated = http.post('/token', data=form).json()
    with provider.store.transaction() as db:
        used = provider.store.get(db, 'refresh', tokens['refresh_token'])
        fresh = provider.store.get(db, 'refresh', rotated['refresh_token'])
    assert used['used'] is True
    assert used['expires_at'] <= time.time() + provider.REFRESH_REPLAY_WINDOW + 5
    assert fresh['expires_at'] > time.time() + 80 * 86400
    # Replay inside the window still revokes the family.
    assert http.post('/token', data=form).status_code == 400
    assert http.post('/token', data={**form, 'refresh_token': rotated['refresh_token']}).status_code == 400
