import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('project_hub', Path(__file__).resolve().parents[1]/'scripts/build_project_hub.py')
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)

class ProjectHubTests(unittest.TestCase):
    def test_rejects_unsafe_urls_and_slugs(self):
        for value in ['javascript:alert(1)', 'https://token@example.org/']:
            with self.assertRaises(ValueError): hub.safe_url(value)
        with self.assertRaises(ValueError): hub.slug_for('repo','../outside')
        self.assertEqual(hub.slug_for('-3D-'),'3d')

    def test_removed_pages_are_not_left_in_public_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'site/projects'; source.mkdir(parents=True)
            (root/'docs/vendor').mkdir(parents=True)
            for name in ['hub.css','hub.js','favicon.svg']: (source/name).write_text('fixture')
            (source/'editorial.json').write_text('{}')
            def catalog(name, private=False):
                data={'synced_at':'2026-09-10', 'projects':[{'name':name,'url':f'https://github.com/{hub.OWNER}/{name}', 'private':private, 'description':'<script>bad</script>', 'language':'Python', 'updated':'2026-09-10'}]}
                (source/'catalog.json').write_text(json.dumps(data))
            with patch.multiple(hub,ROOT=root,SOURCE=source,OUT=root/'docs/projects'):
                catalog('first'); hub.build()
                self.assertIn('&lt;script&gt;', (hub.OUT/'first/index.html').read_text(encoding='utf-8'))
                catalog('second'); hub.build()
                self.assertFalse((hub.OUT/'first').exists())
                catalog('second',True)
                with self.assertRaises(ValueError): hub.build()
                self.assertTrue((hub.OUT/'second/index.html').exists())

if __name__=='__main__': unittest.main()
