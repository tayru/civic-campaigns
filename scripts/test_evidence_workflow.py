"""Synthetic checks for document evidence, private storage and dashboard updates."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from portfolio_store import DASHBOARD_TAG, dashboard_path, fingerprint, read_portfolio, save_registry, write_dashboard
from validate_package import ROOT, EXTENDED_COLLECTIONS, read_json, validate_case


class EvidenceWorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.workspace = Path(self.folder.name) / 'portfolio.json'
        self.registry = read_json(ROOT / 'evals/fixtures/registry-v0.5-valid.json')
        self.registry['schema_version'] = '0.6'
        self.registry.update({name: [] for name in EXTENDED_COLLECTIONS})
        self.registry['documents'] = [dict(id='document', source_id='demo-source', title='Учебное письмо',
            case_id='demo-case', thread_id='demo-thread', page_start=1, page_end=2,
            duplicate_of=None, applicant_relation='unknown')]
        self.registry['intake'] = [dict(id='intake', source_id='demo-source', document_ids=['document'],
            status='saved', notes='Синтетический пример', saved_revision=None)]
        self.schema = read_json(ROOT / 'assets/case-schema.json')

    def test_intake_receipt_and_replay(self):
        save_registry(self.workspace, self.registry, 'missing')
        state, raw = read_portfolio(self.workspace)
        self.assertEqual(state['registries'][0]['intake'][0]['saved_revision'], 1)
        repeated = save_registry(self.workspace, state['registries'][0], fingerprint(self.workspace))
        self.assertFalse(repeated['changed'])
        self.assertEqual(self.workspace.read_bytes(), raw)
        self.assertFalse(save_registry(self.workspace, self.registry, fingerprint(self.workspace))['changed'])

    def test_received_attachment_needs_real_source(self):
        self.registry['attachments'] = [dict(id='attachment', document_id='document', direction='response',
            filename='example.pdf', status='received', received_source_id=None, notes='')]
        with self.assertRaisesRegex(ValueError, 'own source'):
            validate_case(self.registry, self.schema)

    def test_document_duplicates_cannot_cycle(self):
        self.registry['documents'][0]['duplicate_of'] = 'document'
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            validate_case(self.registry, self.schema)

    def test_quarter_keeps_precision_without_a_deadline(self):
        self.registry['evidence_records'] = [dict(id='plan',case_id='demo-case',thread_id='demo-thread',
            document_id='document',kind='plan_period',value='2027-Q2',precision='quarter',location='Стр. 1',conflict_group=None)]
        validate_case(self.registry, self.schema)
        self.registry['evidence_records'][0]['value'] = '2027-Q9'
        with self.assertRaisesRegex(ValueError, 'quarter'):
            validate_case(self.registry, self.schema)

    def test_private_storage_inside_skill_is_rejected_before_writing(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            save_registry(ROOT / 'private' / 'portfolio.json', self.registry, 'missing')

    def test_save_refreshes_dashboard_and_escapes_document_text(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        state, _ = read_portfolio(self.workspace)
        panel = dashboard_path(self.workspace)
        write_dashboard(panel, state)
        updated = state['registries'][0]
        injection = '</script><script>throw Error("must stay text")</script>'
        updated['cases'][0]['title'] = injection
        result = save_registry(self.workspace, updated, first['sha256'])
        html = panel.read_text(encoding='utf-8')
        self.assertNotIn(injection, html)
        snapshot = json.loads(html.split(DASHBOARD_TAG)[1].split('</script>', 1)[0])
        self.assertEqual(snapshot['revision'], result['revision'])
        self.assertEqual(snapshot['registries'][0]['cases'][0]['title'], injection)
        self.assertNotIn('locator', snapshot['registries'][0]['sources'][0])

    def test_failed_dashboard_can_be_retried_after_successful_save(self):
        first = save_registry(self.workspace, self.registry, 'missing')
        state, _ = read_portfolio(self.workspace)
        write_dashboard(dashboard_path(self.workspace), state)
        updated = state['registries'][0]
        updated['cases'][0]['title'] = 'Уточнённый учебный объект'
        with patch('portfolio_store.write_dashboard', side_effect=OSError('simulated failure')):
            result = save_registry(self.workspace, updated, first['sha256'])
        self.assertIn('dashboard_error', result)
        self.assertEqual(read_portfolio(self.workspace)[0]['revision'], 2)
        retry = save_registry(self.workspace, updated, result['sha256'])
        self.assertFalse(retry['changed'])
        self.assertIn('dashboard', retry)

    def test_intake_history_cannot_disappear_or_downgrade(self):
        save_registry(self.workspace, self.registry, 'missing')
        state, _ = read_portfolio(self.workspace)
        updated = copy.deepcopy(state['registries'][0])
        updated['intake'] = []
        with self.assertRaisesRegex(ValueError, 'remove records'):
            save_registry(self.workspace, updated, fingerprint(self.workspace))
        legacy = read_json(ROOT / 'evals/fixtures/registry-v0.5-valid.json')
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            save_registry(self.workspace, legacy, fingerprint(self.workspace))


if __name__ == '__main__':
    unittest.main()
