"""Small, bounded LAN coordinator. No arbitrary shell/API execution adapters.

SQLite belongs to ONE coordinator host. Workers only use pinned HTTPS.
At-least-once read attempts; uncertain write attempts require human reconciliation.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import getpass
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import socket
import sqlite3
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from network import allowed, read_config, resolve_ipv4, discover, Discovery, storage_guard, StoragePaused
from workspace_storage import workspace_storage_guard

# Windows redirected consoles can default to cp1252 although project paths contain Vietnamese.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='backslashreplace')

ROOT = Path(__file__).resolve().parent
MAX_BODY = 65536
ACTIVE = ("running", "needs_review")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + secrets.token_hex(4) + ".tmp")
    with temp.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


class Rejected(Exception):
    pass


class Store:
    def __init__(self, path, clock=time.time, registry=None):
        self.path = str(path)
        self.clock = clock
        self.registry = registry or json.loads((ROOT / "registry.json").read_text(encoding="utf-8"))["tools"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL,
              tool TEXT NOT NULL, payload TEXT NOT NULL, target TEXT, resource TEXT NOT NULL,
              dependency TEXT, effect TEXT NOT NULL, state TEXT NOT NULL,
              attempt INTEGER NOT NULL DEFAULT 0, fence INTEGER NOT NULL DEFAULT 0,
              owner TEXT, lease_until REAL, not_before REAL NOT NULL, max_attempts INTEGER NOT NULL,
              result TEXT, result_hash TEXT, reason TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
              job TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workers(
              name TEXT PRIMARY KEY, token_hash TEXT NOT NULL, tools TEXT NOT NULL, last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS enrollment(
              name TEXT PRIMARY KEY, code_hash TEXT NOT NULL, tools TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS cooldowns(resource TEXT PRIMARY KEY, until REAL NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS claim_requests(
                worker TEXT NOT NULL, request_id TEXT NOT NULL, job_id TEXT NOT NULL,
                fence INTEGER NOT NULL, created REAL NOT NULL,
                PRIMARY KEY(worker,request_id), FOREIGN KEY(job_id) REFERENCES jobs(id));
            """)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, job, kind, detail):
        db.execute("INSERT INTO events(at,job,kind,detail) VALUES(?,?,?,?)", (self.clock(), job, kind, canonical(detail)))

    def enqueue(self, idem, tool, payload, target=None, resource=None, dependency=None,
                not_before=0, max_attempts=3):
        spec = self.registry.get(tool, {})
        if not spec.get("enabled"):
            raise Rejected("tool disabled or unknown")
        validate_payload(tool, payload)
        if tool == 'ai_text':
            max_attempts = 1  # A model call consumes quota; never automatically invoke it again.
        if not idem or len(idem) > 200 or not 1 <= max_attempts <= 10:
            raise Rejected("invalid idempotency key or attempts")
        request = dict(tool=tool, payload=payload, target=target, resource=resource or tool,
                       dependency=dependency, not_before=not_before, max_attempts=max_attempts)
        rh = digest(request)
        with self.db() as db:
            old = db.execute("SELECT id,request_hash FROM jobs WHERE idem=?", (idem,)).fetchone()
            if old:
                if old["request_hash"] != rh:
                    raise Rejected("idempotency key reused with a different request")
                return old["id"]
            if dependency and not db.execute("SELECT 1 FROM jobs WHERE id=?", (dependency,)).fetchone():
                raise Rejected("missing dependency")
            job_id = secrets.token_hex(12)
            db.execute("""INSERT INTO jobs(id,idem,request_hash,tool,payload,target,resource,dependency,
                       effect,state,not_before,max_attempts,created) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)""",
                       (job_id, idem, rh, tool, canonical(payload), target, resource or tool, dependency,
                        spec["effect"], not_before, max_attempts, self.clock()))
            self.event(db, job_id, "enqueued", request)
            return job_id

    def prepare_enrollment(self, name, tools, ttl=600):
        if not name or len(name) > 80 or not tools:
            raise Rejected("invalid worker")
        if any(not self.registry.get(t, {}).get("enabled") for t in tools):
            raise Rejected("cannot enroll a disabled tool")
        code = secrets.token_urlsafe(24)
        with self.db() as db:
            if db.execute("SELECT 1 FROM workers WHERE name=?", (name,)).fetchone():
                raise Rejected("worker already enrolled; identity rotation requires explicit administration")
            db.execute("INSERT OR REPLACE INTO enrollment VALUES(?,?,?,?)",
                       (name, digest(code), canonical(tools), self.clock() + ttl))
            self.event(db, None, "enrollment_prepared", {"worker": name, "tools": tools})
        return code

    def enroll(self, name, code):
        with self.db() as db:
            row = db.execute("SELECT * FROM enrollment WHERE name=?", (name,)).fetchone()
            if not row or row["expires"] <= self.clock() or not hmac.compare_digest(row["code_hash"], digest(code)):
                raise Rejected("invalid or expired enrollment")
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO workers VALUES(?,?,?,?)", (name, digest(token), row["tools"], self.clock()))
            db.execute("DELETE FROM enrollment WHERE name=?", (name,))
            self.event(db, None, "worker_enrolled", {"worker": name})
        return token

    def authenticate(self, name, token):
        with self.db() as db:
            row = db.execute("SELECT * FROM workers WHERE name=?", (name,)).fetchone()
            if not row or not hmac.compare_digest(row["token_hash"], digest(token)):
                raise Rejected("unauthorized")
            db.execute("UPDATE workers SET last_seen=? WHERE name=?", (self.clock(), name))
            return json.loads(row["tools"])

    def expire(self, db):
        for job in db.execute("SELECT * FROM jobs WHERE state='running' AND lease_until<=?", (self.clock(),)).fetchall():
            # Lease expiry DOES NOT prove the old process stopped. Only pure reads may retry.
            state = "needs_review" if job["effect"] != "read" else (
                "queued" if job["attempt"] < job["max_attempts"] else "failed")
            db.execute("UPDATE jobs SET state=?,fence=fence+1,owner=NULL,lease_until=NULL,reason=? WHERE id=?",
                       (state, "lease_expired", job["id"]))
            self.event(db, job["id"], "lease_expired", {"state": state, "old_fence": job["fence"]})

    def claim(self, worker, tools, lease=30, request_id=None):
        if not 2 <= lease <= 300:
            raise Rejected("invalid lease")
        if request_id is not None and (not isinstance(request_id,str) or len(request_id)!=32 or any(x not in '0123456789abcdef' for x in request_id)):
            raise Rejected('invalid claim request id')
        with self.db() as db:
            if request_id is not None:
                prior=db.execute('SELECT job_id,fence FROM claim_requests WHERE worker=? AND request_id=?',(worker,request_id)).fetchone()
                if prior:
                    job=self.owned(db,worker,prior['job_id'],prior['fence'])
                    if job['tool'] not in tools or not self.registry.get(job['tool'],{}).get('enabled'):
                        raise Rejected('claim replay capability disabled')
                    db.execute('UPDATE jobs SET lease_until=? WHERE id=?',(self.clock()+lease,job['id']))
                    self.event(db,job['id'],'claim_replayed',{'worker':worker,'fence':job['fence'],'request_id':request_id})
                    current=dict(db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
                    current['payload']=json.loads(current['payload'])
                    return current
            self.expire(db)
            rows = db.execute("""SELECT * FROM jobs j WHERE state='queued' AND not_before<=?
                AND (target IS NULL OR target=?)
                AND (dependency IS NULL OR EXISTS(SELECT 1 FROM jobs d WHERE d.id=j.dependency AND d.state='succeeded'))
                AND NOT EXISTS(SELECT 1 FROM jobs a WHERE a.resource=j.resource AND a.state IN ('running','needs_review'))
                AND NOT EXISTS(SELECT 1 FROM cooldowns c WHERE c.resource=j.resource AND c.until>?)
                ORDER BY created,id""", (self.clock(), worker, self.clock())).fetchall()
            for job in rows:
                if job["tool"] not in tools or not self.registry.get(job["tool"], {}).get("enabled"):
                    continue
                db.execute("""UPDATE jobs SET state='running',attempt=attempt+1,fence=fence+1,
                           owner=?,lease_until=?,reason=NULL WHERE id=?""", (worker, self.clock()+lease, job["id"]))
                current = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
                self.event(db, job["id"], "claimed", {"worker": worker, "fence": current["fence"]})
                if request_id is not None:
                    db.execute('INSERT INTO claim_requests(worker,request_id,job_id,fence,created) VALUES(?,?,?,?,?)',
                               (worker,request_id,current['id'],current['fence'],self.clock()))
                current["payload"] = json.loads(current["payload"])
                return current
            return None

    def owned(self, db, worker, job_id, fence):
        job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job or job["state"] != "running" or job["owner"] != worker or job["fence"] != fence or job["lease_until"] <= self.clock():
            raise Rejected("stale lease or owner; result requires reconciliation")
        return job

    def heartbeat(self, worker, job_id, fence, lease=30):
        with self.db() as db:
            self.owned(db, worker, job_id, fence)
            db.execute("UPDATE jobs SET lease_until=? WHERE id=?", (self.clock()+lease, job_id))

    def complete(self, worker, job_id, fence, result, success=True):
        if len(canonical(result).encode()) > 32000:
            raise Rejected("result too large")
        with self.db() as db:
            job = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            # ACK can be retried after a lost response, but never with altered content/owner/fence.
            if job and job["state"] in ("succeeded", "failed", "needs_review") and job["owner"] == worker and job["fence"] == fence:
                expected = ('needs_review' if not success and job['effect'] != 'read' and isinstance(result,dict) and result.get('requires_review') is True
                            else ('succeeded' if success else 'failed'))
                if job["state"] == expected and job["result_hash"] == digest(result):
                    return {"state": job["state"], "duplicate_ack": True}
                raise Rejected("conflicting completion")
            self.owned(db, worker, job_id, fence)
            state = ('needs_review' if not success and job['effect'] != 'read' and isinstance(result,dict) and result.get('requires_review') is True
                     else ('succeeded' if success else 'failed'))
            db.execute("UPDATE jobs SET state=?,result=?,result_hash=?,lease_until=NULL WHERE id=?",
                       (state, canonical(result), digest(result), job_id))
            self.event(db, job_id, "completed", {"state": state, "result_hash": digest(result), "worker": worker})
            return {"state": state, "duplicate_ack": False}

    def cooldown(self, resource, until, reason):
        with self.db() as db:
            db.execute("""INSERT INTO cooldowns VALUES(?,?,?) ON CONFLICT(resource) DO UPDATE SET
                until=max(cooldowns.until,excluded.until),reason=excluded.reason""", (resource, until, reason))
            self.event(db, None, "cooldown", {"resource": resource, "until": until, "reason": reason})

    def reconcile(self, job_id, outcome, evidence):
        if outcome not in ("succeeded", "failed") or not evidence.strip():
            raise Rejected("terminal outcome and evidence required")
        with self.db() as db:
            self.expire(db)
            row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["state"] != "needs_review":
                raise Rejected("only uncertain jobs can be reconciled")
            db.execute("UPDATE jobs SET state=?,reason=? WHERE id=?", (outcome, evidence, job_id))
            self.event(db, job_id, "human_reconciled", {"outcome": outcome, "evidence": evidence})

    def snapshot(self):
        with self.db() as db:
            self.expire(db)
            return {"checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "jobs": [dict(x) for x in db.execute("SELECT * FROM jobs ORDER BY created,id")],
                    "workers": [dict(x) for x in db.execute("SELECT name,tools,last_seen FROM workers")],
                    "cooldowns": [dict(x) for x in db.execute("SELECT * FROM cooldowns")],
                    "events": [dict(x) for x in db.execute("SELECT * FROM events ORDER BY seq")]}


def validate_payload(tool, payload):
    if not isinstance(payload, dict):
        raise Rejected("payload must be an object")
    if tool in ("hash_text", "text_stats"):
        if set(payload) != {"text"} or not isinstance(payload["text"], str) or len(payload["text"].encode()) > 16000:
            raise Rejected("expected bounded text only")
    elif tool == "inventory" and payload:
        raise Rejected("inventory takes no arguments")
    elif tool == 'ai_text':
        from ai_runtime import validate
        validate(payload)


def run_tool(tool, payload, job_id=None, cancelled=None):
    validate_payload(tool, payload)
    if tool == "hash_text":
        return {"sha256": hashlib.sha256(payload["text"].encode()).hexdigest(), "bytes": len(payload["text"].encode())}
    if tool == "text_stats":
        return {"words": len(payload["text"].split()), "characters": len(payload["text"])}
    if tool == "inventory":
        from ai_runtime import locate_executables
        paths = locate_executables()
        on_path = {name: shutil.which(name) for name in ('codex','gemini','git','node','python')}
        dependencies = Path(os.environ.get('USERPROFILE',''))/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
        git = dependencies/'native/git/cmd/git.exe'
        return {"hostname": socket.gethostname(), "os": platform.platform(), "python": sys.version.split()[0],
                "executables": {'codex':paths['codex'],'node':paths['node'],'gemini':paths['gemini'],
                                'git':on_path['git'] or (str(git) if git.is_file() else None),'python':sys.executable},
                'discovered_on_path':on_path,'login_or_ai_execution_verified':False}
    if tool == 'ai_text':
        if payload['provider']=='antigravity':
            from antigravity_adapter import AntigravityRuntime
            return AntigravityRuntime().run(job_id,payload,cancelled)
        from ai_runtime import Runtime
        return Runtime().run(job_id,payload,cancelled)
    raise Rejected("no executable adapter")


def ensure_certificate(state):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    cert_path, key_path = state / "server.pem", state / "server.key"
    if not cert_path.exists() or not key_path.exists():
        if cert_path.exists() or key_path.exists():
            raise Rejected("partial TLS identity; do not silently rotate certificate")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI LAN Coordinator")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5))
                .not_valid_after(now+dt.timedelta(days=365)).sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert_der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding='utf-8-sig'))
    return cert_path, key_path, hashlib.sha256(cert_der).hexdigest()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def verify_request(self, request, client_address):
        return allowed(client_address[0], self.allowed_peers)

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(10)
        if not allowed(addr[0], self.allowed_peers):
            sock.close()
            raise ConnectionAbortedError('Source outside configured LAN')
        # Never perform a blocking TLS handshake on the accept-loop thread.
        return sock, addr

    def process_request_thread(self, request, client_address):
        try:
            secured = self.tls.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            request.close()
            return
        super().process_request_thread(secured, client_address)


def make_server(store, state, host="127.0.0.1", port=8840, allowed_peers=None):
    cert, key, pin = ensure_certificate(state)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            packages={'/boot/v1/package.zip':'boot-runtime-v1.zip','/boot/lease-fix-v1/package.zip':'lease-fix-v1.zip','/boot/lease-fix-v2/package.zip':'lease-fix-v2.zip'}
            if self.path in packages:
                package = ROOT / 'public-bootstrap' / packages[self.path]
                if not package.is_file() or package.stat().st_size > 4_000_000:
                    return self.respond(404, {'error': 'bootstrap unavailable'})
                body = package.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == '/health':
                return self.respond(200, {'service': 'ai-lan-coordinator-v02', 'hostname': socket.gethostname(), 'version': '0.3'})
            return self.respond(404, {'error': 'unknown route'})

        def log_message(self, *_):
            pass  # Never log headers, enrollment codes or tokens.

        def respond(self, code, value):
            body = canonical(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            received_at=dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= MAX_BODY:
                    raise Rejected("invalid request size")
                req = json.loads(self.rfile.read(n))
                if self.path == '/probe':
                    return self.respond(200, {'service': 'ai-lan-coordinator-v02', 'hostname': socket.gethostname(), 'version': '0.3'})
                if self.path == "/enroll":
                    token = store.enroll(req["name"], req["code"])
                    return self.respond(200, {"token": token})
                worker = self.headers.get("X-Worker", "")
                auth = self.headers.get("Authorization", "")
                tools = store.authenticate(worker, auth.removeprefix("Bearer "))
                if self.path == '/runtime-status':
                    from runtime_telemetry import validate_status
                    status = validate_status(worker, req)
                    with store.db() as db:
                        store.event(db, None, 'runtime_status', {'worker': worker, 'peer': self.client_address[0], 'status': status})
                    result = {'accepted': True}
                elif self.path == "/claim":
                    result = {"job": store.claim(worker, tools,request_id=req.get('request_id'))}
                elif self.path == "/heartbeat":
                    store.heartbeat(worker, req["id"], req["fence"])
                    result = {"ok": True}
                elif self.path == "/complete":
                    result = store.complete(worker, req["id"], req["fence"], req["result"], req.get("success", True))
                else:
                    return self.respond(404, {"error": "unknown route"})
                claimed = result.get('job') if self.path == '/claim' else None
                if claimed:
                    print(canonical({'at':dt.datetime.now(dt.timezone.utc).isoformat(),
                          'event':'claim_response','phase':'selected','job':claimed['id'],
                          'fence':claimed['fence'],'request_id':req.get('request_id'),'request_received_at':received_at}),file=sys.stderr,flush=True)
                self.respond(200, result)
                if claimed:
                    print(canonical({'at':dt.datetime.now(dt.timezone.utc).isoformat(),
                          'event':'claim_response','phase':'written','job':claimed['id'],
                          'fence':claimed['fence'],'request_id':req.get('request_id'),'request_received_at':received_at}),file=sys.stderr,flush=True)
            except Rejected as exc:
                self.respond(409, {"error": str(exc)})
            except (ValueError, KeyError, TypeError):
                self.respond(400, {"error": "invalid request"})
            except Exception:
                self.respond(500, {"error": "internal failure; inspect coordinator locally"})

    server = Server((host, port), Handler)
    server.allowed_peers = set(allowed_peers or ["127.0.0.1"])
    server.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.tls.minimum_version = ssl.TLSVersion.TLSv1_2
    server.tls.load_cert_chain(cert, key)
    return server, pin


class Client:
    def __init__(self, url, pin, identity=None, config=None):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in ("", "/") or parsed.username or parsed.query or parsed.fragment:
            raise Rejected("expected HTTPS origin only")
        if len(pin) != 64 or any(c not in "0123456789abcdef" for c in pin.lower()):
            raise Rejected("invalid SHA256 certificate pin")
        self.host, self.port, self.pin = parsed.hostname, parsed.port or 443, pin.lower()
        self.identity = identity
        self.config = config
        self.last_endpoint = None

    def candidates(self):
        if self.last_endpoint:
            yield self.last_endpoint
        if self.config is None:
            yield (self.host, self.port)
            return
        networks = self.config['allowed_networks']
        for address in resolve_ipv4(self.host):
            if allowed(address, networks):
                yield (address, self.port)
        for address in self.config.get('bootstrap_addresses', []):
            if allowed(address, networks):
                yield (address, self.port)
        yield from discover(self.config, self.pin)

    def post(self, path, value):
        # TOFU is not used: compare trusted pin BEFORE transmitting authentication or body.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        conn = None
        tried = set()
        mismatched = False
        for host, port in self.candidates():
            if (host, port) in tried:
                continue
            tried.add((host, port))
            attempt = http.client.HTTPSConnection(host, port, context=context, timeout=2)
            try:
                attempt.connect()
                got = hashlib.sha256(attempt.sock.getpeercert(binary_form=True)).hexdigest()
                if not hmac.compare_digest(self.pin, got):
                    mismatched = True
                    attempt.close()
                    continue
                conn = attempt
                self.last_endpoint = (host, port)
                break
            except (OSError, http.client.HTTPException):
                attempt.close()
        if conn is None:
            if mismatched:
                raise Rejected('certificate pin mismatch; no credentials sent')
            raise OSError('No authenticated coordinator endpoint available; verify hostname/discovery/subnet/listener')
        try:
            headers = {"Content-Type": "application/json"}
            if self.identity:
                headers.update({"X-Worker": self.identity["name"], "Authorization": "Bearer " + self.identity["token"]})
            conn.request("POST", path, canonical(value).encode(), headers)
            res = conn.getresponse()
            value = json.loads(res.read(MAX_BODY + 1))
            if res.status != 200:
                raise Rejected(value.get("error", "HTTP error"))
            return value
        finally:
            conn.close()


def execute_once(client, outbox, before_claim=None, heartbeat_interval=5):
    if not 0 < heartbeat_interval < 30:
        raise ValueError('heartbeat interval must be positive and below the 30-second lease')
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    # Persist completion BEFORE sending it; a lost ACK can be replayed without rerunning tool.
    for pending in sorted(outbox.glob("*.pending.json")):
        item = json.loads(pending.read_text(encoding="utf-8"))
        if item.get("worker") != client.identity["name"]:
            raise Rejected("outbox identity mismatch")
        try:
            client.post("/complete", item["completion"])
        except Rejected as exc:
            write_json(pending.with_suffix(".review.json"), {"pending": str(pending), "reason": str(exc)})
            raise Rejected("stale outbox completion; manual review required") from exc
        pending.rename(pending.with_suffix(".acked"))
    if before_claim:
        before_claim()
    intent_path=outbox/'.claim-request.json'
    if intent_path.exists():
        intent=json.loads(intent_path.read_text(encoding='utf-8'))
        rid=intent.get('request_id')
        if intent.get('worker')!=client.identity['name'] or not isinstance(rid,str) or len(rid)!=32 or any(x not in '0123456789abcdef' for x in rid):
            raise Rejected('invalid local claim intent; manual reconciliation required')
        if intent.get('phase')=='executing':
            jid=intent.get('job_id');fence=intent.get('fence')
            if not isinstance(jid,str) or not 1<=len(jid)<=64 or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for x in jid) or not isinstance(fence,int) or fence<1:
                raise Rejected('invalid execution marker; manual reconciliation required')
            acknowledged=outbox/(jid+'.'+str(fence)+'.pending.acked')
            if not acknowledged.exists():
                raise Rejected('unfinished execution marker; manual reconciliation required; tool will not be rerun')
            ack=json.loads(acknowledged.read_text(encoding='utf-8'))
            if ack.get('worker')!=client.identity['name'] or ack.get('completion',{}).get('id')!=jid or ack['completion'].get('fence')!=fence:
                raise Rejected('execution marker ACK mismatch')
            intent_path.rename(outbox/('claim-'+rid+'.resolved.json'))
        elif intent.get('phase')!='requesting':
            raise Rejected('unknown claim intent phase')
    if not intent_path.exists():
        write_json(intent_path,{'worker':client.identity['name'],'request_id':secrets.token_hex(16),'phase':'requesting'})
    intent=json.loads(intent_path.read_text(encoding='utf-8'));rid=intent['request_id']
    print(canonical({'at':dt.datetime.now(dt.timezone.utc).isoformat(),'event':'claim_request_sent','request_id':rid}),file=sys.stderr,flush=True)
    try:
        job = client.post("/claim", {'request_id':rid})["job"]
    except Exception as exc:
        print(canonical({'at':dt.datetime.now(dt.timezone.utc).isoformat(),'event':'claim_request_failed','request_id':rid,'error_type':type(exc).__name__}),file=sys.stderr,flush=True)
        raise
    if job is None:
        # Acknowledged empty poll: discard only this new transient intent.
        intent_path.unlink()
        return None
    stop_heartbeat = threading.Event()
    lost_lease = threading.Event()
    started = time.monotonic()
    def trace(phase, **fields):
        # No payload, result text, tokens, headers or exception messages.
        print(canonical(dict(at=dt.datetime.now(dt.timezone.utc).isoformat(),
              event='job_phase', job=job['id'], fence=job['fence'], tool=job['tool'],
              phase=phase, request_id=rid, elapsed_ms=round((time.monotonic()-started)*1000), **fields)),
              file=sys.stderr, flush=True)
    def maintain_lease():
        while not stop_heartbeat.wait(heartbeat_interval):
            try:
                client.post('/heartbeat',{'id':job['id'],'fence':job['fence']})
                trace('lease_renewed')
            except Exception as exc:
                lost_lease.set()
                trace('lease_renewal_failed', error_type=type(exc).__name__)
                return
    keeper = threading.Thread(target=maintain_lease,daemon=True)
    keeper.start()
    trace('claimed_received')
    try:
        # Persist BEFORE any execution. A restart in this phase requires an ACK
        # or manual review, so uncertain metered work is never silently retried.
        write_json(intent_path,{'worker':client.identity['name'],'request_id':rid,'phase':'executing','job_id':job['id'],'fence':job['fence']})
        try:
            result = run_tool(job['tool'],job['payload'],job['id'],lost_lease.is_set)
            success = True
        except Exception as exc:
            result, success = {"error": type(exc).__name__, "message": str(exc)}, False
            if job['effect'] != 'read':
                result['requires_review']=True
        trace('tool_finished', success=success)
        completion = {"id": job["id"], "fence": job["fence"], "result": result, "success": success}
        pending = outbox / (job["id"] + "." + str(job["fence"]) + ".pending.json")
        trace('outbox_write_started')
        write_json(pending, {"worker": client.identity["name"], "completion": completion})
        trace('outbox_durable')
        acknowledgment = client.post("/complete", completion)
        trace('completion_acknowledged')
        pending.rename(pending.with_suffix(".acked"))
        intent_path.rename(outbox/('claim-'+rid+'.resolved.json'))
        return {"id": job["id"], "state": acknowledgment.get('state', 'succeeded' if success else 'failed')}
    finally:
        # Keep renewing through fsync/replace and the completion HTTP exchange.
        stop_heartbeat.set()
        keeper.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=ROOT / "state")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8840)
    serve.add_argument("--allow-peer", action="append", default=[])
    serve.add_argument('--config', type=Path)
    pair = sub.add_parser("pair")
    pair.add_argument("--name", required=True)
    pair.add_argument("--tools", default="hash_text,text_stats,inventory")
    pair.add_argument("--out", type=Path, required=True)
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--key", required=True)
    enqueue.add_argument("--tool", required=True)
    enqueue.add_argument("--payload", type=Path, required=True)
    enqueue.add_argument("--target")
    enqueue.add_argument("--resource")
    enqueue.add_argument("--dependency")
    status = sub.add_parser("status")
    status.add_argument("--out", type=Path)
    cool = sub.add_parser("cooldown")
    cool.add_argument("--resource", required=True)
    cool.add_argument("--until", type=float, required=True, help="UTC Unix seconds; never shortens existing cooldown")
    cool.add_argument("--reason", required=True)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--job", required=True)
    reconcile.add_argument("--outcome", choices=["succeeded", "failed"], required=True)
    reconcile.add_argument("--evidence", required=True)
    for command in ("enroll", "worker"):
        p = sub.add_parser(command)
        p.add_argument("--url")
        p.add_argument("--pin")
        p.add_argument('--config', type=Path)
        p.add_argument("--identity", type=Path, required=True)
        if command == "enroll":
            p.add_argument("--name", required=True)
            p.add_argument("--pair-file", type=Path)
        else:
            p.add_argument("--outbox", type=Path, required=True)
            p.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if args.command in ('worker', 'serve'):
        from runtime_singleton import acquire_once
        acquire_once(ROOT, 'worker' if args.command == 'worker' else 'coordinator')
    client_config = None
    if args.command in ('worker', 'enroll'):
        client_config = read_config(args.config) if args.config else None
        args.url = args.url or (client_config or {}).get('coordinator_url')
        args.pin = args.pin or (client_config or {}).get('certificate_sha256')
        if not args.url or not args.pin:
            raise Rejected('Coordinator URL and trusted fingerprint required')
    if args.command == "enroll":
        if args.identity.exists():
            raise Rejected("identity exists; refusing overwrite")
        code = (json.loads(args.pair_file.read_text(encoding='utf-8-sig'))["code"] if args.pair_file else getpass.getpass("One-time pairing code: "))
        token = Client(args.url, args.pin, config=client_config).post("/enroll", {"name": args.name, "code": code})["token"]
        write_json(args.identity, {"name": args.name, "token": token})
        print("Enrolled; identity remains local. Do not copy identity files.")
        return
    if args.command == "worker":
        client = Client(args.url, args.pin, json.loads(args.identity.read_text(encoding="utf-8")), client_config)
        while True:
            try:
                result = execute_once(client, args.outbox,
                                      (lambda: workspace_storage_guard(ROOT, client_config)) if client_config else None)
                if result:
                    print(canonical(result), flush=True)
                if not args.watch:
                    break
            except (OSError, http.client.HTTPException) as exc:
                if not args.watch:
                    raise
                print(canonical({'at':dt.datetime.now(dt.timezone.utc).isoformat(),
                      'event':'coordinator_unavailable','error_type':type(exc).__name__,
                      'retry_seconds':5,'outbox_retained':True}),file=sys.stderr,flush=True)
            except StoragePaused as exc:
                if not args.watch:
                    raise Rejected(str(exc))
                print(str(exc), file=sys.stderr, flush=True)
                time.sleep(30)
            if args.watch:
                time.sleep(5)
        return
    store = Store(args.state / "queue.sqlite")
    if args.command == "init":
        _, _, pin = ensure_certificate(args.state)
        print(canonical({"state": str(args.state), "tls_sha256": pin, "production_tools": "disabled"}))
    elif args.command == "serve":
        server_config = read_config(args.config) if args.config else {}
        server, pin = make_server(store, args.state, server_config.get('bind', args.host),
                                  server_config.get('port', args.port), server_config.get('allowed_networks', args.allow_peer))
        udp = None
        if server_config.get('discovery', {}).get('enabled'):
            try:
                udp = Discovery(server.allowed_peers, server_config['discovery']['port'], server.server_port, pin)
                udp.start()
            except BaseException:
                server.server_close()
                raise
        print(canonical({"listen": server.server_address[0], "port": server.server_port, "tls_sha256": pin}), flush=True)
        try:
            server.serve_forever()
        finally:
            if udp:
                udp.close()
            server.server_close()
    elif args.command == "pair":
        if args.out.exists():
            raise Rejected("pair file already exists")
        code = store.prepare_enrollment(args.name, args.tools.split(","))
        write_json(args.out, {"name": args.name, "code": code, "expires_in_seconds": 600})
        print("One-time enrollment written locally; never publish it in reports or source control.")
    elif args.command == "enqueue":
        print(store.enqueue(args.key, args.tool, json.loads(args.payload.read_text(encoding="utf-8-sig")),
                            args.target, args.resource, args.dependency))
    elif args.command == "status":
        value = store.snapshot()
        if args.out:
            write_json(args.out, value)
            print(str(args.out))
        else:
            print(json.dumps(value, ensure_ascii=False, indent=2))
    elif args.command == "cooldown":
        store.cooldown(args.resource, args.until, args.reason)
    elif args.command == "reconcile":
        store.reconcile(args.job, args.outcome, args.evidence)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print('Worker stopped by user. Existing receipts retained.', file=sys.stderr)
        sys.exit(0)
    except Rejected as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
