"""Loopback HTTP adapter for synthetic integration tests; CLI remains offline.

Never enable it against an SSM forwarded port without separate delivery approval.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .collector import BeforeDelivery, PermanentRejection, UncertainDelivery


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalHttpSender:
    def __init__(self, port, timeout=5):
        if type(port) is not int or not 1 <= port <= 65535 or timeout <= 0:
            raise ValueError('Invalid loopback port or timeout')
        self.base = f'http://127.0.0.1:{port}'
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirects())

    def request(self, path, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        req = Request(self.base + path, data=body, headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                return json.loads(response.read(65536))
        except HTTPError as exc:
            # No request or response bodies are included in diagnostic exceptions.
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise PermanentRejection(f'HTTP {exc.code}') from None
            if 300 <= exc.code < 400:
                raise PermanentRejection('Redirect refused') from None
            raise UncertainDelivery(f'HTTP {exc.code}') from None
        except URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError):
                raise BeforeDelivery() from None
            raise UncertainDelivery() from None
        except (ValueError, TimeoutError, ConnectionError, OSError):
            raise UncertainDelivery() from None

    def idempotency_ready(self):
        response = self.request('/ready')
        return response.get('status') == 'ready' and response.get('capabilities', {}).get('source_event_id') == 'v1'

    def __call__(self, payload):
        return self.request('/events', payload)
