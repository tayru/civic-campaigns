#!/usr/bin/env python3
"""Validate and atomically save one registry in a local portfolio."""
import argparse
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import webbrowser

from validate_package import ROOT, EXTENDED_COLLECTIONS, read_json, validate_case
from dashboard_files import file_catalogue

COLLECTIONS = ('cases', 'actors', 'sources', 'claims', 'threads', 'events', 'commitments', 'outcomes', 'actions', *EXTENDED_COLLECTIONS)
DASHBOARD_TAG = '<script id="portfolio-snapshot" type="application/json">'


@contextmanager
def portfolio_lock(path):
    lock = path.with_name(path.name + '.lock')
    # Exclusive creation serializes writers. A stale lock requires inspection.
    with lock.open('x', encoding='utf-8') as handle:
        handle.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()


def replace_file(path, encoded):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def dashboard_path(path):
    name = 'case-dashboard.html' if path.name == 'portfolio.json' else path.name + '-dashboard.html'
    return path.with_name(name)


def write_dashboard(path, data, documents_root=None):
    if ROOT.resolve() in path.parents or path.is_symlink():
        raise ValueError('Keep the generated dashboard in the private portfolio folder, outside the skill')
    if path.exists() and DASHBOARD_TAG not in path.read_text(encoding='utf-8'):
        raise ValueError(f'Dashboard path is occupied by an unrelated file: {path}')
    options_path = path.with_suffix('.viewer.json')
    if documents_root is None and options_path.exists():
        options = read_json(options_path)
        if set(options) != {'documents_root'} or not isinstance(options['documents_root'], str):
            raise ValueError('Invalid private viewer configuration')
        documents_root = Path(options['documents_root'])
    if documents_root is not None:
        documents_root = Path(documents_root).resolve()
        if not documents_root.is_dir() or str(documents_root).startswith('\\\\'):
            raise ValueError('Choose an existing local documents directory')
    files, source_files = file_catalogue(data, path.parent, documents_root)
    registries = []
    for registry in data['registries']:
        # Private file URLs live only in the generated view, outside the package.
        registries.append({
            **registry,
            'actors': [{'id': item['id'], 'label': item['label']} for item in registry['actors']],
            'sources': [{'id': item['id'], 'title': item['title'],
                         'file_id': source_files[(registry['campaign']['id'], item['id'])]}
                        for item in registry['sources']],
        })
    snapshot = json.dumps({**data, 'registries': registries, 'viewer_files': files}, ensure_ascii=False).replace('<', '\\u003c')
    template = (ROOT / 'assets/case-dashboard.html').read_text(encoding='utf-8')
    placeholder = DASHBOARD_TAG + 'null</script>'
    if template.count(placeholder) != 1:
        raise ValueError('Dashboard template has no unique data placeholder')
    encoded = template.replace(placeholder, DASHBOARD_TAG + snapshot + '</script>').encode('utf-8')
    if not path.exists() or path.read_bytes() != encoded:
        replace_file(path, encoded)
    if documents_root is not None:
        options = json.dumps({'documents_root': str(documents_root)}, ensure_ascii=False).encode('utf-8')
        replace_file(options_path, options)


def refresh_linked_dashboard(workspace, data, result):
    path = dashboard_path(workspace)
    if path.exists():
        try:
            write_dashboard(path, data)
            result['dashboard'] = str(path)
        except (OSError, ValueError) as error:
            result['dashboard_error'] = str(error)
    return result


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
    path = Path(path).resolve()
    if ROOT.resolve() in path.parents:
        raise ValueError('Keep private portfolios outside the skill repository')
    schema = read_json(ROOT / 'assets/case-schema.json')
    validate_case(registry, schema)
    registry_id = registry['campaign']['id']
    if registry_id == 'campaign-draft':
        raise ValueError('Replace the template registry id with a unique stable id before saving')
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with portfolio_lock(path):
        data, raw = read_portfolio(path)
        digest = hashlib.sha256(raw).hexdigest() if raw is not None else 'missing'
        if digest != expected_sha256:
            raise ValueError('Portfolio changed; reload it and reconcile the update before saving')
        existing = next((item for item in data['registries'] if item['campaign']['id'] == registry_id), None)
        registry = copy.deepcopy(registry)
        previous_intake = {item['id']: item for item in (existing or {}).get('intake', [])}
        for item in registry.get('intake', []):
            previous = previous_intake.get(item['id'])
            content = {key: value for key, value in item.items() if key != 'saved_revision'}
            previous_content = {key: value for key, value in (previous or {}).items() if key != 'saved_revision'}
            item['saved_revision'] = previous['saved_revision'] if content == previous_content else data['revision'] + 1
        if existing == registry:
            return refresh_linked_dashboard(path, data, {
                'changed': False, 'sha256': digest, 'revision': data['revision'], 'backup': None,
            })
        if existing is not None:
            if existing['schema_version'] > registry['schema_version']:
                raise ValueError('Schema downgrade would lose current metadata')
            for collection in COLLECTIONS:
                previous_ids = {item['id'] for item in existing.get(collection, [])}
                next_ids = {item['id'] for item in registry.get(collection, [])}
                if not previous_ids.issubset(next_ids):
                    raise ValueError(f'Update would remove records from {collection}; retain history and use statuses')
        if existing is not None:
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
        replace_file(path, encoded)
        if path.read_bytes() != encoded:
            raise ValueError('Portfolio changed after save; reload before continuing')
        return refresh_linked_dashboard(path, data, {
            'changed': True,
            'sha256': hashlib.sha256(encoded).hexdigest(),
            'revision': data['revision'],
            'backup': str(backup) if backup else None,
        })


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status', help='Validate the portfolio and print its index and hash')
    status.add_argument('--workspace', type=Path, required=True)
    dashboard = commands.add_parser('dashboard', help='Enable or rebuild the private dashboard beside a saved portfolio')
    dashboard.add_argument('--workspace', type=Path, required=True)
    dashboard.add_argument('--open', action='store_true', help='Open the generated dashboard in the default browser')
    dashboard.add_argument('--documents-root', type=Path, help='Include every file from this private document directory')
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
        elif args.command == 'dashboard':
            workspace = args.workspace.resolve()
            path = dashboard_path(workspace)
            with portfolio_lock(workspace):
                data, raw = read_portfolio(workspace)
                if raw is None:
                    raise ValueError('Save a portfolio before enabling its dashboard')
                write_dashboard(path, data, args.documents_root)
            result = {'dashboard': str(path), 'revision': data['revision']}
            if args.open:
                result['browser_opened'] = webbrowser.open(path.as_uri())
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
