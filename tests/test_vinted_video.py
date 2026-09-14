import json
import tempfile
import unittest
import sys
from unittest.mock import patch
from pathlib import Path

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vinted_video import VintedVideoService


class VintedVideoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.calls = []
        self.submit_timeout = False
        self.preview = False
        self.video = b'\x00\x00\x00\x18ftypmp42' + b'fixture-video'
        self.provider = {'id': 'vinted', 'base_url': 'https://vinted.cam', 'enabled': True,
                         'video_models': ['seedance2.0fast', 'seedance2.0mini', 'seedance2.5']}
        self.service = self.make_service()

    def make_service(self):
        return VintedVideoService(self.directory / 'state', lambda _: self.provider, lambda _: 'test-private-key',
            lambda ref: 'data:image/png;base64,aW1hZ2U=', lambda name: str(self.directory / name),
            lambda name: '/assets/output/' + name,
            client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.upstream)))

    def upstream(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, str(request.url), body, dict(request.headers)))
        if request.url.path == '/download':
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, content=self.video, headers={'Content-Type': 'video/mp4', 'X-Video-Quality': 'original'})
        self.assertEqual(request.headers['authorization'], 'Bearer test-private-key')
        if request.url.path == '/v1/files':
            self.assertEqual(body, {'image_b64': 'data:image/png;base64,aW1hZ2U='})
            return httpx.Response(201, json={'image_id': 'image-one', 'expires_at': 9999999999})
        if request.method == 'POST' and request.url.path == '/v1/videos':
            if self.submit_timeout:
                self.submit_timeout = False
                raise httpx.ReadTimeout('uncertain submission', request=request)
            self.assertEqual(body, {'model':'seedance2.0fast', 'prompt':'Product on a table', 'duration':5,
                'ratio':'9:16', 'resolution':'720p', 'camera_movement':'fixed', 'image_ids':['image-one']})
            return httpx.Response(202, json={'id':'job-one', 'status':'queued'})
        if request.url.path == '/v1/videos/job-one':
            return httpx.Response(200, json={'id':'job-one','status':'succeeded','download_url':'https://vinted.cam/preview'})
        if request.url.path == '/v1/videos/job-one/signed_url':
            self.assertEqual(request.url.params['download'], '1')
            return httpx.Response(200, json={'url':'/download?signature=keep-me&quality=original',
                'quality':'preview' if self.preview else 'original', 'expires_at':9999999999})
        raise AssertionError(str(request.url))

    def payload(self, **changes):
        return dict({'provider_id':'vinted','vinted_operation_id':'a'*32, 'model':'seedance2.0fast',
            'prompt':'Product on a table','duration':5,'aspect_ratio':'9:16','resolution':'720p',
            'images':[{'url':'/assets/product.png'}], 'camerafixed':True}, **changes)

    async def test_upload_async_submit_and_original_download_are_saved_once(self):
        result = await self.service.generate(self.payload())
        self.assertEqual(result['videos'], ['/assets/output/vinted_' + 'a'*32 + '.mp4'])
        self.assertEqual((self.directory / ('vinted_' + 'a'*32 + '.mp4')).read_bytes(), self.video)
        self.assertEqual(self.calls[1][3]['idempotency-key'], 'a'*32)
        before = len(self.calls)
        self.assertEqual(await self.make_service().resume('a'*32), result)
        self.assertEqual(len(self.calls), before)
        record = (self.directory / 'state' / ('a'*32 + '.json')).read_text()
        self.assertNotIn('test-private-key', record)
        self.assertNotIn('signature=keep-me', record)

    async def test_uncertain_submit_resumes_same_operation_and_uploaded_image(self):
        self.submit_timeout = True
        with self.assertRaises(HTTPException):
            await self.service.generate(self.payload())
        await self.make_service().resume('a'*32)
        submissions = [c for c in self.calls if c[0] == 'POST' and c[1].endswith('/v1/videos')]
        self.assertEqual(len(submissions), 2)
        self.assertEqual(submissions[0][2], submissions[1][2])
        self.assertEqual(submissions[0][3]['idempotency-key'], submissions[1][3]['idempotency-key'])
        self.assertEqual(len([c for c in self.calls if c[1].endswith('/v1/files')]), 1)

    async def test_preview_rejection_keeps_job_and_recovery_never_resubmits(self):
        self.preview = True
        with self.assertRaises(HTTPException):
            await self.service.generate(self.payload())
        self.assertFalse(list(self.directory.glob('*.mp4')))
        self.preview = False
        await self.make_service().resume('a'*32)
        self.assertEqual(len([c for c in self.calls if c[0] == 'POST' and c[1].endswith('/v1/videos')]), 1)

    async def test_invalid_model_parameters_and_unsupported_refs_never_submit(self):
        for changes in ({'model':'seedance2.0mini','duration':15}, {'model':'seedance2.5','duration':5},
                        {'resolution':'1080p'}, {'aspect_ratio':'9:21'}, {'videos':['/assets/a.mp4']},
                        {'images':[{'url':'/assets/a.png','role':'last_frame'}]}, {'images':[{'url':'/assets/a.png'}]*10}):
            with self.subTest(changes=changes), self.assertRaises(HTTPException):
                await self.service.generate(self.payload(**changes))
        self.assertEqual(self.calls, [])

    async def test_canvas_video_route_uses_vinted_and_recovers_saved_result(self):
        import main
        with patch.object(main, '_vinted_service', self.service), patch.object(main, 'get_api_provider', return_value=self.provider):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                response = await client.post('/api/canvas-video', json=self.payload())
                self.assertEqual(response.status_code, 200, response.text)
                restored = await client.post('/api/vinted/operations/' + 'a'*32 + '/resume')
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertEqual(response.json(), restored.json())
        self.assertEqual(len([c for c in self.calls if c[0] == 'POST' and c[1].endswith('/v1/videos')]), 1)
