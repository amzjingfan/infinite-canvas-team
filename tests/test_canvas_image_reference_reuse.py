"""Reference-driven image reuse: persisted source data is advice, never a lock."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_agent import CanvasAgentDiscussion, CanvasAgentStore, ConversationPatch, MessageRequest
from image_design_fixtures import card, defaults, provider, research, snapshot


class ReferenceReuseTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.canvas = {'id': 'canvas', 'nodes': snapshot(), 'connections': []}
        self.store = CanvasAgentStore(lambda: self.temp.name, lambda _: deepcopy(self.canvas))
        self.source = self.store.create('canvas')
        old = card()
        old['subject']['name'] = 'OLD_PRODUCT_SECRET'
        old['copy']['exact_text'][0]['text'] = 'OLD_COPY_SECRET'
        old['appearance']['material_rendering'] = 'OLD_MATERIAL_SECRET'
        self.source_run = {'id': 'source_run', 'canvas_id': 'canvas', 'conversation_id': self.source['id'],
            'plan_id': 'old_plan', 'version': 1, 'contract_version': 3, 'status': 'completed',
            'design_card': old, 'case_input_mode': 'single_case', 'image_workflow': research(),
            'operations': [{'id': 'image1', 'kind': 'image', 'op': 'generate', 'prompt': 'OLD_PROMPT_SECRET'}],
            'steps': [{'operation_id': 'image1', 'status': 'completed', 'result': {
                'media': [{'url': '/old-v1.png', 'kind': 'image'}, {'url': '/old-v2.png', 'kind': 'image'}],
                'attempts': [{'round': 0, 'prompt': 'OLD_PROMPT_SECRET', 'media': [{'url': '/old-v1.png', 'kind': 'image'}]},
                             {'round': 1, 'prompt': 'OLD_EDIT_SECRET', 'media': [{'url': '/old-v2.png', 'kind': 'image'}]}]}}]}
        self.store.mutate('canvas', self.source['id'], lambda r: (
            r['runs'].append(deepcopy(self.source_run)),
            r['messages'].append({'role': 'user', 'content': 'OLD_CONVERSATION_SECRET'})))
        self.output = {'id': 'output', 'type': 'smart-image', 'images': [{'url': '/old-v2.png', 'kind': 'image'}],
            'agent': {'canvasId': 'canvas', 'conversationId': self.source['id'], 'runId': 'source_run',
                      'operationId': 'image1', 'role': 'output'},
            'text': 'UNTRUSTED_NODE_PROMPT', 'runSettings': {'prompt': 'UNTRUSTED_NODE_PROMPT'}}
        self.canvas['nodes'].append(self.output)
        self.record = self.store.create('canvas')
        self.calls, self.research_calls = [], []
        self.answer = {'kind': 'chat', 'reply': '可以按本次要求调整。'}
        self.review = {'issues': []}
        parent = self
        class Workflow:
            async def research(self, payload, refs, invocation):
                parent.research_calls.append(payload.message)
                return research()
            @staticmethod
            def planning_context(context):
                return ''
        self.discussion = CanvasAgentDiscussion(self.store, self.llm, provider,
            lambda message, skills: {'instructions': '', 'skills': research()['skills'] if skills else []}, Workflow())

    async def llm(self, payload):
        self.calls.append(deepcopy(payload))
        response = self.review if ':preflight' in payload['system_prompt'] else self.answer
        return {'text': json.dumps(response, ensure_ascii=False)}

    async def send(self, **changes):
        payload = MessageRequest(**{'message': '沿用成图的布局，背景改为黑色，保留产品外观，标题改为新标题',
            'reference_node_ids': ['output', 'product'], 'chat_provider': 'fixture', 'chat_model': 'chat',
            'generation_defaults': defaults(), **changes})
        try:
            return await self.discussion.send('canvas', self.record['id'], payload)
        except HTTPException as exc:
            self.fail('Reference reuse should reach its existing planning boundary: ' + str(exc.detail))

    def new_design(self):
        value = card()
        value['subject']['references'].append({'source_id': 'output', 'role': 'composition'})
        value['appearance']['background'] = '黑色背景'
        value['case_uses'] = []
        value['copy']['exact_text'] = [{'text': '新标题', 'placement': '顶部标题', 'source': 'user', 'evidence': '新标题'}]
        return value

    async def test_reference_links_the_exact_version_without_importing_old_identity_copy_prompts_or_chat(self):
        result = await self.send(message='分析这张图的布局，先不生成')
        self.assertEqual(result['kind'], 'chat')
        data = json.loads(self.calls[0]['message'])
        references = data.get('reference_designs', [])
        self.assertEqual(len(references), 1, 'generated-image design context is missing')
        self.assertEqual(references[0]['source_id'], 'output')
        self.assertEqual(references[0]['media_url'], '/old-v2.png')
        self.assertEqual(references[0]['source']['round'], 1)
        self.assertEqual(references[0]['design_hints']['appearance']['background'], '浅米白背景')
        self.assertNotIn('SECRET', json.dumps(self.calls))
        self.assertNotIn('UNTRUSTED_NODE_PROMPT', json.dumps(self.calls))
        self.assertEqual(self.research_calls, [])
        self.assertEqual(self.store.get('canvas', self.source['id'])['runs'][0], self.source_run)
        self.assertEqual(self.store.get('canvas', self.record['id'])['runs'], [])
        self.assertFalse((Path(self.temp.name) / 'agent/canvas/recipes').exists())

    async def test_new_requirements_override_original_design_and_only_explicit_images_reach_generation(self):
        self.answer = {'kind': 'plan', 'design_card': self.new_design()}
        result = await self.send(recipe_id='recipe_old_hidden', case_input_mode='single_case')
        plan = result['plan']
        self.assertEqual(plan['contract_version'], 3)
        self.assertEqual(plan['design_card']['appearance']['background'], '黑色背景')
        self.assertEqual(plan['design_card']['copy']['exact_text'][0]['text'], '新标题')
        self.assertEqual(plan['case_input_mode'], 'design_only')
        self.assertNotIn('recipe_id', plan)
        self.assertEqual([m['url'] for m in plan['operations'][0]['generation_inputs']],
                         ['/old-v2.png', '/assets/product.png'])
        self.assertEqual(plan['operations'][0]['generation_inputs'][0]['role'], 'composition')
        self.assertEqual(plan['image_workflow']['cases'], [])
        self.assertEqual(plan['operations'][0]['settings']['model'], 'image')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]['images'], ['/old-v2.png', '/assets/product.png'])
        self.assertEqual(self.research_calls, [])
        self.assertNotIn('SECRET', json.dumps(plan))

    async def test_each_referenced_output_version_keeps_its_own_media_association(self):
        self.output['images'] = [{'url': '/old-v2.png', 'kind': 'image'}, {'url': '/old-v1.png', 'kind': 'image'}]
        await self.send(message='只分析这两版图的差别')
        refs = json.loads(self.calls[0]['message']).get('reference_designs', [])
        self.assertEqual([(r['media_url'], r['source']['round']) for r in refs], [('/old-v2.png', 1), ('/old-v1.png', 0)])

    async def test_replaced_media_and_cross_canvas_or_missing_owner_fall_back_to_plain_image(self):
        original = deepcopy(self.output)
        variants = [{'images': [{'url': '/external.png', 'kind': 'image'}]},
                    {'agent': {**original['agent'], 'canvasId': 'other'}},
                    {'agent': {**original['agent'], 'conversationId': 'missing'}},
                    {'agent': {**original['agent'], 'runId': 'missing'}}, {'agent': {}}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.output.clear()
                self.output.update(deepcopy(original))
                self.output.update(changes)
                self.calls.clear()
                result = await self.send(message='分析这张图片')
                self.assertEqual(result['kind'], 'chat')
                self.assertFalse(json.loads(self.calls[0]['message']).get('reference_designs'))
                self.assertEqual(self.calls[0]['images'][0], self.output['images'][0]['url'])

    async def test_reference_can_still_plan_a_video_instead_of_forcing_an_image(self):
        self.answer = {'kind': 'plan', 'reply': '将图片做成视频。', 'summary': '图片转视频', 'tasks': [{
            'id': 'video1', 'kind': 'video', 'prompt': '让画面缓慢向前推进',
            'reference_node_ids': ['output'], 'reference_roles': {'output': 'content'}, 'requirements': [],
            'copy': {'exact_text': [], 'allow_additional_text': True, 'forbidden_text': [], 'exclusivity_evidence': ''},
            'settings': {'provider': 'fixture', 'model': 'video', 'count': 1, 'aspect_ratio': '16:9',
                         'resolution': '720p', 'duration': 5}}]}
        self.review = {'issues': []}
        result = await self.send(message='把这张图做成5秒视频')
        self.assertEqual(result['plan']['operations'][0]['kind'], 'video')
        self.assertNotIn('image_workflow', result['plan'])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.research_calls, [])

    async def test_explicit_skill_still_researches_but_legacy_single_case_options_no_longer_add_case_images(self):
        design = self.new_design()
        design['case_uses'] = card()['case_uses']
        self.answer = {'kind': 'plan', 'design_card': design}
        result = await self.send(skill_ids=['gpt-image-2-style-library'], case_input_mode='single_case',
            message='保留产品外观，标题改为新标题，将案例157原图实际传入生图作为构图参考')
        self.assertEqual(len(self.research_calls), 1)
        self.assertEqual(result['plan']['case_input_mode'], 'design_only')
        self.assertEqual([m['url'] for m in result['plan']['operations'][0]['generation_inputs']],
                         ['/old-v2.png', '/assets/product.png'])

    async def test_retired_preferences_do_not_supersede_a_new_plan_or_change_frozen_runs(self):
        self.store.mutate('canvas', self.record['id'], lambda r: r.update(
            case_input_mode='single_case', recipe_id='recipe_hidden', recipe_overrides=['background'],
            plans=[{'id': 'fresh', 'version': 1, 'status': 'proposed', 'contract_version': 3,
                    'case_input_mode': 'design_only'}], runs=[deepcopy(self.source_run)]))
        loaded = self.store.get('canvas', self.record['id'])
        self.assertEqual((loaded['case_input_mode'], loaded['recipe_id'], loaded['recipe_overrides']), ('design_only', '', []))
        changed = self.store.patch('canvas', self.record['id'], ConversationPatch(
            case_input_mode='single_case', recipe_id='another_hidden_recipe', recipe_overrides=['palette']))
        self.assertEqual(changed['plans'][0]['status'], 'proposed')
        self.assertEqual(changed['runs'][0], self.source_run)

    async def test_unconfirmed_legacy_recipe_and_case_plans_require_replanning_without_rewriting_history(self):
        self.store.mutate('canvas', self.record['id'], lambda r: r.update(plans=[
            {'id': 'old_recipe', 'version': 1, 'status': 'proposed', 'contract_version': 3, 'recipe_id': 'recipe_old'},
            {'id': 'old_case', 'version': 1, 'status': 'proposed', 'contract_version': 3, 'case_input_mode': 'single_case'},
            {'id': 'frozen', 'version': 1, 'status': 'confirmed', 'contract_version': 3, 'case_input_mode': 'single_case'}]))
        path = Path(self.temp.name) / 'agent/canvas' / (self.record['id'] + '.json')
        before = path.read_bytes()
        loaded = self.store.get('canvas', self.record['id'])
        self.assertEqual([p['status'] for p in loaded['plans']], ['superseded', 'superseded', 'confirmed'])
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
