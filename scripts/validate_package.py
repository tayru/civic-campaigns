#!/usr/bin/env python3
"""Read-only package/schema checks. Does not verify facts, law or anonymisation."""
from pathlib import Path
import argparse
import json
import re
import sys
try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    raise SystemExit('NOT RUN: Python package jsonschema is required; no dependency was installed.')

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_case(data, schema):
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = [f'{"/".join(map(str, e.absolute_path))}: {e.message}' for e in validator.iter_errors(data)]
    if errors:
        raise ValueError('\n'.join(errors[:12]))
    collections = ['cases', 'actors', 'sources', 'claims', 'threads', 'events', 'commitments', 'outcomes', 'actions']
    indexes = {}
    for name in collections:
        rows = data[name]
        indexes[name] = {row['id']: row for row in rows}
        if len(indexes[name]) != len(rows):
            raise ValueError(f'Duplicate ids in {name}')

    def link(value, collection, field):
        if value is not None and value not in indexes[collection]:
            raise ValueError(f'Unknown {field}: {value}')

    def sources(ids):
        for value in ids:
            link(value, 'sources', 'source_id')

    def reviewed(ids):
        return any(indexes['sources'][s]['availability'] == 'reviewed' for s in ids)

    for name in collections:
        for row in data[name]:
            sources(row.get('source_ids', []))
    for row in data['claims']:
        if row['epistemic_status'] in ['supported', 'reported', 'conflicting'] and not row['source_ids']:
            raise ValueError('A sourced claim status requires source_ids')
        if row['epistemic_status'] == 'supported' and not reviewed(row['source_ids']):
            raise ValueError('A supported claim requires a reviewed source')
    for row in data['threads']:
        link(row['case_id'], 'cases', 'case_id')
        link(row['recipient_actor_id'], 'actors', 'recipient_actor_id')
        link(row['next_action_id'], 'actions', 'next_action_id')
        sources(row['registration_source_ids'])
        if row['status'] == 'registered' and (not row['registered_on'] or not reviewed(row['registration_source_ids'])):
            raise ValueError('Registered status requires a date and reviewed source')
        deadline = row['deadline']
        sources(deadline['source_ids'])
        if deadline['kind'] == 'unknown' and deadline['due_on'] is not None:
            raise ValueError('Unknown deadline cannot carry a due date')
        if deadline['kind'] in ['legal', 'working'] and (deadline['due_on'] is None or not deadline['basis'].strip()):
            raise ValueError('A dated deadline requires a date and basis')
        if deadline['kind'] == 'legal' and not reviewed(deadline['source_ids']):
            raise ValueError('Legal deadline requires a reviewed source; legal correctness is not validated')
        if row['next_action_id']:
            action = indexes['actions'][row['next_action_id']]
            if action['case_id'] is not None and action['case_id'] != row['case_id']:
                raise ValueError('Thread and next action refer to different cases')
    for row in data['events']:
        link(row['case_id'], 'cases', 'case_id')
        link(row['thread_id'], 'threads', 'thread_id')
        if row['thread_id'] and row['case_id'] and indexes['threads'][row['thread_id']]['case_id'] != row['case_id']:
            raise ValueError('Event and thread refer to different cases')
    for row in data['commitments']:
        link(row['actor_id'], 'actors', 'actor_id')
        if row['status'] in ['agreed', 'formal_decision', 'fulfilled', 'missed', 'withdrawn'] and not row['source_ids']:
            raise ValueError('Commitment status requires a source')
    for row in data['outcomes']:
        link(row['case_id'], 'cases', 'case_id')
        sources(row['attribution_source_ids'])
        if not row['source_ids']:
            raise ValueError('Outcome requires a source; planned outcomes belong in the campaign goal')
        if row['attribution'] != 'unknown' and not row['attribution_source_ids']:
            raise ValueError('Attribution requires sources')
        if row['attribution'] in ['supported', 'directly_documented'] and not reviewed(row['attribution_source_ids']):
            raise ValueError('Supported attribution requires a reviewed source')
    for row in data['actions']:
        link(row['case_id'], 'cases', 'case_id')
        link(row['owner_actor_id'], 'actors', 'owner_actor_id')
    return True


def validate_package(root=ROOT):
    skill = (root / 'SKILL.md').read_text(encoding='utf-8')
    if not skill.startswith('---\n'):
        raise ValueError('Missing frontmatter')
    front = skill.split('---', 2)[1]
    name = re.search(r'^name: (.+)$', front, re.M)
    desc = re.search(r'^description: (.+)$', front, re.M)
    if not name or name.group(1) != 'civic-campaigns-rf' or not desc or not 0 < len(desc.group(1)) <= 1024:
        raise ValueError('Invalid name or description')
    if len(skill.splitlines()) >= 500:
        raise ValueError('SKILL.md too long')
    # Match explicit local file references, relative to each document or package root.
    pattern = r'`([^`\n]+\.(?:md|json|py|yaml))`'
    for doc in root.rglob('*.md'):
        for ref in re.findall(pattern, doc.read_text(encoding='utf-8')):
            if ' ' in ref or ref.startswith(('http:', 'https:', '/')):
                continue
            if not (doc.parent / ref).is_file() and not (root / ref).is_file():
                raise ValueError(f'Broken local reference: {doc.relative_to(root)} -> {ref}')
    for doc in (root / 'references').rglob('*.md'):
        if doc.relative_to(root).as_posix() not in skill:
            raise ValueError(f'Reference missing from SKILL navigation: {doc.name}')
    schema = read_json(root / 'assets/case-schema.json')
    validate_case(read_json(root / 'assets/case-template.json'), schema)
    validate_case(read_json(root / 'evals/fixtures/registry-valid.json'), schema)
    cases = read_json(root / 'evals/cases.json')
    ids = [c['id'] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate evaluation ids')
    for case in cases:
        if set(case) != {'id', 'mode', 'prompt', 'checks'} or not case['prompt'] or not case['checks'] or not all(isinstance(c, str) and c for c in case['checks']):
            raise ValueError(f'Malformed evaluation: {case.get("id")}')
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=Path)
    args = parser.parse_args()
    try:
        count = validate_package()
        if args.case:
            validate_case(read_json(args.case), read_json(ROOT / 'assets/case-schema.json'))
        print(f'PASS: package references, schema, fixtures, {count} evaluation definitions' + ('; supplied case' if args.case else ''))
    except (ValueError, OSError, KeyError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
