import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from skill_catalog import SkillCatalog


class SkillChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        skills = self.root / 'skills'
        (skills / 'brand').mkdir(parents=True)
        (skills / 'brand/SKILL.md').write_text(
            '---\nname: Brand\ndescription: 品牌规则\n---\nUse the exact product name.', encoding='utf-8')
        self.catalog = SkillCatalog(skills, self.root / 'skills.json')
        self.catalog.save(['brand'], 0)
        for name, value in [('SKILL_CATALOG', self.catalog), ('CONVERSATION_DIR', str(self.root / 'conversations')),
                            ('CANVAS_DIR', str(self.root / 'canvases'))]:
            p = patch.object(main, name, value)
            p.start(); self.addCleanup(p.stop)
        (self.root / 'canvases').mkdir()
        (self.root / 'canvases/canvas.json').write_text(json.dumps({'id':'canvas', 'nodes':[], 'connections':[]}), encoding='utf-8')
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    async def test_gpt_chat_transmits_skill_rules_and_records_invocation(self):
        original = httpx.AsyncClient
        def upstream(request):
            body = json.loads(request.content)
            self.assertIn('Use the exact product name.', body['messages'][0]['content'])
            self.assertEqual(body['messages'][-1]['content'], '/brand 写一句文案')
            return httpx.Response(200, json={'choices':[{'message':{'content':'品牌文案'}}]})
        with patch.object(main, 'resolve_chat_provider', return_value=('https://model.invalid/v1', {}, 'chat-model')), \
             patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kw: original(transport=httpx.MockTransport(upstream), **kw)):
            result = await self.client.post('/api/chat', json={'message':'/brand 写一句文案', 'skill_ids':['brand'], 'provider':'fixture'})
        self.assertEqual(result.status_code, 200, result.text)
        user = result.json()['conversation']['messages'][0]
        self.assertEqual(user['skills'][0]['id'], 'brand')
        self.assertTrue(user['skills'][0]['version'])

    async def test_disabled_or_forged_call_is_rejected_before_creating_history(self):
        self.catalog.save([], 1)
        for url in ['/api/chat', '/api/chat/agent', '/api/chat/stream']:
            result = await self.client.post(url, json={'message':'/brand 写文案', 'skill_ids':['brand']})
            self.assertEqual(result.status_code, 422, result.text)
        self.assertFalse((self.root / 'conversations').exists())

    async def test_canvas_planner_keeps_contract_and_records_skill_context(self):
        base = '/api/canvases/canvas/agent/conversations'
        conversation = (await self.client.post(base)).json()['conversation']
        async def llm(payload):
            self.assertIn('Use the exact product name.', payload['system_prompt'])
            self.assertIn('只返回 JSON', payload['system_prompt'])
            return {'text':json.dumps({'kind':'chat', 'reply':'可以先确定品牌方向。'})}
        provider = {'id':'fixture', 'enabled':True, 'api_key':'fixture', 'protocol':'openai', 'chat_models':['chat-model']}
        with patch.object(main, 'get_api_provider_exact', return_value=provider), patch.object(main, 'run_canvas_agent_llm', side_effect=llm):
            result = await self.client.post(f"{base}/{conversation['id']}/messages", json={
                'message':'/brand 讨论方案', 'skill_ids':['brand'], 'reference_node_ids':[],
                'chat_provider':'fixture', 'chat_model':'chat-model',
                'generation_defaults':{'image':{'provider':'fixture','model':'image'}, 'video':{'provider':'fixture','model':'video'}}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['conversation']['messages'][0]['skills'][0]['id'], 'brand')

    async def test_direct_image_endpoint_does_not_send_raw_skill_rules_to_generator(self):
        result = await self.client.post('/api/chat', json={'message':'/brand 生成图片', 'mode':'image', 'skill_ids':['brand']})
        self.assertEqual(result.status_code, 422, result.text)
        self.assertFalse((self.root / 'conversations').exists())

    async def test_skill_router_failure_cannot_fall_back_to_unprocessed_generation(self):
        original = httpx.AsyncClient
        async def forbidden_generation(*args, **kwargs):
            raise HTTPException(599, 'unprocessed generation was attempted')
        with patch.object(main, 'resolve_chat_provider', return_value=('https://model.invalid/v1', {}, 'chat-model')), \
             patch.object(main, 'generate_ai_image', side_effect=forbidden_generation), \
             patch.object(main.httpx, 'AsyncClient', side_effect=lambda **kw: original(
                 transport=httpx.MockTransport(lambda req: httpx.Response(500, text='upstream unavailable')), **kw)):
            result = await self.client.post('/api/chat/agent', json={
                'message':'/brand 生成一张产品图', 'mode':'agent', 'skill_ids':['brand'], 'provider':'fixture'})
        self.assertEqual(result.status_code, 502, result.text)


if __name__ == '__main__':
    unittest.main()
