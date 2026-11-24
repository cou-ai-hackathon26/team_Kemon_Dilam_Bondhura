import {createReadStream} from 'node:fs';
import {readFile, stat} from 'node:fs/promises';
import {createServer} from 'node:http';
import {request as httpsRequest} from 'node:https';
import {extname} from 'node:path';
import {pipeline} from 'node:stream/promises';

const config = JSON.parse(await readFile(new URL('../vercel.json', import.meta.url), 'utf8'));
const api = new URL(config.rewrites.find(rule => rule.source === '/api/:path*').destination);
const files = new URL('../dist/', import.meta.url);
const index = new URL('index.html', files);
await stat(index);
const rules = config.headers.map(({source, headers}) => [new RegExp('^' + source + '$'), headers]);
const mime = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.webmanifest': 'application/manifest+json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.wasm': 'application/wasm',
};

const hopHeaders = ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
  'te', 'trailer', 'transfer-encoding', 'upgrade'];

function forwarded(headers) {
  const copy = {...headers};
  const named = String(headers.connection || '').split(',').map(name => name.trim().toLowerCase());
  for (const name of [...hopHeaders, ...named]) delete copy[name];
  return copy;
}

function relay(req, res, path) {
  const headers = forwarded(req.headers);
  headers.host = api.host;
  const upstream = httpsRequest({hostname: api.hostname, path, method: req.method, headers}, reply => {
    res.writeHead(reply.statusCode || 502, forwarded(reply.headers));
    void pipeline(reply, res).catch(() => {});
  });
  upstream.setTimeout(100_000, () => upstream.destroy());
  upstream.on('error', () => {
    if (res.headersSent) res.destroy();
    else {
      res.writeHead(502, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'});
      res.end(JSON.stringify({error:{code:'API_UNAVAILABLE'}}));
    }
  });
  req.on('aborted', () => upstream.destroy());
  req.pipe(upstream);
}

async function serve(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const path = url.pathname;
  if (path === '/api' || path.startsWith('/api/')) {
    relay(req, res, path + url.search);
    return;
  }
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, {Allow: 'GET, HEAD'}).end();
    return;
  }
  for (const [match, headers] of rules) {
    if (match.test(path)) for (const {key, value} of headers) res.setHeader(key, value);
  }
  const asset = new URL('.' + path, files);
  if (!asset.href.startsWith(files.href)) {
    res.writeHead(404).end();
    return;
  }
  const info = await stat(asset).catch(() => null);
  const missingAsset = !info?.isFile() && extname(path);
  if (missingAsset) {
    res.writeHead(404).end();
    return;
  }
  const file = info?.isFile() ? asset : index;
  res.setHeader('Content-Type', mime[extname(file.pathname)] || 'application/octet-stream');
  if (!res.hasHeader('Cache-Control')) res.setHeader('Cache-Control', 'no-cache');
  if (req.method === 'HEAD') res.end();
  else await pipeline(createReadStream(file), res);
}

createServer((req, res) => {
  void serve(req, res).catch(() => {
    if (res.headersSent) res.destroy();
    else res.writeHead(500).end();
  });
}).listen(Number(process.env.PORT || 10000), '0.0.0.0');
