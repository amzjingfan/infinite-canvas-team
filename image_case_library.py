"""Bounded, read-only adapter for the canonical enhanced image skill's full corpus."""
import base64
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps


class ImageCaseLibrary:
    def __init__(self, root, assets_dir):
        self.root = Path(root).resolve()
        self.assets_dir = Path(assets_dir)
        self.versions = {}
        try:
            rows = json.loads(self._text('upstream/data/cases.json'))['cases']
            if not isinstance(rows, list) or any(not isinstance(r, dict) or type(r.get('id')) is not int for r in rows):
                raise ValueError('invalid corpus')
            self.rows = {r['id']: r for r in rows}
            if len(self.rows) != len(rows):
                raise ValueError('duplicate case IDs')
            self.total = len(rows)
            self.commit = json.loads(self._text('upstream/manifest.json')).get('commit', '')
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(422, '完整案例库无法读取，请检查增强技能的本地素材。') from None

    def _path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise HTTPException(422, '技能资源不存在或超出技能目录。')
        return path

    def _text(self, name):
        try:
            data = self._path(name).read_bytes()
            if len(data) > 20_000_000:
                raise ValueError('resource too large')
            self.versions[name] = hashlib.sha256(data).hexdigest()
            return data.decode('utf-8-sig')
        except (OSError, ValueError):
            raise HTTPException(422, '技能引用文件无法完整读取。') from None

    def _image(self, row):
        name = row.get('image', '')
        if not isinstance(name, str) or not name.startswith('/images/') or '..' in name or '\\' in name:
            raise HTTPException(422, '案例图片路径无效。')
        return self._path('upstream/data' + name)

    def search(self, query, limit=8):
        terms = re.findall(r'[\w-]+', str(query).lower())
        ranked = []
        for row in self.rows.values():
            title, prompt = str(row.get('title', '')).lower(), str(row.get('prompt', '')).lower()
            tags = json.dumps([row.get('category'), row.get('styles'), row.get('scenes')], ensure_ascii=False).lower()
            score = sum((6 if t in title else 0) + (3 if t in prompt else 0) + (1 if t in tags else 0) for t in terms)
            if not score:
                continue
            reason = ''
            try:
                self._image(row)
            except HTTPException:
                reason = '图片缺失或路径无效'
            if not prompt.strip():
                reason = '提示词为空'
            ranked.append({'id': row['id'], 'title': row.get('title', ''), 'score': score,
                           'category': row.get('category', ''), 'available': not reason, 'reason': reason})
        return sorted(ranked, key=lambda r: r['score'], reverse=True)[:max(1, min(32, limit))]

    def candidate(self, case_id):
        row = self.rows.get(case_id)
        if not row or not str(row.get('prompt', '')).strip():
            raise HTTPException(422, '案例不存在或完整提示词为空。')
        try:
            data = self._image(row).read_bytes()
            if len(data) > 40_000_000:
                raise ValueError('image too large')
            with Image.open(BytesIO(data)) as source:
                source.load()
                picture = ImageOps.exif_transpose(source).convert('RGB')
                picture.thumbnail((1024, 1024))
                buffer = BytesIO()
                picture.save(buffer, format='JPEG', quality=90)
            return {'id': case_id, 'title': row.get('title', ''), 'prompt': row['prompt'],
                    'category': row.get('category', ''), 'source_url': row.get('sourceUrl', ''),
                    'image_sha256': hashlib.sha256(data).hexdigest(),
                    'image_data': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()}
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, '案例图片损坏或无法解码，未将其作为已看图案例。') from None

    def instructions(self):
        chapters = self._chapters()
        return {'workflow': self._text('references/image-workflow.md'),
                'style_index': self._text('references/style-library.md'),
                'templates': list(chapters), 'versions': dict(self.versions)}

    def _chapters(self):
        text = self._text('upstream/docs/templates.md')
        matches = list(re.finditer(r'^### ([^\r\n]+)\r?$', text, re.M))
        return {match[1].strip(): text[match.start():matches[i+1].start() if i+1 < len(matches) else len(text)].strip()
                for i, match in enumerate(matches)}

    def template(self, name):
        chapter = self._chapters().get(name)
        if not chapter:
            raise HTTPException(422, '模型选择了不存在的模板章节，请重试研究。')
        return chapter

    def publish(self, case_id, expected_hash=None):
        row = self.rows.get(case_id)
        if not row:
            raise HTTPException(422, '选定案例不存在。')
        data = self._image(row).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if expected_hash and digest != expected_hash:
            raise HTTPException(422, '案例图片在研究期间发生变化，请重新研究。')
        try:
            with Image.open(BytesIO(data)) as picture:
                picture.load()
                suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp', 'GIF': '.gif'}.get(picture.format)
                width, height = picture.size
            if not suffix:
                raise ValueError('unsupported image format')
            directory = self.assets_dir / 'skill-references'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (digest + suffix)
            # Exclusive creation makes repeated/concurrent publication immutable.
            try:
                with path.open('xb') as output:
                    output.write(data)
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError('damaged cached reference')
            return {'url': '/assets/skill-references/' + path.name, 'kind': 'image',
                    'name': f'案例 {case_id} · {row.get("title", "")}', 'width': width, 'height': height,
                    'sha256': digest}
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, '选定案例图片无法保存为稳定参考。') from None
