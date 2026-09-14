"""Full app route, actual multimodal/multipart transport and real PNG persistence."""
import base64
import json
import sys
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
import test_canvas_agent_integration as fixtures
from skill_catalog import SkillCatalog
from test_image_case_library import write_library
from test_canvas_image_workflow import intent, selection, message, review_answer
from test_canvas_image_workflow_integration import plan_answer
from test_canvas_image_workflow_creative_execution import creative_answer
from test_canvas_image_workflow_quality import issue, issue_review


class ImageWorkflowTransportTests(unittest.IsolatedAsyncioTestCase):
    save_canvas = fixtures.CanvasAgentIntegrationTests.save_canvas
    base = fixtures.CanvasAgentIntegrationTests.base
    create = fixtures.CanvasAgentIntegrationTests.create
    confirm = fixtures.CanvasAgentIntegrationTests.confirm
    ready = fixtures.CanvasAgentIntegrationTests.ready
    acknowledge = fixtures.CanvasAgentIntegrationTests.acknowledge
    read_run = fixtures.CanvasAgentIntegrationTests.read_run
    execute = fixtures.CanvasAgentIntegrationTests.execute
    settle = fixtures.CanvasAgentIntegrationTests.settle
    workers = staticmethod(fixtures.CanvasAgentIntegrationTests.workers)
    upload = fixtures.CanvasAgentIntegrationTests.upload
    asyncTearDown = fixtures.CanvasAgentIntegrationTests.asyncTearDown
    assert_private_record = fixtures.CanvasAgentIntegrationTests.assert_private_record

    def setUp(self):
        fixtures.CanvasAgentIntegrationTests.setUp(self)
        self.providers[0]['chat_models'] = ['vision-chat']
        self.image_requests, self.chat_requests = [], []
        self.creation = False
        root = self.root / 'skills'
        write_library(root / 'gpt-image-2-style-library')
        catalog = SkillCatalog(root, self.root / 'skill-state.json')
        catalog.save(['gpt-image-2-style-library'], 0)
        p = patch.object(main, 'SKILL_CATALOG', catalog); p.start(); self.addCleanup(p.stop)

    async def transport(self, request):
        self.requests.append(request)
        if request.method == 'POST' and str(request.url) == 'https://deepseek.invalid/v1/chat/completions':
            body = json.loads(request.content)
            self.chat_requests.append(body)
            self.assertEqual(body['model'], 'vision-chat')
            self.assertEqual(body.get('response_format'), {'type': 'json_object'},
                             'DeepSeek Agent calls must request JSON at the API boundary')
            system = body['messages'][0]['content']
            if 'IMAGE_WORKFLOW:research' in system:
                answer = intent()
            elif 'IMAGE_WORKFLOW:case_vision' in system:
                answer = selection()
            elif 'IMAGE_WORKFLOW:review' in system:
                answer = (issue_review([issue(status='pass',fix='',can_edit=False)] if len(self.image_requests) == 2 else [issue()])
                          if self.creation else review_answer(passed=len(self.image_requests) == 2))
            elif 'CREATIVE_TASK:preflight' in system or 'IMAGE_DESIGN:preflight' in system:
                answer = {'issues': []}
            else:
                answer = creative_answer() if self.creation else plan_answer()
                if self.creation:
                    answer['design_card']['subject']['references'] = [{'source_id': 'product', 'role': 'identity'}]
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': json.dumps(answer)}}]})
        if request.method == 'POST' and str(request.url) == 'https://tugo.invalid/v1/images/edits':
            multipart = BytesParser(policy=default).parsebytes(
                ('Content-Type: ' + request.headers['content-type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + request.content)
            fields, images = {}, []
            for part in multipart.iter_parts():
                name = part.get_param('name', header='content-disposition')
                if part.get_filename():
                    with Image.open(BytesIO(part.get_payload(decode=True))) as picture:
                        picture.load(); images.append({'size': picture.size, 'pixel': picture.convert('RGB').getpixel((0, 0))})
                else:
                    fields[name] = part.get_payload(decode=True).decode('utf-8')
            self.image_requests.append({'fields': fields, 'images': images})
            buffer = BytesIO()
            Image.new('RGB', (32, 18), 'red' if len(self.image_requests) == 1 else 'green').save(buffer, format='PNG')
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(buffer.getvalue()).decode()}]})
        self.unexpected.append((request.method, str(request.url)))
        raise AssertionError('Unexpected external call')

    @staticmethod
    def image_sizes(body):
        sizes = []
        for part in body['messages'][-1]['content']:
            if part['type'] == 'image_url':
                data = part['image_url']['url']
                if not data.startswith('data:image/'):
                    raise AssertionError('Local path was not converted into image pixels')
                with Image.open(BytesIO(base64.b64decode(data.split(',', 1)[1]))) as picture:
                    sizes.append(picture.size)
        return sizes

    async def test_complete_research_generate_review_edit_preserves_real_files_and_selected_transport(self):
        conversation = await self.create()
        payload = message(message='/gpt-image-2-style-library 请搭建海报工作流', reference_node_ids=['product']).model_dump()
        response = await self.client.post(self.base(conversation) + '/messages', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()['plan']
        self.assertEqual(self.image_requests, [], 'research and planning must not generate images')
        self.assertEqual([self.image_sizes(b) for b in self.chat_requests], [[(12, 12)], [(12, 8)], [(12, 12)]])
        run = await self.confirm(conversation, plan)
        await self.ready(conversation, run, prefix='')
        run = await self.execute(conversation, run, operation='g')
        step = run['steps'][-1]
        self.assertEqual(step['status'], 'generated')
        self.assertEqual(step['result']['quality']['status'], 'passed')
        self.assertEqual(len(self.image_requests), 2)
        self.assertEqual([i['size'] for i in self.image_requests[0]['images']], [(12, 12), (12, 8)])
        self.assertEqual([i['size'] for i in self.image_requests[1]['images']], [(32, 18), (12, 12), (12, 8)])
        self.assertEqual(self.image_requests[0]['images'][0]['pixel'], (0, 0, 255))
        self.assertEqual(self.image_requests[1]['images'][0]['pixel'], (255, 0, 0))
        self.assertEqual([self.image_sizes(b) for b in self.chat_requests[-2:]],
                         [[(32, 18), (12, 12), (12, 8)], [(32, 18), (12, 12), (12, 8)]])
        for sent in self.image_requests:
            self.assertEqual(sent['fields']['model'], 'gpt-image-2')
            self.assertEqual(sent['fields']['size'], '768x1024')
            self.assertEqual(sent['fields']['quality'], 'high')
        for media in step['result']['media']:
            path = Path(main.output_file_from_url(media['url']))
            self.assertTrue(path.is_relative_to(self.root))
            with Image.open(path) as saved:
                self.assertEqual(saved.size, (32, 18))
        self.assertEqual(step['result']['best_url'], step['result']['media'][1]['url'])
        completed = await self.acknowledge(conversation, run, 'g')
        self.assertEqual(completed['status'], 'completed')
        output = self.canvas['nodes'][-1]
        self.assertEqual(len(output['images']), 2)
        self.assertEqual(self.canvas['nodes'][0], self.original)
        self.assert_private_record(conversation)

    async def test_creative_attachment_to_real_multipart_edit_and_recheck_needs_no_canvas_graph(self):
        self.creation = True
        self.canvas['nodes'][0].update(text='OLD GENERATION PROMPT', inputNodeIds=['old'])
        self.canvas['nodes'].append({'id': 'old', 'type': 'smart-prompt', 'text': 'UNRELATED VIDEO CREATIVE'})
        self.canvas['connections'].append({'from': 'old', 'to': 'product', 'kind': 'input'})
        self.save_canvas()
        conversation = await self.create()
        response = await self.client.post(self.base(conversation) + '/messages', json=message(
            message='/gpt-image-2-style-library 保留产品外观，生成信息海报', reference_node_ids=['product']).model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()['plan']
        self.assertEqual(plan['mode'], 'creation')
        self.assertEqual([r['id'] for r in plan['reference_snapshot']], ['product'])
        self.assertEqual(plan['reference_snapshot'][0]['text'], '')
        self.assertEqual(self.image_requests, [])
        self.assertEqual(len(self.chat_requests), 4)
        run = await self.confirm(conversation, plan)
        self.canvas['nodes'] = []; self.canvas['connections'] = []; self.save_canvas()
        run = await self.execute(conversation, run, operation='image1')
        result = run['steps'][0]['result']
        self.assertEqual(result['quality']['status'], 'passed')
        self.assertEqual(len(self.image_requests), 2)
        self.assertEqual(self.image_requests[0]['fields']['prompt'], plan['operations'][0]['prompt'])
        self.assertIn('Design for everyday use', self.image_requests[1]['fields']['prompt'])
        self.assertIn('Textured Surface', self.image_requests[1]['fields']['prompt'])
        self.assertNotIn('BAD GLOBAL', self.image_requests[1]['fields']['prompt'])
        self.assertEqual([i['size'] for i in self.image_requests[0]['images']], [(12,12)])
        self.assertEqual([i['size'] for i in self.image_requests[1]['images']], [(32,18),(12,12)])
        self.assertEqual([self.image_sizes(b) for b in self.chat_requests[-2:]],
            [[(32,18),(12,12)],[(32,18),(12,12),(32,18)]])
        self.assertNotIn('OLD GENERATION', json.dumps(self.chat_requests))
        self.assertNotIn('UNRELATED VIDEO', json.dumps(self.chat_requests))
        for media in result['media']:
            with Image.open(main.output_file_from_url(media['url'])) as saved:
                self.assertEqual(saved.size, (32,18))
        completed = await self.acknowledge(conversation, run, 'image1')
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(len(self.canvas['nodes']), 1)
        self.assertEqual(self.canvas['connections'], [])
        self.assert_private_record(conversation)
        calls_before = len(self.chat_requests), len(self.image_requests)
        recipe_payload = {'name': '真实落盘验收方案', 'conversation_id': conversation['id'], 'run_id': run['id'],
                          'operation_id': 'image1', 'round': 1, 'image_index': 0}
        recipe_base = f"/api/canvases/{conversation['canvas_id']}/agent/recipes"
        approved = await self.client.post(recipe_base, json=recipe_payload)
        self.assertEqual(approved.status_code, 200, approved.text)
        recipe = approved.json()['recipe']
        self.assertEqual(recipe['result']['url'], result['media'][1]['url'])
        self.assertEqual(recipe['final_prompt'], self.image_requests[1]['fields']['prompt'])
        self.assertEqual(recipe['status'], 'user-approved')
        self.assertEqual((len(self.chat_requests), len(self.image_requests)), calls_before)
        again = await self.client.post(recipe_base, json=recipe_payload)
        self.assertEqual(again.json()['recipe']['id'], recipe['id'])
        self.assertEqual(len((await self.client.get(recipe_base)).json()['recipes']), 1)


if __name__ == '__main__':
    unittest.main()
