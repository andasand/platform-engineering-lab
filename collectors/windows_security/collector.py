"""Synthetic outbox prototype for the idempotent single-event /events contract.

No sockets, Windows APIs, AWS SDK, or subprocesses are used by this module.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import time


POLICY = 'synthetic-security-v2'
EVENTS = {
    4624: ('Microsoft-Windows-Security-Auditing', 'windows.logon_success', 'INFO'),
    4625: ('Microsoft-Windows-Security-Auditing', 'windows.logon_failure', 'WARNING'),
    1102: ('Microsoft-Windows-Eventlog', 'windows.audit_log_cleared', 'CRITICAL'),
}


class BeforeDelivery(Exception):
    """Transport can prove that no request reached the receiver."""


class UncertainDelivery(Exception):
    """The receiver may have committed; retry using the identical source identifier."""


class PermanentRejection(Exception):
    """Conflicting identity or invalid request: retain for operator review."""


def normalize(event):
    """Build a small allowlisted payload; never copy rendered messages or EventData."""
    if event.get('synthetic') is not True:
        raise ValueError('Only explicitly marked synthetic events are accepted')
    for name in ('host_alias', 'generation'):
        if not isinstance(event.get(name), str) or not re.fullmatch(r'[a-z0-9-]{1,48}', event[name]):
            raise ValueError('Use a stable nonidentifying lowercase alias and generation')
    record = event.get('record_id')
    event_id = event.get('event_id')
    if type(record) is not int or record <= 0 or type(event_id) is not int:
        raise ValueError('record_id must be positive and event_id must be an integer')
    stamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if stamp.utcoffset() is None:
        raise ValueError('Timestamp must contain a timezone')
    stream = json.dumps([event['host_alias'], event['generation'], event['channel']], separators=(',', ':'))
    key = hashlib.sha256(f'{stream}:{record}'.encode()).hexdigest()
    spec = EVENTS.get(event_id)
    if event['channel'] != 'Security' or not spec or event.get('provider') != spec[0]:
        return stream, record, key, None
    if event_id == 4624 and event.get('logon_type') not in (2, 7, 10, 11):
        return stream, record, key, None
    payload = {
        'source_event_id': f'winsec:v1:{key}',
        'timestamp': stamp.isoformat().replace('+00:00', 'Z'),
        'service': 'windows-security-collector',
        'event_type': spec[1],
        'severity': spec[2],
        'source_ip': None,
        'username': None,
        'message': f'Synthetic Windows Security event {event_id}; source_event_key={key}',
    }
    return stream, record, key, payload


@contextmanager
def exclusive_state(directory):
    """Hold one collector lock across staging, sending, and receipt commits."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / 'collector.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield directory
    finally:
        os.close(fd)
        lock.unlink()


class Outbox:
    """Caller must hold exclusive_state for this database's lifetime."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (policy TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints (
                stream TEXT PRIMARY KEY, record_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                event_key TEXT PRIMARY KEY, payload TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending','sending','acked','unknown','blocked')),
                attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
                receipt INTEGER, reason TEXT);
        ''')
        with self.db:
            existing = self.db.execute('SELECT policy FROM settings').fetchall()
            if not existing:
                self.db.execute('INSERT INTO settings VALUES (?)', (POLICY,))
            elif len(existing) != 1 or existing[0]['policy'] != POLICY:
                raise ValueError('Policy changed: review existing checkpoints before migration')
            self.db.execute("UPDATE outbox SET state='unknown', reason='interrupted_delivery' WHERE state='sending'")

    def close(self):
        self.db.close()

    def stage(self, events, max_pending=10000):
        # One ordered stream per capture chunk. Validate the entire chunk before
        # advancing anything, including events that will be filtered out.
        rows = [normalize(event) for event in events]
        if len({row[0] for row in rows}) > 1:
            raise ValueError('Capture chunks must contain exactly one source stream')
        if any(a[1] >= b[1] for a, b in zip(rows, rows[1:])):
            raise ValueError('Capture must be in strictly increasing record order')
        staged = 0
        with self.db:
            count = self.db.execute("SELECT count(*) FROM outbox WHERE state != 'acked'").fetchone()[0]
            for stream, record, key, payload in rows:
                checkpoint = self.db.execute('SELECT record_id FROM checkpoints WHERE stream=?', (stream,)).fetchone()
                if checkpoint and record <= checkpoint[0]:
                    continue
                if payload is not None:
                    if count >= max_pending:
                        raise ValueError('Outbox full: capture transaction rolled back')
                    self.db.execute("INSERT INTO outbox(event_key,payload,state) VALUES (?,?,'pending')",
                                    (key, json.dumps(payload, separators=(',', ':'))))
                    staged += 1
                    count += 1
                self.db.execute('INSERT INTO checkpoints VALUES (?,?) ON CONFLICT(stream) DO UPDATE SET record_id=excluded.record_id', (stream, record))
        return staged

    def flush(self, sender, batch_size=20, max_bytes=65536, max_attempts=5,
              clock=time.time, jitter=random.random):
        if not 1 <= batch_size <= 100 or max_bytes < 2048 or not 1 <= max_attempts <= 10:
            raise ValueError('Invalid delivery bounds')
        # Never retry against an older receiver that silently ignores identifiers.
        # Capability verification occurs before any replay or request is sent.
        if not sender.idempotency_ready():
            raise ValueError('Receiver does not advertise source_event_id v1')
        with self.db:
            self.db.execute("UPDATE outbox SET state='pending' WHERE state='unknown'")
        if self.db.execute("SELECT 1 FROM outbox WHERE state='blocked' LIMIT 1").fetchone():
            return 0
        # Batch controls local memory/work only. The API accepts one object per POST.
        rows = self.db.execute("SELECT rowid,* FROM outbox WHERE state='pending' ORDER BY rowid LIMIT ?", (batch_size,)).fetchall()
        sent = total_bytes = 0
        for row in rows:
            if row['attempts'] >= max_attempts:
                with self.db:
                    self.db.execute("UPDATE outbox SET state='blocked',reason='retry_exhausted' WHERE event_key=?", (row['event_key'],))
                break
            if row['next_at'] > clock():
                break
            total_bytes += len(row['payload'].encode())
            if total_bytes > max_bytes:
                break
            with self.db:
                self.db.execute("UPDATE outbox SET state='sending',attempts=attempts+1 WHERE event_key=?", (row['event_key'],))
            try:
                payload = json.loads(row['payload'])
                reply = sender(payload)
                if not isinstance(reply, dict) or reply.get('status') != 'stored' or type(reply.get('event_id')) is not int or reply['event_id'] <= 0 or reply.get('source_event_id') != payload['source_event_id']:
                    raise UncertainDelivery('Unusable acknowledgement')
            except (BeforeDelivery, UncertainDelivery, TimeoutError, ConnectionError):
                attempt = row['attempts'] + 1
                delay = min(30.0, 0.5 * 2 ** (attempt - 1)) * (0.5 + 0.5 * jitter())
                with self.db:
                    self.db.execute('UPDATE outbox SET state=?,next_at=?,reason=? WHERE event_key=?',
                                    ('blocked' if attempt >= max_attempts else 'pending', clock() + delay,
                                     'retry_exhausted' if attempt >= max_attempts else 'retryable_delivery', row['event_key']))
                break
            except PermanentRejection:
                with self.db:
                    self.db.execute("UPDATE outbox SET state='blocked',reason='permanent_rejection' WHERE event_key=?", (row['event_key'],))
                break
            except Exception:
                # Unexpected failures remain visible and durable; a verified v1
                # receiver can safely replay them on the next invocation.
                with self.db:
                    self.db.execute("UPDATE outbox SET state='unknown',reason='delivery_uncertain' WHERE event_key=?", (row['event_key'],))
                break
            with self.db:
                self.db.execute("UPDATE outbox SET state='acked',receipt=?,reason=NULL WHERE event_key=?", (reply['event_id'], row['event_key']))
            sent += 1
        return sent

    def summary(self):
        return dict(self.db.execute('SELECT state,count(*) FROM outbox GROUP BY state').fetchall())


class SyntheticReceiver:
    """Durable idempotent model of POST /events, not FastAPI/PG.

    Separate storage allows testing a committed event with a lost response.
    """

    def __init__(self, path, failure='none'):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS received (id INTEGER PRIMARY KEY, source_event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)')
        self.failure = failure

    def idempotency_ready(self):
        return True

    def __call__(self, payload):
        failure, self.failure = self.failure, 'none'
        if failure == 'before-send':
            raise BeforeDelivery()
        encoded = json.dumps(payload, sort_keys=True)
        with self.db:
            self.db.execute('INSERT INTO received(source_event_id,payload) VALUES (?,?) ON CONFLICT(source_event_id) DO NOTHING', (payload['source_event_id'], encoded))
            row = self.db.execute('SELECT id,payload FROM received WHERE source_event_id=?', (payload['source_event_id'],)).fetchone()
            if row[1] != encoded:
                raise PermanentRejection()
        if failure == 'after-commit':
            raise TimeoutError('Synthetic lost response after commit')
        return {'status': 'stored', 'event_id': row[0], 'source_event_id': payload['source_event_id']}

    def close(self):
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description='Synthetic-only offline collector; no network sender exists.')
    parser.add_argument('--fixture', type=Path, default=Path(__file__).with_name('fixtures') / 'security.json')
    parser.add_argument('--state-dir', type=Path, default=Path('.collector-state'))
    parser.add_argument('--batch-size', type=int, default=20)
    parser.add_argument('--simulate-failure', choices=('none', 'before-send', 'after-commit'), default='none')
    args = parser.parse_args()
    if args.fixture.stat().st_size > 262144:
        parser.error('Fixture exceeds 256 KiB limit')
    events = json.loads(args.fixture.read_text(encoding='utf-8'))
    if not isinstance(events, list) or not 1 <= len(events) <= 1000:
        parser.error('Fixture must contain 1–1000 synthetic event objects')
    with exclusive_state(args.state_dir) as directory:
        outbox = Outbox(directory / 'outbox.sqlite3')
        receiver = SyntheticReceiver(directory / 'synthetic-receiver.sqlite3', args.simulate_failure)
        try:
            staged = outbox.stage(events)
            sent = outbox.flush(receiver, batch_size=args.batch_size)
            print(json.dumps({'mode': 'offline-synthetic', 'staged': staged, 'acknowledged': sent, 'outbox': outbox.summary()}))
            return 0 if set(outbox.summary()) <= {'acked'} else 2
        finally:
            receiver.close()
            outbox.close()


if __name__ == '__main__':
    raise SystemExit(main())
