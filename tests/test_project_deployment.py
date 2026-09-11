"""Regression checks for integrity validation before privileged cloud deployment."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os

SPEC = importlib.util.spec_from_file_location(
    'project_deploy', Path(__file__).resolve().parents[1]/'scripts/deploy_project_hub.py')
deployment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deployment)


class ReleaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'example').mkdir()
        (self.root/'example/index.html').write_text('verified article')
        self.manifest = dict(format=2, projects=1, paths=['example'], script_hashes=[],
                             files={'example/index.html': hashlib.sha256(b'verified article').hexdigest()})
        self.save()

    def save(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))

    def test_valid_release(self):
        self.assertEqual(deployment.validate_source(self.root), self.manifest)

    def test_modified_asset_rejected(self):
        (self.root/'example/index.html').write_text('modified after review')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            deployment.validate_source(self.root)

    def test_unlisted_asset_rejected(self):
        (self.root/'secret.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            deployment.validate_source(self.root)

    def test_nested_manifest_is_not_exempt_from_inventory(self):
        (self.root/'example/manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'inventory mismatch'):
            deployment.validate_source(self.root)

    def test_pagefind_runtime_and_image_provenance_are_publishable(self):
        for relative, data in [('wasm.unknown.pagefind', b'wasm fixture'),
                               ('README.md', b'Public image provenance')]:
            (self.root/relative).write_bytes(data)
            self.manifest['files'][relative] = hashlib.sha256(data).hexdigest()
        self.save()
        self.assertEqual(deployment.validate_source(self.root), self.manifest)

    def test_project_path_traversal_rejected(self):
        self.manifest['paths'] = ['../../outside']
        self.save()
        with self.assertRaisesRegex(ValueError, 'Missing project page'):
            deployment.validate_source(self.root)

    def test_csp_injection_rejected(self):
        self.manifest['script_hashes'] = ["'unsafe-inline'"]
        self.save()
        with self.assertRaisesRegex(ValueError, 'Invalid inline script CSP'):
            deployment.validate_source(self.root)

    def test_admin_policy_is_not_used_for_blog(self):
        admin, blog = deployment.BLOCK.split('handle_path /projects/*')
        self.assertIn("'unsafe-eval'", admin)
        self.assertNotIn("'unsafe-eval'", blog)
        self.assertIn("'wasm-unsafe-eval'", blog)

    @unittest.skipUnless(os.name == 'posix', 'Privileged deployment uses POSIX symlinks and ownership')
    def test_failed_public_check_restores_config_and_previous_release(self):
        original = b'whm12.art {\n\treverse_proxy https://existing.example\n}\n'
        config = self.root/'Caddyfile'
        config.write_bytes(original)
        source = self.root/'source'
        source.mkdir()
        (source/'index.html').write_text('verified article')
        manifest = dict(format=2, projects=1, paths=['.'], script_hashes=[],
                        files={'index.html': hashlib.sha256(b'verified article').hexdigest()})
        (source/'manifest.json').write_text(json.dumps(manifest))
        base = self.root/'installed'
        previous = base/'previous'
        previous.mkdir(parents=True)
        (base/'current').symlink_to(previous)
        with patch.multiple(deployment, BASE=base, CADDYFILE=config, SOURCE_BASE=self.root,
                            BACKUPS=self.root/'backups'), \
             patch.object(deployment.os, 'geteuid', return_value=0), \
             patch.object(deployment.socket, 'gethostname', return_value='VM-0-12-ubuntu'), \
             patch.object(deployment.os, 'chown'), \
             patch.object(deployment, 'run') as run, \
             patch.object(deployment, 'check_public', side_effect=RuntimeError('broken asset')):
            with self.assertRaisesRegex(RuntimeError, 'broken asset'):
                deployment.deploy(source, hashlib.sha256(original).hexdigest(), False)
        self.assertEqual(config.read_bytes(), original)
        self.assertEqual((base/'current').resolve(), previous)
        self.assertEqual(sum(call.args[0] == ['systemctl','reload','caddy.service']
                             for call in run.call_args_list), 2)


if __name__ == '__main__':
    unittest.main()
