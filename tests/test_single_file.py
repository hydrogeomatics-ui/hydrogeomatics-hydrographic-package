"""Verify the single download works without a neighboring modules directory."""
import importlib.util
import runpy
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publication_checks', ROOT / 'scripts/check_publication.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)

class SingleFileTests(unittest.TestCase):
    def test_generated_file_matches_public_sources(self):
        paths = ['hydrographic_package.py', *checks.SOURCE_MODULES.values()]
        files = {name: (ROOT / name).read_text(encoding='utf-8') for name in paths}
        actual = (ROOT / checks.SINGLE_FILE).read_text(encoding='utf-8')
        self.assertEqual(actual, checks.single_file_source(files))
        compile(actual, checks.SINGLE_FILE, 'exec')

    def test_single_file_loads_from_an_isolated_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / checks.SINGLE_FILE
            shutil.copyfile(ROOT / checks.SINGLE_FILE, script)
            namespace = runpy.run_path(str(script), run_name='isolated_download_test')
            self.assertFalse((Path(folder) / 'modules').exists())
            for variable, source in checks.SOURCE_MODULES.items():
                self.assertEqual(namespace[variable], (ROOT / source).read_text(encoding='utf-8'))

if __name__ == '__main__':
    unittest.main()
