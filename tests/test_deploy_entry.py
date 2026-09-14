import pathlib
import subprocess
import sys
import unittest


class DeploymentEntryTests(unittest.TestCase):
    def test_check_imports_real_application_without_starting_listener(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, '-B', str(root / 'tools/deploy/serve.py'), '--check'],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('DEPENDENCIES_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
