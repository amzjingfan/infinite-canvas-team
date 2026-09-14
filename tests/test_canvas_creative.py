import copy
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import canvas_creative as creative
except ImportError:
    creative = None


def task(**updates):
    return {'id': 'poster', 'kind': 'image', 'prompt': 'Studio product infographic with callouts and detail panels.',
            'reference_node_ids': ['product'], 'reference_roles': {'product': 'identity'},
            'settings': {'provider': 'fixture', 'model': 'image', 'count': 1,
                         'aspect_ratio': '9:16', 'resolution': '2k'},
            'requirements': [{'id': 'identity', 'category': 'identity', 'source': 'user',
                              'text': 'Keep the product unchanged.', 'evidence': '保留产品外观'}],
            'copy': {'exact_text': [{'text': 'Textured Surface', 'placement': 'callout',
                                    'source': 'proposal', 'evidence': ''}],
                     'allow_additional_text': True, 'forbidden_text': [], 'exclusivity_evidence': ''}, **updates}


def references():
    return [{'id': 'product', 'type': 'smart-image', 'title': 'Product', 'text': '', 'input_node_ids': [],
             'images': [{'url': '/assets/product.png', 'kind': 'image', 'name': 'product.png'}]}]


class CreativeContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(creative, 'independent creative task contract is missing')

    def normalize(self, tasks=None, request='保留产品外观，生成信息海报，配卖点说明', **kwargs):
        return creative.normalize_creative_tasks(tasks or [task()], references(), request,
            lambda raw, kind: {**raw, 'size': '1152x2048', 'quality': 'high'}, **kwargs)

    def test_attachment_only_includes_selected_content_not_graph_ancestors_or_generation_metadata(self):
        canvas = {'nodes': [{'id': 'old', 'type': 'smart-prompt', 'text': 'Old video script'},
                            {'id': 'extra', 'images': [{'url': '/old.png', 'kind': 'image'}]},
                            {'id': 'product', 'type': 'smart-image', 'text': 'Node model prompt, not file content',
                             'runPrompt': 'Previous generation instructions', 'runSettings': {'model': 'old'},
                             'images': references()[0]['images']}],
                  'connections': [{'from': 'old', 'to': 'product'}, {'from': 'extra', 'to': 'product'}]}
        result = creative.attachment_snapshot(canvas, ['product'])
        self.assertEqual([r['id'] for r in result], ['product'])
        self.assertEqual(result[0]['text'], '')
        self.assertEqual(result[0]['input_node_ids'], [])
        self.assertNotIn('prompt_fallback', result[0])
        self.assertEqual(creative.attachment_snapshot(canvas, ['old'])[0]['text'], 'Old video script')
        self.assertEqual([r['id'] for r in creative.attachment_snapshot(canvas, ['product', 'old', 'product'])], ['product', 'old'])

    def test_only_an_explicit_affirmative_workflow_request_enables_workflow_mode(self):
        for message, want in [
            ('帮我搭建一个图生视频工作流', True), ('修改这个工作流的生成参数', True),
            ('Build an image generation workflow for me', True), ('帮我搭建一个工作流，但不要生成图片', True),
            ('不要搭建工作流，生成一张海报', False), ('不需要工作流，只生成素材', False),
            ('生成图片，然后检查、修图，再生成视频', False), ('解释这个工作流为什么失败', False),
            ('这张图来自某个工作流，帮我修改图片', False),
            ('Do not build a workflow; generate an image', False),
        ]:
            with self.subTest(message=message):
                self.assertEqual(creative.explicit_workflow_request(message), want)

    def test_normalized_task_has_no_graph_operations_and_keeps_copy_nonexclusive(self):
        operations = self.normalize()
        self.assertEqual([op['op'] for op in operations], ['generate'])
        op = operations[0]
        self.assertNotIn('node', op)
        self.assertEqual(op['kind'], 'image')
        self.assertTrue(op['contract']['copy']['allow_additional_text'])
        self.assertEqual(op['contract']['copy']['exact_text'][0]['source'], 'proposal')
        self.assertIn('Textured Surface', op['prompt'])
        self.assertIn('保留产品外观', op['contract']['requirements'][0]['evidence'])
        self.assertIn('允许', op['prompt'])

    def test_model_cannot_turn_invented_copy_or_restrictions_into_user_requirements(self):
        wrong = task()
        wrong['copy']['exact_text'][0].update(source='user', evidence='Imagined specification')
        with self.assertRaises(HTTPException):
            self.normalize([wrong])
        wrong = task(); wrong['copy']['allow_additional_text'] = False
        with self.assertRaises(HTTPException):
            self.normalize([wrong])
        valid = task(); valid['copy'].update(allow_additional_text=False, exclusivity_evidence='只出现指定文案')
        self.assertFalse(self.normalize([valid], '保留产品外观，只出现指定文案')[0]['contract']['copy']['allow_additional_text'])

    def test_model_response_contract_rejects_invalid_generation_fields_before_adapter_validation(self):
        for variant in ('missing_ratio', 'extra_size', 'numeric_resolution', 'string_count', 'video_with_image_settings'):
            candidate = task()
            if variant == 'missing_ratio':
                candidate['settings'].pop('aspect_ratio')
            elif variant == 'extra_size':
                candidate['settings']['size'] = '1152x2048'
            elif variant == 'numeric_resolution':
                candidate['settings']['resolution'] = 2048
            elif variant == 'string_count':
                candidate['settings']['count'] = '1'
            else:
                candidate['kind'] = 'video'
            answer = {'kind': 'plan', 'reply': '计划', 'summary': '海报', 'tasks': [candidate]}
            with self.subTest(variant=variant), self.assertRaises(ValidationError):
                creative.CreativePlanResponse.model_validate(answer)

    def test_source_errors_identify_every_invalid_field_for_one_targeted_correction(self):
        wrong = task()
        wrong['requirements'][0]['evidence'] = '保留原产品的外观'
        wrong['copy']['exact_text'][0].update(source='user', evidence='Imagined specification')
        other = task(id='detail')
        other['requirements'][0].update(source='reference', evidence='not-provided')
        with self.assertRaises(HTTPException) as error:
            self.normalize([wrong, other])
        self.assertEqual(error.exception.status_code, 422)
        for path in ('tasks[0].requirements[0].evidence', 'tasks[0].copy.exact_text[0].evidence',
                     'tasks[1].requirements[0].evidence'):
            self.assertIn(path, error.exception.detail)
        self.assertIn('poster', error.exception.detail)
        self.assertIn('detail', error.exception.detail)

    def test_application_binds_attachment_names_and_urls_to_the_actual_input(self):
        for evidence in ('product.png', '/assets/product.png', 'node:product', '@[product.png](node:product)'):
            with self.subTest(evidence=evidence):
                candidate = task(reference_node_ids=[], reference_roles={})
                candidate['requirements'][0].update(source='reference', evidence=evidence)
                candidate['copy']['exact_text'][0].update(source='reference', evidence=evidence)
                op = self.normalize([candidate])[0]
                self.assertEqual(op['reference_node_ids'], ['product'])
                self.assertEqual(op['contract']['requirements'][0]['evidence'], 'product')
                self.assertEqual(op['contract']['copy']['exact_text'][0]['evidence'], 'product')
                self.assertEqual(op['contract']['reference_roles'], {'product': 'identity'})
                run = {'operations': [op], 'reference_snapshot': references()}
                self.assertEqual(creative.creative_inputs(run, 'poster')[2], references()[0]['images'])

    def test_reference_roles_and_prior_result_ids_are_resolved_without_canvas_lookup(self):
        first = task(reference_node_ids=['product.png'], reference_roles={'/assets/product.png': 'identity'})
        second = task(id='clip', kind='video', reference_node_ids=['node:poster'],
                      reference_roles={'poster': 'edit_target'}, settings={'provider': 'fixture', 'model': 'video',
                          'count': 1, 'aspect_ratio': '9:16', 'resolution': '720p', 'duration': 8})
        second['requirements'][0].update(source='reference', evidence='poster')
        operations = self.normalize([first, second])
        self.assertEqual(operations[0]['reference_node_ids'], ['product'])
        self.assertEqual(operations[0]['contract']['reference_roles'], {'product': 'identity'})
        self.assertEqual(operations[1]['reference_node_ids'], ['poster'])
        self.assertEqual(operations[1]['contract']['reference_roles'], {'poster': 'edit_target'})

    def test_ambiguous_or_unprovided_attachment_names_are_not_guessed(self):
        refs = references() + [{**references()[0], 'id': 'other',
            'images': [{'url': '/assets/other.png', 'kind': 'image', 'name': 'product.png'}]}]
        for evidence in ('product.png', '/assets/not-provided.png', '用户参考图里的产品', 'future'):
            candidate = task()
            candidate['requirements'][0].update(source='reference', evidence=evidence)
            with self.subTest(evidence=evidence), self.assertRaises(HTTPException):
                creative.normalize_creative_tasks([candidate], refs, '保留产品外观', lambda raw, kind: raw)

    def test_selected_case_sources_are_separate_from_attachment_and_result_ids(self):
        cases = [{'id': 358, 'title': 'Amber poster', 'purpose': '主风格/构图参考',
                  'media': {'url': '/assets/skill-references/case358.png', 'kind': 'image', 'name': 'case358.png'}}]
        for evidence in ('case:358', '358', '案例 358', '/assets/skill-references/case358.png'):
            with self.subTest(evidence=evidence):
                candidate = task(reference_node_ids=['product', '案例 358'],
                                 reference_roles={'product': 'identity', '案例 358': 'style'})
                candidate['requirements'].append({'id': 'layout', 'category': 'composition', 'source': 'reference',
                                                  'text': 'Borrow the amber side lighting.', 'evidence': evidence})
                op = self.normalize([candidate], reference_cases=cases)[0]
                self.assertEqual(op['reference_node_ids'], ['product'])
                self.assertEqual(op['contract']['requirements'][-1]['evidence'], 'case:358')
                self.assertEqual(op['contract']['reference_roles']['case:358'], 'style')
                source = next(s for s in op['contract']['reference_sources'] if s['id'] == 'case:358')
                self.assertEqual(source['kind'], 'case')
                self.assertEqual(source['media'], [cases[0]['media']])
        for evidence, category in (('case:21', 'composition'), ('case:358', 'identity')):
            candidate = task()
            candidate['requirements'][0].update(source='reference', evidence=evidence, category=category)
            with self.subTest(evidence=evidence, category=category), self.assertRaises(HTTPException):
                self.normalize([candidate], reference_cases=cases)

    def test_unknown_sources_conflicting_copy_duplicate_ids_and_future_references_reject(self):
        variants = []
        wrong = task(); wrong['copy']['forbidden_text'] = ['Textured Surface']; variants.append([wrong])
        wrong = task(); wrong['requirements'][0].update(source='reference', evidence='not-provided'); variants.append([wrong])
        wrong = task(reference_node_ids=['future']); variants.append([wrong])
        variants.append([task(), task()])
        for value in variants:
            with self.subTest(value=value), self.assertRaises(HTTPException):
                self.normalize(value)

    def test_inputs_use_only_task_attachments_and_durable_prior_results_not_display_nodes(self):
        second = task(id='clip', kind='video', prompt='Animate the previous result.',
                      reference_node_ids=['poster'], reference_roles={'poster': 'edit_target'},
                      settings={'provider': 'fixture', 'model': 'video', 'count': 1,
                                'aspect_ratio': '9:16', 'resolution': '720p', 'duration': 8})
        operations = self.normalize([task(), second])
        frozen = references()
        frozen[0].update(text='Ignored old text', prompt_fallback='Ignored fallback', input_node_ids=['old'])
        run = {'mode': 'creation', 'operations': operations, 'reference_snapshot': frozen,
               'steps': [{'operation_id': 'poster', 'result': {'media': [{'url': '/output/result.png', 'kind': 'image'}]}}]}
        kind, prompt, media = creative.creative_inputs(run, 'poster')
        self.assertEqual(kind, 'image')
        self.assertEqual(prompt, operations[0]['prompt'])
        self.assertEqual([m['url'] for m in media], ['/assets/product.png'])
        self.assertNotIn('Ignored', prompt)
        dependencies = set()
        kind, prompt, media = creative.creative_inputs(run, 'clip', dependencies=dependencies)
        self.assertEqual(kind, 'video')
        self.assertEqual([m['url'] for m in media], ['/output/result.png'])
        self.assertEqual(dependencies, {'poster'})
        self.assertEqual(prompt, operations[1]['prompt'])


if __name__ == '__main__':
    unittest.main()
