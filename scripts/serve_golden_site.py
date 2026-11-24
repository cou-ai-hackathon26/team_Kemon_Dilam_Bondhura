"""Serve an isolated ephemeral FastAPI + compiled SPA for the browser golden test."""
import base64
import json
import os
from pathlib import Path
from decimal import Decimal
import secrets
import socket
import sys
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="capzoryx-synthetic-") as temp, socket.socket() as sock:
        sock.bind(("127.0.0.1",0))
        origin=f"http://127.0.0.1:{sock.getsockname()[1]}"
        # The test never connects to a hosted database or sends messages.
        for key in list(os.environ):
            if key.startswith(("SAFELEDGER_","CAPZORYX_HISAB_","TURSO_","VERCEL","RENDER","RESEND_","SMTP_")):
                del os.environ[key]
        key=Ed25519PrivateKey.generate()
        b64=lambda b:base64.b64encode(b).decode()
        os.environ.update(SAFELEDGER_PUBLIC_URL=origin,SAFELEDGER_CORS_ORIGINS=origin,
                          SAFELEDGER_DB=str(Path(temp)/"test.sqlite"),SAFELEDGER_SIGNING_KEY=secrets.token_hex(32),
                          SAFELEDGER_PASSPORT_ACTIVE_KEY="test",SAFELEDGER_PASSPORT_KEYS=json.dumps({"test":b64(key.private_bytes_raw())}))
        from api.app.main import app
        from api.app import fx, passport
        def fixture_rates():
            return {"BDT":Decimal(120),"USD":Decimal(1)},"Synthetic test rate",None,False
        fx.load_rates=fixture_rates
        keys=Path(temp)/"public.json";keys.write_text(json.dumps({"test":b64(key.public_key().public_bytes_raw())}))
        passport.KEY_FILE=keys
        dist=ROOT/"web/dist"
        if not (dist/"index.html").exists():raise SystemExit("Compile the web application before running browser checks")
        @app.get('/.well-known/capzoryx-passport-key.json')
        def public_key():return FileResponse(keys)
        @app.get('/verify/{pid}')
        def passport_page(pid:str):return FileResponse(dist/"index.html")
        @app.get('/demo')
        def competition_page():return FileResponse(dist/"index.html")
        config_path=ROOT/"web/vercel.json"
        if not config_path.exists():config_path=ROOT/"vercel.json"
        headers=json.loads(config_path.read_text())["headers"]
        @app.middleware("http")
        async def hosting_headers(request,call_next):
            response=await call_next(request)
            if not request.url.path.startswith('/api/'):
                for rule in headers:
                    if rule["source"] in ("/(.*)",request.url.path):
                        for header in rule["headers"]:response.headers[header["key"]]=header["value"]
            return response
        app.mount("/",StaticFiles(directory=dist,html=True))
        # stdout contains one machine-readable line. Log startup/errors on stderr.
        print(origin,flush=True)
        uvicorn.Server(uvicorn.Config(app,access_log=False,log_level="warning")).run(sockets=[sock])


if __name__=="__main__":
    main()
