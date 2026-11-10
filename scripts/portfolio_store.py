#!/usr/bin/env python3
"""Validate and atomically save one registry in a local portfolio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from validate_package import ROOT, read_json, validate_case

COLLECTIONS = ('cases', 'actors', 'sources', 'claims', 'threads', 'events', 'commitments', 'outcomes', 'actions')


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_portfolio(path):
    path = Path(path)
    if not path.exists():
        return {'format_version': '1.0', 'revision': 0, 'registries': []}, None
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) != {'format_version', 'revision', 'registries'}:
        raise ValueError('Invalid portfolio fields')
    if data['format_version'] != '1.0' or type(data['revision']) is not int or data['revision'] < 1:
        raise ValueError('Invalid portfolio version or revision')
    if not isinstance(data['registries'], list):
        raise ValueError('Portfolio registries must be a list')
    schema = read_json(ROOT / 'assets/case-schema.json')
    identifiers = set()
    for registry in data['registries']:
        validate_case(registry, schema)
        registry_id = registry['campaign']['id']
        if registry_id in identifiers:
            raise ValueError('Duplicate registry id in portfolio')
        identifiers.add(registry_id)
    return data, raw


def describe(path):
    data, raw = read_portfolio(path)
    cases = []
    for registry in data['registries']:
        for case in registry['cases']:
            cases.append({
                'registry_id': registry['campaign']['id'],
                'case_id': case['id'],
                'title': case['title'],
                'topic': case.get('topic', ''),
                'status': case['status'],
                'applicant_actor_ids': case.get('applicant_actor_ids', []),
                'last_verified_on': case.get('last_verified_on'),
            })
    return {
        'workspace': str(Path(path).resolve()),
        'sha256': hashlib.sha256(raw).hexdigest() if raw is not None else 'missing',
        'revision': data['revision'],
        'registry_count': len(data['registries']),
        'cases': cases,
    }


def save_registry(path, registry, expected_sha256):
    schema = read_json(ROOT / 'assets/case-schema.json')
    validate_case(registry, schema)
    registry_id = registry['campaign']['id']
    if registry_id == 'campaign-draft':
        raise ValueError('Replace the template registry id with a unique stable id before saving')
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    # Exclusive creation serializes writers using this helper. A stale lock is
    # intentionally left for a human to inspect after an interrupted process.
    with lock.open('x', encoding='utf-8') as handle:
        handle.write(str(os.getpid()))
    temporary = None
    try:
        data, raw = read_portfolio(path)
        digest = hashlib.sha256(raw).hexdigest() if raw is not None else 'missing'
        if digest != expected_sha256:
            raise ValueError('Portfolio changed; reload it and reconcile the update before saving')
        existing = next((item for item in data['registries'] if item['campaign']['id'] == registry_id), None)
        if existing == registry:
            return {'changed': False, 'sha256': digest, 'revision': data['revision'], 'backup': None}
        if existing is not None:
            if existing['schema_version'] == '0.5' and registry['schema_version'] == '0.4':
                raise ValueError('Schema downgrade would lose current metadata; keep version 0.5')
            for collection in COLLECTIONS:
                previous_ids = {item['id'] for item in existing[collection]}
                next_ids = {item['id'] for item in registry[collection]}
                if not previous_ids.issubset(next_ids):
                    raise ValueError(f'Update would remove records from {collection}; retain history and use statuses')
            data['registries'][data['registries'].index(existing)] = registry
        else:
            data['registries'].append(registry)
        data['revision'] += 1
        encoded = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        backup = None
        if raw is not None:
            history = path.parent / '.history'
            history.mkdir(exist_ok=True)
            backup = history / f'{path.name}.{digest}.json'
            try:
                with backup.open('xb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                if backup.read_bytes() != raw:
                    raise ValueError('Existing backup does not match the previous portfolio') from None
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        if path.read_bytes() != encoded:
            raise ValueError('Portfolio changed after save; reload before continuing')
        return {
            'changed': True,
            'sha256': hashlib.sha256(encoded).hexdigest(),
            'revision': data['revision'],
            'backup': str(backup) if backup else None,
        }
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status', help='Validate the portfolio and print its index and hash')
    status.add_argument('--workspace', type=Path, required=True)
    save = commands.add_parser('save', help='Save a complete registry while preserving other registries')
    save.add_argument('--workspace', type=Path, required=True)
    save.add_argument('--registry', type=Path, required=True)
    save.add_argument('--expected-sha256', required=True)
    document = commands.add_parser('fingerprint', help='Hash original file bytes for duplicate detection')
    document.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'status':
            result = describe(args.workspace)
        elif args.command == 'fingerprint':
            result = {'sha256': fingerprint(args.path)}
        else:
            result = save_registry(args.workspace, read_json(args.registry), args.expected_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f'FAIL: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
