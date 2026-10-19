"""Authenticated Fabric transport. Signer selection is configuration, not input."""
import json
import os
import urllib.error
import urllib.request


class LedgerError(Exception):
    def __init__(self,message,status_code=400):
        super().__init__(message);self.status_code=status_code


class FabricLedger:
    def __init__(self, identity):
        self.identity = identity
        routes = json.loads(os.environ.get('SIGNER_URLS', '{}'))
        if identity.org not in routes:
            raise LedgerError('no signer configured for organisation',503)
        self.url = routes[identity.org].rstrip('/')
        if not self.url.startswith('https://'):
            raise LedgerError('ledger signer requires HTTPS',503)

    def call(self, function, *args, query=False):
        data=json.dumps({'function':function,'args':[str(a) for a in args]},allow_nan=False).encode()
        request=urllib.request.Request(self.url+('/query' if query else '/invoke'),data=data,
            headers={'Content-Type':'application/json','X-API-Key':self.identity.api_key})
        try:
            with urllib.request.urlopen(request,timeout=65) as response: result=json.load(response)
        except urllib.error.HTTPError as exc:
            try: detail=json.loads(exc.read()).get('error','ledger request rejected')
            except ValueError: detail='ledger request rejected'
            raise LedgerError(detail,503 if exc.code>=500 else exc.code) from exc
        except (urllib.error.URLError,OSError) as exc:
            raise LedgerError('ledger signer unavailable',503) from exc
        if not result.get('ok'): raise LedgerError(result.get('error','ledger request failed'))
        return result.get('result')
