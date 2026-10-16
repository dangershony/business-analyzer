"""
Business Analyzer — local web server (stdlib only).

Run:  python3 server.py          (default port 8765)
Then open http://localhost:8765

Routes:
  /                    static frontend (docs/)
  /api/health          backend capability check
  /api/analyze  POST   {name, location, notes?} -> full analysis JSON (also saved to docs/data/)
  /api/analyses        list saved analyses
"""

import json
import os
import re
import sys
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import analyzer

PORT = int(os.environ.get('BA_PORT', '8765'))
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, 'docs')
DATA_DIR = os.path.join(DOCS, 'data')

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.ico': 'image/x-icon',
}


def slugify(s):
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:60] or 'business'


def save_analysis(analysis):
    """Persist an analysis JSON + update the manifest, so static deploys
    (GitHub Pages) can show it too."""
    os.makedirs(DATA_DIR, exist_ok=True)
    slug = slugify(f"{analysis.get('business_name', 'business')}-{analysis.get('location', '').split(',')[0]}")
    analysis['_meta']['slug'] = slug
    path = os.path.join(DATA_DIR, f'{slug}.json')
    with open(path, 'w') as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)
    update_manifest()
    return slug, path


def update_manifest():
    entries = []
    if os.path.isdir(DATA_DIR):
        for fn in sorted(os.listdir(DATA_DIR)):
            if not fn.endswith('.json') or fn == 'index.json':
                continue
            try:
                with open(os.path.join(DATA_DIR, fn)) as f:
                    d = json.load(f)
                meta = d.get('_meta', {})
                entries.append({
                    'slug': meta.get('slug', fn[:-5]),
                    'file': f'data/{fn}',
                    'business_name': d.get('business_name', fn[:-5]),
                    'location': d.get('location', ''),
                    'generated_at': meta.get('generated_at'),
                    'one_liner': (d.get('what_it_does', '') or '')[:140],
                })
            except (OSError, ValueError):
                continue
    entries.sort(key=lambda e: e.get('generated_at') or '', reverse=True)
    with open(os.path.join(DATA_DIR, 'index.json'), 'w') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    server_version = 'BusinessAnalyzer/1.0'

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    # -------------------------------------------------- helpers
    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        if path in ('/', '/index.html'):
            path = '/index.html'
        local = os.path.normpath(os.path.join(DOCS, path.lstrip('/')))
        if not local.startswith(DOCS) or not os.path.isfile(local):
            self._send(404, {'error': 'not found'})
            return
        ext = os.path.splitext(local)[1].lower()
        with open(local, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', MIME.get(ext, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -------------------------------------------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/health':
            llm = True
            try:
                analyzer.load_llm_credential()
            except analyzer.PipelineError:
                llm = False
            self._send(200, {'ok': True, 'llm_available': llm,
                             'model': analyzer.LLM_MODEL, 'mode': 'server'})
        elif parsed.path == '/api/analyses':
            update_manifest()
            manifest_path = os.path.join(DATA_DIR, 'index.json')
            if os.path.isfile(manifest_path):
                with open(manifest_path, 'rb') as f:
                    self._send(200, f.read(), 'application/json; charset=utf-8')
            else:
                self._send(200, [])
        else:
            self._static(parsed.path)

    # -------------------------------------------------- POST
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/api/analyze':
            self._send(404, {'error': 'not found'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            req = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self._send(400, {'error': 'invalid JSON body'})
            return
        name = (req.get('name') or '').strip()
        location = (req.get('location') or '').strip()
        notes = (req.get('notes') or '').strip()
        if not name or not location:
            self._send(400, {'error': 'Both "name" and "location" are required.'})
            return
        if len(name) > 100 or len(location) > 120 or len(notes) > 2000:
            self._send(400, {'error': 'Input too long.'})
            return

        started = time.time()
        try:
            analysis = analyzer.run_analysis(name, location, extra_notes=notes)
        except analyzer.PipelineError as e:
            self._send(502, {'error': str(e)})
            return
        except Exception as e:  # noqa: BLE001
            self._send(500, {'error': f'Analysis failed: {type(e).__name__}: {e}'})
            return
        try:
            slug, _ = save_analysis(analysis)
        except OSError as e:
            slug = None
            analysis['_meta']['save_error'] = str(e)
        analysis['_meta']['slug'] = slug
        analysis['_meta']['elapsed_seconds'] = round(time.time() - started, 1)
        self._send(200, analysis)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(os.path.join(DATA_DIR, 'index.json')):
        update_manifest()
    httpd = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Business Analyzer running: http://localhost:{PORT}')
    print(f'Static root: {DOCS}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nbye')


if __name__ == '__main__':
    main()
