(function() {
    'use strict';
    const byId = id => document.getElementById(id);
    let data = null, saving = false, pending = null, selection = [], renderedKey = '';
    function status(message, error = false) {
        byId('skillsSaveStatus').textContent = message;
        byId('skillsSaveStatus').dataset.error = String(error);
    }
    function render() {
        const list = byId('skillsCatalog');
        byId('skillsSelectAll').disabled = !data;
        byId('skillsClearAll').disabled = !data;
        if(!data) return;
        const selected = new Set(selection);
        const query = byId('skillsSearch').value.trim().toLocaleLowerCase();
        const rows = data.skills.filter(skill => `${skill.id} ${skill.name} ${skill.description}`.toLocaleLowerCase().includes(query));
        byId('skillsCount').textContent = `${data.skills.length} 个技能 · 已选 ${selected.size} 个${query ? ` · 匹配 ${rows.length} 个` : ''}`;
        const key = JSON.stringify([query, rows.map(skill => [skill.id, skill.version, skill.available])]);
        if(key === renderedKey) {
            for(const row of list.querySelectorAll('.skills-row')) {
                const checked = selected.has(row.dataset.skillId);
                row.querySelector('input').checked = checked;
                row.classList.toggle('is-selected', checked);
                row.querySelector('.skills-badge').textContent = row.querySelector('input').disabled ? '不可读取' : checked ? '已选择' : '未选择';
            }
            return;
        }
        renderedKey = key;
        list.replaceChildren();
        if(!rows.length) {
            const empty = document.createElement('div'); empty.className = 'skills-empty';
            empty.textContent = query ? '没有匹配的技能' : '技能目录中暂无可读取的 SKILL.md'; list.append(empty);
        }
        for(const skill of rows) {
            const row = document.createElement('label'); row.className = 'skills-row';
            row.dataset.skillId = skill.id;
            row.classList.toggle('is-selected', selected.has(skill.id));
            const check = document.createElement('input'); check.type = 'checkbox'; check.checked = selected.has(skill.id);
            check.disabled = !skill.available; check.setAttribute('aria-label', `选择 ${skill.id}`);
            check.onchange = () => {
                const next = new Set(selection);
                if(check.checked) next.add(skill.id); else next.delete(skill.id);
                save([...next]);
            };
            const content = document.createElement('div');
            const name = document.createElement('h2'); name.textContent = skill.name;
            const description = document.createElement('p'); description.textContent = skill.description || '暂无简介';
            const command = document.createElement('code'); command.textContent = '/' + skill.id;
            const badge = document.createElement('span'); badge.className = 'skills-badge';
            badge.textContent = !skill.available ? '不可读取' : selected.has(skill.id) ? '已选择' : '未选择';
            content.append(name, description, command); row.append(check, content, badge); list.append(row);
        }
    }
    async function load() {
        if(saving) return;
        byId('skillsRefresh').disabled = true;
        try {
            const response = await fetch('/api/skills', {cache:'no-store'});
            const result = await response.json();
            if(!response.ok) throw new Error(result.detail || '技能列表加载失败');
            data = result; pending = null; selection = [...data.selected_ids]; status('选择后自动保存'); render();
        } catch(error) { status(error.message, true); }
        finally { byId('skillsRefresh').disabled = false; }
    }
    async function save(ids) {
        pending = ids; selection = [...ids]; render(); status('正在保存…');
        if(saving) return;
        saving = true; byId('skillsRefresh').disabled = true;
        try {
            while(pending !== null) {
                const current = pending; pending = null;
                const response = await fetch('/api/skills', {method:'PUT', headers:{'Content-Type':'application/json'},
                    body:JSON.stringify({selected_ids:current, revision:data.revision})});
                const result = await response.json();
                if(!response.ok) {
                    const problem = new Error(result.detail || '保存失败'); problem.conflict = response.status === 409; throw problem;
                }
                data = result;
                StudioSkills.announceChange();
            }
            selection = [...data.selected_ids]; render(); status('已保存');
        } catch(error) {
            pending = null;
            // Re-read after both conflicts and uncertain network outcomes.
            try {
                const response = await fetch('/api/skills', {cache:'no-store'});
                if(response.ok) data = await response.json();
            } catch(ignore) { /* Show last confirmed state with an explicit failure. */ }
            selection = [...data.selected_ids]; render(); status(`${error.message} 请刷新或重新选择。`, true);
        } finally { saving = false; byId('skillsRefresh').disabled = false; }
    }
    byId('skillsSearch').addEventListener('input', render);
    byId('skillsRefresh').onclick = load;
    byId('skillsSelectAll').onclick = () => save(data.skills.filter(skill => skill.available).map(skill => skill.id));
    byId('skillsClearAll').onclick = () => save([]);
    load();
})();
