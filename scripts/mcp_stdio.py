"""Bridge a local MCP stdio client to the authenticated Capzoryx read endpoint."""
import json
import os
import sys
from urllib.parse import urlsplit

import httpx


def main():
    endpoint=os.getenv('CAPZORYX_MCP_URL','')
    key=os.getenv('CAPZORYX_MCP_TOKEN','')
    uri=urlsplit(endpoint)
    loopback=uri.hostname in ('127.0.0.1','localhost','::1')
    if (not key or not uri.hostname or uri.username or uri.password or uri.fragment or uri.query or uri.path!='/api/mcp'
            or not (uri.scheme=='https' or (loopback and uri.scheme=='http'))):
        print('Configure CAPZORYX_MCP_URL and CAPZORYX_MCP_TOKEN for the intended workspace.',file=sys.stderr)
        return 2
    headers={'Authorization':'Bearer '+key,'Accept':'application/json, text/event-stream'}
    with httpx.Client(timeout=60,trust_env=False,follow_redirects=False,headers=headers) as client:
        for line in sys.stdin.buffer:
            identity=None
            try:
                payload=json.loads(line)
                if not isinstance(payload,dict):
                    raise ValueError
                identity=payload.get('id')
                response=client.post(endpoint,json=payload)
                response.raise_for_status()
                if response.status_code==202:
                    continue
                result=response.json()
            except (ValueError,httpx.HTTPError):
                if identity is None:
                    continue
                result=dict(jsonrpc='2.0',id=identity,error=dict(code=-32000,message='MCP request failed'))
            print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
