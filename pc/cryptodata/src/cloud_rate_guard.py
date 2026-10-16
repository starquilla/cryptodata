"""Host-wide cooldowns. Persist state; never automatically retry a blocked host."""
import email.utils, json, os, pathlib, re, time

class RateGuard:
    def __init__(self, path, seed=None, clock=time.time):
        self.path, self.clock = pathlib.Path(path), clock
        self.state = json.loads(self.path.read_text('utf8')) if self.path.exists() else {}
        for host, row in (seed or {}).items():
            if float(row['until']) > float(self.state.get(host, {}).get('until', 0)):
                self.state[host] = row

    def remaining(self, host):
        return max(0, float(self.state.get(host, {}).get('until', 0)) - self.clock())

    def block(self, host, status, headers, body):
        now = self.clock()
        delay = 3 * 86400 if status == 418 else 900
        until = now + delay
        retry = headers.get('Retry-After')
        hints = []
        if retry:
            try:
                hints.append(now + max(0, float(retry)))
            except ValueError:
                try: hints.append(email.utils.parsedate_to_datetime(retry).timestamp())
                except (TypeError, ValueError, OverflowError): pass
        # Retain only the numeric ban deadline, never the provider message with an IP.
        match = re.search(r'banned until\s+(\d{10,16})', body, re.I)
        if match:
            stamp = int(match.group(1))
            hints.append(stamp / 1000 if stamp > 10**11 else stamp)
        future = [x for x in hints if x > now]
        if future: until = max(future) + 5
        until = max(until, float(self.state.get(host, {}).get('until', 0)))
        self.state[host] = {'until': until, 'http_status': status,
                            'basis': 'provider' if future else 'conservative_fallback'}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, indent=2), encoding='utf8')
        os.replace(temp, self.path)
        return dict(self.state[host])
