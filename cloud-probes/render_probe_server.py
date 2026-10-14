"""One probe per process start; cached public results only, no arbitrary fetch endpoint."""
import json, os, pathlib, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
STATE = {'status': 'RUNNING', 'scope': 'BTC source availability; no scheduled collector or storage'}
LOCK = threading.Lock()
def run_probe():
    try:
        with tempfile.TemporaryDirectory() as work:
            result = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name('probe_cloud_sources.py'))], cwd=work, capture_output=True, text=True, timeout=450)
            lines = [x for x in result.stdout.splitlines() if x.startswith('CRYPTODATA_PROBE_REPORT=')]
            if not lines: raise ValueError('probe report absent')
            report = json.loads(lines[-1].split('=', 1)[1])
            report['environment'] = 'Render source availability probe'
            report['exit_code'] = result.returncode
            report['configured_region'] = os.getenv('PROBE_REGION', 'UNSPECIFIED')
            report['region_is_provider_configuration_not_geolocation_proof'] = True
        with LOCK:
            STATE.clear(); STATE.update(report)
        print('CRYPTODATA_PROBE_REPORT=' + json.dumps(report), flush=True)
    except Exception as exc:
        with LOCK:
            STATE.clear(); STATE.update(status='ERROR', error_type=type(exc).__name__)
        print('Probe failed: ' + type(exc).__name__, flush=True)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/report', '/health'):
            self.send_error(404); return
        with LOCK:
            payload = {'service': 'cryptodata-source-probe', 'ready': True} if self.path == '/health' else dict(STATE)
        body = json.dumps(payload).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
if __name__ == '__main__':
    threading.Thread(target=run_probe, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '10000'))), Handler).serve_forever()