"""Real MCP -> service -> encrypted store -> synthetic TDX transport checks."""

import pytest
from starlette.testclient import TestClient

from dynamix_manager.hosted import HostedFastMCP
from dynamix_manager.plugin import create_server
import test_ticket_write_service as service_fixtures


setup = service_fixtures.setup


@pytest.mark.parametrize('name,action', [
    ('add_ticket_comment', {'kind': 'comment', 'ticket_id': 1001, 'comments': 'Hello'}),
    ('update_ticket_status', {'kind': 'status', 'ticket_id': 1001, 'comments': 'Done', 'status_id': 5}),
    ('assign_ticket', {'kind': 'assign', 'ticket_id': 1001, 'responsible_group_id': 4}),
    ('edit_ticket', {'kind': 'edit', 'ticket_id': 1001, 'title': 'After'}),
])
def test_direct_mcp_http_dispatches_once_without_browser(setup, monkeypatch, name, action):
    monkeypatch.setattr('dynamix_manager.ticket_writes.tools.get_access_token', lambda: 'principal')
    server = create_server('/unused', write_service=setup.service, connection_provider=lambda: None,
                           fastmcp_class=HostedFastMCP, stateless_http=True,
                           json_response=True)
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': name, 'arguments': {'action': action, 'request_id': 'http-request-1'},
    }}
    with TestClient(server.streamable_http_app(), base_url='http://127.0.0.1:8000') as client:
        headers = {'Accept': 'application/json, text/event-stream'}
        first = client.post('/mcp', json=request, headers=headers).json()['result']
        assert not first.get('isError')
        assert first['structuredContent']['outcome'] == 'applied'
        assert 'review_url' not in first['structuredContent']
        second = client.post('/mcp', json=request, headers=headers).json()['result']
        assert second['structuredContent'] == first['structuredContent']
    assert setup.upstream.apply_count == 1


def test_direct_mcp_http_cannot_submit_without_current_write_grant(setup, monkeypatch):
    monkeypatch.setattr('dynamix_manager.ticket_writes.tools.get_access_token', lambda: None)
    server = create_server('/unused', write_service=setup.service, connection_provider=lambda: None,
                           fastmcp_class=HostedFastMCP, stateless_http=True,
                           json_response=True)
    with TestClient(server.streamable_http_app(), base_url='http://127.0.0.1:8000') as client:
        result = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
                             json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                                 'name': 'add_ticket_comment', 'arguments': {
                                     'action': {'kind': 'comment', 'ticket_id': 1001, 'comments': 'Hello'},
                                     'request_id': 'unauthorized-request',
                                 },
                             }}).json()['result']
    assert result['isError']
    assert 'insufficient_scope' in result['_meta']['mcp/www_authenticate'][0]
    assert setup.upstream.apply_count == 0


def test_blocked_direct_request_stays_conflict_after_other_operation_resolves(setup):
    from dynamix_manager.ticket_writes.models import parse_action
    from dynamix_manager.ticket_writes.store import AuthoritativeAppliedEvidence

    setup.upstream.apply_error = TimeoutError('synthetic timeout')
    first = setup.service.submit('principal', parse_action({
        'kind': 'comment', 'ticket_id': 1001, 'comments': 'First',
    }), 'first-request')
    assert first.outcome == 'unknown'
    action = parse_action({'kind': 'edit', 'ticket_id': 1001, 'title': 'After'})
    blocked = setup.service.submit('principal', action, 'blocked-request')
    assert blocked.outcome == 'conflict'
    setup.store.reconcile_authoritative(first.operation_id, AuthoritativeAppliedEvidence(
        'synthetic-test-feed-evidence', setup.clock[0],
    ))
    setup.upstream.apply_error = None
    replay = setup.service.submit('principal', action, 'blocked-request')
    assert replay == blocked
    assert setup.upstream.apply_count == 1
