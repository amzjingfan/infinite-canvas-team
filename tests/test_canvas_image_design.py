import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from canvas_agent import normalized_settings
from image_design_fixtures import card, snapshot, research, defaults, provider


class ImageDesignTests(unittest.TestCase):
    def build(self, design=None, refs=None, **changes):
        self.assertIsNotNone(importlib.util.find_spec('canvas_image_design'), 'v3 design compiler is missing')
        module = importlib.import_module('canvas_image_design')
        options = {'research': research(), **changes}
        return module.compile_design(design or card(), snapshot() if refs is None else refs,
            '保留产品外观，生成海报', defaults(),
            lambda raw, kind: normalized_settings(raw, kind, defaults(), provider), **options)

    def test_default_generation_uses_product_but_not_research_case(self):
        plan = self.build()
        op = plan['operations'][0]
        self.assertEqual(plan['contract_version'], 3)
        self.assertEqual([m['url'] for m in op['generation_inputs']], ['/assets/product.png'])
        self.assertEqual(op['reference_node_ids'], ['product'])
        self.assertEqual(op['settings'], {'provider': 'fixture', 'model': 'image', 'count': 1,
            'aspect_ratio': '3:4', 'resolution': '2k', 'quality': 'high', 'size': '1536x2048'})
        self.assertIn('上中下分区与细引线位置', op['prompt'])
        self.assertNotIn('主风格/构图参考', op['prompt'])
        self.assertNotIn('UNRELATED DARK', op['prompt'])
        self.assertNotIn('旧研究观察', op['prompt'])
        self.assertEqual(plan['image_workflow']['cases'][0]['id'], 157)
        self.assertEqual(op['contract']['design_card']['layout']['pose'], 'grounded')
        self.assertEqual(plan['image_workflow']['generation_contract']['effective_prompt'], op['prompt'])

    def test_explicit_single_case_keeps_final_composition_role_not_research_label(self):
        design = card()
        design['case_uses'][0]['use'] = 'generation'
        op = self.build(design, case_input_mode='single_case')['operations'][0]
        self.assertEqual([m['url'] for m in op['generation_inputs']],
                         ['/assets/product.png', '/assets/case157.png'])
        self.assertEqual(op['generation_inputs'][1]['role'], 'composition')
        self.assertIn('上中下分区与细引线位置', op['generation_inputs'][1]['purpose'])
        self.assertNotIn('主风格', op['prompt'])

    def test_model_cannot_add_actual_case_in_default_mode(self):
        design = card()
        design['case_uses'][0]['use'] = 'generation'
        with self.assertRaises(HTTPException) as error:
            self.build(design)
        self.assertEqual(error.exception.status_code, 422)

    def test_single_case_preference_cannot_silently_become_research_only(self):
        with self.assertRaises(HTTPException) as error:
            self.build(case_input_mode='single_case')
        self.assertIn('一张', error.exception.detail)

    def test_all_explicit_user_images_are_preserved_and_style_attachment_keeps_its_role(self):
        refs = snapshot()
        refs.append({'id': 'userstyle', 'title': '布局图', 'text': '', 'images': [
            {'url': '/assets/userstyle.png', 'kind': 'image', 'name': '布局图'}]})
        design = card()
        design['subject']['references'] = [{'source_id': 'userstyle', 'role': 'composition'}]
        op = self.build(design, refs)['operations'][0]
        self.assertEqual([m['url'] for m in op['generation_inputs']],
                         ['/assets/product.png', '/assets/userstyle.png'])
        self.assertEqual([m['role'] for m in op['generation_inputs']], ['identity', 'composition'])

    def test_user_and_case_evidence_cannot_be_invented_or_promoted_to_product_identity(self):
        for field, value in [('user', '用户未说过的要求'), ('case', 'case:157')]:
            design = card()
            if field == 'user':
                design['constraints'][0]['evidence'] = value
            else:
                design['subject']['observations'] = [{'id': 'wrong', 'source': 'reference',
                    'category': 'identity', 'text': '产品具有案例里的接口', 'evidence': value}]
            with self.subTest(field=field), self.assertRaises(HTTPException):
                self.build(design)

    def test_observation_filename_binds_without_overriding_actual_identity_image(self):
        design = card()
        design['subject']['observations'] = [{'id': 'color', 'category': 'identity', 'source': 'reference',
            'text': '粉白外观', 'evidence': '参考图.jpg'}]
        op = self.build(design)['operations'][0]
        self.assertEqual(op['contract']['requirements'][0]['evidence'], 'product')
        self.assertIn('与原图矛盾时以原图为准', op['prompt'])

    def test_explicit_source_id_in_observation_and_copy_evidence_binds_current_attachment(self):
        design = card()
        evidence = '本次附件《参考图.jpg》（source_id: product）左下格可见粉白机身与原有字样。'
        design['subject']['observations'] = [{'id': 'color', 'category': 'identity', 'source': 'reference',
            'text': '粉白外观', 'evidence': evidence}]
        design['constraints'].append({'id': 'marking', 'category': 'text', 'source': 'reference',
            'text': '保留原有品牌字样', 'evidence': evidence})
        design['copy']['exact_text'] = [{'text': 'SAMPLE', 'placement': '原机身标签',
            'source': 'reference', 'evidence': evidence}]
        try:
            plan = self.build(design)
        except HTTPException as exc:
            self.fail('明确且唯一的真实 source_id 不应因附带观察说明而被拒绝：' + str(exc.detail))
        self.assertEqual(plan['design_card']['subject']['observations'][0]['evidence'], 'product')
        self.assertEqual(plan['design_card']['constraints'][-1]['evidence'], 'product')
        self.assertEqual(plan['design_card']['copy']['exact_text'][0]['evidence'], 'product')
        self.assertEqual([m['url'] for m in plan['operations'][0]['generation_inputs']], ['/assets/product.png'])
        self.assertEqual(design['subject']['observations'][0]['evidence'], evidence)

    def test_reference_prose_still_rejects_unknown_ambiguous_or_unmarked_sources(self):
        refs = snapshot() + [{'id': 'second', 'title': '第二件产品', 'images': [
            {'url': '/assets/second.png', 'kind': 'image', 'name': '第二件产品'}]}]
        for evidence in ('参考图中可见粉白外观', 'product 的左下格可见粉白外观',
                         'source_id: missing', 'source_id: product-other',
                         'source_id: product；source_id: second',
                         'source_id: product；source_id: missing', 'source_id: product；source_id:'):
            design = card()
            design['subject']['observations'] = [{'id': 'color', 'category': 'identity', 'source': 'reference',
                'text': '粉白外观', 'evidence': evidence}]
            with self.subTest(evidence=evidence), self.assertRaises(HTTPException):
                self.build(design, refs)

    def test_explicit_case_source_in_prose_can_prove_layout_but_not_identity_or_exact_copy(self):
        evidence = '已采用案例（source_id: case:157）的上下分区与细引线。'
        design = card()
        design['constraints'].append({'id': 'layout', 'category': 'composition', 'source': 'reference',
                                     'text': '上下分区', 'evidence': evidence})
        try:
            plan = self.build(design)
        except HTTPException as exc:
            self.fail('研究案例的明确来源可以支撑构图观察：' + str(exc.detail))
        self.assertEqual(plan['design_card']['constraints'][-1]['evidence'], 'case:157')
        self.assertEqual(len(plan['operations'][0]['generation_inputs']), 1)
        design['constraints'][-1]['category'] = 'identity'
        with self.assertRaises(HTTPException) as identity_error:
            self.build(design)
        self.assertIn('案例不是', identity_error.exception.detail)
        design['constraints'].pop()
        design['copy']['exact_text'][0].update(source='reference', evidence=evidence)
        with self.assertRaises(HTTPException) as copy_error:
            self.build(design)
        self.assertIn('案例不是', copy_error.exception.detail)

    def test_pose_controls_shadow_without_a_second_independent_option(self):
        design = card()
        design['layout']['pose'] = 'floating'
        floating = self.build(design)['operations'][0]['prompt']
        self.assertIn('投射阴影', floating)
        self.assertNotIn('接触阴影', floating)
        self.assertIn('接触阴影', self.build()['operations'][0]['prompt'])
        design['layout']['pose'] = 'floating or grounded'
        with self.assertRaises(HTTPException):
            self.build(design)

    def test_model_cannot_return_provider_or_size_as_design_options(self):
        for key, value in [('provider', 'another'), ('size', '999x999')]:
            design = card()
            design['render_options'][key] = value
            with self.subTest(key=key), self.assertRaises(HTTPException):
                self.build(design)

    def test_copy_policy_keeps_additional_text_allowed_but_rejects_false_exclusivity(self):
        op = self.build()['operations'][0]
        self.assertTrue(op['contract']['copy']['allow_additional_text'])
        self.assertIn('日常之选', op['prompt'])
        design = card()
        design['copy']['allow_additional_text'] = False
        with self.assertRaises(HTTPException):
            self.build(design)

    def test_unknown_or_duplicate_cases_are_not_executable_inputs(self):
        for change in ('unknown', 'duplicate'):
            design = card()
            if change == 'unknown':
                design['case_uses'][0]['case_id'] = 999
            else:
                design['case_uses'] *= 2
            with self.subTest(change=change), self.assertRaises(HTTPException):
                self.build(design, case_input_mode='single_case')

    def test_compilation_does_not_mutate_inputs_and_can_be_serialized(self):
        design = card()
        before = json.dumps(design, ensure_ascii=False)
        plan = self.build(design)
        self.assertEqual(json.dumps(design, ensure_ascii=False), before)
        json.dumps(plan, ensure_ascii=False, allow_nan=False).encode('utf-8')


if __name__ == '__main__':
    unittest.main()
