"""One probe per process start; cached public results only, no arbitrary fetch endpoint."""
import argparse, json, os, pathlib, subprocess, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
STATE = {'status': 'IDLE', 'scope': 'No provider requests until explicit --run-once launch'}
LOCK = threading.Lock()
def run_probe():
    try:
        with tempfile.TemporaryDirectory() as work:
            script = 'probe_asset_coverage.py' if os.getenv('PROBE_MODE') == 'coverage' else 'probe_cloud_sources.py'
            process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve().with_name(script))], cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            watchdog=threading.Timer(1600,process.kill);watchdog.daemon=True;watchdog.start()
            lines=[]
            for line in process.stdout:
                if line.startswith('CRYPTODATA_PROGRESS='):
                    with LOCK: STATE.update(progress=json.loads(line.split('=',1)[1]))
                if line.startswith('CRYPTODATA_PROBE_REPORT='): lines.append(line.strip())
            process.wait(timeout=30)
            watchdog.cancel()
            if not lines: raise ValueError('probe report absent')
            report = json.loads(lines[-1].split('=', 1)[1])
            report['environment'] = 'Render source availability probe'
            report['exit_code'] = process.returncode
            report['configured_region'] = os.getenv('PROBE_REGION', 'UNSPECIFIED')
            report['region_is_provider_configuration_not_geolocation_proof'] = True
            report['cooldown_persistence'] = 'local file within this probe; export cooldowns into Render env before any restart; production external persistence NOT_IMPLEMENTED'
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
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-once', action='store_true', help='Explicitly launch one probe; omit for network-idle boot')
    parser.add_argument('--mode', choices=('sample','coverage'), default='sample')
    parser.add_argument('--rounds', choices=(1,3), type=int, default=1)
    args = parser.parse_args(argv)
    STATE.clear()
    STATE.update(status='IDLE', provider_requests_enabled=False,
                 commit=os.getenv('RENDER_GIT_COMMIT','unknown'),
                 scope='No provider requests until explicit --run-once launch')
    if args.run_once:
        os.environ['PROBE_MODE'] = args.mode
        os.environ['PROBE_ROUNDS'] = str(args.rounds)
        STATE.update(status='RUNNING', provider_requests_enabled=True)
        threading.Thread(target=run_probe, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '10000'))), Handler).serve_forever()

if __name__ == '__main__':
    main()
