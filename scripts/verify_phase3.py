#!/usr/bin/env python3
"""Read-only rollout checks and repeatable synthetic acceptance; Python 3.9+.

Used identically before/after rollout and after rollback. No AWS SDK, credential
output, migration execution, or service-control commands. Only `accept` submits
the existing, approved synthetic event; it never creates a new source identifier.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


PRIOR_RELEASE = '60ba7141b1294679c1b0ee2eecb08558d2a1ee3f'
PRIOR_KEY = 'phase3-rollout:' + PRIOR_RELEASE
PRIOR_ID = 3
RFC3339 = re.compile(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$')

# The database baseline exposes only IDs, digests, schema flags, and counts.
# Every column (including raw_event and source_event_id) contributes to each hash.
SNAPSHOT_SQL = """
SELECT json_build_object(
 'rows', (SELECT count(*) FROM public.security_events),
 'records', (SELECT coalesce(json_agg(json_build_object('id', id, 'digest', md5(to_jsonb(e)::text)) ORDER BY id), '[]'::json) FROM public.security_events e),
 'column_ok', EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='public.security_events'::regclass AND attname='source_event_id' AND atttypid='text'::regtype AND NOT attnotnull AND NOT attisdropped),
 'constraint_ok', EXISTS(SELECT 1 FROM pg_constraint c JOIN pg_index i ON i.indexrelid=c.conindid WHERE c.conrelid='public.security_events'::regclass AND c.conname='security_events_source_event_id_key' AND c.contype='u' AND c.convalidated AND NOT c.condeferrable AND i.indisvalid AND c.conkey=ARRAY[(SELECT attnum FROM pg_attribute WHERE attrelid='public.security_events'::regclass AND attname='source_event_id')]),
 'prior_ids', (SELECT coalesce(json_agg(id ORDER BY id), '[]'::json) FROM public.security_events WHERE source_event_id='phase3-rollout:60ba7141b1294679c1b0ee2eecb08558d2a1ee3f'),
 'synthetic_rows', (SELECT count(*) FROM public.security_events WHERE service='phase3-synthetic-rollout'),
 'legacy_synthetic_rows', (SELECT count(*) FROM public.security_events WHERE service='phase3-synthetic-rollout' AND source_event_id IS NULL),
 'sequence', (SELECT last_value FROM public.security_events_id_seq)
);
"""


def parse_prometheus_timestamp(value):
    """Parse RFC3339 with 0–9 fractional digits consistently on Python 3.9+.

    Truncate sub-microsecond digits before fromisoformat; this can conservatively
    delay a freshness match by <1 microsecond but cannot admit a stale sample.
    Reject missing offsets/invalid dates instead of treating malformed data as UP.
    """
    match = RFC3339.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError('Invalid Prometheus RFC3339 timestamp')
    stamp, fraction, offset = match.groups()
    if offset != 'Z' and (int(offset[1:3]) > 23 or int(offset[4:6]) > 59):
        raise ValueError('Invalid Prometheus timestamp offset')
    if fraction:
        stamp += '.' + (fraction + '000000')[:6]
    stamp += '+00:00' if offset == 'Z' else offset
    return datetime.fromisoformat(stamp).astimezone(timezone.utc)


def targets_healthy(payload, fresh_after):
    if fresh_after.tzinfo is None or fresh_after.utcoffset() is None:
        raise ValueError('Freshness cutoff must contain a timezone')
    jobs = {}
    for target in payload['data']['activeTargets']:
        job = target.get('labels', {}).get('job')
        if job in ('eventflow', 'log-ingestor'):
            if job in jobs:
                raise ValueError('Ambiguous duplicate scrape job')
            jobs[job] = target
    for job in ('eventflow', 'log-ingestor'):
        target = jobs.get(job)
        if target is None or target.get('health') != 'up' or target.get('lastError'):
            return False
        if parse_prometheus_timestamp(target['lastScrape']) < fresh_after:
            return False
    return True


def validate_baseline(snapshot):
    if not snapshot['column_ok'] or not snapshot['constraint_ok']:
        raise ValueError('Migration 002 column/valid uniqueness constraint required; do not reapply automatically')
    if snapshot['rows'] != 4 or len(snapshot['records']) != 4:
        raise ValueError('Expected reviewed four-record baseline; refresh backup and review changed data')
    if snapshot['prior_ids'] != [PRIOR_ID] or snapshot['synthetic_rows'] != 2 or snapshot['legacy_synthetic_rows'] != 1:
        raise ValueError('Prior synthetic acceptance history does not match reviewed baseline')
    if snapshot['sequence'] < max(row['id'] for row in snapshot['records']):
        raise ValueError('Identity sequence is behind existing records')
    return snapshot


def compare_baseline(before, after):
    validate_baseline(before)
    validate_baseline(after)
    # ON CONFLICT can consume sequence values even without inserting a new row.
    for key in ('rows', 'records', 'column_ok', 'constraint_ok', 'prior_ids', 'synthetic_rows', 'legacy_synthetic_rows'):
        if before[key] != after[key]:
            raise ValueError('Database records or schema changed from captured baseline')
    if after['sequence'] < before['sequence']:
        raise ValueError('Identity sequence moved backwards')


def database_snapshot():
    root = '/opt/platform-engineering-lab'
    command = ['docker', 'compose', '--project-directory', root, '--env-file', root + '/.env',
               '-f', root + '/compose.yaml', '-f', root + '/infra/deploy/compose.aws.yaml',
               'exec', '-T', 'postgres', 'sh', '-c',
               'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SET TIME ZONE \'UTC\'" -c "$1"',
               'sh', SNAPSHOT_SQL]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Database snapshot query failed; sensitive diagnostics suppressed')
    return validate_baseline(json.loads(result.stdout))


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(base, path, payload=None, expected=200):
    opener = build_opener(ProxyHandler({}), NoRedirects())
    body = None if payload is None else json.dumps(payload).encode()
    req = Request(base + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        with opener.open(req, timeout=10) as response:
            code, data = response.status, response.read(65536)
    except HTTPError as exc:
        code, data = exc.code, exc.read(65536)
    if code != expected:
        raise RuntimeError('Unexpected HTTP status: ' + str(code))
    return json.loads(data)


def existing_event():
    return dict(source_event_id=PRIOR_KEY, timestamp='2026-10-30T00:00:00Z',
                service='phase3-synthetic-rollout', event_type='idempotency_check',
                severity='INFO', source_ip=None, username=None,
                message='Synthetic Phase 3 rollout acceptance')


def acceptance(send):
    if send('/health')['status'] != 'healthy' or send('/ready')['capabilities'].get('source_event_id') != 'v1':
        raise RuntimeError('Updated ingestor is not ready')
    event = existing_event()
    first = send('/events', event)
    second = send('/events', event)
    if first != second or first.get('event_id') != PRIOR_ID or first.get('source_event_id') != PRIOR_KEY:
        raise RuntimeError('Duplicate submissions did not return original record ID 3')
    send('/events', dict(event, message='Synthetic conflicting content'), expected=409)
    send('/events', dict(event, severity='invalid'), expected=422)
    return dict(event_id=PRIOR_ID, conflict_status=409, validation_status=422, new_rows=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('--out', type=Path, required=True)
    compare = commands.add_parser('compare')
    compare.add_argument('--baseline', type=Path, required=True)
    accept = commands.add_parser('accept')
    accept.add_argument('--baseline', type=Path, required=True)
    monitor = commands.add_parser('prometheus')
    monitor.add_argument('--timeout', type=float, default=60)
    monitor.add_argument('--interval', type=float, default=5)
    args = parser.parse_args()
    if args.command == 'capture':
        snapshot = database_snapshot()
        # Exclusive creation protects the baseline from accidental overwrite.
        fd = os.open(str(args.out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(snapshot, output, indent=2)
        print('PASS: migration 002 validated; four-record baseline captured')
    elif args.command in ('compare', 'accept'):
        baseline = json.loads(args.baseline.read_text())
        compare_baseline(baseline, database_snapshot())
        if args.command == 'accept':
            result = acceptance(lambda path, payload=None, expected=200:
                                request('http://127.0.0.1:8002', path, payload, expected))
            print(json.dumps(result))
            compare_baseline(baseline, database_snapshot())
        print('PASS: all four records, synthetic history, schema and sequence preserved')
    else:
        if args.timeout <= 0 or args.interval <= 0:
            parser.error('Timeout and interval must be positive')
        fresh_after = datetime.now(timezone.utc)
        deadline = time.monotonic() + args.timeout
        while True:
            if targets_healthy(request('http://127.0.0.1:9090', '/api/v1/targets?state=active'), fresh_after):
                print('PASS: both Prometheus targets UP with fresh scrapes')
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Prometheus targets did not recover before deadline')
            time.sleep(min(args.interval, remaining))


if __name__ == '__main__':
    main()
