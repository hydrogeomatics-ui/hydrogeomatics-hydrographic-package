"""Startup regression tests; no GUI dependencies or real survey data required."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class SourceBundleTests(unittest.TestCase):
    def test_launcher_only_explains_full_download(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'hydrographic_package.py'
            shutil.copyfile(ROOT / script.name, script)
            result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('download is incomplete', result.stderr)
        self.assertIn('main.zip', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_bundle_preflight_accepts_complete_source_tree(self):
        source = (ROOT / 'hydrographic_package.py').read_text(encoding='utf-8')
        preflight = source.split('def _module(', 1)[0]
        # Exercise the same preflight without importing GUI or instrument code.
        exec(compile(preflight, str(ROOT / 'hydrographic_package.py'), 'exec'),
             {'__file__': str(ROOT / 'hydrographic_package.py')})

if __name__ == '__main__':
    unittest.main()
