import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from team_update import allowed, inspect_archive, apply_files, restore_files

class TeamUpdateTests(unittest.TestCase):
    def test_protected_and_windows_escape_paths_rejected(self):
        for name in ['API/.env','data/canvas.json','assets/a.png','output/a.png','../main.py',
                     '/main.py','static/../API/a','static/a:ads','static/a.','static/CON','static/a\\b','PRIVATE-API-KEYS.txt']:
            self.assertFalse(allowed(name), name)
        self.assertTrue(allowed('main.py'))
        self.assertTrue(allowed('static/js/smart-canvas.js'))

    def make_zip(self, root, extra=None):
        files={'main.py':b'pass\n','VERSION':b'2026.09.14.1','requirements.txt':b'fastapi\n','static/index.html':b'new'}
        files.update(extra or {})
        manifest={'format':1,'version':'2026.09.14.1','files':[
            {'path':p,'sha256':hashlib.sha256(v).hexdigest()} for p,v in files.items()]}
        archive=root/'release.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for p,v in files.items(): z.writestr(p,v)
            z.writestr('release-manifest.json',json.dumps(manifest))
        return archive

    def test_archive_rejects_protected_payload_even_with_valid_hash(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): inspect_archive(self.make_zip(Path(d),{'API/.env':b'secret'}))

    def test_archive_rejects_modified_content(self):
        with tempfile.TemporaryDirectory() as d:
            archive=self.make_zip(Path(d))
            with zipfile.ZipFile(archive,'a') as z: z.writestr('unlisted.py',b'pass')
            with self.assertRaises(ValueError): inspect_archive(archive)

    def test_update_preserves_user_files_and_rollback_removes_new_code_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'app'; root.mkdir(); backup=Path(d)/'backup'
            (root/'main.py').write_bytes(b'old')
            protected={'data/canvas.json':b'user','API/.env':b'key','assets/a.png':b'pixels',
                       'static/system-prompts/custom.md':b'custom','static/runninghub/api_providers.json':b'customconfig'}
            for p,v in protected.items():
                f=root/p; f.parent.mkdir(parents=True,exist_ok=True); f.write_bytes(v)
            files={'main.py':b'new','new_module.py':b'pass','static/system-prompts/custom.md':b'default',
                   'static/runninghub/api_providers.json':b'default'}
            apply_files(root,files,backup)
            self.assertEqual((root/'main.py').read_bytes(),b'new')
            for p,v in protected.items(): self.assertEqual((root/p).read_bytes(),v)
            restore_files(root,backup)
            self.assertEqual((root/'main.py').read_bytes(),b'old')
            self.assertFalse((root/'new_module.py').exists())
            for p,v in protected.items(): self.assertEqual((root/p).read_bytes(),v)

    def test_partial_write_failure_restores_previous_program(self):
        from unittest.mock import patch
        import team_update
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'app';root.mkdir();(root/'main.py').write_bytes(b'old')
            original=team_update.atomic_write
            def fail(path,content):
                if Path(path)==root/'new_module.py': raise OSError('disk full')
                return original(path,content)
            with patch.object(team_update,'atomic_write',side_effect=fail):
                with self.assertRaises(OSError): apply_files(root,{'main.py':b'new','new_module.py':b'pass'},Path(d)/'backup')
            self.assertEqual((root/'main.py').read_bytes(),b'old')

    def test_worker_startup_failure_restores_code_and_preserves_data(self):
        from unittest.mock import patch
        import team_update_worker as worker
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'app';root.mkdir();(root/'main.py').write_bytes(b'old');(root/'VERSION').write_text('2026.09.13.1')
            state=root/'data/team-update/status.json';state.parent.mkdir(parents=True)
            state.write_text(json.dumps({'operation':'test','phase':'prepared'}))
            user=root/'data/canvas.json';user.write_bytes(b'important canvas')
            archive=self.make_zip(Path(d))
            with patch.object(worker,'installation_task',return_value={'TaskName':'test'}),patch.object(worker,'task_action'),patch.object(worker,'healthy',side_effect=[False,True]),patch.object(worker.time,'sleep'):
                worker.run(root,archive,59998)
            self.assertEqual((root/'main.py').read_bytes(),b'old')
            self.assertEqual(user.read_bytes(),b'important canvas')
            self.assertEqual(json.loads(state.read_text())['phase'],'rolled_back')

class TeamUpdateAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_update_does_not_start_worker_and_legacy_rollback_is_removed(self):
        import httpx
        from fastapi import FastAPI
        from team_update_api import install_team_updates
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'VERSION').write_text('2026.09.14.1')
            app=FastAPI()
            @app.post('/api/update-rollback')
            def dangerous_legacy(): raise AssertionError('Legacy path must be retired')
            install_team_updates(app,root,lambda:True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
                self.assertEqual((await client.post('/api/team-update/apply')).status_code,409)
                self.assertEqual((await client.post('/api/update-rollback')).status_code,404)
            self.assertFalse((root/'data/team-update/status.json').exists())

    async def test_maintenance_blocks_mutations_but_allows_health_and_status(self):
        import httpx
        from fastapi import FastAPI
        from team_update_api import install_team_updates
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'VERSION').write_text('2026.09.14.1')
            status=root/'data/team-update/status.json';status.parent.mkdir(parents=True);status.write_text('{"phase":"applying"}')
            app=FastAPI()
            @app.post('/api/canvas-video')
            def generate(): raise AssertionError('Must not generate during maintenance')
            install_team_updates(app,root,lambda:False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
                self.assertEqual((await client.post('/api/canvas-video')).status_code,503)
                self.assertEqual((await client.get('/api/team-update/health')).status_code,200)
                self.assertEqual((await client.get('/api/team-update/status')).json()['phase'],'applying')
