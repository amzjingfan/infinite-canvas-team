import subprocess
import sys
import unittest
from pathlib import Path


class CanvasAgentStartupTests(unittest.TestCase):
    def test_script_loads_agent_with_isolated_module_search_path(self):
        root = Path(__file__).resolve().parents[1]
        # Unlike unittest imports, the bundled Windows runtime omits the project
        # directory. Load the real entry script without a test-added sys.path.
        result = subprocess.run(
            [sys.executable, "-I", "-c",
             "import runpy; app = runpy.run_path('main.py')['app']; "
             "assert '/api/canvases/{canvas_id}/agent/conversations' "
             "in app.openapi()['paths']"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
