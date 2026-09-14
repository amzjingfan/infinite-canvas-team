import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_agent import CanvasAgentDiscussion, CanvasAgentStore, ConversationPatch, MessageRequest
from canvas_image_design import compile_design
from image_design_fixtures import card, snapshot, research, defaults, provider


class RecipeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = CanvasAgentStore(lambda: self.root, lambda cid: {'id': cid, 'nodes': snapshot(), 'connections': []})
        self.record = self.store.create('canvas')
        plan = compile_design(card(), snapshot(), '保留产品外观，生成海报', defaults(), lambda raw, kind: raw,
                              research=research())
        plan.update(id='plan', version=1, status='confirmed')
        review = {'passed': False, 'summary': '控制标识待人工核验', 'issues': [{'id': 'marking', 'status': 'uncertain'}]}
        attempt = {'round': 1, 'prompt': '精确修正后的最终提示词', 'input_media': [{'url': '/out0.png', 'kind': 'image'}],
                   'input_roles': [], 'edit_targets': [{'id': 'title'}], 'media': [{'url': '/out1.png', 'kind': 'image'}],
                   'review': review}
        self.run = {**deepcopy(plan), 'id': 'run', 'plan_id': 'plan', 'conversation_id': self.record['id'], 'canvas_id': 'canvas',
            'status': 'completed', 'steps': [{'operation_id': 'image1', 'status': 'completed', 'result': {
                'media': attempt['media'], 'attempts': [attempt], 'best_url': '/out1.png',
                'quality': {'status': 'needs_review', 'phase': 'finished', 'best_round': 1, 'best_review': review}}}]}
        self.store.mutate('canvas', self.record['id'], lambda r: (r['plans'].append(plan), r['runs'].append(deepcopy(self.run))))
        self.output = self.root / 'output.png'
        self.output.write_bytes(b'persisted-image-fixture')
        self.payload = {'name': '已认可浅色设计', 'conversation_id': self.record['id'], 'run_id': 'run',
                        'operation_id': 'image1', 'round': 1, 'image_index': 0}

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('canvas_image_recipes'), 'approved recipe store is missing')
        return importlib.import_module('canvas_image_recipes')

    def recipes(self):
        return self.module().ImageRecipeStore(self.store, resolve_media=lambda url: self.output if url == '/out1.png' else None)

    async def test_real_completed_version_is_saved_immutably_and_duplicate_request_is_idempotent(self):
        recipes = self.recipes()
        saved = recipes.save('canvas', self.payload)
        again = recipes.save('canvas', {**self.payload, 'name': '不能覆盖旧记录'})
        self.assertEqual(saved, again)
        self.assertEqual(saved['status'], 'user-approved')
        self.assertEqual(saved['final_prompt'], '精确修正后的最终提示词')
        self.assertEqual(saved['quality']['status'], 'needs_review')
        self.assertFalse(saved['review']['passed'])
        self.assertEqual(saved['source']['round'], 1)
        self.assertEqual(len(recipes.list('canvas')), 1)
        self.assertEqual(len(self.store.list('canvas')), 1)
        self.assertEqual(self.store.get('canvas', self.record['id'])['runs'][0], self.run)
        self.assertEqual(len(list((self.root / 'agent/canvas/recipes').glob('*.json'))), 1)

    async def test_unknown_or_unfinished_source_result_and_cross_canvas_read_are_rejected(self):
        recipes = self.recipes()
        saved = recipes.save('canvas', self.payload)
        for canvas_id, changes in [('other', {}), ('canvas', {'run_id': 'no-run'}), ('canvas', {'round': 0}),
                                    ('canvas', {'image_index': 1})]:
            with self.subTest(canvas=canvas_id, changes=changes), self.assertRaises(HTTPException):
                recipes.save(canvas_id, {**self.payload, **changes})
        with self.assertRaises(HTTPException):
            recipes.get('other', saved['id'])
        for status in ('running', 'unknown', 'failed'):
            self.store.mutate('canvas', self.record['id'], lambda r: r['runs'][0].update(status=status, id='unfinished'))
            with self.subTest(status=status), self.assertRaises(HTTPException):
                recipes.save('canvas', {**self.payload, 'run_id': 'unfinished'})

    async def test_missing_actual_file_and_client_supplied_design_or_url_cannot_create_recipe(self):
        recipes = self.recipes()
        self.output.unlink()
        with self.assertRaises(HTTPException):
            recipes.save('canvas', self.payload)
        for key in ('design_card', 'result_url', 'status'):
            with self.subTest(key=key), self.assertRaises(HTTPException):
                recipes.save('canvas', {**self.payload, key: 'invented'})
        self.assertEqual(recipes.list('canvas'), [])

    async def test_atomic_replace_failure_leaves_no_partial_recipe_or_temporary_file(self):
        recipes = self.recipes()
        with patch('canvas_image_recipes.os.replace', side_effect=OSError('disk fixture')):
            with self.assertRaises(HTTPException) as error:
                recipes.save('canvas', self.payload)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(recipes.list('canvas'), [])
        self.assertEqual(list((self.root / 'agent/canvas/recipes').iterdir()), [])

    async def test_recipe_routes_list_get_save_and_refuse_untrusted_source_fields(self):
        recipes = self.recipes()
        app = FastAPI()
        app.include_router(self.module().create_recipe_router(recipes))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            base = '/api/canvases/canvas/agent/recipes'
            response = await client.post(base, json=self.payload)
            self.assertEqual(response.status_code, 200, response.text)
            recipe = response.json()['recipe']
            self.assertEqual((await client.get(base)).json()['recipes'][0]['id'], recipe['id'])
            self.assertEqual((await client.get(base + '/' + recipe['id'])).json()['recipe'], recipe)
            invalid = await client.post(base, json={**self.payload, 'result_url': 'https://example.invalid/no.png'})
            self.assertEqual(invalid.status_code, 422)

    async def test_retired_recipe_selection_cannot_import_saved_design_or_media(self):
        recipes = self.recipes()
        saved = recipes.save('canvas', self.payload)
        new_canvas = {'id': 'canvas', 'nodes': [{'id': 'newproduct', 'title': '新产品', 'images': [
            {'url': '/assets/newproduct.png', 'kind': 'image'}]}], 'connections': []}
        self.store.load_canvas = lambda _: deepcopy(new_canvas)
        conversation = self.store.create('canvas')
        self.store.patch('canvas', conversation['id'], ConversationPatch(recipe_id=saved['id']))
        calls = []
        async def llm(payload):
            calls.append(deepcopy(payload))
            return {'text': json.dumps({'kind': 'chat', 'reply': '请直接引用要沿用的成图。'}, ensure_ascii=False)}
        class NoResearch:
            async def research(self, *args):
                raise AssertionError('recipe reuse must not research cases again')
        discussion = CanvasAgentDiscussion(self.store, llm, provider, image_workflow=NoResearch(), recipe_store=recipes)
        result = await discussion.send('canvas', conversation['id'], MessageRequest(message='用新产品制作同版式，文案全新系列',
            reference_node_ids=['newproduct'], chat_provider='fixture', chat_model='chat',
            generation_defaults=defaults(), recipe_id=saved['id']))
        self.assertEqual(result['kind'], 'chat')
        self.assertEqual(result['conversation']['recipe_id'], '')
        self.assertEqual(calls[0]['images'], ['/assets/newproduct.png'])
        self.assertNotIn('日常之选', json.dumps(calls, ensure_ascii=False))
        self.assertNotIn('/assets/product.png', json.dumps(calls))
        self.assertNotIn('/out1.png', json.dumps(calls))
        self.assertEqual(len(calls), 1)
        self.assertEqual(recipes.get('canvas', saved['id']), saved)

    async def test_selected_overrides_unlock_only_those_design_fields(self):
        recipes = self.recipes()
        saved = recipes.save('canvas', self.payload)
        modified = card()
        modified['appearance']['background'] = '允许更换的蓝色背景'
        modified['appearance']['lighting'] = '不允许更换的光'
        plan = compile_design(modified, snapshot(), '保留产品外观，生成海报', defaults(), lambda raw, kind: raw,
            research=research(), recipe=saved, recipe_overrides=['background'])
        self.assertEqual(plan['design_card']['appearance']['background'], '允许更换的蓝色背景')
        self.assertEqual(plan['design_card']['appearance']['lighting'], card()['appearance']['lighting'])
        self.assertEqual(saved['design_card']['appearance']['background'], '浅米白背景')

    async def test_retired_recipe_tag_does_not_filter_the_actual_current_conversation(self):
        recipes = self.recipes()
        saved = recipes.save('canvas', self.payload)
        conversation = self.store.create('canvas')
        self.store.patch('canvas', conversation['id'], ConversationPatch(recipe_id=saved['id']))
        self.store.mutate('canvas', conversation['id'], lambda r: r['messages'].extend([
            {'role': 'user', 'content': 'OLD_PRODUCT_BRAND', 'references': snapshot()},
            {'role': 'user', 'content': '本次标题全新系列，右侧保留说明', 'references': snapshot(),
             'image_preferences': {'recipe_id': saved['id']}}]))
        calls = []
        async def llm(payload):
            calls.append(payload)
            return {'text': json.dumps({'kind': 'chat', 'reply': '继续讨论本次文案。'}, ensure_ascii=False)}
        discussion = CanvasAgentDiscussion(self.store, llm, provider, image_workflow=object(), recipe_store=recipes)
        await discussion.send('canvas', conversation['id'], MessageRequest(message='继续讨论本次右侧说明',
            reference_node_ids=['product'], chat_provider='fixture', chat_model='chat', generation_defaults=defaults(), recipe_id=saved['id']))
        sent = json.dumps(calls, ensure_ascii=False)
        self.assertIn('本次标题全新系列', sent)
        self.assertIn('OLD_PRODUCT_BRAND', sent)
        self.assertNotIn('/assets/case157.png', sent)
        self.assertNotIn('/out1.png', sent)

    async def test_retired_recipe_without_new_attachment_allows_discussion_without_importing_old_inputs(self):
        recipes = self.recipes()
        saved = recipes.save('canvas', self.payload)
        conversation = self.store.create('canvas')
        self.store.patch('canvas', conversation['id'], ConversationPatch(recipe_id=saved['id']))
        calls = []
        answer = {'kind': 'chat', 'reply': '可以先讨论这份布局；生成前再附本次产品图。'}
        async def llm(payload):
            calls.append(deepcopy(payload))
            return {'text': json.dumps(answer, ensure_ascii=False)}
        discussion = CanvasAgentDiscussion(self.store, llm, provider, image_workflow=object(), recipe_store=recipes)
        async def send(message):
            return await discussion.send('canvas', conversation['id'], MessageRequest(message=message,
                reference_node_ids=[], chat_provider='fixture', chat_model='chat',
                generation_defaults=defaults(), recipe_id=saved['id']))
        try:
            result = await send('先讨论这份方案的文字层级，不生成')
        except HTTPException as exc:
            self.fail('纯讨论不应要求先添加产品图：' + str(exc.detail))
        self.assertEqual(result['kind'], 'chat')
        self.assertNotIn('plan', result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['images'], [])
        self.assertNotIn('/out1.png', json.dumps(calls))
        record = self.store.get('canvas', conversation['id'])
        self.assertEqual(record['plans'], [])
        self.assertEqual(record['runs'], [])


if __name__ == '__main__':
    unittest.main()
