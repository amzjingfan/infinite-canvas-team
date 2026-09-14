import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_agent import CanvasAgentDiscussion, CanvasAgentStore, MessageRequest
from test_canvas_creative import task


class CreativeDiscussionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.canvas = {'id': 'canvas', 'nodes': [
            {'id': 'upstream', 'type': 'smart-prompt', 'text': 'OLD UGC VIDEO'},
            {'id': 'product', 'type': 'smart-image', 'runPrompt': 'OLDER IMAGE PROMPT',
             'images': [{'url': '/assets/product.png', 'kind': 'image', 'name': 'product.png'}]}],
            'connections': [{'from': 'upstream', 'to': 'product', 'kind': 'input'}]}
        self.store = CanvasAgentStore(lambda: self.temp.name, lambda _: copy.deepcopy(self.canvas))
        self.record = self.store.create('canvas')
        self.calls = []
        self.answer = {'kind': 'plan', 'reply': '生成独立信息图。', 'summary': 'Product infographic', 'tasks': [task()]}
        self.preflight = {'issues': []}
        self.discussion = CanvasAgentDiscussion(self.store, self.llm, self.provider)

    @staticmethod
    def provider(pid):
        return {'id': pid, 'protocol': 'openai', 'enabled': True,
                'chat_models': ['deepseek-fixture'], 'image_models': ['image'], 'video_models': ['video']}

    async def llm(self, payload):
        self.calls.append(copy.deepcopy(payload))
        return {'text': json.dumps(self.preflight if 'CREATIVE_TASK:preflight' in payload['system_prompt'] else self.answer)}

    def payload(self, **changes):
        return MessageRequest(**{'message': '保留产品外观，生成信息海报，配卖点说明', 'reference_node_ids': ['product'],
            'chat_provider': 'fixture', 'chat_model': 'deepseek-fixture',
            'generation_defaults': {'image': {'provider': 'fixture', 'model': 'image'},
                                    'video': {'provider': 'fixture', 'model': 'video'}}, **changes})

    async def send(self, **changes):
        return await self.discussion.send('canvas', self.record['id'], self.payload(**changes))

    async def test_default_discussion_plans_asset_tasks_with_direct_attachments_and_frozen_contract(self):
        result = await self.send()
        plan = result['plan']
        self.assertEqual(plan['mode'], 'creation')
        self.assertEqual(plan['contract_version'], 2)
        self.assertEqual([o['op'] for o in plan['operations']], ['generate'])
        self.assertEqual([r['id'] for r in plan['reference_snapshot']], ['product'])
        self.assertEqual(plan['preflight']['status'], 'passed')
        self.assertEqual(plan['request'], self.payload().message)
        self.assertNotIn('OLD UGC VIDEO', json.dumps(self.calls))
        self.assertNotIn('OLDER IMAGE PROMPT', json.dumps(self.calls))
        self.assertTrue(all(call['model'] == 'deepseek-fixture' for call in self.calls))
        self.assertEqual(self.canvas['connections'], [{'from': 'upstream', 'to': 'product', 'kind': 'input'}])
        self.assertEqual(self.store.get('canvas', self.record['id'])['runs'], [])

    async def test_unresolved_semantic_conflict_never_becomes_confirmable_and_correction_is_bounded(self):
        self.preflight = {'issues': [{'task_id': 'poster', 'detail': '版式要求说明文字，但提示词禁止一切说明文字。',
                                     'correction': '保留说明版式，删除禁止说明文字的冲突要求。'}]}
        with self.assertRaises(HTTPException) as error:
            await self.send()
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn('冲突', error.exception.detail)
        self.assertLessEqual(len(self.calls), 4)
        self.assertEqual(self.store.get('canvas', self.record['id'])['plans'], [])
        retry = self.calls[2]
        self.assertNotIn(self.preflight['issues'][0]['detail'], retry['system_prompt'])
        self.assertEqual(json.loads(retry['message'])['previous_answer'], self.answer)

    async def test_source_correction_receives_failed_draft_and_persists_only_the_corrected_plan(self):
        failed = copy.deepcopy(self.answer)
        failed['tasks'][0]['requirements'][0]['evidence'] = '保留原产品的外观'
        failed['tasks'][0]['copy']['exact_text'][0].update(source='user', evidence='Imagined specification')

        async def correcting_llm(payload):
            self.calls.append(copy.deepcopy(payload))
            if 'CREATIVE_TASK:preflight' in payload['system_prompt']:
                return {'text': json.dumps({'issues': []})}
            if len(self.calls) == 1:
                return {'text': json.dumps(failed)}
            return {'text': json.dumps(self.answer)}

        self.discussion.run_llm = correcting_llm
        result = await self.send()
        self.assertEqual(len(self.calls), 3)
        data = json.loads(self.calls[1]['message'])
        self.assertEqual(data.get('previous_answer'), failed)
        self.assertEqual(data['request'], self.payload().message)
        self.assertIn('tasks[0].requirements[0].evidence', data.get('correction', ''))
        self.assertIn('tasks[0].copy.exact_text[0].evidence', data.get('correction', ''))
        contract = result['plan']['operations'][0]['contract']
        self.assertEqual(contract['requirements'][0]['source'], 'user')
        self.assertEqual(contract['requirements'][0]['evidence'], '保留产品外观')
        self.assertEqual(contract['copy']['exact_text'][0]['source'], 'proposal')
        self.assertEqual(self.calls[0]['images'], self.calls[1]['images'])
        self.assertTrue(all(call['model'] == 'deepseek-fixture' for call in self.calls))
        record = self.store.get('canvas', self.record['id'])
        self.assertEqual(len(record['plans']), 1)
        self.assertEqual(record['runs'], [])

    async def test_settings_correction_receives_exact_fields_and_the_invalid_json_draft(self):
        failed = copy.deepcopy(self.answer)
        failed['tasks'][0]['settings'].pop('aspect_ratio')
        failed['tasks'][0]['settings']['size'] = '1152x2048'

        async def correcting_llm(payload):
            self.calls.append(copy.deepcopy(payload))
            if 'CREATIVE_TASK:preflight' in payload['system_prompt']:
                return {'text': '{"issues":[]}'}
            return {'text': json.dumps(failed if len(self.calls) == 1 else self.answer)}

        self.discussion.run_llm = correcting_llm
        result = await self.send()
        self.assertEqual(len(self.calls), 3)
        retry = json.loads(self.calls[1]['message'])
        self.assertEqual(retry['previous_answer'], failed)
        self.assertIn('tasks[0].settings.aspect_ratio', retry['correction'])
        self.assertIn('tasks[0].settings.size', retry['correction'])
        self.assertNotIn('计划无效', retry['correction'])
        settings = result['plan']['operations'][0]['settings']
        self.assertEqual((settings['size'], settings['quality']), ('1152x2048', 'high'))

    async def test_invalid_parameters_keep_durable_diagnostics_without_a_confirmable_plan(self):
        self.answer['tasks'][0]['settings']['size'] = 'made-up-size'
        with self.assertRaises(HTTPException) as error:
            await self.send()
        self.assertNotIn('说明修改要求', error.exception.detail)
        self.assertNotIn('tasks[', error.exception.detail)
        self.assertEqual(len(self.calls), 2)
        record = self.store.get('canvas', self.record['id'])
        diagnostic = record['messages'][-1].get('planning_diagnostics', {})
        attempts = diagnostic.get('attempts', [])
        self.assertEqual(len(attempts), 2)
        self.assertEqual(diagnostic.get('model'), 'deepseek-fixture')
        self.assertEqual([a['stage'] for a in attempts], ['planning', 'planning'])
        self.assertIn('tasks[0].settings.size', attempts[-1]['detail'])
        self.assertEqual(attempts[-1]['candidate']['tasks'][0]['settings']['size'], 'made-up-size')
        self.assertEqual(record['plans'], [])
        self.assertEqual(record['runs'], [])
        # Diagnostics are retained for inspection, not replayed as conversation instructions.
        self.answer = {'kind': 'chat', 'reply': '继续讨论。'}
        await self.send(message='继续讨论')
        self.assertNotIn('made-up-size', json.dumps(self.calls[-1]['messages']))

    async def test_invalid_json_numbers_cannot_corrupt_the_saved_diagnostic_or_conversation(self):
        self.answer['tasks'][0]['settings']['size'] = float('nan')
        with self.assertRaises(HTTPException):
            await self.send()
        record = self.store.get('canvas', self.record['id'])
        try:
            json.dumps(record, ensure_ascii=False, allow_nan=False)
        except ValueError:
            self.fail('Rejected model output corrupted the conversation JSON through diagnostics')
        attempts = record['messages'][-1]['planning_diagnostics']['attempts']
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(a['candidate'] is None for a in attempts))

    async def test_parameter_repair_does_not_consume_the_content_conflict_correction(self):
        conflict = copy.deepcopy(self.answer)
        conflict['tasks'][0]['prompt'] = '禁止烟雾，同时必须轻雾气光晕环绕产品。'
        conflict['tasks'][0]['copy']['exact_text'][0]['text'] = '足形硅胶垫'
        bad_parameters = copy.deepcopy(conflict)
        bad_parameters['tasks'][0]['settings']['size'] = '1152x2048'
        corrected = copy.deepcopy(self.answer)
        corrected['tasks'][0]['prompt'] = '中性柔和虚化光晕，无可见烟雾颗粒。'
        corrected['tasks'][0]['copy']['exact_text'][0]['text'] = '足形垫'
        issues = [{'task_id': 'poster', 'detail': '禁止烟雾与必须轻雾气光晕相互矛盾，硅胶材质没有文字资料依据。',
                   'correction': '统一为中性柔和虚化光晕，无可见烟雾颗粒，并把足形硅胶垫改为足形垫。'}]
        plans = [bad_parameters, conflict, corrected]

        async def staged_llm(payload):
            self.calls.append(copy.deepcopy(payload))
            if 'CREATIVE_TASK:preflight' in payload['system_prompt']:
                prompt = json.loads(payload['message'])['tasks'][0]['prompt']
                return {'text': json.dumps({'issues': issues if '硅胶' in prompt else []})}
            return {'text': json.dumps(plans.pop(0))}

        self.discussion.run_llm = staged_llm
        try:
            result = await self.send()
        except HTTPException as error:
            self.fail('A parameter repair must not consume content correction: ' + str(error.detail))
        self.assertEqual(len(self.calls), 5)
        second_correction = json.loads(self.calls[3]['message'])
        self.assertEqual(second_correction['previous_answer'], conflict)
        self.assertIn('硅胶', second_correction['correction'])
        op = result['plan']['operations'][0]
        self.assertNotIn('硅胶', op['prompt'])
        self.assertNotIn('必须轻雾', op['prompt'])
        self.assertEqual(op['contract']['requirements'][0]['evidence'], '保留产品外观')
        self.assertEqual(op['settings']['model'], 'image')
        self.assertEqual(result['plan']['preflight']['status'], 'passed')
        self.assertEqual(len(result['plan']['preflight']['attempts']), 2)
        self.assertEqual(self.store.get('canvas', self.record['id'])['runs'], [])

    async def test_preflight_format_is_repaired_without_replanning_the_valid_task(self):
        reviews = [{'issues': 'invalid-type'}, {'issues': []}]

        async def malformed_review_llm(payload):
            self.calls.append(copy.deepcopy(payload))
            return {'text': json.dumps(reviews.pop(0) if 'CREATIVE_TASK:preflight' in payload['system_prompt'] else self.answer)}

        self.discussion.run_llm = malformed_review_llm
        result = await self.send()
        plans = [p for p in self.calls if 'CREATIVE_TASK:plan' in p['system_prompt']]
        checks = [p for p in self.calls if 'CREATIVE_TASK:preflight' in p['system_prompt']]
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0]['images'], checks[1]['images'])
        self.assertEqual(json.loads(checks[1]['message'])['previous_answer'], {'issues': 'invalid-type'})
        self.assertEqual(result['plan']['preflight']['status'], 'passed')

    async def test_repeated_preflight_format_failure_stays_bounded_and_never_becomes_a_plan(self):
        self.preflight = {'issues': 'invalid-type'}
        with self.assertRaises(HTTPException):
            await self.send()
        self.assertEqual(sum('CREATIVE_TASK:plan' in p['system_prompt'] for p in self.calls), 1)
        self.assertEqual(sum('CREATIVE_TASK:preflight' in p['system_prompt'] for p in self.calls), 2)
        record = self.store.get('canvas', self.record['id'])
        self.assertEqual(record['plans'], [])
        self.assertEqual(record['runs'], [])
        self.assertEqual(record['messages'][-1]['planning_diagnostics']['attempts'][-1]['stage'], 'preflight')

    async def test_uncorrected_source_stops_after_one_retry_without_preflight_or_generation(self):
        self.answer['tasks'][0]['requirements'][0]['evidence'] = '保留原产品的外观'
        with self.assertRaises(HTTPException) as error:
            await self.send()
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn('来源', error.exception.detail)
        self.assertNotIn('tasks[', error.exception.detail)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all('CREATIVE_TASK:preflight' not in call['system_prompt'] for call in self.calls))
        record = self.store.get('canvas', self.record['id'])
        self.assertEqual(record['plans'], [])
        self.assertEqual(record['runs'], [])

    async def test_unresolved_reference_keeps_diagnostics_internal_and_retries_with_actual_source_catalog(self):
        self.answer['tasks'][0]['requirements'][0].update(source='reference', evidence='案例999')
        with self.assertLogs('canvas_creative', level='WARNING') as diagnostics:
            with self.assertRaises(HTTPException) as error:
                await self.send()
        self.assertNotIn('tasks[', error.exception.detail)
        self.assertNotIn('source=', error.exception.detail)
        self.assertIn('未生成图片', error.exception.detail)
        self.assertIn('tasks[0].requirements[0].evidence', '\n'.join(diagnostics.output))
        self.assertIn('案例999', '\n'.join(diagnostics.output))
        self.assertEqual(len(self.calls), 2)
        retry = json.loads(self.calls[1]['message'])
        self.assertEqual([s['id'] for s in retry['reference_sources']], ['product'])
        self.assertIn('product.png', retry['reference_sources'][0]['aliases'])
        self.assertIn('案例999', retry['correction'])
        self.assertEqual(retry['previous_answer'], self.answer)
        record = self.store.get('canvas', self.record['id'])
        self.assertEqual(record['plans'], [])
        self.assertEqual(record['runs'], [])
        self.assertEqual(record['messages'][-1]['content'], error.exception.detail)

    async def test_default_requests_cannot_receive_workflow_operations_from_model(self):
        self.answer = {'kind': 'plan', 'reply': 'graph', 'summary': 'graph', 'operations': [
            {'id': 'p', 'op': 'create_prompt', 'text': 'Example'},
            {'id': 'm', 'op': 'create_media', 'kind': 'image', 'reference_node_ids': []},
            {'id': 'c', 'op': 'connect', 'from': 'p', 'to': 'm'},
            {'id': 'g', 'op': 'generate', 'node': 'm', 'settings': task()['settings']}]}
        with self.assertRaises(HTTPException):
            await self.send()
        self.assertEqual(self.store.get('canvas', self.record['id'])['plans'], [])
        self.calls.clear()
        result = await self.send(message='帮我搭建一个产品生图工作流')
        self.assertEqual(result['plan']['mode'], 'workflow')
        self.assertEqual(len(result['plan']['operations']), 4)
        self.assertIn('upstream', [r['id'] for r in result['plan']['reference_snapshot']])

    async def test_a_later_creation_request_does_not_inherit_a_previous_workflow_protocol(self):
        self.store.mutate('canvas', self.record['id'], lambda record: record['plans'].append(
            {'id': 'oldplan', 'version': 1, 'mode': 'workflow', 'operations': [], 'status': 'proposed',
             'reference_snapshot': [{'text': 'UNRELATED OLD WORKFLOW'}]}))
        result = await self.send()
        self.assertEqual(result['plan']['mode'], 'creation')
        self.assertNotIn('UNRELATED OLD WORKFLOW', json.dumps(self.calls))

    async def test_complete_bound_prompt_is_validated_before_preflight_and_confirmation(self):
        from canvas_creative import attachment_snapshot, plan_creative
        snapshot = attachment_snapshot(self.canvas, ['product'])
        def oversized_binding(plan):
            plan['operations'][0]['prompt'] += 'X' * 20000
        with self.assertRaises(HTTPException) as error:
            await plan_creative(self.llm, {'provider':'fixture','model':'deepseek-fixture',
                'system_prompt':'CREATIVE_TASK:plan','images':['/assets/product.png'],'messages':[]},
                snapshot, self.payload().message, lambda raw, kind: raw, oversized_binding)
        self.assertEqual(error.exception.status_code, 422)
        self.assertTrue(all('CREATIVE_TASK:preflight' not in call['system_prompt'] for call in self.calls))


if __name__ == '__main__':
    unittest.main()
