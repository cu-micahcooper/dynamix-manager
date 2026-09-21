import asyncio
import time
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa


def test_vault_encrypts_binds_and_revokes(tmp_path):
    from dynamix_manager.hosted_vault import CredentialVault
    path = tmp_path / 'vault.sqlite'
    vault = CredentialVault(path, Fernet.generate_key())
    vault.put('issuer', 'alice', '00000000-0000-0000-0000-000000000001',
              'sensitive-tdx-token', int(time.time()) + 60)
    assert vault.get('issuer', 'alice')['token'] == 'sensitive-tdx-token'
    assert b'sensitive-tdx-token' not in path.read_bytes()
    with pytest.raises(RuntimeError, match='linked'):
        vault.get('issuer', 'bob')
    vault.revoke('issuer', 'alice')
    with pytest.raises(RuntimeError, match='linked'):
        vault.get('issuer', 'alice')


def test_vault_rejects_expired_credentials(tmp_path):
    from dynamix_manager.hosted_vault import CredentialVault
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    with pytest.raises(ValueError):
        vault.put('issuer', 'alice', '00000000-0000-0000-0000-000000000001', 'token', 1)


def test_vault_rejects_swapped_and_tampered_records(tmp_path):
    from dynamix_manager.hosted_vault import CredentialVault
    path = tmp_path / 'vault.sqlite'
    vault = CredentialVault(path, Fernet.generate_key())
    vault.put('issuer', 'alice', '00000000-0000-0000-0000-000000000001', 'secret', time.time() + 60)
    with sqlite3.connect(path) as db:
        value = db.execute('SELECT value FROM credentials').fetchone()[0]
        db.execute('INSERT INTO credentials VALUES (?,?)', (vault._id('issuer', 'bob'), value))
    with pytest.raises(RuntimeError, match='relinked'):
        vault.get('issuer', 'bob')
    with sqlite3.connect(path) as db:
        db.execute('UPDATE credentials SET value=?', (b'broken',))
    with pytest.raises(RuntimeError, match='relinked'):
        vault.get('issuer', 'alice')


def test_vault_checks_expiry_on_each_read(tmp_path, monkeypatch):
    from dynamix_manager.hosted_vault import CredentialVault
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    vault.put('issuer', 'alice', '00000000-0000-0000-0000-000000000001', 'secret', time.time() + 60)
    monkeypatch.setattr('dynamix_manager.hosted_vault.time.time', lambda: 10**12)
    with pytest.raises(RuntimeError, match='relinked'):
        vault.get('issuer', 'alice')


def test_vault_stores_renewal_credentials_and_updates_tokens(tmp_path, monkeypatch):
    from dynamix_manager.hosted_vault import CredentialVault
    path = tmp_path / 'vault.sqlite'
    vault = CredentialVault(path, Fernet.generate_key())
    uid = '00000000-0000-0000-0000-000000000001'
    now = time.time()
    vault.put('issuer', 'alice', uid, 'token-1', now + 60, username='alice@example.edu', password='pw-secret')
    record = vault.get('issuer', 'alice')
    assert record['username'] == 'alice@example.edu' and record['password'] == 'pw-secret'
    assert b'pw-secret' not in path.read_bytes() and b'alice@example.edu' not in path.read_bytes()
    vault.update_token('issuer', 'alice', 'token-2', now + 7200)
    record = vault.get('issuer', 'alice')
    assert record['token'] == 'token-2' and record['expires_at'] == now + 7200
    assert record['password'] == 'pw-secret' and record['uid'] == uid
    with pytest.raises(RuntimeError, match='linked'):
        vault.update_token('issuer', 'bob', 'token', now + 60)
    with pytest.raises(ValueError):
        vault.update_token('issuer', 'alice', 'token-3', now - 1)
    monkeypatch.setattr('dynamix_manager.hosted_vault.time.time', lambda: now + 10_000)
    with pytest.raises(RuntimeError, match='relinked'):
        vault.get('issuer', 'alice')
    expired = vault.get('issuer', 'alice', allow_expired=True)
    assert expired['token'] == 'token-2' and expired['password'] == 'pw-secret'
    plain = CredentialVault(tmp_path / 'plain.sqlite', Fernet.generate_key())
    plain.put('issuer', 'carol', uid, 'token', now + 10_060)
    assert plain.get('issuer', 'carol').get('password') is None


def test_signed_oauth_tokens_are_strictly_validated():
    from dynamix_manager.hosted import OAuthVerifier
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk['kid'] = 'test'

    async def keys():
        return {'keys': [jwk]}

    verifier = OAuthVerifier('https://identity.test', 'https://connector.test/mcp',
                             'https://identity.test/keys', keys_loader=keys)
    claims = dict(iss='https://identity.test', aud='https://connector.test/mcp',
                  sub='alice', exp=int(time.time()) + 60, iat=int(time.time()),
                  scope='tdx.read', client_id='chatgpt')

    def verify(values):
        token = jwt.encode(values, key, algorithm='RS256', headers={'kid': 'test'})
        return asyncio.run(verifier.verify_token(token))

    assert verify(claims).subject == 'alice'
    for change in [{'iss': 'https://evil.test'}, {'aud': 'other'}, {'exp': 1},
                   {'scope': 'other'}, {'sub': ''}, {'client_id': ''},
                   {'aud': [claims['aud'], 'other']}, {'scope': ['tdx.read']},
                   {'iat': int(time.time()) + 600}]:
        assert verify({**claims, **change}) is None
    for missing in ('exp', 'sub', 'aud', 'iss', 'iat'):
        assert verify({k: v for k, v in claims.items() if k != missing}) is None
    assert asyncio.run(verifier.verify_token('not-a-token')) is None
    unknown = jwt.encode(claims, key, algorithm='RS256', headers={'kid': 'unknown'})
    assert asyncio.run(verifier.verify_token(unknown)) is None
    forged = jwt.encode(claims, 'a' * 32, algorithm='HS256', headers={'kid': 'test'})
    assert asyncio.run(verifier.verify_token(forged)) is None


def test_jwks_refetches_unknown_kid_once_and_keeps_cached_keys_when_refresh_fails(monkeypatch):
    import httpx
    import dynamix_manager.hosted as hosted
    from dynamix_manager.hosted import OAuthVerifier
    keys = {}
    for name in ('a', 'b'):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
        jwk['kid'] = name
        keys[name] = (private, jwk)
    published = {'keys': [keys['a'][1]]}
    calls, failing = [], [False]
    now = [1_000.0]
    monkeypatch.setattr(hosted.time, 'monotonic', lambda: now[0])

    async def loader():
        calls.append(1)
        if failing[0]:
            raise httpx.HTTPError('identity provider is down')
        return published

    verifier = OAuthVerifier('https://identity.test', 'https://connector.test/mcp',
                             'https://identity.test/keys', keys_loader=loader)

    def verify(kid):
        claims = dict(iss='https://identity.test', aud='https://connector.test/mcp', sub='alice',
                      exp=int(time.time()) + 60, iat=int(time.time()), scope='tdx.read', client_id='chatgpt')
        token = jwt.encode(claims, keys[kid][0], algorithm='RS256', headers={'kid': kid})
        return asyncio.run(verifier.verify_token(token))

    assert verify('a').subject == 'alice'
    assert verify('a').subject == 'alice'
    assert len(calls) == 1
    # Key rotation: a token with a freshly published kid triggers one refetch.
    published = {'keys': [keys['a'][1], keys['b'][1]]}
    now[0] += 60
    assert verify('b').subject == 'alice'
    assert len(calls) == 2
    # Repeated unknown kids do not refetch inside the minimum refresh interval.
    published = {'keys': [keys['a'][1]]}
    keys['c'] = keys['b']
    assert verify('c') is None
    assert len(calls) == 2
    # After the cache TTL, a failing refresh keeps serving the last good key set.
    now[0] += 3600
    failing[0] = True
    assert verify('a').subject == 'alice'
    assert verify('b').subject == 'alice'
    assert len(calls) == 3


def test_jwks_first_fetch_failure_denies_access():
    import httpx
    from dynamix_manager.hosted import OAuthVerifier

    async def loader():
        raise httpx.HTTPError('down')

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({'sub': 'alice'}, key, algorithm='RS256', headers={'kid': 'test'})
    verifier = OAuthVerifier('https://identity.test', 'https://connector.test/mcp',
                             'https://identity.test/keys', keys_loader=loader)
    assert asyncio.run(verifier.verify_token(token)) is None


@pytest.mark.parametrize('payload', [None, {}, {'keys': None}, {'keys': ['invalid']}, {'keys': []}])
def test_jwks_malformed_responses_fail_closed(payload):
    from dynamix_manager.hosted import OAuthVerifier
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({'sub': 'alice'}, key, algorithm='RS256', headers={'kid': 'test'})

    async def keys():
        return payload

    verifier = OAuthVerifier('https://identity.test', 'https://connector.test/mcp',
                             'https://identity.test/keys', keys_loader=keys)
    assert asyncio.run(verifier.verify_token(token)) is None


def test_hosted_http_isolation_and_authentication(tmp_path, monkeypatch):
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedSettings, create_app
    from dynamix_manager.hosted_vault import CredentialVault
    from dynamix_manager.plugin import Connection
    monkeypatch.setenv('TDX_USERNAME', 'SHARED_ACCOUNT_MUST_NOT_BE_USED')
    monkeypatch.setenv('TDX_PASSWORD', 'SHARED_SECRET_MUST_NOT_BE_USED')

    settings = HostedSettings('https://connector.test', 'https://identity.test',
                              'https://identity.test/keys')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    uids = {'alice': '00000000-0000-0000-0000-000000000001',
            'bob': '00000000-0000-0000-0000-000000000002'}
    for user, uid in uids.items():
        vault.put(settings.issuer, user, uid, user + '-tdx', int(time.time()) + 60)
    clients = []
    mismatch = set()
    slow_started, release_slow = threading.Event(), threading.Event()
    slow = set()

    def make_connection(values):
        assert 'TDX_USERNAME' not in values and 'TDX_PASSWORD' not in values
        client = Mock()
        personal = values['WORKBENCH_PERSONAL_TOKEN'].split('-')[0]
        client.list_ticketing_applications.side_effect = lambda x: x
        client.fetch_applications.return_value = [{'AppID': 634, 'Name': 'InfoTech Tickets'}]
        client.session.get.return_value.json.return_value = {
            'UID': uids['bob'] if personal in mismatch else uids[personal]}
        def get_ticket(*args, **kwargs):
            if personal in slow:
                slow_started.set()
                release_slow.wait(3)
            return {'ID': 1, 'Title': personal}
        client.get_ticket.side_effect = get_ticket
        clients.append(client)
        return Connection(values, client=client)

    from mcp.server.auth.provider import AccessToken

    class Verifier:
        async def verify_token(self, token):
            if token not in {*uids, 'no-scope'}:
                return None
            return AccessToken(token=token, client_id='chatgpt', subject=token,
                               scopes=[] if token == 'no-scope' else ['tdx.read'], resource=settings.resource,
                               claims={'iss': settings.issuer})

    app = create_app(settings, vault, verifier=Verifier(), connection_factory=make_connection)
    with TestClient(app, base_url=settings.public_url) as http:
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'get_ticket', 'arguments': {'ticket_id': 1}}}
        accept = {'Accept': 'application/json, text/event-stream'}
        assert http.post('/mcp', json=body, headers=accept).status_code == 401
        assert http.post('/mcp', json=body, headers={**accept, 'Authorization': 'Bearer no-scope'}).status_code == 403
        assert http.get('/healthz').json() == {'status': 'ok'}
        resource_body = {'jsonrpc': '2.0', 'id': 2, 'method': 'resources/read',
                         'params': {'uri': 'ui://teamdynamix/tickets-v2.html'}}
        assert http.post('/mcp', json=resource_body, headers=accept).status_code == 401
        metadata = http.get('/.well-known/oauth-protected-resource/mcp').json()
        assert metadata['resource'] == settings.resource
        assert metadata['scopes_supported'] == ['tdx.read']
        for user in uids:
            response = http.post('/mcp', json=body, headers={**accept, 'Authorization': f'Bearer {user}'})
            assert response.status_code == 200, response.text
            result = response.json()['result']['structuredContent']
            assert result['detail']['Title'] == user
            clients[-1].get_ticket.assert_called_once_with(1, user + '-tdx', 634, max_attempts=1)
            clients[-1].session.close.assert_called_once()
        def request(user):
            r = http.post('/mcp', json=body, headers={**accept, 'Authorization': f'Bearer {user}'})
            assert r.json()['result']['structuredContent']['detail']['Title'] == user
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(request, ['alice', 'bob'] * 5))
        slow.add('alice')
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(request, 'alice')
            assert slow_started.wait(2)
            started = time.monotonic()
            try:
                assert http.get('/healthz').status_code == 200
                request('bob')
                assert time.monotonic() - started < 1.5
            finally:
                release_slow.set()
            pending.result()
        slow.clear()
        for client in clients:
            client.session.close.assert_called_once()
        mismatch.add('alice')
        response = http.post('/mcp', json=body, headers={**accept, 'Authorization': 'Bearer alice'})
        assert response.json()['result']['isError']
        clients[-1].get_ticket.assert_not_called()
        clients[-1].session.close.assert_called_once()
        count = len(clients)
        vault.revoke(settings.issuer, 'alice')
        response = http.post('/mcp', json=body, headers={**accept, 'Authorization': 'Bearer alice'})
        assert response.json()['result']['isError']
        assert len(clients) == count


def test_missing_hosted_configuration_never_uses_project_env(monkeypatch):
    from dynamix_manager.hosted import from_environment
    monkeypatch.delenv('TDX_HOSTED_PUBLIC_URL', raising=False)
    with pytest.raises(ValueError, match='Missing required'):
        from_environment()


@pytest.mark.parametrize('public', ['http://connector.test', 'https://user:pass@connector.test',
                                   'https://connector.test/subpath', 'https://connector.test?x=1'])
def test_hosted_rejects_unsafe_urls(public):
    from dynamix_manager.hosted import HostedSettings
    with pytest.raises(ValueError):
        HostedSettings(public, 'https://identity.test', 'https://identity.test/keys')


def test_real_signed_tokens_protect_http_tools_and_resources(tmp_path):
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedSettings, OAuthVerifier, create_app
    from dynamix_manager.hosted_vault import CredentialVault
    settings = HostedSettings('https://connector.test', 'https://identity.test',
                              'https://identity.test/keys')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk['kid'] = 'test'

    async def keys():
        return {'keys': [jwk]}

    verifier = OAuthVerifier(settings.issuer, settings.resource, settings.jwks_url, keys_loader=keys)
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    claims = dict(iss=settings.issuer, aud=settings.resource, sub='alice',
                  exp=int(time.time()) + 60, iat=int(time.time()), scope='tdx.read', client_id='chatgpt')

    def headers(changes=None):
        token = jwt.encode({**claims, **(changes or {})}, key, algorithm='RS256', headers={'kid': 'test'})
        return {'Authorization': 'Bearer ' + token, 'Accept': 'application/json, text/event-stream'}

    with TestClient(create_app(settings, vault, verifier=verifier), base_url=settings.public_url) as http:
        listing = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        for change in [{'aud': 'other'}, {'iss': 'other'}, {'exp': 1}, {'scope': 'other'}]:
            response = http.post('/mcp', json=listing, headers=headers(change))
            assert response.status_code == 401
            assert 'resource_metadata=' in response.headers['www-authenticate']
        tools = http.post('/mcp', json=listing, headers=headers()).json()['result']['tools']
        assert len(tools) == 8
        assert all(t['_meta']['securitySchemes'][0]['scopes'] == ['tdx.read'] for t in tools)
        resource = {'jsonrpc': '2.0', 'id': 2, 'method': 'resources/read',
                    'params': {'uri': 'ui://teamdynamix/tickets-v2.html'}}
        result = http.post('/mcp', json=resource, headers=headers())
        assert result.status_code == 200
        assert 'text/html' in result.json()['result']['contents'][0]['mimeType']
        assert result.headers['cache-control'] == 'no-store'
        # A valid connector identity alone does not grant upstream ticket access.
        call = {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                'params': {'name': 'get_ticket', 'arguments': {'ticket_id': 1}}}
        assert http.post('/mcp', json=call, headers=headers()).json()['result']['isError']


def test_hosted_write_runtime_is_dynamic_personal_and_fail_closed():
    from dynamix_manager.hosted import HostedWriteRuntime
    state = {'enabled': False}
    runtime = HostedWriteRuntime(lambda: state['enabled'], personal_auth=True)
    with pytest.raises(RuntimeError, match='disabled'):
        runtime.require_enabled()
    state['enabled'] = True
    runtime.require_enabled()
    state['enabled'] = False
    with pytest.raises(RuntimeError, match='disabled'):
        runtime.require_enabled()

    external = HostedWriteRuntime(lambda: state['enabled'], personal_auth=False)
    state['enabled'] = True
    with pytest.raises(RuntimeError, match='personal authorization'):
        external.require_enabled()
    broken = HostedWriteRuntime(lambda: (_ for _ in ()).throw(ValueError('secret')), personal_auth=True)
    with pytest.raises(RuntimeError, match='disabled') as error:
        broken.require_enabled()
    assert 'secret' not in str(error.value)
    for value in (1, 'true', None):
        with pytest.raises(RuntimeError, match='disabled'):
            HostedWriteRuntime(lambda value=value: value, personal_auth=True).require_enabled()
    with pytest.raises(TypeError):
        HostedWriteRuntime(True, personal_auth=True)


def test_create_app_exposes_rechecked_write_runtime(tmp_path):
    from dynamix_manager.hosted import HostedSettings, create_app
    from dynamix_manager.hosted_vault import CredentialVault
    settings = HostedSettings('https://connector.test', 'https://identity.test',
                              'https://identity.test/keys')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    state = {'enabled': False}

    class Verifier:
        async def verify_token(self, token):
            return None

    app = create_app(settings, vault, verifier=Verifier(),
                     writes_enabled_provider=lambda: state['enabled'])
    assert app.write_runtime.personal_auth is False
    with pytest.raises(RuntimeError, match='disabled'):
        app.write_runtime.require_enabled()
    state['enabled'] = True
    with pytest.raises(RuntimeError, match='personal authorization'):
        app.write_runtime.require_enabled()
    with pytest.raises(TypeError):
        create_app(settings, vault, verifier=Verifier(), writes_enabled_provider=False)


def test_personal_app_retires_write_review_routes_and_service_only_in_personal_mode(tmp_path):
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedSettings, create_app
    from dynamix_manager.hosted_vault import CredentialVault

    settings = HostedSettings('https://connector.test', 'https://identity.test',
                              'https://identity.test/keys')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())

    class Provider:
        routes = []

        def __init__(self):
            from dynamix_manager.personal_auth_store import OAuthStore
            self.store = OAuthStore(vault)

        def guard(self, app):
            return app

    personal = create_app(settings, vault, auth_provider=Provider())
    assert personal.write_service is not None
    with TestClient(personal, base_url=settings.public_url) as http:
        assert http.get('/writes/review').status_code == 410

    class Verifier:
        async def verify_token(self, token):
            return None

    external = create_app(settings, vault, verifier=Verifier())
    assert external.write_service is None
    with TestClient(external, base_url=settings.public_url) as http:
        assert http.get('/writes/review').status_code == 404


def test_personal_hosted_server_registers_prepare_tools_and_preserves_per_tool_scopes(
        tmp_path, monkeypatch):
    import dynamix_manager.hosted as hosted
    from dynamix_manager.hosted_vault import CredentialVault
    from dynamix_manager.personal_auth_store import OAuthStore

    settings = hosted.HostedSettings('https://connector.test', 'https://connector.test',
                                     'https://connector.test/unused')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    captured = {}
    real_create_server = hosted.create_server

    def capture_server(*args, **kwargs):
        captured['kwargs'] = kwargs
        captured['server'] = real_create_server(*args, **kwargs)
        return captured['server']

    monkeypatch.setattr(hosted, 'create_server', capture_server)

    class Provider:
        routes = []

        def __init__(self):
            self.store = OAuthStore(vault)
            self.allow = False
            self.calls = 0

        def guard(self, app):
            return app

        def write_grant_binding(self, principal):
            self.calls += 1
            if not self.allow:
                raise RuntimeError('no current grant')
            return {'verified': True}

    provider = Provider()
    state = {'enabled': False}
    app = hosted.create_app(
        settings, vault, auth_provider=provider,
        writes_enabled_provider=lambda: state['enabled'],
    )
    assert app.write_service is not None
    tools = {tool.name: tool for tool in captured['server']._tool_manager.list_tools()}
    assert len(tools) == 18
    assert tools['ticket_write_metadata'].meta['securitySchemes'][0]['scopes'] == ['tdx.read']
    for name in ('add_ticket_comment', 'update_ticket_status',
                 'assign_ticket', 'edit_ticket', 'complete_ticket_task', 'create_ticket',
                 'ticket_write_result'):
        assert tools[name].meta['securitySchemes'][0]['scopes'] == ['tdx.read', 'tdx.write']
    for name in ('connection_status', 'ticket_statuses', 'search_tickets', 'my_queue',
                 'get_ticket', 'ticket_feed', 'survey_report', 'days_off'):
        assert tools[name].meta['securitySchemes'][0]['scopes'] == ['tdx.read']
    assert 'explicit user request' in captured['kwargs']['instructions']
    assert 'review link' not in captured['kwargs']['instructions']
    capability = captured['kwargs']['capability_provider']
    assert capability()['write_available'] is False
    assert provider.calls == 0
    state['enabled'] = True
    assert capability()['write_available'] is False
    assert provider.calls == 1
    provider.allow = True
    assert capability()['write_available'] is True
    assert capability()['read_only'] is False


@pytest.mark.parametrize('value', ['', '0', '1', 'TRUE', 'False', 'yes', ' true '])
def test_writes_enabled_environment_is_strict(value, monkeypatch):
    from dynamix_manager.hosted import from_environment
    monkeypatch.setenv('TDX_HOSTED_WRITES_ENABLED', value)
    with pytest.raises(ValueError, match='TDX_HOSTED_WRITES_ENABLED'):
        from_environment()


def test_external_identity_provider_cannot_enable_writes(tmp_path, monkeypatch):
    from dynamix_manager.hosted import from_environment
    values = {
        'TDX_HOSTED_AUTH_MODE': 'external',
        'TDX_HOSTED_WRITES_ENABLED': 'true',
        'TDX_HOSTED_PUBLIC_URL': 'https://connector.test',
        'TDX_HOSTED_ISSUER': 'https://identity.test',
        'TDX_HOSTED_JWKS_URL': 'https://identity.test/keys',
        'TDX_HOSTED_VAULT_PATH': str(tmp_path / 'vault.sqlite'),
        'TDX_HOSTED_VAULT_KEY': Fernet.generate_key().decode(),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match='personal authorization'):
        from_environment()
    # Without an explicit flag, external mode simply stays read-only.
    monkeypatch.delenv('TDX_HOSTED_WRITES_ENABLED')
    with pytest.raises(RuntimeError, match='disabled'):
        from_environment().write_runtime.require_enabled()


def test_personal_environment_defaults_writes_on_and_can_disable(tmp_path, monkeypatch):
    from dynamix_manager.hosted import from_environment
    values = {
        'TDX_HOSTED_AUTH_MODE': 'personal',
        'TDX_HOSTED_PUBLIC_URL': 'https://connector.test',
        'TDX_HOSTED_VAULT_PATH': str(tmp_path / 'vault.sqlite'),
        'TDX_HOSTED_VAULT_KEY': Fernet.generate_key().decode(),
        'TDX_HOSTED_ALLOWED_UID': '00000000-0000-0000-0000-000000000001',
        'TDX_HOSTED_REDIRECT_URIS': '[]',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv('TDX_HOSTED_WRITES_ENABLED', raising=False)
    from_environment().write_runtime.require_enabled()
    monkeypatch.setenv('TDX_HOSTED_WRITES_ENABLED', 'false')
    with pytest.raises(RuntimeError, match='disabled'):
        from_environment().write_runtime.require_enabled()
    monkeypatch.setenv('TDX_HOSTED_WRITES_ENABLED', 'true')
    from_environment().write_runtime.require_enabled()


def test_personal_app_renews_expiring_tdx_token_before_serving_a_request(tmp_path):
    from starlette.testclient import TestClient
    from dynamix_manager.hosted import HostedSettings, create_app
    from dynamix_manager.hosted_vault import CredentialVault
    from dynamix_manager.personal_auth import PersonalAuthProvider
    from dynamix_manager.plugin import Connection
    import test_personal_auth as flow

    settings = HostedSettings('https://connector.test', 'https://connector.test', 'https://connector.test/unused')
    vault = CredentialVault(tmp_path / 'vault.sqlite', Fernet.generate_key())
    logins, tokens_seen = [], []

    def login(username, password):
        logins.append((username, password))
        return flow.UID, f'tdx-token-{len(logins)}', int(time.time()) + 86400

    def make_connection(values):
        tokens_seen.append(values['WORKBENCH_PERSONAL_TOKEN'])
        client = Mock()
        client.list_ticketing_applications.side_effect = lambda x: x
        client.fetch_applications.return_value = [{'AppID': 634, 'Name': 'InfoTech Tickets'}]
        client.session.get.return_value.json.return_value = {'UID': flow.UID}
        return Connection(values, client=client)

    provider = PersonalAuthProvider(settings, vault, flow.UID, [flow.CALLBACK], login=login)
    app = create_app(settings, vault, auth_provider=provider, connection_factory=make_connection)
    with TestClient(app, base_url=settings.public_url) as http:
        client = flow.register(http).json()['client_id']
        code = flow.obtain_code(http, client, remember=True)
        access = flow.exchange(http, client, code).json()['access_token']
        headers = {'Authorization': 'Bearer ' + access, 'Accept': 'application/json, text/event-stream'}
        call = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'connection_status', 'arguments': {}}}
        assert http.post('/mcp', json=call, headers=headers).json()['result']['structuredContent']['connected']
        assert tokens_seen == ['tdx-token-1'] and len(logins) == 1
        # The stored TDX token is about to expire: the next request renews it transparently.
        vault.update_token(settings.issuer, flow.UID, 'tdx-token-1', time.time() + 30)
        assert http.post('/mcp', json=call, headers=headers).json()['result']['structuredContent']['connected']
        assert tokens_seen[-1] == 'tdx-token-2' and len(logins) == 2
        assert logins[-1] == ('allowed', 'private-password')
        assert vault.get(settings.issuer, flow.UID)['token'] == 'tdx-token-2'
