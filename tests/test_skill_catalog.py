import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skill_catalog import SkillCatalog, create_skill_router


class SkillCatalogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.root = self.path / 'skills'
        self.root.mkdir()
        self.config = self.path / 'settings.json'
        self.catalog = SkillCatalog(self.root, self.config)
        self.skill('alpha', 'Use exact brand text.', '品牌 图片')
        self.skill('beta', 'Keep useful context.', 'Write notes')

    def skill(self, name, body, description=''):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        (folder / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n{body}', encoding='utf-8')
        return folder

    def test_default_off_save_reopen_and_new_discovery_stays_off(self):
        initial = self.catalog.list()
        self.assertEqual(initial['selected_ids'], [])
        saved = self.catalog.save(['alpha'], initial['revision'])
        self.skill('gamma', 'Another skill.')
        reopened = SkillCatalog(self.root, self.config).list()
        self.assertEqual(reopened['selected_ids'], ['alpha'])
        self.assertEqual([s['id'] for s in reopened['skills']], ['alpha', 'beta', 'gamma'])
        self.assertEqual(reopened['revision'], saved['revision'])
        self.assertFalse(next(s for s in reopened['skills'] if s['id'] == 'gamma')['selected'])

    def test_stale_save_does_not_overwrite_another_page(self):
        self.catalog.save(['alpha'], 0)
        with self.assertRaises(HTTPException) as result:
            self.catalog.save(['beta'], 0)
        self.assertEqual(result.exception.status_code, 409)
        self.assertEqual(self.catalog.list()['selected_ids'], ['alpha'])

    def test_exact_selected_invocation_deduplication_and_real_rules(self):
        self.catalog.save(['alpha', 'beta'], 0)
        value = self.catalog.invocation('/alpha /beta 为商品写文案 /alpha')
        self.assertIn('Use exact brand text.', value['instructions'])
        self.assertIn('Keep useful context.', value['instructions'])
        self.assertEqual([s['id'] for s in value['skills']], ['alpha', 'beta'])
        self.assertTrue(all(s['version'] for s in value['skills']))
        self.assertEqual(self.catalog.invocation('https://x/alpha /alpha/file a/alpha')['skills'], [])
        self.assertEqual(self.catalog.invocation('用 /unknown 表示路径')['skills'], [])

    def test_disabled_skill_is_rejected_even_when_typed_manually(self):
        with self.assertRaises(HTTPException) as result:
            self.catalog.invocation('/alpha do it')
        self.assertEqual(result.exception.status_code, 422)
        self.catalog.save(['alpha'], 0)
        self.catalog.save([], 1)
        with self.assertRaises(HTTPException):
            self.catalog.invocation('/alpha do it')

    def test_removed_selected_skill_is_rejected_and_no_longer_listed(self):
        self.catalog.save(['alpha'], 0)
        (self.root / 'alpha' / 'SKILL.md').unlink()
        self.assertEqual(self.catalog.list()['selected_ids'], [])
        with self.assertRaises(HTTPException):
            self.catalog.invocation('/alpha do it')

    def test_wrapper_loads_source_instead_of_inert_forwarding_text(self):
        folder = self.skill('wrapped', 'Read [source](_source/skills/actual/SKILL.md).')
        source = folder / '_source/skills/actual'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('---\nname: actual\ndescription: "真实规则"\n---\n# Actual\nActual instruction.', encoding='utf-8')
        self.catalog.save(['wrapped'], 0)
        value = self.catalog.invocation('/wrapped go')
        self.assertIn('Actual instruction.', value['instructions'])
        self.assertEqual(next(s for s in self.catalog.list()['skills'] if s['id'] == 'wrapped')['description'], '真实规则')

    def test_no_outside_wrapper_or_arbitrary_selected_paths(self):
        (self.path / 'outside.md').write_text('PRIVATE OUTSIDE CONTENT', encoding='utf-8')
        self.skill('outside', 'Read [source](../../outside.md).')
        with self.assertRaises(HTTPException):
            self.catalog.save(['../outside'], 0)
        self.catalog.save(['outside'], 0)
        self.assertNotIn('PRIVATE OUTSIDE CONTENT', self.catalog.invocation('/outside task')['instructions'])

    def test_corrupt_settings_are_reported_not_reset(self):
        self.config.write_text('{broken', encoding='utf-8')
        with self.assertRaises(HTTPException):
            self.catalog.save(['alpha'], 0)
        self.assertEqual(self.config.read_text(encoding='utf-8'), '{broken')


class SkillRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_and_put_share_catalog_and_validate_unknown_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / 'sample'
            skill.mkdir()
            (skill / 'SKILL.md').write_text('---\nname: sample\ndescription: Example\n---\nActual rules.', encoding='utf-8')
            catalog = SkillCatalog(root, root / 'state.json')
            app = FastAPI()
            app.include_router(create_skill_router(lambda: catalog))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                initial = (await client.get('/api/skills')).json()
                self.assertEqual(initial['selected_ids'], [])
                saved = await client.put('/api/skills', json={'selected_ids': ['sample'], 'revision': initial['revision']})
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual((await client.get('/api/skills')).json()['selected_ids'], ['sample'])
                bad = await client.put('/api/skills', json={'selected_ids': ['missing'], 'revision': 1})
                self.assertEqual(bad.status_code, 422)
                stale = await client.put('/api/skills', json={'selected_ids': [], 'revision': 0})
                self.assertEqual(stale.status_code, 409)


if __name__ == '__main__':
    unittest.main()
