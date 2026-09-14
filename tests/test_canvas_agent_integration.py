"""Real Agent routes/planner/adapters with HTTP and public upload boundaries isolated.

ASGITransport deliberately skips lifespan. Graph acknowledgements use saved fixture
nodes; the JavaScript bridge suite and parent browser acceptance test the real host.
"""
import asyncio
import base64
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from email.parser import BytesParser
from email.policy import default
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main


class CanvasAgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        assets = self.root / 'assets'
        output = assets / 'output'
        output.mkdir(parents=True)
        self.providers = [
            {'id': 'deepseek', 'protocol': 'openai', 'enabled': True,
             'base_url': 'https://deepseek.invalid/v1', 'chat_models': ['deepseek-chat']},
            {'id': 'tugo', 'protocol': 'openai', 'enabled': True,
             'base_url': 'https://tugo.invalid/v1', 'chat_models': ['gpt-4o'],
             'image_models': ['gpt-image-2'], 'video_models': ['veo3-fast']},
            {'id': 'autodl', 'protocol': 'autodl', 'enabled': True,
             'video_models': ['minimax-h3']},
        ]
        for name, value in [('CANVAS_DIR', str(self.root)), ('ASSETS_DIR', str(assets)),
                            ('OUTPUT_OUTPUT_DIR', str(output)), ('OUTPUT_DIR', str(output)),
                            ('HISTORY_FILE', str(self.root / 'history.json')), ('GLOBAL_LOOP', None),
                            ('load_api_providers', lambda: deepcopy(self.providers)),
                            ('provider_env_key_value', lambda pid: f'fixture-secret-{pid}')]:
            patcher = patch.object(main, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Both reference and result are valid PNGs, with deliberately different sizes.
        Image.new('RGBA', (12, 12), 'blue').save(output / 'reference.png')
        buffer = BytesIO()
        Image.new('RGBA', (32, 18), 'red').save(buffer, format='PNG')
        self.png = buffer.getvalue()
        self.original = {'id': 'product', 'type': 'smart-image', 'title': 'Product',
                         'text': '', 'images': [{'url': '/assets/output/reference.png',
                                                'kind': 'image', 'name': 'reference.png'}]}
        self.canvas = {'id': 'integration', 'nodes': [deepcopy(self.original)], 'connections': []}
        self.save_canvas()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)
        self.requests, self.unexpected, self.uploads = [], [], []
        self.answer = {'kind': 'chat', 'reply': 'Three directions: studio, outdoors, abstract.'}
        self.video_status = 200
        self.interrupt_query = False
        self.query_count = 0
        original_client = httpx.AsyncClient
        patcher = patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kwargs:
                               original_client(**{**kwargs, 'transport': httpx.MockTransport(self.transport)}))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(main, 'upload_video_to_litterbox', self.upload)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        workers = self.workers()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self.assertEqual(self.unexpected, [], 'unexpected upstream must fail even if an adapter catches it')

    @staticmethod
    def workers():
        return [task for task in asyncio.all_tasks()
                if task.get_coro().__qualname__ == 'CanvasAgentExecution.worker']

    async def settle(self):
        await asyncio.wait_for(asyncio.gather(*self.workers()), timeout=3)

    async def upload(self, path, source_url):
        self.uploads.append((path, source_url))
        self.assertTrue(Path(path).is_relative_to(self.root))
        self.assertEqual(Path(path).read_bytes(), self.png)
        return {'url': 'https://litterbox.invalid/generated.png'}

    async def transport(self, request):
        self.requests.append(request)
        target = (request.method, str(request.url))
        if target in [('POST', 'https://deepseek.invalid/v1/chat/completions'),
                      ('POST', 'https://tugo.invalid/v1/chat/completions')]:
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant',
                'content': json.dumps(self.answer)}}], 'usage': {'total_tokens': 11}, 'raw_marker': 'provider-only'})
        if target == ('POST', 'https://tugo.invalid/v1/images/edits'):
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(self.png).decode()}],
                                            'usage': {'total_tokens': 7}, 'raw_marker': 'provider-only'})
        if target == ('POST', 'https://tugo.invalid/v1/videos/generations'):
            if self.video_status != 200:
                return httpx.Response(self.video_status, json={'error': 'specified duration is not supported'})
            return httpx.Response(200, json={'id': 'video-known', 'status': 'completed',
                'data': [{'url': 'https://media.invalid/video.mp4'}], 'usage': {'total_tokens': 9},
                'raw_marker': 'provider-only'})
        if target == ('POST', main.AUTODL_ROOT + '/minimax_h3_image_audio_to_video_v2_15s'):
            return httpx.Response(200, json={'code': 'Success', 'data': {'task_id': 'h3-known', 'status': 'QUEUED'}})
        if target == ('GET', main.AUTODL_ROOT + '/result/h3-known'):
            self.query_count += 1
            if self.interrupt_query:
                raise httpx.ReadError('synthetic query interruption', request=request)
            return httpx.Response(200, json={'code': 'Success', 'data': {'task_id': 'h3-known',
                'status': 'SUCCESS', 'results': [{'type': 'video', 'url': 'https://media.invalid/video.mp4'}]}})
        if target == ('GET', 'https://media.invalid/video.mp4'):
            # Synthetic bytes exercise download/file handling, not video decoding.
            return httpx.Response(200, content=b'synthetic-video-fixture', headers={'Content-Type': 'video/mp4'})
        self.unexpected.append(target)
        raise AssertionError(f'unexpected upstream: {target}')

    def save_canvas(self):
        (self.root / 'integration.json').write_text(json.dumps(self.canvas), encoding='utf-8')

    def base(self, conversation):
        return f"/api/canvases/integration/agent/conversations/{conversation['id']}"

    async def create(self):
        response = await self.client.post('/api/canvases/integration/agent/conversations')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['conversation']

    async def send(self, conversation, message='choose studio', refs=None, chat='tugo', video='tugo'):
        response = await self.client.post(self.base(conversation) + '/messages', json={
            'message': message, 'reference_node_ids': refs or [], 'chat_provider': chat,
            'chat_model': 'deepseek-chat' if chat == 'deepseek' else 'gpt-4o',
            'generation_defaults': {'image': {'provider': 'tugo', 'model': 'gpt-image-2'},
                                    'video': {'provider': video, 'model': 'minimax-h3' if video == 'autodl' else 'veo3-fast'}}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def operations(self, kind='image', provider='tugo', refs=None, prefix='i'):
        return [
            {'id': prefix + 'p', 'op': 'create_prompt', 'text': 'Studio product' if kind == 'image' else 'Slow orbit'},
            {'id': prefix + 'm', 'op': 'create_media', 'kind': kind, 'reference_node_ids': refs or []},
            {'id': prefix + 'c', 'op': 'connect', 'from': prefix + 'p', 'to': prefix + 'm'},
            {'id': prefix + 'g', 'op': 'generate', 'node': prefix + 'm', 'settings': {
                'provider': provider, 'model': 'gpt-image-2' if kind == 'image' else ('minimax-h3' if provider == 'autodl' else 'veo3-fast'),
                'count': 1, 'aspect_ratio': '1:1' if kind == 'image' else '9:16',
                'resolution': '1k' if kind == 'image' else '768p', **({'duration': 5} if kind == 'video' else {})}},
        ]

    async def plan(self, conversation, operations, **kwargs):
        self.answer = {'kind': 'plan', 'reply': 'Review then confirm.', 'summary': 'Studio product plan', 'operations': operations}
        kwargs['message'] = '请搭建工作流：' + kwargs.get('message', 'choose studio')
        return (await self.send(conversation, **kwargs))['plan']

    async def confirm(self, conversation, plan):
        response = await self.client.post(self.base(conversation) + f"/plans/{plan['id']}/confirm",
                                          json={'version': plan['version'], 'client_id': 'integration-client'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['run']

    async def read_run(self, conversation, run):
        response = await self.client.get(self.base(conversation) + f"/runs/{run['id']}/events")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['run']

    async def execute(self, conversation, run, operation='ig', resume=False):
        response = await self.client.post(self.base(conversation) + f"/runs/{run['id']}/steps/{operation}/execute",
                                          json={'client_id': 'integration-client', 'resume_only': resume})
        self.assertEqual(response.status_code, 200, response.text)
        await self.settle()
        return await self.read_run(conversation, run)

    async def acknowledge(self, conversation, run, operation_id):
        """Fixture for the host's strict saved-node/connection acknowledgement boundary."""
        run = await self.read_run(conversation, run)
        op = next(op for op in run['operations'] if op['id'] == operation_id)
        node_id = f"agent_{run['id']}_{operation_id}"
        if op['op'] == 'connect':
            self.canvas['connections'].append({'from': f"agent_{run['id']}_{op['from']}",
                                               'to': f"agent_{run['id']}_{op['to']}", 'kind': 'input'})
            ids = []
        else:
            node = {'id': node_id, 'agent': {'canvasId': 'integration', 'conversationId': conversation['id'],
                'runId': run['id'], 'operationId': operation_id,
                'role': {'create_prompt': 'prompt', 'create_media': 'source', 'generate': 'output'}[op['op']]}}
            if op['op'] == 'generate':
                node['images'] = next(s for s in run['steps'] if s['operation_id'] == operation_id)['result']['media']
            self.canvas['nodes'].append(node)
            ids = [node_id]
        self.save_canvas()
        response = await self.client.post(self.base(conversation) + f"/runs/{run['id']}/events", json={
            'type': 'operation_completed', 'operation_id': operation_id,
            'created_node_ids': ids, 'client_id': 'integration-client'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['run']

    async def ready(self, conversation, run, prefix='i'):
        for suffix in ('p', 'm', 'c'):
            await self.acknowledge(conversation, run, prefix + suffix)

    def assert_private_record(self, conversation):
        saved = (self.root / 'agent' / 'integration' / f"{conversation['id']}.json").read_text(encoding='utf-8')
        for forbidden in ('fixture-secret', 'provider-only', 'raw_usage', 'total_tokens', 'b64_json', 'Authorization'):
            self.assertNotIn(forbidden, saved)
        for request in self.requests:
            self.assertNotIn(b'fixture-secret', request.content, 'keys belong in headers, not model context')

    async def test_cross_provider_switch_retains_only_own_history_and_selected_endpoint(self):
        conversation, other = await self.create(), await self.create()
        await self.send(other, 'OTHER CONVERSATION ONLY')
        first = await self.send(conversation, 'FIRST OWN TURN', chat='deepseek')
        self.assertEqual(first['kind'], 'chat')
        first_request = self.requests[-1]
        self.assertEqual(str(first_request.url), 'https://deepseek.invalid/v1/chat/completions')
        self.assertEqual(json.loads(first_request.content)['model'], 'deepseek-chat')
        self.assertEqual(first_request.headers['Authorization'], 'Bearer fixture-secret-deepseek')
        second = await self.send(conversation, 'SECOND OWN TURN', chat='tugo')
        request = self.requests[-1]
        self.assertEqual(str(request.url), 'https://tugo.invalid/v1/chat/completions')
        self.assertEqual(json.loads(request.content)['model'], 'gpt-4o')
        self.assertIn(b'FIRST OWN TURN', request.content)
        self.assertIn(b'Three directions', request.content)
        self.assertNotIn(b'OTHER CONVERSATION ONLY', request.content)
        self.assertEqual(len(second['conversation']['messages']), 4)
        self.assertEqual(second['conversation']['plans'], [])
        self.assertTrue(all(r.url.path.endswith('/chat/completions') for r in self.requests))
        self.assert_private_record(conversation)

    async def test_discuss_confirm_real_image_save_dimensions_and_followup(self):
        conversation = await self.create()
        await self.send(conversation, 'Discuss three product directions', refs=['product'])
        self.assertEqual(len(self.requests), 1, 'discussion cannot generate')
        plan = await self.plan(conversation, self.operations(refs=['product']), refs=['product'])
        self.assertEqual(len(self.requests), 2, 'unconfirmed plan cannot generate')
        run = await self.confirm(conversation, plan)
        self.assertEqual((await self.confirm(conversation, plan))['id'], run['id'])
        await self.ready(conversation, run)
        run = await self.execute(conversation, run)
        step = run['steps'][-1]
        self.assertEqual(step['status'], 'generated')
        media = step['result']['media'][0]
        self.assertEqual((media['width'], media['height']), (32, 18))
        self.assertEqual(run['operations'][-1]['settings']['size'], '1024x1024')
        self.assertEqual(Path(main.output_file_from_url(media['url'])).read_bytes(), self.png)
        request = next(r for r in self.requests if r.url.path.endswith('/images/edits'))
        parts = BytesParser(policy=default).parsebytes(b'Content-Type: ' + request.headers['Content-Type'].encode()
                                                     + b'\r\nMIME-Version: 1.0\r\n\r\n' + request.content)
        fields = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                  for part in parts.iter_parts()}
        self.assertEqual({k: fields[k] for k in ('model', 'prompt', 'size', 'quality')},
                         {'model': b'gpt-image-2', 'prompt': b'Studio product', 'size': b'1024x1024', 'quality': b'high'})
        self.assertEqual(fields['image'], (self.root / 'assets/output/reference.png').read_bytes())
        self.assertEqual(request.headers['Authorization'], 'Bearer fixture-secret-tugo')
        history = json.loads((self.root / 'history.json').read_text(encoding='utf-8'))
        self.assertEqual(history[0]['images'], [media['url']])
        self.assertEqual(history[0]['image_items'][0]['width'], 32)
        completed = await self.acknowledge(conversation, run, 'ig')
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual((completed['canvas_id'], completed['conversation_id']), ('integration', conversation['id']))
        await self.execute(conversation, run)
        self.assertEqual(sum(r.url.path.endswith('/images/edits') for r in self.requests), 1)
        revised = await self.plan(conversation, self.operations(refs=[f"agent_{run['id']}_ig"]),
                                  message='Modify the generated image', refs=[f"agent_{run['id']}_ig"])
        self.assertNotEqual(revised['id'], plan['id'])
        self.assertEqual(revised['reference_snapshot'][0]['images'][0]['url'], media['url'])
        self.assertEqual(self.canvas['nodes'][0], self.original)
        self.assert_private_record(conversation)

    async def test_generic_tugo_video_real_reference_conversion_download_and_owned_result(self):
        conversation = await self.create()
        plan = await self.plan(conversation, self.operations('video', refs=['product']), refs=['product'])
        run = await self.confirm(conversation, plan)
        await self.ready(conversation, run)
        run = await self.execute(conversation, run)
        self.assertEqual(run['steps'][-1]['status'], 'generated')
        self.assertEqual(run['steps'][-1]['provider_task_id'], 'video-known')
        request = next(r for r in self.requests if r.url.path.endswith('/videos/generations'))
        self.assertEqual(request.headers['Authorization'], 'Bearer fixture-secret-tugo')
        body = json.loads(request.content)
        images = body.pop('images')
        self.assertEqual(body, {'prompt': 'Slow orbit', 'model': 'veo3-fast', 'duration': 5, 'watermark': False,
                               'aspect_ratio': '9:16', 'ratio': '9:16', 'size': '9:16', 'resolution': '768p'})
        self.assertEqual(len(images), 1)
        with Image.open(BytesIO(base64.b64decode(images[0].split(',', 1)[1]))) as reference:
            self.assertEqual(reference.size, (12, 12))
        media = run['steps'][-1]['result']['media'][0]
        self.assertEqual(media['kind'], 'video')
        self.assertEqual(Path(main.output_file_from_url(media['url'])).read_bytes(), b'synthetic-video-fixture')
        completed = await self.acknowledge(conversation, run, 'ig')
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual((completed['canvas_id'], completed['conversation_id']), ('integration', conversation['id']))
        await self.execute(conversation, run)
        self.assertEqual(sum(r.url.path.endswith('/videos/generations') for r in self.requests), 1)
        self.assertEqual(self.canvas['nodes'][0], self.original)
        self.assert_private_record(conversation)

    async def test_h3_generated_dependency_query_interruption_and_explicit_recovery_without_resubmit(self):
        conversation = await self.create()
        operations = self.operations(refs=['product']) + self.operations('video', 'autodl', ['ig'], 'v')
        plan = await self.plan(conversation, operations, refs=['product'], video='autodl')
        run = await self.confirm(conversation, plan)
        await self.ready(conversation, run)
        run = await self.execute(conversation, run)
        self.assertEqual(run['steps'][3]['status'], 'generated')
        generated_url = run['steps'][3]['result']['media'][0]['url']
        await self.acknowledge(conversation, run, 'ig')
        await self.ready(conversation, run, 'v')
        self.interrupt_query = True
        run = await self.execute(conversation, run, 'vg')
        step = run['steps'][-1]
        self.assertEqual((run['status'], step['status'], step['provider_task_id']), ('paused', 'running', 'h3-known'))
        self.assertIn('查询或下载失败', step['error'])
        submitted = [r for r in self.requests if r.method == 'POST' and r.url.host == 'autodl.art']
        self.assertEqual(len(submitted), 1)
        self.assertEqual(str(submitted[0].url),
                         'https://autodl.art/api/v1/comfyui/comfyui_workflow/minimax_h3_image_audio_to_video_v2_15s')
        self.assertEqual(json.loads(submitted[0].content), {'prompt': 'Slow orbit', 'duration': 5,
                         'resolution': '768p竖', 'ref_image_0': 'https://litterbox.invalid/generated.png'})
        self.assertEqual(submitted[0].headers['Authorization'], 'fixture-secret-autodl')
        self.assertEqual(self.uploads, [(main.output_file_from_url(generated_url), generated_url)])
        # A refresh/read cannot query or submit. Recovery must be an explicit action.
        before = len(self.requests)
        await self.read_run(conversation, run)
        await self.client.get(self.base(conversation))
        self.assertEqual(len(self.requests), before)
        self.interrupt_query = False
        resumed = await self.client.post(self.base(conversation) + f"/runs/{run['id']}/events",
                                         json={'type': 'resume', 'client_id': 'integration-client'})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        run = await self.execute(conversation, run, 'vg', resume=True)
        self.assertEqual(run['steps'][-1]['status'], 'generated')
        self.assertEqual(run['steps'][-1]['error'], '')
        self.assertEqual(run['steps'][-1]['provider_task_id'], 'h3-known')
        media = run['steps'][-1]['result']['media'][0]
        self.assertEqual(media['kind'], 'video')
        self.assertEqual(Path(main.output_file_from_url(media['url'])).read_bytes(), b'synthetic-video-fixture')
        self.assertEqual(self.query_count, 3, 'interrupted query, recovery query, download re-query')
        completed = await self.acknowledge(conversation, run, 'vg')
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual((completed['canvas_id'], completed['conversation_id']), ('integration', conversation['id']))
        self.assertEqual(sum(r.method == 'POST' and r.url.host == 'autodl.art' for r in self.requests), 1)
        self.assertEqual(len(self.uploads), 1)
        self.assertEqual(sum(r.url.path.endswith('/images/edits') for r in self.requests), 1)
        self.assertEqual(self.canvas['nodes'][0], self.original)
        self.assert_private_record(conversation)

    async def test_video_upstream_rejection_or_failure_is_unknown_and_cannot_resubmit(self):
        for status in (400, 503):
            with self.subTest(status=status):
                self.video_status = status
                conversation = await self.create()
                plan = await self.plan(conversation, self.operations('video'))
                run = await self.confirm(conversation, plan)
                await self.ready(conversation, run)
                run = await self.execute(conversation, run)
                self.assertEqual((run['status'], run['steps'][-1]['status']), ('unknown', 'unknown'))
                self.assertIn('平台核实', run['steps'][-1]['error'])
                before = len(self.requests)
                await self.execute(conversation, run)
                self.assertEqual(len(self.requests), before)
                self.assert_private_record(conversation)


if __name__ == '__main__':
    unittest.main()
