"""Local persistence checks with synthetic registries and fresh CLI processes."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from portfolio_store import describe, fingerprint, read_portfolio, save_registry
from validate_package import ROOT, read_json


class PortfolioChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.workspace = self.directory / 'portfolio.json'
        self.registry = read_json(ROOT / 'evals/fixtures/registry-v0.5-valid.json')

    def cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(ROOT / 'scripts/portfolio_store.py'), *arguments],
            cwd=self.directory, capture_output=True, text=True, encoding='utf-8', timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def cli_save(self, registry):
        candidate = self.directory / 'candidate.json'
        candidate.write_text(json.dumps(registry, ensure_ascii=False), encoding='utf-8')
        state = self.cli('status', '--workspace', str(self.workspace))
        return self.cli(
            'save', '--workspace', str(self.workspace), '--registry', str(candidate),
            '--expected-sha256', state['sha256'],
        )

    def test_multiple_rounds_and_fresh_process_resume(self):
        individual = copy.deepcopy(self.registry)
        individual['campaign']['id'] = 'personal-document'
        individual['cases'][0].update(title='Исправление справки', applicant_type='individual')
        collective = copy.deepcopy(self.registry)
        collective['campaign']['id'] = 'collective-lighting'
        collective['cases'][0]['title'] = 'Освещение двора'
        campaign = copy.deepcopy(self.registry)
        campaign['campaign']['id'] = 'library-campaign'
        campaign['cases'][0].update(title='Сохранение библиотеки', status='closed')
        second_case = copy.deepcopy(campaign['cases'][0])
        second_case.update(id='access-case', title='Доступность помещения', status='unknown')
        campaign['cases'].append(second_case)
        for registry in [individual, collective, campaign]:
            self.cli_save(registry)
        before, _ = read_portfolio(self.workspace)
        self.assertEqual(len(self.cli('status', '--workspace', str(self.workspace))['cases']), 4)

        # Two incoming documents update two independent registries. Equal local
        # IDs across registries are intentional; their registry IDs disambiguate.
        for registry, name in [(individual, 'personal'), (collective, 'collective')]:
            document = self.directory / f'{name}-reply.txt'
            document.write_text(f'Учебный ответ {name}', encoding='utf-8')
            content_hash = self.cli('fingerprint', str(document))['sha256']
            source = copy.deepcopy(registry['sources'][0])
            source.update(id='reply-source', title='Учебный ответ', locator=str(document), content_sha256=content_hash)
            registry['sources'].append(source)
            event = copy.deepcopy(registry['events'][0])
            event.update(id='reply-event', event_type='replied', source_ids=['reply-source'], reply_source_id='reply-source')
            registry['events'].append(event)
            registry['threads'][0]['status'] = 'answered'
            registry['cases'][0]['last_verified_on'] = '2026-11-10'
            self.cli_save(registry)

        first_import = self.workspace.read_bytes()
        repeated = self.cli_save(collective)
        self.assertFalse(repeated['changed'])
        self.assertEqual(self.workspace.read_bytes(), first_import)

        # Correct a fact without erasing either the incoming answer or its dates.
        individual['cases'][0]['requested_change'] = 'Исправить сведения и выдать исправленный документ'
        correction = copy.deepcopy(individual['events'][0])
        correction.update(id='correction', event_type='other', summary='Уточнено требование по учебному ответу', source_ids=['reply-source'])
        individual['events'].append(correction)
        self.cli_save(individual)

        resumed = self.cli('status', '--workspace', str(self.workspace))
        after, _ = read_portfolio(self.workspace)
        self.assertEqual(after['registries'], [individual, collective, campaign])
        self.assertEqual(resumed['registry_count'], 3)
        self.assertEqual(resumed['revision'], 6)
        self.assertEqual(after['registries'][2], before['registries'][2])
        for previous, current in zip(before['registries'], after['registries']):
            self.assertEqual(current['threads'][0]['deadline'], previous['threads'][0]['deadline'])
            self.assertEqual(current['actions'][0]['due_on'], previous['actions'][0]['due_on'])
            self.assertEqual(current['commitments'][0]['due_on'], previous['commitments'][0]['due_on'])
        self.assertEqual(sum(event['event_type'] == 'replied' for registry in after['registries'] for event in registry['events']), 2)

    def test_legacy_registry_is_preserved(self):
        legacy = read_json(ROOT / 'evals/fixtures/registry-valid.json')
        self.cli_save(legacy)
        self.assertEqual(read_portfolio(self.workspace)[0]['registries'], [legacy])

    def test_stale_writer_cannot_overwrite(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        updated = copy.deepcopy(self.registry)
        updated['cases'][0]['status'] = 'waiting'
        save_registry(self.workspace, updated, first['sha256'])
        saved = self.workspace.read_bytes()
        self.registry['cases'][0]['status'] = 'closed'
        with self.assertRaisesRegex(ValueError, 'changed'):
            save_registry(self.workspace, self.registry, first['sha256'])
        self.assertEqual(self.workspace.read_bytes(), saved)

    def test_backup_is_the_exact_previous_state(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        previous = self.workspace.read_bytes()
        self.registry['cases'][0]['status'] = 'waiting'
        second = save_registry(self.workspace, self.registry, first['sha256'])
        backup = Path(second['backup'])
        self.assertEqual(backup.read_bytes(), previous)
        self.assertEqual(read_portfolio(backup)[0]['revision'], 1)

    def test_interrupted_replace_preserves_current_and_allows_retry(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        previous = self.workspace.read_bytes()
        self.registry['cases'][0]['status'] = 'waiting'
        with patch('portfolio_store.os.replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError):
                save_registry(self.workspace, self.registry, first['sha256'])
        self.assertEqual(self.workspace.read_bytes(), previous)
        self.assertFalse(self.workspace.with_name('portfolio.json.lock').exists())
        self.assertEqual(list(self.directory.glob('*.tmp')), [])
        save_registry(self.workspace, self.registry, first['sha256'])
        self.assertEqual(read_portfolio(self.workspace)[0]['revision'], 2)

    def test_existing_lock_blocks_another_writer(self):
        lock = self.workspace.with_name('portfolio.json.lock')
        lock.write_text('another writer', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            save_registry(self.workspace, self.registry, 'missing')
        self.assertFalse(self.workspace.exists())
        self.assertEqual(lock.read_text(encoding='utf-8'), 'another writer')

    def test_removing_history_or_cases_is_rejected(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        previous = self.workspace.read_bytes()
        self.registry['events'] = []
        with self.assertRaisesRegex(ValueError, 'remove records'):
            save_registry(self.workspace, self.registry, first['sha256'])
        self.assertEqual(self.workspace.read_bytes(), previous)

    def test_invalid_input_does_not_modify_portfolio(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        previous = self.workspace.read_bytes()
        self.registry['cases'][0]['applicant_actor_ids'] = ['missing']
        with self.assertRaises(ValueError):
            save_registry(self.workspace, self.registry, first['sha256'])
        self.assertEqual(self.workspace.read_bytes(), previous)

    def test_status_rejects_duplicate_registry_ids(self):
        data = {'format_version': '1.0', 'revision': 1, 'registries': [self.registry, self.registry]}
        self.workspace.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Duplicate registry'):
            describe(self.workspace)

    def test_template_id_cannot_be_saved(self):
        self.registry['campaign']['id'] = 'campaign-draft'
        with self.assertRaisesRegex(ValueError, 'template registry id'):
            save_registry(self.workspace, self.registry, 'missing')

    def test_old_client_cannot_downgrade_saved_registry(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        previous = self.workspace.read_bytes()
        legacy = read_json(ROOT / 'evals/fixtures/registry-valid.json')
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            save_registry(self.workspace, legacy, first['sha256'])
        self.assertEqual(self.workspace.read_bytes(), previous)

    def test_renamed_copy_has_the_same_fingerprint(self):
        first = self.directory / 'first.txt'
        renamed = self.directory / 'renamed.txt'
        first.write_bytes(b'synthetic document')
        renamed.write_bytes(first.read_bytes())
        self.assertEqual(fingerprint(first), fingerprint(renamed))


if __name__ == '__main__':
    unittest.main(verbosity=2)
