"""Address discovery supplies hints only. Pinned TLS supplies server identity."""
import ipaddress
import json
from pathlib import Path
import queue
import secrets
import socket
import threading
import time

SERVICE = 'ai-lan-coordinator-v02'


def allowed(address, networks):
    try:
        addr = ipaddress.ip_address(address)
        return any(addr in ipaddress.ip_network(n) for n in networks)
    except ValueError:
        return False


def read_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    networks = config.get('allowed_networks', ['127.0.0.0/8'])
    if not networks or any(not ipaddress.ip_network(n).is_private or ipaddress.ip_network(n).prefixlen == 0 for n in networks):
        raise ValueError('Only explicitly configured private/loopback networks are allowed')
    return config


def resolve_ipv4(host, timeout=2):
    """Bound the caller's DNS wait; the OS resolver cannot reliably be cancelled."""
    output = queue.Queue()
    def run():
        try:
            output.put(list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM))))
        except OSError:
            output.put([])
    threading.Thread(target=run, daemon=True).start()
    try:
        return output.get(timeout=timeout)
    except queue.Empty:
        return []


class Discovery:
    def __init__(self, networks, port, tls_port, pin, bind='0.0.0.0'):
        self.networks, self.tls_port, self.pin = networks, tls_port, pin
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((bind, port))
        self.socket.settimeout(.3)
        self.port = self.socket.getsockname()[1]
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            try:
                data, source = self.socket.recvfrom(1024)
                if not allowed(source[0], self.networks):
                    continue
                request = json.loads(data)
                nonce = request.get('nonce', '')
                if request.get('service') != SERVICE or len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
                    continue
                response = {'service': SERVICE, 'nonce': nonce, 'port': self.tls_port,
                            'pin': self.pin, 'hostname': socket.gethostname()}
                self.socket.sendto(json.dumps(response).encode(), source)
            except (OSError, ValueError, TypeError, AttributeError):
                continue

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)
        self.socket.close()


def discover(config, pin, timeout=.8):
    spec = config.get('discovery', {})
    if not spec.get('enabled', False):
        return []
    nonce = secrets.token_hex(16)
    request = json.dumps({'service': SERVICE, 'nonce': nonce}).encode()
    hints = []
    networks = config['allowed_networks']
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(.15)
        for address in spec.get('targets', []):
            if allowed(address, networks):
                try:
                    sock.sendto(request, (address, spec.get('port', 8842)))
                except OSError:
                    pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(hints) < 4:
            try:
                data, source = sock.recvfrom(1024)
                reply = json.loads(data)
                if (allowed(source[0], networks) and reply.get('service') == SERVICE
                        and reply.get('nonce') == nonce and reply.get('pin') == pin
                        and type(reply.get('port')) is int and 1 <= reply['port'] <= 65535):
                    endpoint = (source[0], reply['port'])
                    if endpoint not in hints:
                        hints.append(endpoint)
            except (OSError, ValueError, TypeError, AttributeError):
                pass
    return hints  # Every hint MUST pass TLS fingerprint verification before credentials.


class StoragePaused(Exception):
    pass


def storage_guard(root, config):
    import shutil
    root = Path(root).resolve()
    spec = config.get('storage', {})
    required = spec.get('required_drive_by_host', {}).get(socket.gethostname())
    if required and root.drive.casefold() != required.casefold():
        raise StoragePaused('Wrong storage drive for this worker; use the authorized D: project')
    limit = spec.get('project_limit_bytes', 30 * 1024**3)
    high = spec.get('pause_at_bytes', 27 * 1024**3)
    minimum = spec.get('minimum_disk_free_bytes', 10 * 1024**3)
    if not 0 < high <= limit or minimum < 0:
        raise ValueError('Invalid storage budget')
    total = 0
    # No deletion. Refuse linked/reparse trees rather than count or traverse outside project.
    import os
    import stat
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            st = path.lstat()
            if path.is_symlink() or getattr(st, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
                raise StoragePaused('Linked/reparse paths require operator review')
            if name in files:
                total += st.st_size
        if total >= high:
            raise StoragePaused('Project high-water mark reached; no new job claimed')
    free = shutil.disk_usage(root).free
    if free < minimum:
        raise StoragePaused('Disk free space below reserve; no new job claimed')
    return {'project_bytes': total, 'free_bytes': free, 'limit_bytes': limit, 'pause_at_bytes': high}
