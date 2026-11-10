"""Synthetic local-file preview checks. No real case files are bundled."""
import json
from pathlib import Path
import tempfile
import unittest

from dashboard_files import TEXT_LIMIT, file_catalogue, local_path
from portfolio_store import DASHBOARD_TAG, dashboard_path, read_portfolio, save_registry, write_dashboard
from validate_package import ROOT, read_json


class DashboardFilesChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.registry = read_json(ROOT / 'evals/fixtures/registry-v0.5-valid.json')
        self.data = {'format_version': '1.0', 'revision': 1, 'registries': [self.registry]}

    def test_catalogue_deduplicates_sources_and_includes_unassigned_files(self):
        root = self.folder / 'documents'
        root.mkdir()
        letter = root / 'Письмо с пробелами.txt'
        letter.write_text('Учебное письмо', encoding='utf-8')
        (root / 'additional.csv').write_text('a,b\n1,2', encoding='utf-8')
        source = self.registry['sources'][0]
        source['locator'] = 'documents/' + letter.name
        self.registry['sources'].append({**source, 'id': 'copy'})
        files, mapping = file_catalogue(self.data, self.folder, root)
        self.assertEqual(len(files), 2)
        self.assertEqual(mapping[('demo-campaign', 'demo-source')], mapping[('demo-campaign', 'copy')])
        self.assertEqual(next(f for f in files if f['name'] == letter.name)['text'], 'Учебное письмо')

    def test_external_urls_and_missing_files_do_not_become_open_links(self):
        self.assertIsNone(local_path('https://example.invalid/private.pdf', self.folder))
        self.assertIsNone(local_path('file://server/share/private.pdf', self.folder))
        self.assertIsNone(local_path('\\\\server\\share\\private.pdf', self.folder))
        self.registry['sources'][0]['locator'] = 'missing.pdf'
        files, _ = file_catalogue(self.data, self.folder)
        self.assertFalse(files[0]['available'])
        self.assertIsNone(files[0]['url'])

    def test_text_preview_has_a_visible_size_limit(self):
        file = self.folder / 'large.txt'
        file.write_bytes(b'a' * (TEXT_LIMIT + 40))
        self.registry['sources'][0]['locator'] = str(file)
        files, _ = file_catalogue(self.data, self.folder)
        self.assertTrue(files[0]['truncated'])
        self.assertEqual(len(files[0]['text']), TEXT_LIMIT)

    def test_preview_content_cannot_escape_json_script(self):
        payload = '</script><script>window.unwanted=true</script>'
        text_file = self.folder / 'example.txt'
        text_file.write_text(payload, encoding='utf-8')
        self.registry['sources'][0]['locator'] = str(text_file)
        panel = self.folder / 'case-dashboard.html'
        write_dashboard(panel, self.data)
        html = panel.read_text(encoding='utf-8')
        self.assertNotIn(payload, html)
        snapshot = json.loads(html.split(DASHBOARD_TAG)[1].split('</script>', 1)[0])
        self.assertEqual(snapshot['viewer_files'][0]['text'], payload)

    def test_document_root_is_remembered_when_registry_is_saved(self):
        workspace = self.folder / 'portfolio.json'
        root = self.folder / 'archive'
        root.mkdir()
        (root / 'unassigned.txt').write_text('Учебное приложение', encoding='utf-8')
        saved = save_registry(workspace, self.registry, 'missing')
        data, _ = read_portfolio(workspace)
        panel = dashboard_path(workspace)
        write_dashboard(panel, data, root)
        self.registry['cases'][0]['title'] = 'Обновлённое учебное дело'
        result = save_registry(workspace, self.registry, saved['sha256'])
        self.assertIn('dashboard', result)
        snapshot = json.loads(panel.read_text(encoding='utf-8').split(DASHBOARD_TAG)[1].split('</script>', 1)[0])
        self.assertIn('unassigned.txt', [f['name'] for f in snapshot['viewer_files']])


if __name__ == '__main__':
    unittest.main()
