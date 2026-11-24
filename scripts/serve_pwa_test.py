"""Serve a production app shell and a disposable local API for the PWA browser checks."""
import os
import secrets
import socket
import sys
import tempfile
from pathlib import Path

import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def run():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    dist=root/'web/dist'
    with tempfile.TemporaryDirectory(prefix='capzoryx-pwa-') as directory, socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
        origin=f'http://127.0.0.1:{port}'
        for name in list(os.environ):
            if name.startswith(('SAFELEDGER_', 'TURSO_', 'RENDER', 'VERCEL', 'RESEND_', 'SMTP_')):
                del os.environ[name]
        os.environ.update(SAFELEDGER_DB=str(Path(directory)/'test.sqlite'),SAFELEDGER_SIGNING_KEY=secrets.token_hex(32),
                          SAFELEDGER_PUBLIC_URL=origin,SAFELEDGER_CORS_ORIGINS=origin)
        from api.app.main import app
        @app.get('/share')
        def share():
            return FileResponse(dist/'index.html')
        app.mount('/',StaticFiles(directory=dist,html=True))
        print(origin,flush=True)
        uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False)).run(sockets=[sock])


if __name__=='__main__':
    run()
