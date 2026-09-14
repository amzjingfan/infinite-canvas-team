"""Read-only local skill discovery and project-owned, opt-in slash invocation."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr


COMMAND = re.compile(r'(?<!\S)/([a-zA-Z0-9][a-zA-Z0-9_-]*)(?=\s|$)')
LINK = re.compile(r'\[[^\]]*\]\(([^)]+SKILL\.md)\)', re.I)
MAX_INSTRUCTION_CHARS = 100_000


def metadata(text):
    """Read the simple scalar fields used by SKILL frontmatter, including folds."""
    match = re.match(r'\A\ufeff?---\s*\n(.*?)\n---\s*(?:\n|$)', text, re.S)
    if not match:
        return {}, text
    values, key = {}, None
    for line in match[1].splitlines():
        field = re.match(r'^([\w-]+):\s*(.*)$', line)
        if field:
            key, value = field.groups()
            values[key] = '' if value in ('>', '|', '>-', '|-') else value.strip().strip('"\'')
        elif key and line[:1].isspace():
            values[key] += ' ' + line.strip().strip('"\'')
    return {k:v.strip() for k,v in values.items()}, text[match.end():]


class SkillCatalog:
    def __init__(self, root, config_path):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path)
        self.lock = RLock()

    def _read_skill(self, entry):
        source = entry.resolve()
        if not source.is_relative_to(self.root):
            raise ValueError('Skill outside catalog root')
        texts, visited = [], set()
        while source not in visited:
            visited.add(source)
            if source.stat().st_size > 2_000_000:
                raise ValueError('Skill entry too large')
            text = source.read_text(encoding='utf-8-sig')
            texts.append(text)
            info, body = metadata(text)
            # Bundled compatibility wrappers forward to the real instruction file.
            links = LINK.findall(body) if len(body.splitlines()) < 12 else []
            if not links:
                return info, body, hashlib.sha256('\n'.join(texts).encode()).hexdigest(), source
            target = (source.parent / links[0]).resolve()
            if not target.is_relative_to(self.root) or not target.is_file():
                raise ValueError('Invalid skill forwarding target')
            source = target
        raise ValueError('Cyclic skill forwarding')

    def _discover(self):
        if not self.root.is_dir():
            raise HTTPException(503, '技能目录不可用，请检查服务端技能目录配置。')
        entries = sorted(self.root.glob('*/SKILL.md'))
        # Prefer canonical user copies; expose built-ins only when not duplicated.
        entries += sorted((self.root / '.system').glob('*/SKILL.md'))
        result, seen_sources, seen_names = {}, set(), set()
        for entry in entries:
            skill_id = entry.parent.name
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]*', skill_id):
                continue
            try:
                info, body, version, source = self._read_skill(entry)
            except (OSError, UnicodeError, ValueError):
                # Discovery must still show damaged entries instead of hiding them.
                if skill_id not in result:
                    result[skill_id] = {'id':skill_id, 'name':skill_id, 'description':'技能文件无法读取或转发入口无效',
                                        'available':False, 'version':'', '_entry':entry}
                continue
            name = info.get('name') or skill_id
            if source in seen_sources or name in seen_names or skill_id in result:
                continue
            seen_sources.add(source)
            seen_names.add(name)
            result[skill_id] = {'id':skill_id, 'name':name, 'description':info.get('description', ''),
                                'available':True, 'version':version, '_entry':entry}
        return dict(sorted(result.items()))

    def _state(self):
        if not self.config_path.exists():
            return {'revision':0, 'selected_ids':[]}
        try:
            state = json.loads(self.config_path.read_text(encoding='utf-8'))
            if (type(state['revision']) is not int or state['revision'] < 0
                    or not isinstance(state['selected_ids'], list)
                    or any(not isinstance(s, str) for s in state['selected_ids'])):
                raise ValueError('Invalid selection state')
            return state
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            raise HTTPException(503, 'Skills 设置无法读取，未覆盖原配置。请检查 data/skills.json。') from None

    def _public(self, entries, state):
        selected = set(state['selected_ids'])
        return {'revision':state['revision'],
                'selected_ids':[key for key in entries if key in selected and entries[key]['available']],
                'skills':[{**{k:v for k,v in item.items() if not k.startswith('_')},
                           'selected':key in selected and item['available']} for key,item in entries.items()]}

    def list(self):
        with self.lock:
            return self._public(self._discover(), self._state())

    def directory(self, skill_id):
        """Internal resource adapter entry point; never resolves a model-supplied path."""
        with self.lock:
            entry = self._discover().get(skill_id)
            if not entry or not entry['available'] or skill_id not in self._state()['selected_ids']:
                raise HTTPException(422, '技能未勾选或资源不可用，请在 Skills 页面检查。')
            try:
                return self._read_skill(entry['_entry'])[3].parent
            except (OSError, UnicodeError, ValueError):
                raise HTTPException(422, '技能真实入口无法读取。') from None

    def save(self, selected_ids, revision):
        with self.lock:
            entries, state = self._discover(), self._state()
            if revision != state['revision']:
                raise HTTPException(409, 'Skills 设置已在其他页面更新，请重新加载后选择。')
            selected = sorted(set(selected_ids))
            if any(key not in entries or not entries[key]['available'] for key in selected):
                raise HTTPException(422, '所选技能不存在或不可读取，请刷新技能列表。')
            if selected == sorted(set(state['selected_ids'])):
                return self._public(entries, state)
            next_state = {'revision':state['revision'] + 1, 'selected_ids':selected}
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.config_path.parent,
                                                 suffix='.tmp', delete=False) as stream:
                    temp = Path(stream.name)
                    json.dump(next_state, stream, ensure_ascii=False, indent=2)
                os.replace(temp, self.config_path)
            except OSError:
                raise HTTPException(503, 'Skills 设置保存失败，原配置未被覆盖。') from None
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
            return self._public(entries, next_state)

    def invocation(self, message, requested_ids=None):
        commands = list(dict.fromkeys(COMMAND.findall(message)))
        if any(skill_id not in commands for skill_id in (requested_ids or [])):
            raise HTTPException(422, '技能调用与消息中的斜杠指令不一致，请重新选择。')
        if not commands:
            return {'instructions':'', 'skills':[]}
        with self.lock:
            entries, state = self._discover(), self._state()
            selected = set(state['selected_ids'])
            sections, invoked = [], []
            for command in commands:
                entry = entries.get(command)
                if entry is None:
                    if command in selected or command in (requested_ids or []):
                        raise HTTPException(422, f'技能 /{command} 已被移除，请更新消息。')
                    continue  # An ordinary slash-delimited word is not a skill command.
                if command not in selected:
                    raise HTTPException(422, f'技能 /{command} 未勾选，请先在 Skills 页面选择。')
                if not entry['available']:
                    raise HTTPException(422, f'技能 /{command} 无法读取，请检查技能文件。')
                try:
                    _, body, version, _ = self._read_skill(entry['_entry'])
                except (OSError, UnicodeError, ValueError):
                    raise HTTPException(422, f'技能 /{command} 读取失败，请刷新后重试。') from None
                sections.append(f'### /{command}\n{body.strip()}')
                invoked.append({'id':command, 'name':entry['name'], 'version':version})
            instructions = '\n\n'.join(sections)
            if len(instructions) > MAX_INSTRUCTION_CHARS:
                raise HTTPException(422, '本次技能指令过长，请减少同时调用的技能。')
            if instructions:
                instructions = (
                    '本条消息通过斜杠显式调用以下技能。只在本次任务范围内应用其规则。'
                    '用户明确要求及应用既有输出协议、模型选择、执行确认规则优先。'
                    '技能内的路径和工具名称是说明，不表示本环境已执行读取或拥有该工具。'
                    '当前仅加载技能入口正文；未提供的引用文件、脚本、外部工具不能声称已读取或调用。'
                    '如任务必须依赖这些能力，请明确指出缺少哪一步，不编造工具结果。\n\n' + instructions)
            return {'instructions':instructions, 'skills':invoked}


class SkillSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected_ids: list[StrictStr] = Field(max_length=1000)
    revision: StrictInt = Field(ge=0)


def create_skill_router(get_catalog):
    router = APIRouter(prefix='/api/skills')

    @router.get('')
    def skills():
        return get_catalog().list()

    @router.put('')
    def select_skills(payload: SkillSelection):
        return get_catalog().save(payload.selected_ids, payload.revision)

    return router
