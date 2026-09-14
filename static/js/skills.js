(function(root) {
    'use strict';
    function slashQuery(text, caret) {
        const before = String(text).slice(0, caret);
        const match = /(?:^|\s)\/([^\s/\\]*)$/.exec(before);
        if(!match) return null;
        // A caret inside an existing URL/path must not reinterpret its prefix.
        if(/^[^\s]*[/\\]/.test(String(text).slice(caret))) return null;
        return {start:caret - match[1].length - 1, end:caret, query:match[1]};
    }
    function filterSkills(skills, query) {
        const terms = String(query).trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
        return skills.filter(skill => skill.selected && skill.available !== false && terms.every(term =>
            `${skill.id} ${skill.name} ${skill.description}`.toLocaleLowerCase().includes(term)));
    }
    function insertSkill(text, query, id) {
        const before = text.slice(0, query.start), after = text.slice(query.end);
        const command = '/' + id;
        const suffix = after.startsWith(' ') ? after : ' ' + after;
        return {text:before + command + suffix, caret:before.length + command.length + 1};
    }

    let catalog = null, inFlight = null, serial = 0;
    const listeners = new Set();
    async function load(force = false) {
        if(inFlight) {
            const result = await inFlight;
            return force ? load(true) : result;
        }
        if(catalog && !force) return catalog;
        inFlight = (async () => {
            const response = await root.fetch('/api/skills', {cache:'no-store'});
            const data = await response.json();
            if(!response.ok) throw new Error(data.detail || '技能列表加载失败');
            catalog = data;
            for(const fn of listeners) fn(data);
            return data;
        })();
        try { return await inFlight; } finally { inFlight = null; }
    }
    function announceChange() {
        catalog = null;
        try {
            const channel = new root.BroadcastChannel('studio-skills');
            channel.postMessage({type:'skills-changed'}); channel.close();
        } catch(ignore) { /* Focus reload remains available. */ }
        root.parent?.postMessage({type:'skills-changed'}, root.location.origin);
    }
    function invokedIds(text) {
        const known = new Set((catalog?.skills || []).map(skill => skill.id));
        return [...new Set([...String(text).matchAll(/(?:^|\s)\/([a-zA-Z0-9][a-zA-Z0-9_-]*)(?=\s|$)/g)]
            .map(match => match[1]).filter(id => known.has(id)))];
    }
    function attach(editor, options = {}) {
        if(!editor) return null;
        const document = editor.ownerDocument;
        const read = options.read || (() => editor.value);
        const caret = options.caret || (() => editor.selectionStart);
        const replace = options.replace || ((value, offset) => {
            editor.value = value; editor.focus(); editor.setSelectionRange(offset, offset);
            editor.dispatchEvent(new root.Event('input', {bubbles:true}));
        });
        const popup = document.createElement('div');
        popup.className = 'skills-slash-popup'; popup.hidden = true;
        popup.id = `skills-slash-${++serial}`;
        popup.setAttribute('role', 'listbox'); popup.setAttribute('aria-label', 'Skills');
        // Outside the scaled body: getBoundingClientRect already uses viewport pixels.
        document.documentElement.append(popup);
        ['pointerdown','mousedown','click'].forEach(type => popup.addEventListener(type, event => event.stopPropagation()));
        editor.setAttribute('aria-autocomplete', 'list');
        editor.setAttribute('aria-controls', popup.id);
        editor.setAttribute('aria-expanded', 'false');
        let choices = [], selected = 0, query = null, composing = false, dismissed = false, error = '';
        function close() {
            popup.hidden = true; query = null;
            editor.setAttribute('aria-expanded', 'false'); editor.removeAttribute('aria-activedescendant');
        }
        function position() {
            if(popup.hidden) return;
            const box = editor.getBoundingClientRect();
            const width = Math.min(430, Math.max(270, box.width), root.innerWidth - 24);
            popup.style.width = width + 'px';
            popup.style.left = Math.max(12, Math.min(box.left, root.innerWidth - width - 12)) + 'px';
            popup.style.bottom = Math.max(12, root.innerHeight - box.top + 8) + 'px';
            popup.style.maxHeight = Math.max(90, Math.min(340, box.top - 20)) + 'px';
        }
        function choose(index) {
            const skill = choices[index];
            if(!skill || !query) return;
            const inserted = insertSkill(read(), query, skill.id);
            dismissed = true; close();
            replace(inserted.text, inserted.caret);
        }
        function draw() {
            popup.replaceChildren();
            const heading = document.createElement('div'); heading.className = 'skills-slash-heading';
            heading.textContent = 'Skills'; popup.append(heading);
            if(!choices.length) {
                const empty = document.createElement('div'); empty.className = 'skills-slash-empty';
                empty.textContent = error || (!catalog ? '正在读取技能…' : !catalog.selected_ids.length
                    ? '尚未勾选技能，请到侧栏 Skills 页面选择。' : '没有匹配的已勾选技能');
                popup.append(empty); editor.removeAttribute('aria-activedescendant');
            }
            choices.forEach((skill, index) => {
                const button = document.createElement('button'); button.type = 'button'; button.tabIndex = -1;
                button.id = `${popup.id}-${index}`; button.setAttribute('role', 'option');
                button.setAttribute('aria-selected', String(index === selected));
                const title = document.createElement('strong'); title.textContent = '/' + skill.id;
                const description = document.createElement('span'); description.textContent = skill.description || skill.name;
                button.append(title, description); button.title = `${skill.name}\n${skill.description}`;
                button.onmousedown = event => event.preventDefault();
                button.onclick = () => choose(index); popup.append(button);
                if(index === selected) editor.setAttribute('aria-activedescendant', button.id);
            });
            position();
        }
        function update() {
            if(composing || dismissed || document.activeElement !== editor) return close();
            const next = slashQuery(read(), caret());
            if(!next) return close();
            if(!query || query.query !== next.query || query.start !== next.start) selected = 0;
            query = next;
            choices = filterSkills(catalog?.skills || [], query.query);
            selected = Math.min(selected, Math.max(0, choices.length - 1));
            popup.hidden = false; editor.setAttribute('aria-expanded', 'true'); draw();
        }
        async function reload() {
            try { await load(true); error = ''; }
            catch(problem) { catalog = null; error = problem.message; }
            update();
        }
        editor.addEventListener('input', () => { dismissed = false; update(); });
        editor.addEventListener('focus', () => { dismissed = false; update(); reload(); });
        editor.addEventListener('click', () => { dismissed = false; update(); });
        editor.addEventListener('compositionstart', () => { composing = true; close(); });
        editor.addEventListener('compositionend', () => { composing = false; dismissed = false; update(); });
        editor.addEventListener('blur', close);
        editor.addEventListener('keydown', event => {
            if(composing || event.isComposing || event.keyCode === 229 || popup.hidden) return;
            if(!['ArrowDown','ArrowUp','Enter','Tab','Escape'].includes(event.key)) return;
            if(event.key === 'Tab' && !choices.length) { close(); return; }
            event.preventDefault(); event.stopImmediatePropagation();
            if(event.key === 'Escape') { dismissed = true; close(); return; }
            if(event.key === 'Enter' || event.key === 'Tab') { choose(selected); return; }
            if(choices.length) {
                selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length;
                draw(); popup.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest'});
            }
        }, true);
        editor.addEventListener('keyup', event => {
            if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) { dismissed = false; update(); }
        });
        root.addEventListener('resize', position);
        document.addEventListener('scroll', position, true);
        root.addEventListener('focus', reload);
        root.addEventListener('message', event => {
            if(event.origin === root.location.origin && event.data?.type === 'skills-changed') reload();
        });
        try {
            const channel = new root.BroadcastChannel('studio-skills');
            channel.onmessage = event => { if(event.data?.type === 'skills-changed') reload(); };
        } catch(ignore) { /* Window focus refresh supports browsers without BroadcastChannel. */ }
        listeners.add(update);
        load().then(update).catch(problem => { error = problem.message; update(); });
        return {close, refresh:reload};
    }
    const api = {slashQuery, filterSkills, insertSkill, invokedIds, load, announceChange, attach};
    if(typeof module !== 'undefined' && module.exports) module.exports = api;
    if(root) root.StudioSkills = api;
})(typeof window !== 'undefined' ? window : null);
