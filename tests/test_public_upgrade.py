import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class PublicUpgradeTests(unittest.TestCase):
    def test_legacy_health_uses_homepage_even_if_old_health_route_exists(self):
        import team_update_worker as worker
        def response(request,timeout):
            if str(request).endswith('/'):
                value=io.BytesIO(b'old homepage');value.status=200;return value
            return io.BytesIO(b'{"version":"2026.09.14.1"}')
        with patch('urllib.request.urlopen',side_effect=response):
            self.assertTrue(worker.healthy(3098,None,1))

    def test_public_check_without_gh(self):
        import asyncio
        import httpx
        from fastapi import FastAPI
        import team_update_api
        payload={'tag_name':'v2026.09.14.2','assets':[{'name':'infinite-canvas-update.zip'},{'name':'infinite-canvas-update.zip.sha256'}]}
        async def check(root):
            app=FastAPI();team_update_api.install_team_updates(app,root,lambda:False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
                return (await client.get('/api/team-update/check')).json()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'VERSION').write_text('2026.09.14.1')
            with patch('urllib.request.urlopen',return_value=io.BytesIO(json.dumps(payload).encode())),patch('subprocess.run',side_effect=OSError('gh unavailable')):
                result=asyncio.run(check(root))
            self.assertTrue(result['reachable'],result)
            self.assertTrue(result['update_available'])

    def test_transition_refuses_pending_canvas_without_changing_it(self):
        import team_transition
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);f=root/'data/canvases/a.json';f.parent.mkdir(parents=True)
            f.write_text('{"nodes":[{"pending":true}]}')
            with self.assertRaisesRegex(RuntimeError,'unfinished'):
                team_transition.check_saved_tasks(root)
            self.assertEqual(f.read_text(),'{"nodes":[{"pending":true}]}')

    def test_legacy_rollback_without_version_file(self):
        import team_update_worker as worker
        from test_team_update import TeamUpdateTests
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'app';root.mkdir();(root/'main.py').write_bytes(b'old')
            state=root/'data/team-update/status.json';state.parent.mkdir(parents=True)
            state.write_text(json.dumps({'operation':'legacy','phase':'prepared','legacy':True}))
            archive=TeamUpdateTests().make_zip(Path(d))
            with patch.object(worker,'installation_task',return_value={'TaskName':'test'}),patch.object(worker,'task_action'),patch.object(worker,'healthy',side_effect=[False,True]),patch.object(worker.time,'sleep'):
                worker.run(root,archive,59998)
            self.assertEqual(json.loads(state.read_text())['phase'],'rolled_back')
            self.assertEqual((root/'main.py').read_bytes(),b'old')
            self.assertFalse((root/'VERSION').exists())
