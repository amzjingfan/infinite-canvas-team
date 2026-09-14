import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from image_case_library import ImageCaseLibrary
except ImportError:
    ImageCaseLibrary = None
from skill_catalog import SkillCatalog


def write_library(root):
    """Small real corpus: full text differs from its deliberately incomplete preview."""
    (root / 'upstream/data/images').mkdir(parents=True)
    (root / 'upstream/docs').mkdir(parents=True)
    (root / 'references').mkdir()
    (root / 'SKILL.md').write_text('---\nname: gpt-image-2-style-library\n---\nEnhanced image workflow', encoding='utf-8')
    rows = [
        {'id': 358, 'title': '香水 perfume', 'prompt': 'Perfume glass bottle. full final sentence with amber.',
         'promptPreview': 'Glass', 'image': '/images/case358.png', 'category': 'Products',
         'styles': ['Product'], 'scenes': ['Commerce'], 'sourceUrl': 'https://example.org/358'},
        {'id': 9, 'title': 'Missing perfume', 'prompt': 'Perfume', 'image': '/images/missing.png'},
        {'id': 7, 'title': 'Empty perfume', 'prompt': '', 'image': '/images/case358.png'},
        {'id': 21, 'title': 'Other', 'prompt': 'a chair', 'image': '/images/case21.png'},
    ]
    (root / 'upstream/data/cases.json').write_text(json.dumps({'cases': rows}, ensure_ascii=False), encoding='utf-8')
    (root / 'upstream/manifest.json').write_text(json.dumps({'commit': 'fixture-commit'}), encoding='utf-8')
    (root / 'upstream/docs/templates.md').write_text(
        '# Templates\n### 产品\nComplete product template and pitfalls.\n### 海报\nExact text and whitespace.\n', encoding='utf-8')
    (root / 'references/style-library.md').write_text('# Index\nProduct and poster templates.', encoding='utf-8')
    (root / 'references/image-workflow.md').write_text('View real results. Preserve identity; check text.', encoding='utf-8')
    Image.new('RGB', (12, 8), '#ad753f').save(root / 'upstream/data/images/case358.png')
    Image.new('RGB', (8, 12), '#1871ba').save(root / 'upstream/data/images/case21.png')


class CaseLibraryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.root = self.path / 'skills/gpt-image-2-style-library'
        write_library(self.root)

    def library(self):
        self.assertTrue(callable(ImageCaseLibrary), 'Read-only image case adapter is not implemented')
        return ImageCaseLibrary(self.root, self.path / 'assets')

    def test_search_uses_full_corpus_text_and_reports_unusable_cases(self):
        library = self.library()
        self.assertEqual([r['id'] for r in library.search('amber')], [358])
        rows = library.search('perfume')
        self.assertEqual(rows[0]['id'], 358)
        self.assertTrue(rows[0]['available'])
        self.assertEqual({r['id'] for r in rows if not r['available']}, {9, 7})
        self.assertEqual(library.search('unmatched_term'), [])
        self.assertEqual(library.total, 4)

    def test_candidate_contains_full_prompt_and_actual_decodable_image(self):
        data = self.library().candidate(358)
        self.assertIn('full final sentence with amber.', data['prompt'])
        self.assertTrue(data['image_data'].startswith('data:image/'))
        from io import BytesIO
        with Image.open(BytesIO(base64.b64decode(data['image_data'].split(',', 1)[1]))) as actual:
            self.assertEqual(actual.size, (12, 8))
        self.assertEqual(len(data['image_sha256']), 64)
        for case_id in (9, 7, 999):
            with self.subTest(case_id=case_id), self.assertRaises(HTTPException):
                self.library().candidate(case_id)

    def test_case_paths_cannot_escape_resource_root(self):
        outside = self.path / 'outside.png'
        Image.new('RGB', (4, 4)).save(outside)
        path = self.root / 'upstream/data/cases.json'
        for name in ('/images/../../../../../outside.png', str(outside), '/images/..\\..\\outside.png'):
            path.write_text(json.dumps({'cases': [{'id': 1, 'title': 'escape', 'prompt': 'escape', 'image': name}]}), encoding='utf-8')
            with self.subTest(name=name), self.assertRaises(HTTPException):
                self.library().candidate(1)

    def test_publish_is_content_addressed_immutable_and_does_not_copy_whole_library(self):
        library = self.library()
        first = library.publish(358)
        second = library.publish(358)
        self.assertEqual(first, second)
        self.assertTrue(first['url'].startswith('/assets/skill-references/'))
        saved = self.path / first['url'].lstrip('/')
        original = self.root / 'upstream/data/images/case358.png'
        self.assertEqual(saved.read_bytes(), original.read_bytes())
        self.assertEqual(len(list((self.path / 'assets').rglob('*.*'))), 1)
        Image.new('RGB', (12, 8), 'red').save(original)
        changed = library.publish(358)
        self.assertNotEqual(changed['url'], first['url'])
        self.assertNotEqual(saved.read_bytes(), original.read_bytes())

    def test_template_reads_full_selected_chapter_and_records_resource_versions(self):
        library = self.library()
        instructions = library.instructions()
        self.assertEqual(instructions['templates'], ['产品', '海报'])
        self.assertIn('View real results.', instructions['workflow'])
        self.assertIn('Product and poster', instructions['style_index'])
        self.assertIn('references/image-workflow.md', instructions['versions'])
        self.assertIn('Complete product template and pitfalls.', library.template('产品'))
        self.assertNotIn('Exact text', library.template('产品'))
        with self.assertRaises(HTTPException):
            library.template('../../outside')

    def test_catalog_directory_resolves_only_available_selected_skill_source(self):
        catalog = SkillCatalog(self.path / 'skills', self.path / 'selection.json')
        self.assertTrue(callable(getattr(catalog, 'directory', None)), 'catalog must resolve real skill directory')
        with self.assertRaises(HTTPException):
            catalog.directory('gpt-image-2-style-library')
        catalog.save(['gpt-image-2-style-library'], 0)
        self.assertEqual(catalog.directory('gpt-image-2-style-library'), self.root.resolve())
        with self.assertRaises(HTTPException):
            catalog.directory('../outside')


if __name__ == '__main__':
    unittest.main()
