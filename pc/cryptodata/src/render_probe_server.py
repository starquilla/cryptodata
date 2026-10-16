"""One probe per process start; cached public results only, no arbitrary fetch endpoint."""
import argparse, json, os, pathlib, threading
from controlled_probe import execute_probe
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
STATE = {'status': 'IDLE', 'scope': 'No provider requests until explicit --run-once launch'}
LOCK = threading.Lock()
def run_probe():
    try:
        script = 'probe_asset_coverage.py' if os.getenv('PROBE_MODE') == 'coverage' else 'probe_cloud_sources.py'
        report = execute_probe(pathlib.Path(__file__).resolve().with_name(script),
                               os.environ['PROBE_CONTROL_ROOT'], os.environ['PROBE_RUN_ID'])
        report['environment'] = 'Controlled source availability probe'
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
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-once', action='store_true', help='Explicitly launch one probe; omit for network-idle boot')
    parser.add_argument('--mode', choices=('sample','coverage'), default='sample')
    parser.add_argument('--rounds', choices=(1,3), type=int, default=1)
    parser.add_argument('--run-id')
    parser.add_argument('--state-dir')
    parser.add_argument('--sources', nargs='+')
    args = parser.parse_args(argv)
    STATE.clear()
    STATE.update(status='IDLE', provider_requests_enabled=False,
                 commit=os.getenv('RENDER_GIT_COMMIT','unknown'),
                 scope='No provider requests until explicit --run-once launch')
    if args.run_once:
        if not args.run_id or not args.state_dir or not pathlib.Path(args.state_dir).is_absolute() or not args.sources:
            parser.error('--run-once requires --run-id, absolute --state-dir and explicit --sources')
        from probe_cloud_sources import configured_sources
        configured_sources({'PROBE_SOURCES_JSON':json.dumps(args.sources)})
        os.environ['PROBE_MODE'] = args.mode
        os.environ['PROBE_ROUNDS'] = str(args.rounds)
        os.environ['PROBE_RUN_ID'] = args.run_id
        os.environ['PROBE_CONTROL_ROOT'] = args.state_dir
        os.environ['PROBE_SOURCES_JSON'] = json.dumps(args.sources)
        STATE.update(status='RUNNING', provider_requests_enabled=True)
        threading.Thread(target=run_probe, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', int(os.getenv('PORT', '10000'))), Handler).serve_forever()

if __name__ == '__main__':
    main()
