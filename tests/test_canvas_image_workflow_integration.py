import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from canvas_agent import generation_inputs
from skill_catalog import SkillCatalog
from test_image_case_library import write_library
from test_canvas_image_workflow import intent, selection, message, snapshot


def plan_answer():
    return {'kind': 'plan', 'reply': '采用琥珀色侧光，先生成再检查。', 'summary': '产品海报', 'operations': [
        {'id': 'p', 'op': 'create_prompt', 'text': '夏日香气，保留产品外观，琥珀色背景。'},
        {'id': 'm', 'op': 'create_media', 'kind': 'image', 'reference_node_ids': []},
        {'id': 'c', 'op': 'connect', 'from': 'p', 'to': 'm'},
        {'id': 'g', 'op': 'generate', 'node': 'm', 'settings': {'provider': 'tugo', 'model': 'gpt-image-2',
         'count': 1, 'aspect_ratio': '3:4', 'resolution': '1k', 'quality': 'high'}}]}


class WorkflowIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        skills = self.root / 'skills'
        write_library(skills / 'gpt-image-2-style-library')
        catalog = SkillCatalog(skills, self.root / 'selection.json')
        catalog.save(['gpt-image-2-style-library'], 0)
        (self.root / 'canvases').mkdir()
        self.canvas = {'id': 'canvas', 'nodes': snapshot(), 'connections': []}
        self.write_canvas()
        self.calls, self.image_calls = [], []
        self.intent = intent()
        self.plan = plan_answer()
        self.planner_replies = []
        for name, value in [('SKILL_CATALOG', catalog), ('ASSETS_DIR', str(self.root / 'assets')),
                            ('CANVAS_DIR', str(self.root / 'canvases')),
                            ('get_api_provider_exact', self.provider), ('provider_env_key_value', lambda _: 'fixture'),
                            ('run_canvas_agent_llm', self.llm), ('build_online_image_result', self.no_image)]:
            p = patch.object(main, name, value); p.start(); self.addCleanup(p.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    def write_canvas(self):
        (self.root / 'canvases/canvas.json').write_text(json.dumps(self.canvas), encoding='utf-8')

    @staticmethod
    def provider(pid):
        return {'id': pid, 'enabled': True, 'protocol': 'openai', 'chat_models': ['vision-chat'],
                'image_models': ['gpt-image-2'], 'video_models': []}

    async def llm(self, payload):
        self.calls.append(payload)
        system = payload['system_prompt']
        result = self.intent if 'IMAGE_WORKFLOW:research' in system else (
            selection() if 'IMAGE_WORKFLOW:case_vision' in system else self.plan)
        if 'IMAGE_WORKFLOW:research' not in system and 'IMAGE_WORKFLOW:case_vision' not in system and self.planner_replies:
            return {'text': self.planner_replies.pop(0)}
        return {'text': json.dumps(result, ensure_ascii=False)}

    async def no_image(self, payload):
        self.image_calls.append(payload)
        raise AssertionError('Image generation before confirmation')

    async def send(self, value=None):
        created = (await self.client.post('/api/canvases/canvas/agent/conversations')).json()['conversation']
        self.base = f"/api/canvases/canvas/agent/conversations/{created['id']}"
        return await self.client.post(self.base + '/messages', json=(value or message(
            message='/gpt-image-2-style-library 请搭建海报工作流，检查后定向修图')).model_dump())

    async def test_real_message_route_researches_before_plan_and_binds_identity_and_case_inputs(self):
        response = await self.send()
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()['plan']
        self.assertIn('image_workflow', plan, 'the route must execute, not only inject, the image skill')
        context = plan['image_workflow']
        self.assertEqual(context['cases'][0]['id'], 358)
        self.assertEqual(context['max_revisions'], 2)
        self.assertEqual(len(self.calls), 3)
        self.assertIn('Complete product template', self.calls[-1]['system_prompt'])
        self.assertIn('琥珀色背景，瓶身居中', self.calls[-1]['system_prompt'])
        kind, prompt, media = generation_inputs(plan, 'g', preview=True)
        self.assertEqual(kind, 'image')
        self.assertEqual(media[0]['url'], '/assets/product.png')
        self.assertTrue(media[1]['url'].startswith('/assets/skill-references/'))
        self.assertIn('身份', prompt)
        self.assertEqual(self.image_calls, [])

    async def test_prompt_only_is_not_allowed_to_become_paid_plan(self):
        self.intent = intent(output='prompt', max_revisions=0)
        response = await self.send(message(message='/gpt-image-2-style-library 只要提示词'))
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.image_calls, [])

    async def test_batch_plan_is_rejected_before_confirmation(self):
        self.plan['operations'][-1]['settings']['count'] = 2
        response = await self.send()
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.image_calls, [])

    async def test_unselected_skill_does_not_research_or_append_message(self):
        main.SKILL_CATALOG.save([], 1)
        response = await self.send()
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.calls, [])
        record = (await self.client.get(self.base)).json()['conversation']
        self.assertEqual(record['messages'], [])

    async def test_ordinary_canvas_prompt_does_not_trigger_case_research(self):
        response = await self.send(message(message='请搭建海报工作流', skill_ids=[]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn('image_workflow', response.json()['plan'])
        self.assertEqual(len(self.calls), 1)

    async def test_empty_enhanced_planner_reply_is_corrected_before_creating_confirmable_plan(self):
        self.planner_replies = ['', json.dumps(self.plan, ensure_ascii=False)]
        response = await self.send()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['plan']['image_workflow']['cases'][0]['id'], 358)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.image_calls, [])
        record = (await self.client.get(self.base)).json()['conversation']
        self.assertEqual(len(record['plans']), 1)
        self.assertEqual(record['runs'], [])

    async def test_repeated_empty_enhanced_planner_reply_reports_stage_without_plan_or_images(self):
        self.planner_replies = ['', '']
        response = await self.send()
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('方案规划', response.json()['detail'])
        self.assertEqual(len(self.calls), 4)
        record = (await self.client.get(self.base)).json()['conversation']
        self.assertEqual(record['plans'], [])
        self.assertEqual(self.image_calls, [])

    async def test_enhanced_plan_corrects_invalid_operation_ids_without_weakening_id_rules(self):
        bad = json.loads(json.dumps(self.plan))
        bad['operations'][0]['id'] = 'prompt_poster_001'
        bad['operations'][2]['from'] = 'prompt_poster_001'
        self.planner_replies = [json.dumps(bad), json.dumps(self.plan)]
        response = await self.send()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['plan']['operations'][0]['id'], 'p')
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.image_calls, [])


if __name__ == '__main__':
    unittest.main()
