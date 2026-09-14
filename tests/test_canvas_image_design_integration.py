import asyncio
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


def issue(kind='suggestion', path='layout.subject_count', **changes):
    return {'id': 'collage', 'kind': kind, 'paths': [path],
            'evidence': '产品参考包含多个视角，定稿设计仍为一个主体。',
            'correction': '成图后留意是否出现重复主体，不增加设计禁令。', **changes}


class ResearchBoundary:
    async def research(self, payload, refs, invocation):
        return research()

    @staticmethod
    def planning_context(context):
        return 'LEGACY WHOLE RESEARCH AND PRIMARY STYLE INSTRUCTION'

    @staticmethod
    def bind_plan(plan, context):
        raise AssertionError('v3 must not bind legacy case purposes')


class DesignDiscussionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.canvas = {'id': 'canvas', 'nodes': snapshot(), 'connections': []}
        self.store = CanvasAgentStore(lambda: self.temp.name, lambda _: deepcopy(self.canvas))
        self.record = self.store.create('canvas')
        self.calls = []
        self.answers = [{'kind': 'plan', 'design_card': card()}]
        self.reviews = [{'issues': []}]
        self.discussion = CanvasAgentDiscussion(self.store, self.llm, provider,
            lambda message, skills: {'instructions': 'CANONICAL_SKILL_INSTRUCTIONS', 'skills': research()['skills']},
            ResearchBoundary())

    async def llm(self, payload):
        self.calls.append(deepcopy(payload))
        responses = self.reviews if ':preflight' in payload['system_prompt'] else self.answers
        response = responses.pop(0) if len(responses) > 1 else responses[0]
        return {'text': response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)}

    def payload(self, **changes):
        return MessageRequest(**{'message': '保留产品外观，生成海报', 'reference_node_ids': ['product'],
            'skill_ids': ['gpt-image-2-style-library'], 'chat_provider': 'fixture', 'chat_model': 'chat',
            'generation_defaults': defaults(), **changes})

    async def send(self, **changes):
        return await self.discussion.send('canvas', self.record['id'], self.payload(**changes))

    async def test_default_skill_route_compiles_once_without_legacy_instruction_or_summary_call(self):
        result = await self.send()
        self.assertEqual(result['plan']['contract_version'], 3)
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn('LEGACY WHOLE', json.dumps(self.calls))
        self.assertNotIn('CANONICAL_SKILL_INSTRUCTIONS', self.calls[0]['system_prompt'])
        self.assertEqual(self.calls[0]['images'], ['/assets/product.png', '/assets/case157.png'])
        self.assertEqual(self.calls[1]['images'], ['/assets/product.png'])
        self.assertEqual(self.store.get('canvas', self.record['id'])['runs'], [])

    async def test_suggestions_and_uncertainty_are_saved_without_replanning_or_blocking(self):
        self.reviews = [{'issues': [issue(), issue('uncertain', 'subject.name', id='markings')]}]
        result = await self.send()
        self.assertEqual(result['plan']['preflight']['status'], 'ready_with_notes')
        self.assertEqual(len(self.calls), 2)
        attempts = result['conversation']['messages'][-1]['planning_diagnostics']['attempts']
        self.assertEqual([a['status'] for a in attempts], ['passed', 'ready_with_notes'])

    async def test_actual_conflict_uses_one_targeted_patch_preserving_other_decisions(self):
        bad = card()
        bad['appearance']['background'] = '黑色背景'
        conflict = issue('conflict', 'appearance.background', id='background',
            evidence='当前用户明确要求浅米白背景，设计字段却为黑色背景。', correction='将背景改为浅米白背景。')
        self.answers = [{'kind': 'plan', 'design_card': bad},
                        {'changes': [{'path': 'appearance.background', 'value': '浅米白背景'}]}]
        self.reviews = [{'issues': [conflict]}, {'issues': []}]
        result = await self.send(message='保留产品外观，生成海报，使用浅米白背景')
        self.assertEqual(result['plan']['design_card'], card())
        self.assertEqual(len(self.calls), 4)
        patch = json.loads(self.calls[2]['message'])
        self.assertEqual(patch['allowed_paths'], ['appearance.background'])
        self.assertEqual(result['plan']['preflight']['attempts'][0]['issues'], [conflict])

    async def test_conflict_cannot_disappear_as_suggestion_without_changing_affected_value(self):
        conflict = issue('conflict', 'appearance.background', id='background',
            evidence='原请求规定浅色背景，与定稿字段黑色背景相冲突。', correction='修正背景为浅色。')
        self.answers = [{'kind': 'plan', 'design_card': card()}, {'changes': []}]
        self.reviews = [{'issues': [conflict]}, {'issues': [dict(conflict, kind='suggestion')]}]
        with self.assertRaises(HTTPException) as error:
            await self.send()
        self.assertIn('未生成图片', error.exception.detail)
        self.assertEqual(self.store.get('canvas', self.record['id'])['plans'], [])
        self.assertLessEqual(len(self.calls), 6)

    async def test_patch_cannot_delete_real_user_requirement_to_resolve_conflict(self):
        conflict = issue('conflict', 'constraints', correction='删除保留产品外观的要求。')
        self.answers = [{'kind': 'plan', 'design_card': card()}, {'changes': [{'path': 'constraints', 'value': []}]}]
        self.reviews = [{'issues': [conflict]}]
        with self.assertRaises(HTTPException):
            await self.send()
        self.assertEqual(self.store.get('canvas', self.record['id'])['plans'], [])

    async def test_invalid_preflight_path_repairs_format_not_design_and_budget_never_resets(self):
        self.reviews = [{'issues': [issue(path='nonexistent.path')]}, {'issues': []}]
        result = await self.send()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(result['plan']['preflight']['status'], 'passed')
        self.assertIn('paths', result['conversation']['messages'][-1]['planning_diagnostics']['attempts'][1]['detail'])

    async def test_structure_content_and_review_format_each_have_independent_single_repair(self):
        malformed = card()
        malformed['render_options']['size'] = 'unexpected'
        self.answers = [{'kind': 'plan', 'design_card': malformed}, {'kind': 'plan', 'design_card': card()},
            {'changes': [{'path': 'appearance.background', 'value': '统一浅灰背景'}]}]
        self.reviews = ['not json', {'issues': [issue('conflict', 'appearance.background')]}, {'issues': []}]
        result = await self.send()
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(result['plan']['preflight']['status'], 'passed')
        self.assertEqual(result['plan']['design_card']['constraints'], card()['constraints'])

    async def test_discussion_does_not_create_plan_and_failed_assistant_drafts_do_not_reenter_history(self):
        bad = card()
        bad['subject']['observations'] = [{'id': 'bad', 'category': 'identity', 'source': 'user',
            'text': 'REJECTED_MODEL_INVENTION', 'evidence': 'not supplied'}]
        self.answers = [{'kind': 'plan', 'design_card': bad}]
        with self.assertRaises(HTTPException):
            await self.send()
        self.store.mutate('canvas', self.record['id'], lambda r: r['messages'].append(
            {'role': 'assistant', 'content': 'REJECTED_ASSISTANT_ANALYSIS', 'planning_diagnostics': {'attempts': []}}))
        self.answers = [{'kind': 'chat', 'reply': '可以继续讨论版面。'}]
        result = await self.send(message='保留前面的浅色方向，继续讨论')
        self.assertEqual(result['kind'], 'chat')
        self.assertNotIn('plan', result)
        self.assertNotIn('REJECTED_', json.dumps(self.calls[-1]))
        self.assertIn('保留产品外观', json.dumps(self.calls[-1], ensure_ascii=False))

    async def test_retired_case_preference_does_not_invalidate_current_plans(self):
        result = await self.send()
        self.store.patch('canvas', self.record['id'], ConversationPatch(case_input_mode='single_case'))
        record = self.store.get('canvas', self.record['id'])
        self.assertEqual(record['plans'][-1]['status'], 'proposed')
        self.assertEqual(record['case_input_mode'], 'design_only')
        self.assertEqual(result['plan']['case_input_mode'], 'design_only')

    async def test_retired_preference_write_during_planning_does_not_invalidate_late_proposal(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        original_llm = self.discussion.run_llm
        async def gated_llm(payload):
            entered.set()
            await resume.wait()
            return await original_llm(payload)
        self.discussion.run_llm = gated_llm
        pending = asyncio.create_task(self.send())
        await asyncio.wait_for(entered.wait(), 1)
        self.store.patch('canvas', self.record['id'], ConversationPatch(case_input_mode='single_case'))
        resume.set()
        result = await pending
        self.assertEqual(result['plan']['status'], 'proposed')

    async def test_retired_case_text_shortcut_does_not_add_implicit_media(self):
        design = card()
        self.answers = [{'kind': 'plan', 'design_card': design}]
        result = await self.send(message='保留产品外观，生成海报，将案例157原图实际传入生图作为构图参考')
        self.assertEqual(result['plan']['case_input_mode'], 'design_only')
        self.assertEqual([m['url'] for m in result['plan']['operations'][0]['generation_inputs']], ['/assets/product.png'])

    async def test_explicit_revision_can_read_previous_proposed_design_without_promoting_it_to_user_quote(self):
        first = await self.send()
        self.answers = [{'kind': 'chat', 'reply': '可以继续调整这份未确认设计。'}]
        await self.send(message='继续修改上一份方案，把背景调亮一点，先讨论')
        data = json.loads(self.calls[-1]['message'])
        self.assertEqual(data.get('design_target', {}).get('design_card'), first['plan']['design_card'])
        self.assertEqual(data['design_target']['status'], 'proposed')
        self.assertEqual(data['design_target']['source'], 'previous_proposal_not_user_quote')

    async def test_source_correction_with_explicit_id_in_prose_reaches_preflight_without_extra_replanning(self):
        initial = card()
        initial['subject']['observations'] = [{'id': 'color', 'category': 'identity', 'source': 'reference',
            'text': '粉白外观', 'evidence': '参考图左下格可见粉白外观'}]
        corrected = deepcopy(initial)
        corrected['subject']['observations'][0]['evidence'] = '本次附件（source_id: product）左下格可见粉白外观。'
        self.answers = [{'kind': 'plan', 'design_card': initial}, {'kind': 'plan', 'design_card': corrected}]
        try:
            result = await self.send()
        except HTTPException as exc:
            self.fail('带明确 source_id 的纠正稿应通过来源阶段：' + str(exc.detail))
        self.assertEqual(len(self.calls), 3)
        self.assertIn('IMAGE_DESIGN:preflight', self.calls[-1]['system_prompt'])
        self.assertEqual(result['plan']['design_card']['subject']['observations'][0]['evidence'], 'product')
        self.assertEqual([m['url'] for m in result['plan']['operations'][0]['generation_inputs']], ['/assets/product.png'])
        attempts = result['conversation']['messages'][-1]['planning_diagnostics']['attempts']
        self.assertEqual([a['status'] for a in attempts], ['rejected', 'passed', 'passed'])
        self.assertIn('subject.observations[0].evidence', attempts[0]['detail'])
        self.assertEqual(attempts[1]['candidate']['design_card']['subject']['observations'][0]['evidence'],
                         corrected['subject']['observations'][0]['evidence'])
        self.assertEqual(self.store.get('canvas', self.record['id'])['runs'], [])


if __name__ == '__main__':
    unittest.main()
