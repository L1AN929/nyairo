"""Reject a hash-valid release that registers as a different product version."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('metadata_verifier', ROOT / 'scripts/verify_manifest.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


class ReleaseMetadata(unittest.TestCase):
    def fixture(self, root, declared):
        (root / 'scripts').mkdir()
        (root / 'scripts/release.json').write_text(json.dumps({'version': declared}), encoding='utf8')
        files = {'scripts/release.json': hashlib.sha256((root / 'scripts/release.json').read_bytes()).hexdigest()}
        lines = ''.join(f'{digest}  {name}\n' for name, digest in sorted(files.items()))
        (root / 'MANIFEST.json').write_text(json.dumps({
            'version': '0.2.0-rc3', 'status': 'SOURCE_REVIEWED_TARGETED_TESTS_PASSED',
            'files': files, 'file_count': len(files), 'tree_sha256': hashlib.sha256(lines.encode()).hexdigest()
        }), encoding='utf8')

    def test_shipped_archive_version_matches_update_registration(self):
        manifest = json.loads((ROOT / 'MANIFEST.json').read_text(encoding='utf8'))
        release = json.loads((ROOT / 'scripts/release.json').read_text(encoding='utf8'))
        self.assertEqual(release['version'], 'v' + manifest['version'].removeprefix('v'))

    def test_hash_valid_archive_with_stale_update_version_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'v0.1.0-rc6')
            result = verifier.verify(root)
            self.assertFalse(result['valid'])
            self.assertEqual(result['changed_or_missing'], ['release_version_mismatch'])
            self.assertEqual(result['unexpected_files'], [])

    def test_matching_version_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root, 'v0.2.0-rc3')
            self.assertTrue(verifier.verify(root)['valid'])


if __name__ == '__main__':
    unittest.main()
