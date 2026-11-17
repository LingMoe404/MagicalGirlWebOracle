"""Optional independent HTTPS management listener; MCP transport stays unchanged."""
from contextlib import asynccontextmanager
import os

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount,Route

from ..config.settings import get_settings
from ..router.oracle import Oracle
from .app import create_admin_app


def build_app(settings=None):
    settings=settings or get_settings()
    state={}
    def oracle():
        if 'oracle' not in state:
            state['oracle']=Oracle(settings=settings)
            executor=state['oracle'].registry.credentials
            if executor is not None:
                executor.scheduler_status={'enabled':settings.balance_sync_interval>0,'running':False,'external':True,
                    'interval_seconds':settings.balance_sync_interval,'last_tick':None,'last_error':None}
        return state['oracle']
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if 'oracle' in state:
                await state.pop('oracle').aclose()
    async def health(request):
        return JSONResponse({'ok':True,'service':'oracle-admin-https'})
    return Starlette(routes=[Route('/healthz',health),Mount('/admin',create_admin_app(oracle,settings.admin_auth_token))],lifespan=lifespan)


def main():
    import uvicorn
    certificate=os.environ.get('ORACLE_ADMIN_TLS_CERT','/app/data/private/tls/cert.pem')
    key=os.environ.get('ORACLE_ADMIN_TLS_KEY','/app/data/private/tls/key.pem')
    if not os.path.isfile(certificate) or not os.path.isfile(key):
        raise ValueError('HTTPS management certificate and key files required')
    uvicorn.run(build_app(),host='0.0.0.0',port=int(os.environ.get('ORACLE_ADMIN_HTTPS_PORT','28443')),
                ssl_certfile=certificate,ssl_keyfile=key)


if __name__=='__main__':
    main()
