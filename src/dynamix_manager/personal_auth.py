"""Account-restricted TDX login and local OAuth grants for the personal pilot."""

import base64
import hashlib
import html
import logging
import math
import secrets
import threading
import time
from contextlib import contextmanager
from collections.abc import Mapping
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

import jwt
from mcp.server.auth.provider import (
    AccessToken, AuthorizationCode, AuthorizeError, RefreshToken, RegistrationError,
    TokenError, construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from dynamix_manager.personal_auth_store import OAuthStore, StateCapacityError
from dynamix_manager.plugin import Connection

# One inline stylesheet, allowed by hash only: no scripts, no external assets, no inline attributes.
PAGE_STYLE = """
:root{--blue:#003963;--blue-deep:#002a4a;--gold:#FBB93A;--orange:#F59536;--rule:#E7E6E6;
--ink:#1F2A37;--muted:#5B6673;--paper:#F3F5F8;--card:#FFFFFF;--focus:#F59536}
*{box-sizing:border-box}
html{background:var(--paper);color:var(--ink);font:16px/1.55 "Minion Pro","Iowan Old Style",Georgia,serif}
body{margin:0;padding:2.5rem 1rem 4rem}
main{max-width:46rem;margin:0 auto}
.card{background:var(--card);border:1px solid var(--rule);border-top:6px solid var(--blue);
box-shadow:0 1px 2px rgba(0,41,74,.06),0 12px 32px -18px rgba(0,41,74,.35)}
.card>*{padding-left:2rem;padding-right:2rem}
.eyebrow{margin:0;padding-top:1.1rem;padding-bottom:.9rem;border-bottom:1px solid var(--gold);
font:600 .78rem/1.2 "Myriad Pro","Segoe UI",system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--blue)}
h1,h2,h3{font-family:"Myriad Pro","Segoe UI",system-ui,sans-serif;font-weight:600;color:var(--blue);margin:0}
h1{font-size:1.85rem;line-height:1.15;padding-top:1.5rem}
h2{font-size:1.1rem;line-height:1.3;margin-bottom:.35rem}
p{margin:.65rem 0}
.lead{font-size:1.05rem;padding-bottom:.25rem}
.meta{color:var(--muted);font-size:.9rem;word-break:break-all}
.options{display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;padding-top:1rem;padding-bottom:1.25rem}
.option{border:1px solid var(--rule);border-radius:4px;padding:1.1rem 1.15rem;background:#FBFCFD}
.option p,.option ol{font-size:.95rem}
ol{margin:.5rem 0 .75rem;padding-left:1.25rem}
li{margin:.25rem 0}
label{display:block;margin:.6rem 0 .35rem;font:600 .85rem/1.3 "Myriad Pro","Segoe UI",system-ui,sans-serif;color:var(--ink)}
input[type=text],input[type=password],input:not([type]),textarea{display:block;width:100%;font:1rem/1.4 inherit;color:var(--ink);
background:#fff;border:1px solid #B9C2CC;border-radius:3px;padding:.55rem .65rem}
textarea{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.85rem;resize:vertical}
.check{display:flex;gap:.6rem;align-items:flex-start;font:400 .95rem/1.5 inherit;margin:.75rem 0}
.check input{margin:.3rem 0 0;flex:none;width:1.05rem;height:1.05rem;accent-color:var(--blue)}
.footer{border-top:1px solid var(--rule);background:#F8FAFC;padding-top:1.1rem;padding-bottom:1.5rem}
.actions{display:flex;flex-wrap:wrap;gap:1rem;align-items:center;margin-top:.9rem}
button,.button{display:inline-block;font:600 1rem/1 "Myriad Pro","Segoe UI",system-ui,sans-serif;color:#fff;
background:var(--blue);border:0;border-bottom:3px solid var(--gold);border-radius:3px;padding:.85rem 1.5rem;cursor:pointer;text-decoration:none}
button:hover,.button:hover{background:var(--blue-deep)}
a{color:var(--blue);text-underline-offset:.15em}
a:focus-visible,button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.status{display:flex;align-items:center;gap:1rem;padding-top:1.6rem}
.mark{flex:none;width:3rem;height:3rem;border-radius:50%;background:var(--blue);color:var(--gold);
font:700 1.6rem/3rem "Myriad Pro","Segoe UI",system-ui,sans-serif;text-align:center}
.mark.warn{background:var(--orange);color:#fff}
.small{font-size:.9rem;color:var(--muted)}
.card>:last-child{padding-bottom:1.75rem}
@media (max-width:40rem){body{padding:1rem .75rem 3rem}.card>*{padding-left:1.15rem;padding-right:1.15rem}
.options{grid-template-columns:1fr}h1{font-size:1.5rem}}
@media (prefers-reduced-motion:no-preference){.card{animation:rise .35s ease-out}}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
""".strip()
PAGE_STYLE_HASH = base64.b64encode(hashlib.sha256(PAGE_STYLE.encode()).digest()).decode()

# no-referrer makes native browser form POSTs send Origin: null, breaking the
# strict Origin check below. same-origin retains it without cross-site leakage.
HEADERS = {'Cache-Control': 'no-store', 'Pragma': 'no-cache', 'Referrer-Policy': 'same-origin',
           'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff',
           'Content-Security-Policy': (f"default-src 'none'; style-src 'sha256-{PAGE_STYLE_HASH}'; "
                                       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")}
EYEBROW = 'Cedarville University · TeamDynamix connector for ChatGPT'


def render_page(title, body, *, status=200, refresh=None):
    """Render one page under the hash-only CSP; ``refresh`` is an already-escaped URL to return to."""
    head = (f'<meta http-equiv="refresh" content="4;url={refresh}">' if refresh else '')
    return HTMLResponse(f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
{head}<style>{PAGE_STYLE}</style></head><body><main><section class="card">
<p class="eyebrow">{EYEBROW}</p>
{body}
</section></main></body></html>''', status_code=status, headers=HEADERS)
COOKIE = '__Host-tdx-login'
logger = logging.getLogger(__name__)
READ_SCOPES = frozenset({'tdx.read'})
WRITE_SCOPES = frozenset({'tdx.read', 'tdx.write'})
SUPPORTED_SCOPE_SETS = frozenset({READ_SCOPES, WRITE_SCOPES})
GRANT_BINDING_KEYS = frozenset({
    'subject', 'client_id', 'resource', 'family', 'scopes', 'grant_expiry',
})


def _canonical_scopes(scopes):
    selected = READ_SCOPES if scopes is None else frozenset(scopes)
    if selected not in SUPPORTED_SCOPE_SETS:
        raise ValueError('Unsupported OAuth scope set.')
    return ['tdx.read', 'tdx.write'] if selected == WRITE_SCOPES else ['tdx.read']


class PersonalAuthProvider:
    # Renew the stored TDX token when it has less than this long to live.
    RENEW_MARGIN = 3600
    # Connector grants for a remembered login outlive the 24-hour TDX token.
    GRANT_LIFETIME = 90 * 24 * 60 * 60
    # A rotated (used) refresh token is kept only long enough to detect replay.
    REFRESH_REPLAY_WINDOW = 24 * 60 * 60

    MAX_TOKEN_LENGTH = 8192

    def __init__(self, settings, vault, allowed_uid, redirect_uris, *, login=None, token_login=None):
        """``allowed_uid`` restricts the connector to one person; ``None`` admits anyone TDX authenticates."""
        self.settings, self.vault = settings, vault
        self._renewal_lock = threading.Lock()
        self.allowed_uid = str(UUID(allowed_uid)) if allowed_uid else None
        if not isinstance(redirect_uris, list) or any(not isinstance(x, str) for x in redirect_uris):
            raise ValueError('Redirect allowlist must be a JSON list of exact HTTPS URLs.')
        exact, prefixes = set(), set()
        for uri in redirect_uris:
            # "https://host/some/path/*" admits exactly one extra path segment under that prefix,
            # so each ChatGPT user's per-app callback (…/connector/oauth/<id>) can be accepted.
            is_prefix = uri.endswith('/*')
            base = uri[:-1] if is_prefix else uri
            parts = urlsplit(base)
            if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
                    or parts.fragment or '*' in base or (is_prefix and (parts.query or len(parts.path) < 2))):
                raise ValueError('Redirect allowlist must contain exact HTTPS URLs or an HTTPS path prefix ending in /*.')
            (prefixes if is_prefix else exact).add(base)
        self.redirect_uris = frozenset(exact)
        self.redirect_prefixes = frozenset(prefixes)
        self.store = OAuthStore(vault)
        self.login = login or self._tdx_login
        self.token_login = token_login or self._tdx_token_login
        self.routes = [Route('/personal/login', self.login_page, methods=['GET', 'POST'])]

    def redirect_allowed(self, uri):
        """Exact allowlisted URL, or one clean path segment under an allowlisted prefix."""
        uri = str(uri)
        if uri in self.redirect_uris:
            return True
        parts = urlsplit(uri)
        if parts.scheme != 'https' or parts.query or parts.fragment or parts.username or parts.password:
            return False
        for prefix in self.redirect_prefixes:
            if uri.startswith(prefix):
                tail = uri[len(prefix):]
                if tail and '/' not in tail and tail not in {'.', '..'}:
                    return True
        return False

    def _tdx_login(self, username, password):
        connection = Connection({
            'TDX_BASE_URL': self.settings.tdx_url, 'TDX_APP_ID': self.settings.tdx_client_id,
            'WORKBENCH_PERSONAL_USERNAME': username, 'WORKBENCH_PERSONAL_PASSWORD': password})
        try:
            if connection.auth_mode != 'user':
                raise ValueError('Personal account required.')
            # The only token decoded here comes directly from an HTTPS TDX login response.
            connection.token = connection.client.authenticate()
            connection.client.password = ''
            claims = jwt.decode(connection.token, options={'verify_signature': False})
            expiry = claims.get('exp')
            if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                    or not math.isfinite(expiry) or expiry <= time.time()):
                raise ValueError('Known upstream expiry required.')
            return connection.identity(), connection.token, int(expiry)
        finally:
            connection.client.password = ''
            connection.client.session.close()

    def _tdx_token_login(self, token):
        """Verify a bearer token the person obtained themselves via TDX single sign-on.

        The token is used only as-is: ``getuser`` proves who it belongs to and its own ``exp``
        bounds the link. Nothing is stored that could mint a new token.
        """
        if not isinstance(token, str) or not 0 < len(token) <= self.MAX_TOKEN_LENGTH or token.count('.') != 2:
            raise ValueError('A TeamDynamix bearer token is required.')
        claims = jwt.decode(token, options={'verify_signature': False})
        expiry = claims.get('exp')
        if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or expiry <= time.time()):
            raise ValueError('Known upstream expiry required.')
        connection = Connection({
            'TDX_BASE_URL': self.settings.tdx_url, 'TDX_APP_ID': self.settings.tdx_client_id,
            'WORKBENCH_PERSONAL_TOKEN': token})
        try:
            return connection.identity(), token, int(expiry)
        finally:
            connection.client.session.close()

    def personal_credential(self, subject):
        """Return the linked TDX credential, renewing an expiring token when a password is stored.

        Without stored renewal credentials the record is only valid until the TDX token
        expires. A failed renewal keeps serving a still-valid token and never leaks the
        upstream error; renewal that authenticates as a different person wipes the link.
        """
        issuer = self.settings.issuer
        with self._renewal_lock:
            record = self.vault.get(issuer, subject, allow_expired=True)
            expired = record['expires_at'] <= time.time()
            if record['expires_at'] - time.time() > self.RENEW_MARGIN:
                return record
            if not record.get('username') or not record.get('password'):
                if expired:
                    raise RuntimeError('Personal TeamDynamix access must be relinked.')
                return record
            try:
                uid, token, expiry = self.login(record['username'], record['password'])
                uid = str(UUID(str(uid)))
                if (isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                        or not math.isfinite(expiry) or expiry <= time.time() or not token):
                    raise ValueError('Known upstream expiry required.')
            except Exception:
                logger.warning('personal_renewal_failed')
                if expired:
                    raise RuntimeError('Personal TeamDynamix access must be relinked.') from None
                return record
            if uid != record['uid'] or uid != subject or (self.allowed_uid and uid != self.allowed_uid):
                logger.warning('personal_renewal_identity_mismatch')
                self.vault.revoke(issuer, subject)
                raise RuntimeError('Personal TeamDynamix access must be relinked.')
            self.vault.update_token(issuer, subject, token, int(expiry))
            return {**record, 'token': token, 'expires_at': int(expiry)}

    async def get_client(self, client_id):
        with self.store.transaction() as db:
            value = self.store.get(db, 'client', client_id)
        if not value:
            return None
        # Scope registration describes client capability, not user consent. Existing
        # read-only DCR records must be able to initiate a fresh write step-up, while
        # authorize() still requires a new login and explicit selected-scope consent.
        return OAuthClientInformationFull.model_validate({**value, 'scope': 'tdx.read tdx.write'})

    async def register_client(self, client_info):
        if (not client_info.redirect_uris or any(not self.redirect_allowed(uri) for uri in client_info.redirect_uris)):
            raise RegistrationError('invalid_redirect_uri', 'Callback is not approved.')
        try:
            _canonical_scopes(client_info.scope.split() if client_info.scope else None)
        except ValueError:
            raise RegistrationError('invalid_client_metadata', 'Requested scope set is not supported.') from None
        try:
            with self.store.transaction() as db:
                self.store.put(db, 'client', client_info.client_id, client_info.model_dump(mode='json'))
        except StateCapacityError:
            raise RegistrationError('invalid_client_metadata', 'Registration capacity reached.') from None

    async def authorize(self, client, params):
        try:
            scopes = _canonical_scopes(params.scopes)
        except ValueError:
            raise AuthorizeError('invalid_scope', 'Requested scope set is not supported.') from None
        if (not self.redirect_allowed(params.redirect_uri)
                or params.resource != self.settings.resource):
            raise AuthorizeError('invalid_request', 'Authorization request is not permitted.')
        transaction = secrets.token_urlsafe(32)
        try:
            with self.store.transaction() as db:
                self.store.put(db, 'transaction', transaction, {
                    'client': client.client_id,
                    'params': {**params.model_dump(mode='json'), 'scopes': scopes},
                    'expires': time.time() + 300, 'browser': None})
        except StateCapacityError:
            raise AuthorizeError('temporarily_unavailable', 'Try again later.') from None
        return self.settings.public_url + '/personal/login?transaction=' + transaction

    async def login_page(self, request):
        def denied(reason='session'):
            # Only fixed internal labels; never log request values or exceptions.
            logger.warning('personal_login_denied: %s', reason)
            return render_page('Login could not be completed', '''<div class="status"><div class="mark warn">!</div>
<h1>Login could not be completed.</h1></div>
<p class="lead">Nothing was changed. One of these is the usual cause:</p>
<div><ol><li>The link expired: it is good for five minutes and can be opened only once.</li>
<li>The username, password, or pasted token was not accepted by TeamDynamix.</li>
<li>Both sign-in options were filled in at once.</li></ol></div>
<p><strong>Start again from ChatGPT:</strong> open the TeamDynamix app there and choose Connect to get a fresh link.</p>''', status=400)
        if request.method == 'GET':
            key = request.query_params.get('transaction', '')
            browser, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with self.store.transaction() as db:
                value = self.store.get(db, 'transaction', key)
                if not value or value['expires'] <= time.time() or value['browser']:
                    return denied()
                value.update(browser=hashlib.sha256(browser.encode()).hexdigest(), csrf=csrf)
                self.store.put(db, 'transaction', key, value)
            callback = html.escape(value['params']['redirect_uri'], quote=True)
            write_access = set(value['params']['scopes']) == WRITE_SCOPES
            access_description = ('read and modify access (tdx.read and tdx.write)'
                                  if write_access else 'read-only access (tdx.read)')
            consent_label = ('Allow read and modify access (tdx.read and tdx.write)'
                             if write_access else 'Allow read-only access (tdx.read)')
            audience = ('Only the approved personal account can connect.' if self.allowed_uid else
                        'Sign in with your own TeamDynamix account; what you can see and change is '
                        'governed by your TeamDynamix permissions.')
            sso = html.escape(self.settings.tdx_url, quote=True) + '/api/auth/loginsso'
            response = render_page('Connect your TeamDynamix account', f'''<h1>Connect your TeamDynamix account</h1>
<p class="lead">This connector requests {access_description}. {audience}</p>
<p class="meta">Returns to: {callback} · This page expires five minutes after it opened.</p>
<form method="post" action="/personal/login">
<input type="hidden" name="transaction" value="{html.escape(key, quote=True)}">
<input type="hidden" name="csrf" value="{csrf}">
<div class="options">
<div class="option"><h2>Sign in with your TeamDynamix password</h2>
<p>Your password is sent to TeamDynamix once and is not kept unless you ask below.</p>
<label for="username">Username</label><input id="username" name="username" autocomplete="username" maxlength="254">
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" maxlength="1024">
<label class="check"><input type="checkbox" name="remember" value="yes"> <span>Keep me connected: store my
username and password encrypted so the connector can renew TeamDynamix access itself instead of
asking me to sign in every day. Leave unchecked to reconnect manually when TeamDynamix access expires.</span></label>
</div>
<div class="option"><h2>Sign in with Cedarville single sign-on</h2>
<p>No password is shared with this connector.</p>
<ol><li><a href="{sso}" target="_blank" rel="noopener noreferrer">Open the TeamDynamix SSO login</a> in a new tab and sign in.</li>
<li>TeamDynamix shows a long block of text: that is your access token. Copy all of it.</li>
<li>Paste it here.</li></ol>
<label for="token">Token</label><textarea id="token" name="token" rows="3" maxlength="8192" autocomplete="off" spellcheck="false"></textarea>
<p class="small">SSO tokens last 24 hours, so this option asks you to repeat this daily; nothing is stored that could sign in as you again.</p>
</div>
</div>
<div class="footer"><p>Fill in one option only, then confirm:</p>
<label class="check"><input type="checkbox" name="consent" value="yes" required> <span>{consent_label}</span></label>
<div class="actions"><button type="submit">Connect</button></div></div>
</form>''')
            response.set_cookie(COOKIE, browser, max_age=300, secure=True, httponly=True, samesite='strict', path='/')
            return response
        form = await request.form()
        key = str(form.get('transaction', ''))
        with self.store.transaction() as db:
            value = self.store.get(db, 'transaction', key)
            self.store.delete(db, 'transaction', key)
        if not value or value['expires'] <= time.time():
            return denied('session')
        if request.headers.get('origin') != self.settings.public_url:
            return denied('origin')
        if not secrets.compare_digest(value.get('browser') or '', hashlib.sha256(request.cookies.get(COOKIE, '').encode()).hexdigest()):
            return denied('browser_cookie')
        if not secrets.compare_digest((value.get('csrf') or '').encode(), str(form.get('csrf', '')).encode()):
            return denied('csrf')
        if form.get('consent') != 'yes':
            return denied('consent')
        username, password = form.get('username') or '', form.get('password') or ''
        token = (form.get('token') or '').strip() if isinstance(form.get('token'), str) else ''
        if token:
            # Option B: a token the person obtained through TDX single sign-on. Exclusive with a password.
            if username or password or len(token) > self.MAX_TOKEN_LENGTH:
                return denied('credential_fields')
            remember = False
        else:
            if (not isinstance(username, str) or not 0 < len(username) <= 254
                    or not isinstance(password, str) or not 0 < len(password) <= 1024):
                return denied('credential_fields')
            remember = form.get('remember') == 'yes'
        try:
            if token:
                uid, upstream, expiry = await run_in_threadpool(self.token_login, token)
            else:
                uid, upstream, expiry = await run_in_threadpool(self.login, username, password)
            uid = str(UUID(str(uid)))
            if ((self.allowed_uid and uid != self.allowed_uid) or isinstance(expiry, bool)
                    or not isinstance(expiry, (int, float)) or not math.isfinite(expiry)
                    or expiry <= time.time() or not upstream):
                return denied('account_or_expiry')
            # Each authenticated person is their own OAuth subject; TDX permissions govern what they can do.
            self.vault.put(self.settings.issuer, uid, uid, upstream, expiry,
                           username=username if remember else None,
                           password=password if remember else None)
        except Exception:
            # Never log request values, upstream responses, or exception strings.
            return denied('upstream_or_vault')
        finally:
            password = token = None
        params = value['params']
        if not self.redirect_allowed(params['redirect_uri']):
            return denied()
        # A remembered login can renew its TDX token, so the connector grant may outlive it.
        grant_expiry = int(time.time() + self.GRANT_LIFETIME) if remember else int(expiry)
        code = secrets.token_urlsafe(32)
        try:
            with self.store.transaction() as db:
                self.store.put(db, 'code', code, {**params, 'client_id': value['client'],
                    'expires_at': min(time.time() + 120, grant_expiry), 'subject': uid,
                    'upstream_expiry': grant_expiry})
        except StateCapacityError:
            return denied()
        # Keep credential form submissions same-origin. Some browsers also apply
        # form-action to redirect targets, so finish via an explicit GET link.
        target = html.escape(construct_redirect_uri(params['redirect_uri'], code=code, state=params['state']), quote=True)
        renewal = ('The connector will renew your TeamDynamix access automatically; you can revoke it '
                   'by disconnecting the app in ChatGPT.' if remember else
                   'No password was stored. When TeamDynamix access expires, about 24 hours from now, '
                   'ChatGPT will ask you to sign in again.')
        response = render_page("You're connected", f'''<div class="status"><div class="mark">✓</div>
<h1>You're connected</h1></div>
<p class="lead">TeamDynamix accepted your sign-in. Returning you to ChatGPT in a few seconds.</p>
<div class="actions"><a class="button" href="{target}" rel="noreferrer">Continue to ChatGPT</a></div>
<p class="small">{renewal} If nothing happens, use the button above; do not submit the login form again.
This step must finish within two minutes.</p>''', refresh=target)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='strict')
        return response

    async def load_authorization_code(self, client, authorization_code):
        with self.store.transaction() as db:
            value = self.store.get(db, 'code', authorization_code)
        if not value or value['client_id'] != client.client_id or value['expires_at'] <= time.time():
            return None
        return AuthorizationCode(code=authorization_code, **value)

    def _issue(self, db, grant, family):
        now = int(time.time())
        expires = min(now + 600, grant['upstream_expiry'])
        if expires <= now:
            raise TokenError('invalid_grant')
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        common = {**grant, 'scopes': _canonical_scopes(grant['scopes']), 'family': family}
        self.store.put(db, 'access', access, {**common, 'expires_at': expires})
        self.store.put(db, 'refresh', refresh, {**common, 'expires_at': grant['upstream_expiry'], 'used': False})
        return OAuthToken(access_token=access, refresh_token=refresh, token_type='Bearer', expires_in=expires-now, scope=' '.join(grant['scopes']))

    @contextmanager
    def _token_transaction(self):
        try:
            with self.store.transaction() as db:
                yield db
        except StateCapacityError:
            raise TokenError('invalid_grant', 'Token capacity reached; try again later.') from None

    async def exchange_authorization_code(self, client, authorization_code):
        with self._token_transaction() as db:
            value = self.store.get(db, 'code', authorization_code.code)
            if (not value or value['client_id'] != client.client_id or value['expires_at'] <= time.time()
                    or value['resource'] != self.settings.resource):
                raise TokenError('invalid_grant')
            self.store.delete(db, 'code', authorization_code.code)
            family = secrets.token_urlsafe(32)
            scopes = _canonical_scopes(value['scopes'])
            self.store.put(db, 'family', family, {
                'revoked': False, 'expires_at': value['upstream_expiry'],
                'issuer': self.settings.issuer,
                'subject': value['subject'], 'client_id': value['client_id'],
                'resource': value['resource'], 'scopes': scopes,
                'grant_expiry': value['upstream_expiry'],
            })
            return self._issue(db, {**{k: value[k] for k in (
                'client_id', 'subject', 'resource', 'upstream_expiry')}, 'scopes': scopes}, family)

    def _subject_allowed(self, subject):
        return isinstance(subject, str) and bool(subject) and (self.allowed_uid is None or subject == self.allowed_uid)

    def _valid(self, db, value):
        if (not value or value['expires_at'] <= time.time() or not self._subject_allowed(value.get('subject'))
                or value['resource'] != self.settings.resource):
            return False
        family = self.store.get(db, 'family', value['family'])
        return family and not family['revoked'] and family['expires_at'] > time.time()

    async def load_refresh_token(self, client, refresh_token):
        with self.store.transaction() as db:
            value = self.store.get(db, 'refresh', refresh_token)
            if not value or value['client_id'] != client.client_id:
                return None
            if value['used']:
                self.store.revoke_family(db, value['family'], value['upstream_expiry'])
                return None
            if not self._valid(db, value):
                return None
        return RefreshToken(token=refresh_token, **value)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        result = None
        try:
            scopes = _canonical_scopes(scopes)
        except ValueError:
            raise TokenError('invalid_grant') from None
        with self._token_transaction() as db:
            value = self.store.get(db, 'refresh', refresh_token.token)
            if value and value['client_id'] == client.client_id:
                if value['used']:
                    self.store.revoke_family(db, value['family'], value['upstream_expiry'])
                elif self._valid(db, value) and set(scopes).issubset(value['scopes']):
                    # Keep the spent token just long enough to detect replay, not for the whole grant.
                    used = {**value, 'used': True,
                            'expires_at': min(value['expires_at'], int(time.time()) + self.REFRESH_REPLAY_WINDOW)}
                    self.store.put(db, 'refresh', refresh_token.token, used)
                    result = self._issue(db, {**value, 'scopes': scopes}, value['family'])
        if result is None:
            raise TokenError('invalid_grant')
        return result

    async def load_access_token(self, token):
        with self.store.transaction() as db:
            value = self.store.get(db, 'access', token)
            if not self._valid(db, value):
                return None
        # AccessToken drops unknown model fields. Carry the server-internal family
        # only in claims; write_grant_binding still re-loads and validates the
        # access record and durable family rather than trusting this context alone.
        return AccessToken(token=token, claims={
            'iss': self.settings.issuer, 'tdx_grant_family': value['family']}, **value)

    def validate_write_grant(self, binding):
        """Return a normalized binding only when durable write authority is current."""
        denied = RuntimeError('Current write authorization is required.')
        if not isinstance(binding, Mapping) or set(binding) != GRANT_BINDING_KEYS:
            raise denied
        subject, client_id = binding.get('subject'), binding.get('client_id')
        resource, family = binding.get('resource'), binding.get('family')
        expiry, scopes = binding.get('grant_expiry'), binding.get('scopes')
        if (any(not isinstance(value, str) or not value for value in
                (subject, client_id, resource, family))
                or not self._subject_allowed(subject) or resource != self.settings.resource
                or isinstance(expiry, bool) or not isinstance(expiry, (int, float))
                or not math.isfinite(expiry) or expiry <= time.time()):
            raise denied
        try:
            scopes = _canonical_scopes(scopes)
        except (TypeError, ValueError):
            raise denied from None
        if set(scopes) != WRITE_SCOPES:
            raise denied
        with self.store.transaction() as db:
            durable = self.store.get(db, 'family', family)
        expected = {
            'subject': subject, 'client_id': client_id, 'resource': resource,
            'scopes': scopes, 'grant_expiry': expiry,
        }
        if (not durable or durable.get('revoked') is not False
                or durable.get('issuer') != self.settings.issuer
                or durable.get('expires_at') != expiry
                or durable.get('expires_at', 0) <= time.time()
                or any(durable.get(key) != value for key, value in expected.items())):
            raise denied
        return {**expected, 'family': family}

    def write_grant_binding(self, access_token):
        """Extract a write binding from a server-loaded, currently valid access token."""
        denied = RuntimeError('Current write authorization is required.')
        if not isinstance(access_token, AccessToken):
            raise denied
        claims = access_token.claims if isinstance(access_token.claims, dict) else {}
        family = claims.get('tdx_grant_family')
        if (claims.get('iss') != self.settings.issuer or not isinstance(family, str)
                or set(access_token.scopes) != WRITE_SCOPES
                or access_token.resource != self.settings.resource
                or not self._subject_allowed(access_token.subject)
                or not isinstance(access_token.client_id, str) or not access_token.client_id):
            raise denied
        with self.store.transaction() as db:
            stored = self.store.get(db, 'access', access_token.token)
            if not self._valid(db, stored):
                raise denied
        if (stored['family'] != family or stored['subject'] != access_token.subject
                or stored['client_id'] != access_token.client_id
                or stored['resource'] != access_token.resource
                or stored['scopes'] != access_token.scopes
                or stored['expires_at'] != access_token.expires_at):
            raise denied
        return self.validate_write_grant({
            'subject': stored['subject'], 'client_id': stored['client_id'],
            'resource': stored['resource'], 'family': family,
            'scopes': stored['scopes'], 'grant_expiry': stored['upstream_expiry'],
        })

    async def revoke_token(self, token):
        with self.store.transaction() as db:
            for kind in ('access', 'refresh'):
                value = self.store.get(db, kind, token.token)
                if value:
                    self.store.revoke_family(db, value['family'], value['upstream_expiry'])

    def guard(self, app):
        provider = self

        async def guarded(scope, receive, send):
            if scope['type'] != 'http' or scope['path'] not in ('/token', '/register', '/authorize', '/revoke', '/personal/login'):
                return await app(scope, receive, send)
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > 16384 or len(scope.get('query_string', b'')) > 8192:
                    return await JSONResponse({'error': 'invalid_request'}, status_code=413, headers=HEADERS)(scope, receive, send)
                if not message.get('more_body'):
                    break
            # Single-replica persistent global rate bound also survives restarts and cannot
            # be bypassed by spoofing forwarding headers. No usernames/IPs are retained.
            with provider.store.transaction() as db:
                bucket = provider.store.get(db, 'rate', scope['path']) or {'start': time.time(), 'count': 0}
                if time.time() - bucket['start'] >= 60:
                    bucket = {'start': time.time(), 'count': 0}
                bucket['count'] += 1
                provider.store.put(db, 'rate', scope['path'], bucket)
            if bucket['count'] > 30:
                return await JSONResponse({'error': 'temporarily_unavailable'}, status_code=429, headers=HEADERS)(scope, receive, send)
            if scope['path'] != '/register' and scope['method'] == 'POST':
                try:
                    form = parse_qs(body.decode('utf-8'), keep_blank_values=True, max_num_fields=30)
                    content_type = dict(scope['headers']).get(b'content-type', b'').split(b';')[0].strip().lower()
                    valid = content_type == b'application/x-www-form-urlencoded' and all(len(v) == 1 for v in form.values())
                    if scope['path'] in ('/token', '/authorize'):
                        valid = valid and form.get('resource') == [provider.settings.resource]
                except (ValueError, UnicodeError):
                    valid = False
                if not valid:
                    return await JSONResponse({'error': 'invalid_request'}, status_code=400, headers=HEADERS)(scope, receive, send)
                if scope['path'] == '/revoke' and 'client_secret' not in form:
                    # SDK 1.x requires this nullable field even for public clients.
                    # Its client authenticator still checks registered confidential clients.
                    form['client_secret'] = ['']
                    body = bytearray(urlencode(form, doseq=True).encode())
                    scope = {**scope, 'headers': [(k, v) for k, v in scope['headers'] if k != b'content-length']}
            if scope['path'] == '/authorize' and scope['method'] == 'GET':
                try:
                    query = parse_qs(scope.get('query_string', b'').decode(), keep_blank_values=True, max_num_fields=30)
                    valid = query.get('resource') == [provider.settings.resource] and all(len(v) == 1 for v in query.values())
                except (ValueError, UnicodeError):
                    valid = False
                if not valid:
                    return await JSONResponse({'error': 'invalid_request'}, status_code=400, headers=HEADERS)(scope, receive, send)
            async def replay():
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            async def private_send(message):
                if message['type'] == 'http.response.start':
                    message['headers'] = [(k, v) for k, v in message['headers'] if k.decode().lower() not in {x.lower() for x in HEADERS}]
                    message['headers'] += [(k.lower().encode(), v.encode()) for k, v in HEADERS.items()]
                await send(message)
            await app(scope, replay, private_send)
        return guarded
