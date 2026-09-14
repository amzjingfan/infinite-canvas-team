const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createController, mount} = require('../static/js/smart-canvas-agent.js');
const clone = x => JSON.parse(JSON.stringify(x));
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return {promise, resolve}; };
const tick = () => new Promise(r => setImmediate(r));
function fixture() {
    const operations = [{id:'p',op:'create_prompt',text:'hello'}, {id:'m',op:'create_media',kind:'image',reference_node_ids:[]},
        {id:'c',op:'connect',from:'p',to:'m'}, {id:'g',op:'generate',node:'m',settings:{provider:'test',model:'image',count:1}},
        {id:'next',op:'create_prompt',text:'next'}];
    const records = new Map(['a','b'].map(id => [id, {id,canvas_id:'canvas',title:id,updated_at:1,messages:[],
        plans:[{id:'plan',version:1,status:'proposed',reference_snapshot:[],operations}],runs:[], draft:'',reference_node_ids:[],
        chat_provider:'',chat_model:'',image_provider:'',image_model:'',video_provider:'',video_model:''}]));
    const nodes = new Map(), calls = [], saved = [], gates = {}, finalized = [];
    let failSave = false;
    const response = data => ({ok:true,json:async () => clone(data)});
    const host = {canvasId:'canvas',clientId:'client', getAgentCanvasContext:() => ({}),
        async ensureSaved() { if(gates.save) await gates.save.promise; if(failSave) throw Error('save failed'); saved.push([...nodes.keys()]); },
        applyAgentCanvasOperations({conversationId,runId,operations}) {
            for(const op of operations) if(op.op !== 'connect') nodes.set(`agent_${runId}_${op.id}`, {conversationId,op:op.id});
            calls.push(['graph',conversationId,operations[0].id]);
            return {created_node_ids:operations[0].op === 'connect' ? [] : [`agent_${runId}_${operations[0].id}`],operation_nodes:{}};
        },
        applyAgentGenerationResult({conversationId,runId,operation,result,run}) {
            if(!nodes.has(`agent_${runId}_${operation.id}`)) {
                assert.equal(run.id,runId);
                assert.equal(run.conversation_id,conversationId);
                assert.ok(run.steps.find(step=>step.operation_id===operation.id).result.media.length);
                nodes.set(`agent_${runId}_${operation.id}`,{conversationId,op:operation.id});
            }
            finalized.push({conversationId,result});
            return {created_node_ids:[`agent_${runId}_${operation.id}`],operation_nodes:{}};
        },
        setAgentRunState() {},
        async resumeCanvasAgentGeneration(args) { return send(`/api/canvases/canvas/agent/conversations/${args.conversationId}/runs/${args.runId}/steps/${args.operation.id}/execute`, {method:'POST',body:JSON.stringify({client_id:'client',resume_only:true})}).then(r => r.json()); }
    };
    async function send(url, options={}) {
        const method = options.method || 'GET', body = options.body ? JSON.parse(options.body) : {};
        const parts = url.split('/'), id = parts[6], record = records.get(id);
        calls.push([method,id,url,body]);
        if(method === 'PATCH') {
            Object.assign(record,body); record.updated_at++;
            const snapshot = clone(record);
            if(gates.patch) await gates.patch.promise;
            return response({conversation:snapshot});
        }
        if(url.includes('/confirm')) {
            record.plans[0].status='confirmed';
            if(!record.runs.length) { record.runs.push({id:`run${id}`,canvas_id:'canvas',conversation_id:id,plan_id:'plan',version:1,
                client_id:'client',status:'running',stop_requested:false,operations:clone(operations),reference_snapshot:[],
                steps:operations.map(o => ({operation_id:o.id,status:'ready',created_node_ids:[],provider_task_id:'',result:null,error:''}))}); record.updated_at++; }
        }
        const run = record.runs[0];
        if(url.endsWith('/execute')) {
            const step = run.steps.find(s => s.operation_id === 'g');
            if(gates.rejectExecute && !body.resume_only) {
                step.status='failed'; run.status='failed'; record.updated_at++;
                return {ok:false,status:409,json:async()=>({detail:'reference changed'})};
            }
            if(!body.resume_only && step.status === 'ready') {
                assert.ok(saved.some(ids => ids.includes(`agent_${run.id}_g`)), 'pending output must be strictly saved before execute');
                step.status = 'running'; record.updated_at++;
            }
        }
        if(url.endsWith('/stop')) {
            if(gates.stop) await gates.stop.promise;
            run.status = 'paused'; run.stop_requested = true; record.updated_at++;
        }
        if(url.endsWith('/events') && method === 'POST') {
            if(body.type === 'restore_results') { run.client_id=body.client_id; }
            else if(body.type === 'resume') { run.status = 'running'; run.stop_requested = false; run.client_id='client'; }
            else {
                const step = run.steps.find(s => s.operation_id === body.operation_id);
                if(body.operation_id === 'g') assert.equal(step.status,'generated');
                step.status = 'completed'; step.created_node_ids=body.created_node_ids;
                if(run.steps.every(s => s.status === 'completed')) run.status='completed';
            }
            record.updated_at++;
        }
        const data = clone({conversation:record,...(run ? {run} : {})});
        if(gates.stopReply && url.endsWith('/stop')) await gates.stopReply.promise;
        if(gates.execute && url.endsWith('/execute')) await gates.execute.promise;
        return response(data);
    }
    const c = createController({canvasId:'canvas',host,fetch:send});
    function generated(id, status='generated') {
        const record = records.get(id), step = record.runs[0].steps.find(s => s.operation_id==='g');
        step.status=status; step.result={media:[{url:`/output/${id}.png`,kind:'image',name:id}]};
        if(status==='unknown') { record.runs[0].status='unknown'; step.error='提交状态未知；已返回素材已保留。请到平台核实，不会自动重新提交。'; }
        record.updated_at++;
    }
    async function until(predicate) { for(let i=0;i<300;i++) { if(predicate()) return; await new Promise(r=>setTimeout(r,10)); } assert.fail('controller did not reach expected state'); }
    return {c,host,fetch:send,records,nodes,calls,gates,finalized,generated,until,setFailSave:v=>{failSave=v;}};
}

test('long image review does not repeatedly overwrite and save unchanged returned pictures', async () => {
    const f=fixture();
    await f.c.selectConversation('a');
    const running=f.c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.generated('a','running');
    f.records.get('a').runs[0].steps[3].result.quality={status:'reviewing',phase:'reviewing'};
    let savedWhileUnchanged;
    try {
        await f.until(()=>f.finalized.length>0);
        await new Promise(resolve=>setTimeout(resolve,1150));
        savedWhileUnchanged=f.finalized.length;
    } finally {
        f.generated('a');
        await running;
    }
    assert.equal(savedWhileUnchanged,1,'read-only quality polling must not overwrite the canvas every second');
    assert.equal(f.finalized.length,2,'the final changed result still needs a strict save before acknowledgment');
});

test('independent runs submit together and late A result never switches B or its draft', async () => {
    const f=fixture(), {c}=f;
    assert.equal(typeof c.confirmPlan,'function');
    await c.selectConversation('a'); const a=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    await c.selectConversation('b'); c.updatePreferences({draft:'B next message'}); const b=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('b').runs[0]?.steps[3].status==='running');
    f.generated('b'); await b;
    assert.equal(f.finalized[0].conversationId,'b');
    f.generated('a'); await a;
    assert.equal(f.finalized[1].conversationId,'a');
    assert.equal(c.getSnapshot().activeId,'b');
    assert.equal(c.getSnapshot().conversation.draft,'B next message');
});

test('local stop intent blocks next graph operation before stop HTTP response; paid output still finalizes', async () => {
    const f=fixture(), {c}=f;
    assert.equal(typeof c.confirmPlan,'function');
    await c.selectConversation('a'); const running=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.gates.stop=deferred(); const stopping=c.stopRun('runa','a');
    assert.equal(c.getSnapshot().conversation.runs[0].status,'paused');
    f.generated('a'); await running;
    assert.equal(f.finalized.length,1);
    assert.ok(!f.calls.some(call=>call[0]==='graph' && call[2]==='next'));
    f.gates.stop.resolve(); await stopping;
    await c.resumeRun('runa','a');
    assert.equal(c.getSnapshot().conversation.runs[0].status,'completed');
});

test('save failure leaves generated step unacknowledged and explicit continue replays output', async () => {
    const f=fixture(), {c}=f;
    assert.equal(typeof c.confirmPlan,'function');
    await c.selectConversation('a'); const running=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.setFailSave(true); f.generated('a');
    await assert.rejects(running,/save failed/);
    assert.equal(f.records.get('a').runs[0].steps[3].status,'generated');
    assert.ok(!f.calls.some(call=>call[0]==='graph' && call[2]==='next'));
    f.nodes.delete('agent_runa_g'); f.setFailSave(false);
    await c.resumeRun('runa','a');
    assert.equal(f.records.get('a').runs[0].status,'completed');
    assert.equal(f.calls.filter(call=>call[3]?.resume_only===false).length,1);
});

test('late PATCH cannot erase newer run or overwrite paused status', async () => {
    const f=fixture(), {c}=f;
    assert.equal(typeof c.confirmPlan,'function');
    await c.selectConversation('a'); const running=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.gates.patch=deferred(); c.updatePreferences({draft:'next'});
    const patch=c.savePreferences(); await tick();
    await c.stopRun('runa','a');
    f.gates.patch.resolve(); await patch;
    assert.equal(c.getSnapshot().conversation.runs[0].status,'paused');
    f.generated('a'); await running;
    assert.equal(c.getSnapshot().conversation.draft,'next');
});

test('partial unknown results display and persist without acknowledgement or dependent execution', async () => {
    const f=fixture(), {c}=f;
    assert.equal(typeof c.confirmPlan,'function');
    await c.selectConversation('a'); const running=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.generated('a','unknown'); await running;
    assert.equal(f.finalized.length,1);
    assert.equal(f.records.get('a').runs[0].steps[3].status,'unknown');
    assert.ok(!f.calls.some(call=>call[0]==='graph' && call[2]==='next'));
});

test('unknown result-only recovery retries strict save and fresh-client reload without execute or advance', async () => {
    const f=fixture();
    await f.c.selectConversation('a'); const running=f.c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.setFailSave(true); f.generated('a','unknown');
    await assert.rejects(running,/save failed/);
    f.nodes.delete('agent_runa_g'); f.setFailSave(false);
    const before=f.calls.length;
    const reloaded=createController({canvasId:'canvas',host:{...f.host,clientId:'fresh'},fetch:f.fetch});
    await reloaded.selectConversation('a');
    assert.equal(f.finalized.length,1,'loading alone does not restore');
    await reloaded.restoreResults('runa','a');
    await reloaded.restoreResults('runa','a');
    assert.ok(f.nodes.has('agent_runa_g'));
    const run=f.records.get('a').runs[0];
    assert.equal(run.client_id,'fresh'); assert.equal(run.status,'unknown');
    assert.equal(run.steps[3].status,'unknown'); assert.equal(run.steps[4].status,'ready');
    assert.ok(!f.calls.slice(before).some(c=>c[2]?.endsWith('/execute') || c[0]==='graph' || c[3]?.type==='operation_completed'));
});

test('late stop response cannot replace completed state after explicit continue', async () => {
    const f=fixture(), {c}=f;
    await c.selectConversation('a'); const running=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.gates.stopReply=deferred(); const stopping=c.stopRun('runa','a'); await tick();
    f.generated('a');
    await c.resumeRun('runa','a'); await running;
    f.gates.stopReply.resolve(); await stopping;
    assert.equal(c.getSnapshot().conversation.runs[0].status,'completed');
});

test('reloaded read-only history and repeated confirmation do not create or submit any nodes', async () => {
    const f=fixture();
    await f.c.selectConversation('a'); const running=f.c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    await f.c.stopRun('runa','a'); f.generated('a'); await running;
    const graphCount=f.calls.filter(call=>call[0]==='graph').length;
    const reloaded=createController({canvasId:'canvas',host:{...f.host,clientId:'second-tab'},fetch:f.fetch});
    await reloaded.selectConversation('a');
    await reloaded.pollRun('runa','a');
    await reloaded.confirmPlan('plan',1,'a');
    assert.equal(f.calls.filter(call=>call[0]==='graph').length,graphCount);
    assert.equal(f.calls.filter(call=>call[3]?.resume_only===false).length,1);
});

function messageDocument() {
    const elements=new Map();
    const element=()=>({children:[],get childNodes(){return this.children;},hidden:false,value:'',scrollTop:0,scrollHeight:2000,clientHeight:300,
        classList:{toggle(){},add(){}},setAttribute(){},addEventListener(){},querySelectorAll(){return [];},
        append(...items){this.children.push(...items);},replaceChildren(){this.children=[];},focus(){}});
    const document={getElementById(id){if(!elements.has(id)) elements.set(id,element()); return elements.get(id);},
        createElement:element,createTextNode:text=>({nodeType:3,textContent:text}),addEventListener(){}};
    document.getElementById('agentModelPopup').hidden=true;
    return document;
}

test('run controls show one relevant action across execution, pause, resume and reload', async () => {
    const f=fixture(), document=messageDocument(), resumeGate=deferred();
    let resumeAttempts=0;
    f.records.get('a').messages=[{id:'assistant',role:'assistant',content:'plan',plan_id:'plan',plan_version:1}];
    const ui=mount({document,host:f.host,fetch:async (url,options)=>{
        if(options?.body && JSON.parse(options.body).type==='resume') {
            await resumeGate.promise;
            if(++resumeAttempts===1) return {ok:false,status:503,json:async()=>({detail:'resume unavailable'})};
        }
        return f.fetch(url,options);
    }});
    const c=ui.controller;
    const flatten=node=>[node,...(node.children||[]).flatMap(flatten)];
    const actions=()=>flatten(document.getElementById('agentMessages')).filter(node=>typeof node.onclick==='function');
    await c.selectConversation('a');
    const running=c.confirmPlan('plan',1);
    let resuming;
    try {
        await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
        assert.deepEqual(actions().map(button=>button.textContent),['停止后续步骤']);
        await actions()[0].onclick();
        assert.deepEqual(actions().map(button=>button.textContent),['继续执行']);
        resuming=actions()[0].onclick();
        assert.equal(actions().length,1);
        assert.equal(actions()[0].disabled,true,'a re-render while resuming must not re-enable the action');
        resumeGate.resolve();
        await resuming;
        assert.equal(document.getElementById('agentStatus').textContent,'resume unavailable');
        assert.equal(actions()[0].disabled,false);
        resuming=actions()[0].onclick();
        await f.until(()=>actions()[0]?.textContent==='停止后续步骤');
        f.generated('a');
        await resuming; await running;
        assert.equal(actions().length,0);

        // Server still says running, but this page has no driver after reload.
        const record=f.records.get('a'); record.runs[0].status='running'; record.updated_at++;
        const reloadedDocument=messageDocument();
        const reloaded=mount({document:reloadedDocument,host:f.host,fetch:f.fetch});
        await reloaded.controller.selectConversation('a');
        const recovery=flatten(reloadedDocument.getElementById('agentMessages')).filter(node=>typeof node.onclick==='function');
        assert.deepEqual(recovery.map(button=>button.textContent),['继续执行']);
    } finally {
        resumeGate.resolve(); f.generated('a');
        if(resuming) await resuming;
        await running;
    }
});

test('an interrupted local driver exposes recovery even when server run status remains running', async () => {
    const f=fixture(), document=messageDocument();
    f.records.get('a').messages=[{id:'assistant',role:'assistant',content:'plan',plan_id:'plan',plan_version:1}];
    const ui=mount({document,host:f.host,fetch:f.fetch});
    await ui.controller.selectConversation('a');
    const running=ui.controller.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    f.setFailSave(true); f.generated('a');
    await assert.rejects(running,/save failed/);
    assert.equal(ui.controller.getSnapshot().conversation.runs[0].status,'running');
    const flatten=node=>[node,...(node.children||[]).flatMap(flatten)];
    const actions=flatten(document.getElementById('agentMessages')).filter(node=>typeof node.onclick==='function');
    assert.deepEqual(actions.map(button=>button.textContent),['继续执行']);
});

for(const outcome of ['success','failure']) test(`discussion waiting message follows the sent text, stays with its conversation and clears on ${outcome}`, async () => {
    const f=fixture(), gate=deferred(), document=messageDocument();
    for(const record of f.records.values()) record.plans=[];
    f.records.get('a').messages=[{id:'old',role:'user',content:'same request'}];
    f.host.getProviders=()=>[{id:'test',has_key:true,chat_models:['chat'],image_models:['image'],video_models:['video']}];
    const ui=mount({document,host:f.host,fetch:async (url,options)=>{
        if(!url.endsWith('/messages')) return f.fetch(url,options);
        await gate.promise;
        if(outcome==='failure') return {ok:false,status:503,json:async()=>({detail:'reply unavailable'})};
        const record=f.records.get('a');
        record.messages.push({id:'sent',role:'user',content:'same request'},{id:'reply',role:'assistant',content:'ready'});
        record.updated_at++;
        return {ok:true,json:async()=>({conversation:clone(record)})};
    }});
    const c=ui.controller, messages=document.getElementById('agentMessages');
    const pending=()=>messages.children.filter(item=>item.className?.includes('agent-message-pending'));
    await c.selectConversation('a');
    c.updatePreferences({draft:'same request'});
    const sending=c.sendMessage();
    try {
        assert.equal(pending().length,1,'waiting indicator must appear immediately in the message log');
        assert.equal(messages.children.at(-2).children[0].textContent,'same request');
        assert.equal(messages.children.length,3,'a repeated request still needs its own optimistic user message');
        assert.equal(c.getSnapshot().conversation.messages.length,1,'temporary messages must not enter saved history');
        assert.doesNotMatch(document.getElementById('agentStatus').textContent,/正在讨论/);
        await c.selectConversation('b');
        assert.equal(pending().length,0);
        await c.selectConversation('a');
        assert.equal(pending().length,1);
        c.updatePreferences({draft:'next turn'});
        assert.equal(messages.children.at(-2).children[0].textContent,'same request');
    } finally {
        gate.resolve();
        if(outcome==='failure') await assert.rejects(sending,/reply unavailable/);
        else await sending;
    }
    assert.equal(pending().length,0,'waiting state must clear even when no new reply was saved');
    assert.equal(messages.children.length,outcome==='success'?3:1,'temporary message must not duplicate the saved reply');
    assert.equal(c.getSnapshot().conversation.draft,'next turn');
});

test('run progress preserves a reader scroll position even while a separate discussion reply is pending', async () => {
    const f=fixture(), messageGate=deferred();
    f.records.get('a').messages=[{id:'assistant',role:'assistant',content:'plan',plan_id:'plan',plan_version:1}];
    f.host.getProviders=()=>[{id:'test',has_key:true,chat_models:['chat'],image_models:['image'],video_models:['video']}];
    const document=messageDocument();
    const ui=mount({document,host:f.host,fetch:async (url,options)=>{
        if(url.endsWith('/messages')) { await messageGate.promise; return {ok:true,json:async()=>({conversation:clone(f.records.get('a'))})}; }
        return f.fetch(url,options);
    }});
    const c=ui.controller;
    await c.selectConversation('a'); const running=c.confirmPlan('plan',1);
    await f.until(()=>f.records.get('a').runs[0]?.steps[3].status==='running');
    c.updatePreferences({draft:'discuss while running'});
    const sending=c.sendMessage(); await tick();
    const messages=document.getElementById('agentMessages'); messages.scrollTop=120;
    f.generated('a');
    try { await running; assert.equal(messages.scrollTop,120); }
    finally { messageGate.resolve(); await sending; }
});

test('failed pre-submit validation is reconciled immediately into the originating run UI', async () => {
    const f=fixture();
    f.gates.rejectExecute=true;
    await f.c.selectConversation('a');
    await assert.rejects(f.c.confirmPlan('plan',1),/reference changed/);
    assert.equal(f.c.getSnapshot().conversation.runs[0].status,'failed');
    assert.equal(f.c.getSnapshot().conversation.runs[0].steps[3].status,'failed');
});
