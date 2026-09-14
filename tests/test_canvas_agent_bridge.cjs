const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/smart-canvas.js'), 'utf8');
const agent = require('../static/js/smart-canvas-agent.js');
const copy = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
function production(name) {
    const match = source.match(new RegExp(`^(?:async )?function ${name}\\([^]*?^}`, 'm'));
    assert.ok(match, `production function ${name} exists`);
    return match[0];
}
function setup() {
    let sequence = 0;
    const ctx = {
        canvasId:'canvas/one', smartClientId:'tab-one', canvas:{title:'Canvas', updated_at:1, connections:[]},
        nodes:[{id:'ref', type:'smart-image', title:'Original', x:10, y:20, images:[{url:'/cat.png', kind:'image'}]}],
        selectedId:'ref', selectedIds:[], selectedImage:{nodeId:'ref', index:0}, settings:{provider_id:'wrong', ratio:'9:16'},
        initialSmartSettings:{}, canvasDefaultSmartSettings:{}, viewport:{x:0,y:0,scale:1},
        canvasSyncInFlight:false, saveTimer:null, activeComposerSubject:null, lastComposerNodeId:'',
        smartNodeRunTokens:new Map(), SMART_LOG_PREVIEW_NODE_ID:'preview', MEDIA_NODE_DEFAULT_SCALE:2,
        MEDIA_GROUP_DEFAULT_SCALE:0.8, apiProviders:[], window:{CanvasAgent:agent},
        uid:prefix => `${prefix}_${++sequence}`, nowMs:() => 100, tr:x => x,
        render:() => {}, scheduleSave:() => {}, pushUndo:() => {}, savePromptDraftForCurrent:() => {},
        resolveChatProviderId:() => '', resolveChatModel:() => '', cloneSmartSettings:copy,
        mediaKindForItem:item => item.kind || 'image', mediaNodeDefaultScale:node => node.images.length > 1 ? 0.8 : 2,
        nodeRect:node => ({x:node.x,y:node.y,width:node.w || 316,height:node.h || 240}),
        viewportCenter:() => ({x:0,y:0}), toggleAssetLibrary:() => {},
        clearTimeout:() => {}, setTimeout:() => 1, document:{getElementById:() => null},
        recoverStuckLoopOutputsFromLogs:() => false, clearCompletedNodeBusyStates:() => false,
        resumeSmartPendingTasks:() => {}, resumeJimengPendingNodes:() => {},
        toasts:[], toast:message => ctx.toasts.push(message),
        calls:[], fetch:async (url, options) => {
            ctx.calls.push({url, method:options?.method, body:options?.body && JSON.parse(options.body)});
            return {ok:true, json:async () => ({canvas:{updated_at:2}})};
        }
    };
    vm.createContext(ctx);
    vm.runInContext(source.slice(source.indexOf('const SIZE_MAP ='),source.indexOf('function tr(')),ctx);
    const names = ['selectedNodeIds','getAgentCanvasContext','createNode','createPromptNode','inheritNodeMetaFromImage',
        'stripImageGenerationMeta','mediaItemForStorage','settingsForStorage','canvasForStorage',
        'clearSmartNodeTransientRunState','serializableSmartNode','normalizeLegacySmartNode','cloneSmartNode',
        'insertSmartWorkflowIntoCanvas','addConnection','connectInputNode','attachRunMeta','finalizePendingNode',
        'liveSmartNode','markSmartNodeComplete','clearSmartNodeBusyState','smartPendingTasks',
        'smartNodeHasDisplayResult','smartNodeHasCompletedResult','smartNodeInFlight',
        'clearSourceBusyStateIfDownstreamDone','copyMediaSizeFields','nonPreviewOutputImages','cleanHistoryImages',
        'mergeSmartImageLists','mergeSmartNode','completeSmartNodeWithImages','mergeSmartNodeLists',
        'mergeSmartConnections','applyMergedServerCanvas','apiErrorMessage','apiImageSize','parseRatioValue'];
    vm.runInContext(names.map(production).join('\n'), ctx);
    // Includes the actual host and bridge helpers, without evaluating DOM boot code.
    vm.runInContext(source.slice(source.indexOf('function createCanvasAgentHost('), source.indexOf('function safeScale(')), ctx);
    vm.runInContext('let canvasSaveQueue = Promise.resolve();\n' +
        ['saveCanvas','queueCanvasSave','persistCanvas'].filter(n => source.includes(`function ${n}(`)).map(production).join('\n'), ctx);
    ctx.host = ctx.createCanvasAgentHost();
    // Supply the explicit frozen run now required by the production bridge.
    // Older bridge fixtures build plans incrementally; freeze each introduced
    // reference once, then retain that snapshot across later live edits.
    const runs=new Map();
    const fixtureRun=args=>{
        if(args.run) return args.run;
        if(!runs.has(args.runId)) runs.set(args.runId,{id:args.runId,canvas_id:ctx.canvasId,
            conversation_id:args.conversationId,client_id:ctx.smartClientId,operations:[],reference_snapshot:[],steps:[]});
        const run=runs.get(args.runId);
        const freeze=id=>{
            if(run.operations.some(op=>op.id===id) || run.reference_snapshot.some(ref=>ref.id===id)) return;
            const node=ctx.nodes.find(n=>n.id===id); if(!node) return;
            const output=node.agent?.role==='output' || (node.runSettings && (node.runModelPrompt || node.runPrompt));
            const parents=output?[]:[...new Set(node.inputNodeIds || [])].sort();
            run.reference_snapshot.push({id,type:node.type || '',text:node.text || '',
                images:(node.images || []).map(m=>({url:m.url,kind:m.kind || 'image',name:m.name || ''})),
                input_node_ids:parents,...(output?{prompt_fallback:node.runModelPrompt || node.runPrompt || ''}:{})});
            parents.forEach(freeze);
        };
        for(const op of args.operations || (args.operation?[args.operation]:[])) {
            if(op.op==='create_media') op.reference_node_ids.forEach(freeze);
            if(!run.operations.some(o=>o.id===op.id)) {
                run.operations.push(copy(op)); run.steps.push({operation_id:op.id,status:'ready',result:null});
            }
        }
        if(args.result) Object.assign(run.steps.find(s=>s.operation_id===args.operation.id),{status:'generated',result:copy(args.result)});
        return copy(run);
    };
    for(const name of ['applyAgentCanvasOperations','runCanvasAgentGeneration','applyAgentGenerationResult']) {
        const productionHost=ctx.host[name];
        ctx.host[name]=args=>productionHost({...args,run:fixtureRun(args)});
    }
    return ctx;
}
const ids = {conversationId:'conversation/a', runId:'run-a'};
const prefix = [{id:'p1',op:'create_prompt',text:'confirmed cat'},
    {id:'m1',op:'create_media',kind:'image',reference_node_ids:['ref']},
    {id:'c1',op:'connect',from:'p1',to:'m1'}];
const generate = {id:'g1',op:'generate',node:'m1',settings:{provider:'confirmed',model:'image-model',count:2,aspect_ratio:'16:9',resolution:'2k',size:'2048x1152',quality:'high'}};
function creativeRun(ctx) {
    return {id:ids.runId,canvas_id:ctx.canvasId,conversation_id:ids.conversationId,client_id:ctx.smartClientId,
        mode:'creation',status:'running',reference_snapshot:[{id:'ref',text:'',images:[{url:'/cat.png',kind:'image',name:''}],input_node_ids:[]}],
        operations:[{id:'g1',op:'generate',kind:'image',prompt:'Frozen initial instruction',reference_node_ids:['ref'],settings:copy(generate.settings)}],
        steps:[{operation_id:'g1',status:'ready',result:null}]};
}

test('creative generation creates only independent display prompt and output, saved before submission', async () => {
    const ctx=setup(), run=creativeRun(ctx), operation=run.operations[0];
    ctx.nodes=[]; // Frozen attachments do not require the original canvas node.
    await ctx.host.runCanvasAgentGeneration({...ids,run,operation});
    assert.deepEqual(ctx.nodes.map(n=>n.id),['agent_run-a_g1_prompt','agent_run-a_g1']);
    const [prompt,output]=ctx.nodes;
    assert.equal(prompt.text,'Frozen initial instruction'); assert.equal(prompt.agent.displayOnly,true);
    assert.equal(output.runModelPrompt,'Frozen initial instruction');
    assert.deepEqual(copy(output.runInputRefs).map(m=>m.url),['/cat.png']);
    assert.equal(output.sourceNodeId,undefined); assert.equal(output.runSourceNodeId,undefined);
    assert.deepEqual(copy(ctx.canvas.connections),[]);
    assert.ok(ctx.nodes.every(n=>!n.inputNodeIds?.length));
    assert.equal(ctx.calls[0].method,'PUT');
    assert.equal(ctx.calls[0].body.nodes.length,2);
    assert.ok(ctx.calls[1].url.endsWith('/steps/g1/execute'));
    prompt.text='Manually edited display';
    ctx.host.applyAgentCanvasOperations({...ids,run,operations:[operation]});
    assert.equal(prompt.text,'Manually edited display'); assert.equal(ctx.nodes.length,2);
});

test('creative revisions retain initial display prompt and restore only durable output without any source graph', () => {
    const ctx=setup(), run=creativeRun(ctx), operation=run.operations[0];
    ctx.host.applyAgentCanvasOperations({...ids,run,operations:[operation]});
    run.steps[0]={operation_id:'g1',status:'generated',result:{best_url:'/edit.png',media:[{url:'/edit.png',kind:'image'}],attempts:[
        {round:1,prompt:'Only correct the title',input_media:[{url:'/first.png',kind:'image'}],media:[{url:'/edit.png',kind:'image'}]}]}};
    const applied=ctx.host.applyAgentGenerationResult({...ids,run,operation});
    assert.deepEqual(copy(applied.created_node_ids),['agent_run-a_g1']);
    assert.equal(ctx.nodes.find(n=>n.agent?.displayOnly).text,'Frozen initial instruction');
    assert.equal(ctx.nodes.find(n=>n.agent?.role==='output').runModelPrompt,'Only correct the title');
    ctx.nodes=[];
    ctx.host.applyAgentGenerationResult({...ids,run,operation});
    assert.deepEqual(ctx.nodes.map(n=>n.id),['agent_run-a_g1']);
    assert.deepEqual(copy(ctx.canvas.connections),[]);
    assert.equal(ctx.nodes[0].runSourceNodeId,undefined);
});

test('creative display prompt remains usable by manual composer while the original Agent task runs', async () => {
    const ctx=setup(), run=creativeRun(ctx);
    ctx.host.applyAgentCanvasOperations({...ids,run,operations:run.operations});
    const prompt=ctx.nodes.find(n=>n.agent?.displayOnly); prompt.text='Manually reused instruction';
    ctx.selectedNode=()=>prompt; ctx.smartLoopContext={};
    ctx.buildPromptRequest=node=>{assert.equal(node.text,'Manually reused instruction');throw Error('manual composer reached');};
    vm.runInContext(production('runGeneration'),ctx);
    await assert.rejects(ctx.runGeneration(),/manual composer reached/);
    assert.equal(run.operations[0].prompt,'Frozen initial instruction');
});

test('creative confirmation drives real host with deleted attachments and strictly saves independent result before acknowledgment', async () => {
    const ctx=setup(), run=creativeRun(ctx);
    run.plan_id='plan'; run.version=1; ctx.nodes=[];
    const plan={...copy(run),id:'plan',version:1,status:'proposed'};
    const record={id:ids.conversationId,updated_at:1,plans:[plan],messages:[],runs:[],draft:'',reference_node_ids:[],
        chat_provider:'',chat_model:'',image_provider:'',image_model:'',video_provider:'',video_model:''};
    let executed=0;
    const saveFetch=ctx.fetch;
    ctx.fetch=async (url, options={})=>{
        if(options.method==='PUT') return saveFetch(url,options);
        const body=options.body && JSON.parse(options.body);
        if(url.endsWith('/confirm')) {record.runs=[run];plan.status='confirmed';}
        if(url.endsWith('/execute')) {
            executed++;
            assert.equal(ctx.calls.at(-1).body.nodes.length,2);
            run.steps[0]={operation_id:'g1',status:'generated',result:{media:[{url:'/durable.png',kind:'image'}]}};
        }
        if(body?.type==='operation_completed') {
            assert.deepEqual(body.created_node_ids,['agent_run-a_g1']);
            const saved=ctx.calls.at(-1).body;
            assert.equal(saved.nodes.find(n=>n.id==='agent_run-a_g1').images[0].url,'/durable.png');
            assert.deepEqual(saved.connections,[]);
            run.steps[0].status='completed';run.status='completed';
        }
        record.updated_at++;
        return {ok:true,json:async()=>copy({conversation:record,run})};
    };
    const c=agent.createController({canvasId:ctx.canvasId,host:ctx.host,fetch:ctx.fetch});
    await c.selectConversation(ids.conversationId);
    await c.confirmPlan('plan',1); await c.confirmPlan('plan',1);
    assert.equal(executed,1); assert.equal(run.status,'completed');
    assert.equal(ctx.nodes.find(n=>n.agent?.displayOnly).text,'Frozen initial instruction');
    assert.deepEqual(ctx.nodes.map(n=>n.id),['agent_run-a_g1_prompt','agent_run-a_g1']);
});

function graph(ctx, operations=prefix, scope=ids) {
    assert.equal(typeof ctx.host.applyAgentCanvasOperations, 'function', 'host exposes production graph bridge');
    return ctx.host.applyAgentCanvasOperations({...scope,operations});
}

test('graph is deterministic across repeats, reload and two tabs; originals and selection stay untouched', () => {
    const a=setup(), b=setup(), original=copy(a.nodes[0]);
    const first=graph(a), other=graph(b);
    assert.deepEqual(copy(first),copy(other));
    assert.deepEqual(copy(first.operation_nodes),{p1:'agent_run-a_p1',m1:'agent_run-a_m1'});
    assert.equal(a.nodes.find(n => n.id === first.operation_nodes.p1).text,'confirmed cat');
    graph(a); assert.deepEqual(copy(a.nodes[0]),original);
    a.applyMergedServerCanvas({...b.canvas,nodes:copy(b.nodes)});
    assert.equal(a.nodes.length,3); assert.equal(a.canvas.connections.length,2);
    assert.deepEqual(copy(a.nodes[0].images),original.images); assert.equal(a.nodes[0].x,original.x); assert.equal(a.nodes[0].y,original.y);
    assert.equal(a.selectedId,'ref'); assert.deepEqual(copy(a.selectedImage),{nodeId:'ref',index:0});
    const reloaded=setup(); reloaded.nodes=copy(a.nodes);
    assert.deepEqual(copy(graph(reloaded,[]).operation_nodes),copy(first.operation_nodes));
    graph(a,prefix,{...ids,runId:'run-b'});
    assert.equal(a.nodes.length,5);
    for(const n of a.nodes.slice(1)) assert.ok(n.x >= 406, 'new nodes placed right of reference');
    const positions=a.nodes.slice(1).map(n => `${n.x},${n.y}`);
    assert.equal(new Set(positions).size,4,'collision search separates nodes');
});

test('missing dependencies and attempts to modify originals reject the entire batch before mutation', () => {
    const ctx=setup();
    assert.equal(typeof ctx.host.applyAgentCanvasOperations,'function');
    for(const operations of [ [...prefix,{id:'bad',op:'connect',from:'missing',to:'m1'}],
        [...prefix,{id:'bad',op:'connect',from:'p1',to:'ref'}], [{id:'bad',op:'delete',node:'ref'}]]) {
        assert.throws(() => graph(ctx,operations));
        assert.equal(ctx.nodes.length,1); assert.equal(ctx.canvas.connections.length,0);
    }
});

test('generation saves first and uses only captured run/step IDs while selection and settings change', async () => {
    const ctx=setup(); graph(ctx);
    const gate=deferred(), entered=deferred();
    const reply={conversation:{id:ids.conversationId},run:{id:ids.runId},step:{operation_id:'g1',status:'running',result:null}};
    ctx.fetch=async (url,options) => {
        ctx.calls.push({url,method:options.method,body:JSON.parse(options.body)});
        if(options.method === 'PUT') { entered.resolve(); await gate.promise; return {ok:true,json:async () => ({canvas:{updated_at:2}})}; }
        return {ok:true,json:async () => reply};
    };
    const running=ctx.host.runCanvasAgentGeneration({...ids,operation:generate,clientId:'tab-one'});
    await entered.promise;
    ctx.selectedId='elsewhere'; ctx.selectedImage={nodeId:'elsewhere',index:3}; ctx.settings={provider_id:'changed',ratio:'1:1'};
    gate.resolve(); assert.equal(await running,reply);
    assert.equal(ctx.calls.length,2); assert.equal(ctx.calls[0].method,'PUT');
    assert.equal(ctx.calls[1].url,'/api/canvases/canvas%2Fone/agent/conversations/conversation%2Fa/runs/run-a/steps/g1/execute');
    assert.deepEqual(ctx.calls[1].body,{client_id:'tab-one',resume_only:false});
    const output=ctx.nodes.find(n => n.id === 'agent_run-a_g1');
    assert.ok(ctx.calls[0].body.nodes.some(n => n.id === output.id));
    assert.equal(output.pending,2); assert.ok(output.w > output.h);
    assert.equal(output.runSettings.provider_id,'confirmed'); assert.equal(output.runSettings.model,'image-model');
    assert.equal(output.runPrompt,'confirmed cat');
    assert.equal(ctx.selectedId,'elsewhere'); assert.equal(ctx.selectedImage.index,3);
    const resume=await ctx.host.resumeCanvasAgentGeneration({...ids,operation:generate,clientId:'tab-one'});
    assert.equal(resume,reply); assert.deepEqual(ctx.calls.at(-1).body,{client_id:'tab-one',resume_only:true});
    assert.equal(ctx.host.clientId,'tab-one');
});

test('finalization resolves merged live node, retains current selection and maps generated output downstream', async () => {
    const ctx=setup(); graph(ctx);
    await ctx.host.runCanvasAgentGeneration({...ids,operation:generate});
    const old=ctx.nodes.find(n => n.id === 'agent_run-a_g1');
    ctx.nodes=copy(ctx.nodes); ctx.selectedId=''; ctx.selectedImage={nodeId:'ref',index:0};
    const result={media:[{url:'/result.png',kind:'image',name:'cat',width:2048,height:1152,raw:{secret:'never'}}]};
    const mapped=ctx.host.applyAgentGenerationResult({...ids,operation:generate,result});
    const live=ctx.nodes.find(n => n.id === mapped.operation_nodes.g1);
    assert.equal(old.images.length,0); assert.equal(live.images[0].url,'/result.png');
    assert.equal(live.images[0].width,2048); assert.equal(live.images[0].raw,undefined); assert.equal(live.pending,0);
    assert.equal(ctx.selectedId,''); assert.deepEqual(copy(ctx.selectedImage),{nodeId:'ref',index:0});
    ctx.host.applyAgentGenerationResult({...ids,operation:generate,result});
    graph(ctx,[{id:'m2',op:'create_media',kind:'video',reference_node_ids:['g1']}]);
    assert.ok(ctx.canvas.connections.some(c => c.from === live.id && c.to === 'agent_run-a_m2'));
    assert.equal(ctx.nodes.filter(n => n.id === live.id).length,1);
});

test('failed strict save forbids generation submission; manual save catches visibly and queue recovers', async () => {
    const ctx=setup(); graph(ctx);
    ctx.fetch=async (url,options) => { ctx.calls.push(options.method); return {ok:false,status:500,json:async () => ({detail:'disk full'})}; };
    await assert.rejects(ctx.host.runCanvasAgentGeneration({...ids,operation:generate}),/disk full|保存/);
    assert.deepEqual(ctx.calls,['PUT']);
    await ctx.saveCanvas(); assert.equal(ctx.toasts.length,1);
    ctx.fetch=async () => ({ok:true,json:async () => ({canvas:{updated_at:3}})});
    await ctx.host.ensureSaved(); assert.equal(ctx.canvas.updated_at,3);
});

test('shared save queue snapshots at execution and retries merged 409 before acknowledging either run', async () => {
    const ctx=setup(); graph(ctx);
    const gate=deferred(), entered=deferred(); let active=0,maxActive=0;
    const remote={title:'Canvas',updated_at:7,nodes:[{id:'remote',type:'smart-prompt',text:'remote'}],connections:[]};
    ctx.fetch=async (url,options) => {
        active++; maxActive=Math.max(maxActive,active); const body=JSON.parse(options.body); ctx.calls.push(body);
        if(ctx.calls.length===1) { entered.resolve(); await gate.promise; active--; return {ok:false,status:409,json:async () => ({detail:{canvas:remote}})}; }
        active--; return {ok:true,json:async () => ({canvas:{updated_at:8}})};
    };
    const first=ctx.host.ensureSaved(); await entered.promise;
    graph(ctx,prefix,{...ids,runId:'run-b'}); const second=ctx.saveCanvas();
    gate.resolve(); await Promise.all([first,second]);
    assert.equal(maxActive,1); assert.equal(ctx.calls.length,3);
    assert.equal(ctx.calls[1].base_updated_at,7);
    assert.deepEqual(ctx.calls[2].nodes.map(n => n.id).sort(),['ref','remote','agent_run-a_p1','agent_run-a_m1','agent_run-b_p1','agent_run-b_m1'].sort());
    assert.equal(ctx.calls[2].connections.length,4);
});

test('strict save rejects references edited during PUT and bounds repeated conflicts', async () => {
    const ctx=setup();
    ctx.fetch=async () => { ctx.nodes[0].images[0].url='/edited.png'; return {ok:true,json:async () => ({canvas:{updated_at:2,nodes:copy(ctx.nodes)}})}; };
    await assert.rejects(ctx.host.ensureSaved(['ref']),/变化|保存/);
    let attempts=0;
    ctx.fetch=async () => { attempts++; return {ok:false,status:409,json:async () => ({detail:{updated_at:attempts+2}})}; };
    await assert.rejects(ctx.host.ensureSaved()); assert.ok(attempts>=2 && attempts<=4);
});

test('ownership survives autosave but clone, workflow export and import strip it and busy markers', () => {
    const ctx=setup(); const node=ctx.nodes[0];
    node.agent={canvasId:ctx.canvasId,...ids,operationId:'g1',role:'output',status:'paused'};
    node.pending=2; node.running=true; node.autodlTaskId='paid-task'; ctx.canvas.nodes=ctx.nodes;
    assert.deepEqual(copy(ctx.canvasForStorage().nodes[0].agent),copy(node.agent));
    for(const clean of [ctx.serializableSmartNode(node),ctx.cloneSmartNode(node)]) {
        assert.equal(clean.agent,undefined); assert.equal(clean.running,false); assert.equal(clean.pending,0); assert.equal(clean.autodlTaskId,undefined);
    }
    ctx.insertSmartWorkflowIntoCanvas({nodes:[node],connections:[]}); assert.equal(ctx.nodes[1].agent,undefined);
    assert.equal(node.agent.status,'paused');
});

test('manual generation guard precedes engine dispatch and run-state hook only updates owned markers', async () => {
    const ctx=setup(); graph(ctx);
    vm.runInContext(production('runGeneration'),ctx);
    const node=ctx.nodes.find(n => n.id === 'agent_run-a_m1'); node.type='smart-minimax'; node.pending=2;
    ctx.selectedNode=() => node; let dispatched=0; ctx.runMinimaxNode=() => { dispatched++; };
    for(const status of ['running','paused','unknown',undefined]) { node.agent.status=status; await ctx.runGeneration(); }
    assert.equal(dispatched,0); assert.equal(ctx.toasts.length,4);
    ctx.host.setAgentRunState({...ids,status:'paused'}); assert.equal(node.pending,2);
    assert.equal(ctx.nodes[0].agent,undefined);
    for(const status of ['completed','failed']) {
        ctx.host.setAgentRunState({...ids,status}); assert.equal(node.agent.runId,ids.runId); await ctx.runGeneration();
    }
    assert.equal(dispatched,2); assert.equal(node.agent,undefined,'explicit manual takeover releases failed ownership');
});

test('two tabs create the same pending output and video metadata uses confirmed composer fields', async () => {
    const a=setup(), b=setup();
    const operation={...generate,settings:{provider:'video-provider',model:'video-model',count:1,aspect_ratio:'9:16',resolution:'720p',size:'720x1280',duration:6}};
    for(const ctx of [a,b]) {
        graph(ctx,[{id:'m1',op:'create_media',kind:'video',reference_node_ids:['ref']}]);
        await ctx.host.runCanvasAgentGeneration({...ids,operation});
    }
    a.applyMergedServerCanvas({...b.canvas,nodes:copy(b.nodes)});
    const outputs=a.nodes.filter(n => n.agent?.role === 'output');
    assert.equal(outputs.length,1); assert.equal(outputs[0].id,'agent_run-a_g1');
    assert.equal(a.canvas.connections.length,2);
    assert.equal(outputs[0].runSettings.videoProvider,'video-provider');
    assert.equal(outputs[0].runSettings.videoModel,'video-model');
    assert.equal(outputs[0].runSettings.videoAspect,'9:16');
    assert.equal(outputs[0].runSettings.videoDuration,6); assert.ok(outputs[0].h > outputs[0].w);
});

test('resume rejection does not create output, submit a ready step or retry as a new generation', async () => {
    const ctx=setup(); const before=copy(ctx.nodes);
    ctx.fetch=async (url,options) => { ctx.calls.push({url,body:JSON.parse(options.body)}); return {ok:false,status:409,json:async () => ({detail:'step is ready; resume cannot submit'})}; };
    await assert.rejects(ctx.host.resumeCanvasAgentGeneration({...ids,operation:generate}),/resume cannot submit/);
    assert.equal(ctx.calls.length,1); assert.deepEqual(ctx.calls[0].body,{client_id:'tab-one',resume_only:true});
    assert.deepEqual(copy(ctx.nodes),before);
});

test('waiting for execute response does not hold the canvas save queue', async () => {
    const ctx=setup(); graph(ctx); const gate=deferred(),entered=deferred();
    ctx.fetch=async (url,options) => {
        if(options.method === 'POST') { entered.resolve(); await gate.promise; return {ok:true,json:async () => ({step:{status:'unknown'}})}; }
        return {ok:true,json:async () => ({canvas:{updated_at:2}})};
    };
    const running=ctx.host.runCanvasAgentGeneration({...ids,operation:generate}); await entered.promise;
    await ctx.host.ensureSaved(); gate.resolve(); assert.equal((await running).step.status,'unknown');
});

test('unreadable PUT acknowledgment cannot allow a paid submission', async () => {
    const ctx=setup(); graph(ctx);
    ctx.fetch=async (url,options) => { ctx.calls.push(options.method); return {ok:true,json:async () => { throw new Error('response disconnected'); }}; };
    await assert.rejects(ctx.host.runCanvasAgentGeneration({...ids,operation:generate}));
    assert.deepEqual(ctx.calls,['PUT']);
});

test('reload preserves pending Agent outputs in running, paused and unknown states', async () => {
    const ctx=setup(); graph(ctx); await ctx.host.runCanvasAgentGeneration({...ids,operation:generate});
    vm.runInContext(production('loadCanvas'),ctx);
    Object.assign(ctx,{
        rememberCanvasListProject:() => {}, migrateSmartGroupImageMembers:() => {}, hideCompletedRunTimers:() => false,
        cleanupDetachedRunInputRefs:() => false, safeScale:x => x, normalizeSmartVideoModeSettings:() => {},
        loadRecentSmartSettings:() => {}, updateProviderModels:() => {}, applyViewport:() => {}, startCanvasMetaPoll:() => {},
        document:{getElementById:() => ({textContent:''})}
    });
    for(const status of ['running','paused','unknown']) {
        const stored=copy({...ctx.canvas,nodes:ctx.nodes});
        const output=stored.nodes.find(n => n.id === 'agent_run-a_g1'); output.pending=2; output.running=true; output.agent.status=status;
        ctx.fetch=async () => ({ok:true,json:async () => ({canvas:stored})});
        await ctx.loadCanvas();
        assert.equal(ctx.toasts.length,0,'load executes without swallowed errors');
        assert.equal(ctx.nodes.find(n => n.id === output.id).pending,2);
        assert.equal(ctx.nodes.find(n => n.id === output.id).agent.status,status);
    }
});

test('known failed output can be manually retried without retaining the Agent pending lock', async () => {
    const ctx=setup(); graph(ctx); await ctx.host.runCanvasAgentGeneration({...ids,operation:generate});
    vm.runInContext(production('runGeneration'),ctx);
    const node=ctx.nodes.find(n => n.id === 'agent_run-a_g1');
    ctx.host.setAgentRunState({...ids,status:'failed'});
    Object.assign(ctx,{selectedNode:() => node,buildPromptRequest:() => ({prompt:'',refs:[]}),
        smartLoopContext:null,smartSettingsForNode:() => ({}),smartRunNeedsPrompt:() => true});
    await ctx.runGeneration();
    assert.equal(node.pending,0); assert.equal(ctx.toasts.at(-1),'smart.toastNeedPrompt');
    node.pending=3; node.running=true;
    await ctx.runGeneration(); assert.equal(node.pending,3,'a subsequent manual run retains its own busy lock');
});

test('alphanumeric operation IDs that match object property names still map to deterministic nodes', () => {
    const ctx=setup();
    const mapped=graph(ctx,[{id:'constructor',op:'create_prompt',text:'valid ID'},
        {id:'toString',op:'create_media',kind:'image',reference_node_ids:['constructor']}]);
    assert.equal(mapped.operation_nodes.constructor,'agent_run-a_constructor');
    assert.equal(mapped.operation_nodes.toString,'agent_run-a_toString');
    assert.ok(ctx.canvas.connections.some(c => c.from === 'agent_run-a_constructor' && c.to === 'agent_run-a_toString'));
});

test('confirmed image metadata is consumable by the current manual composer and size resolver', async () => {
    const ctx=setup(); graph(ctx); await ctx.host.runCanvasAgentGeneration({...ids,operation:generate});
    const saved=ctx.nodes.find(n => n.id === 'agent_run-a_g1').runSettings;
    assert.equal(ctx.apiImageSize(saved.ratio,saved.resolution,saved.customRatio,saved.customSize),'2048x1152');
    assert.equal(saved.provider_id,'confirmed'); assert.equal(saved.model,'image-model');
});

test('same-run same-node 409 preserves pending Agent input and completed status while merging remote layout', async () => {
    const ctx=setup(); graph(ctx,prefix.slice(0,2)); await ctx.host.ensureSaved();
    const remote=copy({...ctx.canvas,nodes:ctx.nodes,updated_at:7});
    remote.nodes.find(n => n.id === 'agent_run-a_m1').x=900;
    graph(ctx,[prefix[2]]); ctx.host.setAgentRunState({...ids,status:'completed'});
    ctx.calls=[];
    ctx.fetch=async (url,options) => {
        const body=JSON.parse(options.body); ctx.calls.push(body);
        return ctx.calls.length===1
            ? {ok:false,status:409,json:async () => ({detail:{canvas:remote}})}
            : {ok:true,json:async () => ({canvas:{...body,updated_at:8}})};
    };
    await ctx.host.ensureSaved();
    assert.equal(ctx.calls.length,2);
    const saved=ctx.calls[1].nodes.find(n => n.id === 'agent_run-a_m1');
    assert.deepEqual(saved.inputNodeIds,['ref','agent_run-a_p1']);
    assert.equal(saved.agent.status,'completed'); assert.equal(saved.x,900,'remote layout still merges');
    assert.deepEqual(saved.inputNodeIds,ctx.calls[1].connections.filter(c => c.to===saved.id && c.kind==='input').map(c => c.from));
    const live=ctx.nodes.find(n => n.id === saved.id);
    assert.deepEqual(copy(live.inputNodeIds),saved.inputNodeIds); assert.equal(live.agent.status,'completed');
    vm.runInContext(production('runGeneration'),ctx);
    let built=false;
    Object.assign(ctx,{selectedNode:() => live,buildPromptRequest:() => { built=true; return {prompt:'',refs:[]}; },
        smartLoopContext:null,smartSettingsForNode:() => ({}),smartRunNeedsPrompt:() => true});
    await ctx.runGeneration(); assert.equal(built,true,'completed source must no longer be blocked by Agent guard');
});

test('acknowledged local Agent status does not override later remote status; failed saves retain pending status', async () => {
    const ctx=setup(); graph(ctx,prefix.slice(0,2)); await ctx.host.ensureSaved();
    const remote=copy({...ctx.canvas,nodes:ctx.nodes,updated_at:7});
    ctx.host.setAgentRunState({...ids,status:'paused'});
    let calls=0;
    ctx.fetch=async () => ++calls===1
        ? {ok:false,status:409,json:async () => ({detail:{canvas:remote}})}
        : {ok:false,status:500,json:async () => ({detail:'disk full'})};
    await assert.rejects(ctx.host.ensureSaved(),/disk full/);
    assert.equal(ctx.nodes.find(n => n.id==='agent_run-a_m1').agent.status,'paused');
    calls=0;
    ctx.fetch=async () => ++calls===1
        ? {ok:false,status:409,json:async () => ({detail:{canvas:remote}})}
        : {ok:true,json:async () => ({canvas:{updated_at:8}})};
    await ctx.host.ensureSaved();
    const completed=copy(remote);
    completed.nodes.filter(n => n.agent).forEach(n => { n.agent.status='completed'; });
    ctx.applyMergedServerCanvas(completed);
    assert.equal(ctx.nodes.find(n => n.id==='agent_run-a_m1').agent.status,'completed','persisted paused state is no longer a local pending edit');
});

test('Agent status changed during PUT remains pending through the next same-node conflict', async () => {
    const ctx=setup(); graph(ctx,prefix.slice(0,2)); await ctx.host.ensureSaved();
    ctx.host.setAgentRunState({...ids,status:'paused'});
    const gate=deferred(),entered=deferred(); let remote;
    ctx.fetch=async (url,options) => {
        remote={...JSON.parse(options.body),updated_at:7}; entered.resolve(); await gate.promise;
        return {ok:true,json:async () => ({canvas:remote})};
    };
    const saving=ctx.host.ensureSaved(); await entered.promise;
    ctx.host.setAgentRunState({...ids,status:'completed'}); gate.resolve(); await saving;
    let calls=0;
    ctx.fetch=async () => ++calls===1
        ? {ok:false,status:409,json:async () => ({detail:{canvas:remote}})}
        : {ok:true,json:async () => ({canvas:{updated_at:8}})};
    await ctx.host.ensureSaved();
    assert.equal(ctx.nodes.find(n => n.id==='agent_run-a_m1').agent.status,'completed');
});

for(const newPrompt of [false,true]) test(`generated-only media reference retains frozen prompt fallback${newPrompt ? ' while explicit new prompt takes precedence' : ''}`, async () => {
    const ctx=setup(); graph(ctx,[{...prefix[0],text:'Frozen first prompt'},...prefix.slice(1)]);
    await ctx.host.runCanvasAgentGeneration({...ids,operation:generate});
    ctx.host.applyAgentGenerationResult({...ids,operation:generate,result:{media:[{url:'/first.png',kind:'image',name:'first'}]}});
    // Changes to old prompt/media must never be imported through the generated output.
    ctx.nodes.find(n => n.id==='agent_run-a_p1').text='edited original prompt';
    const operations=[{id:'m2',op:'create_media',kind:'image',reference_node_ids:['g1']}];
    if(newPrompt) operations.push({id:'p2',op:'create_prompt',text:'New second prompt'},{id:'c2',op:'connect',from:'p2',to:'m2'});
    graph(ctx,operations);
    await ctx.host.runCanvasAgentGeneration({...ids,operation:{...generate,id:'g2',node:'m2'}});
    const output=ctx.nodes.find(n => n.id==='agent_run-a_g2');
    assert.equal(output.runModelPrompt,newPrompt ? 'New second prompt' : 'Frozen first prompt');
    assert.equal(output.runPrompt,output.runModelPrompt);
    assert.deepEqual(copy(output.runInputRefs),[{url:'/first.png',name:'first',kind:'image',nodeId:'agent_run-a_g1',imageIndex:0}]);
});

test('reusing a manual generated output preserves its own media and frozen prompt boundary', async () => {
    const ctx=setup();
    ctx.nodes.push({id:'manual-result',type:'smart-image',images:[{url:'/manual.png',kind:'image'}],
        inputNodeIds:['ref'],runSettings:{engine:'api'},runModelPrompt:'Manual frozen prompt',x:400,y:0});
    graph(ctx,[{id:'m2',op:'create_media',kind:'image',reference_node_ids:['manual-result']}]);
    await ctx.host.runCanvasAgentGeneration({...ids,operation:{...generate,id:'g2',node:'m2'}});
    const result=ctx.nodes.find(n=>n.id==='agent_run-a_g2');
    assert.equal(result.runModelPrompt,'Manual frozen prompt');
    assert.deepEqual(copy(result.runInputRefs).map(m=>m.url),['/manual.png']);
});

for(const missingOutput of [false,true]) test(`accepted result survives deleted source with ${missingOutput ? 'missing' : 'existing'} output through controller and real bridge`, async () => {
    const ctx=setup(); graph(ctx); graph(ctx,[generate]);
    const operations=copy([...prefix,generate]);
    const result={media:[{url:'/output/paid.png',kind:'image',name:'paid',width:800,height:450}]};
    const run={id:ids.runId,canvas_id:ctx.canvasId,conversation_id:ids.conversationId,client_id:ctx.smartClientId,
        plan_id:'plan',version:1,status:'paused',stop_requested:true,operations,
        reference_snapshot:[{id:'ref',type:'smart-image',text:'',images:[{url:'/cat.png',kind:'image',name:''}],input_node_ids:[]}],
        steps:operations.map(op=>({operation_id:op.id,status:op.op==='generate' ? 'generated' : 'completed',
            result:op.op==='generate' ? result : null,provider_task_id:'',created_node_ids:[],error:''}))};
    const record={id:ids.conversationId,canvas_id:ctx.canvasId,title:'Recovery',updated_at:1,plans:[],messages:[],runs:[run],
        draft:'',reference_node_ids:[],chat_provider:'',chat_model:'',image_provider:'',image_model:'',video_provider:'',video_model:''};
    // The source and its ancestors were completed and then deleted by the user.
    // A surviving output already has the correct frozen metadata.
    ctx.nodes=ctx.nodes.filter(n=>n.id==='agent_run-a_g1' && !missingOutput);
    ctx.nodes.push({id:'kept',type:'smart-image',x:0,y:0,images:[]});
    ctx.canvas.connections=[]; ctx.selectedId='kept'; ctx.selectedImage=null;
    const acknowledgements=[];
    const controller=agent.createController({canvasId:ctx.canvasId,host:ctx.host,fetch:async (url,options={})=>{
        const body=options.body && JSON.parse(options.body);
        assert.ok(!url.endsWith('/execute'),'accepted output recovery must never submit or query generation');
        if(body?.type==='resume') { run.status='running'; run.stop_requested=false; record.updated_at++; }
        if(body?.type==='operation_completed') {
            const saved=ctx.calls.at(-1);
            assert.equal(saved.method,'PUT');
            assert.equal(saved.body.nodes.find(n=>n.id==='agent_run-a_g1').images[0].url,'/output/paid.png');
            acknowledgements.push(body); run.steps.at(-1).status='completed'; run.status='completed'; record.updated_at++;
        }
        return {ok:true,json:async()=>copy({conversation:record,run})};
    }});
    await controller.selectConversation(ids.conversationId);
    await controller.resumeRun(ids.runId,ids.conversationId);
    assert.equal(acknowledgements.length,1);
    assert.deepEqual(acknowledgements[0].created_node_ids,['agent_run-a_g1']);
    assert.deepEqual(ctx.nodes.map(n=>n.id).sort(),['agent_run-a_g1','kept']);
    const output=ctx.nodes.find(n=>n.id==='agent_run-a_g1');
    assert.equal(output.images[0].url,'/output/paid.png');
    assert.equal(output.runModelPrompt,'confirmed cat');
    assert.equal(output.runSettings.provider_id,'confirmed');
    assert.equal(output.runSettings.customSize,'2048x1152');
    assert.deepEqual(copy(output.runInputRefs).map(m=>m.url),['/cat.png']);
    assert.equal(ctx.canvas.connections.length,0,'no links to deleted nodes are restored');
    assert.equal(ctx.selectedId,'kept');
    assert.equal(controller.getSnapshot().conversation.runs[0].status,'completed');
});

for(const explicitPrompt of [false,true]) test(`missing accepted output restores frozen generated-media boundary${explicitPrompt ? ' with explicit new prompt' : ' with prior prompt fallback'}`, () => {
    const ctx=setup();
    const second={...generate,id:'g2',node:'m2',settings:{provider:'autodl',model:'h3',count:1,
        aspect_ratio:'9:16',resolution:'480p',size:'9:16',duration:6}};
    const operations=[...prefix,generate,{id:'m2',op:'create_media',kind:'video',reference_node_ids:['g1']}];
    if(explicitPrompt) operations.push({id:'p2',op:'create_prompt',text:'Frozen second prompt'},{id:'c2',op:'connect',from:'p2',to:'m2'});
    operations.push(second);
    const result={media:[{url:'/output/partial.mp4',kind:'video',name:'partial'}]};
    const run={id:ids.runId,canvas_id:ctx.canvasId,conversation_id:ids.conversationId,client_id:ctx.smartClientId,status:'unknown',
        operations,reference_snapshot:[{id:'ref',type:'smart-image',text:'',images:[{url:'/cat.png',kind:'image',name:''}],input_node_ids:[]}],
        steps:operations.map(op=>({operation_id:op.id,status:op.id==='g2' ? 'unknown' : 'completed',
            result:op.id==='g2' ? result : op.id==='g1' ? {media:[{url:'/first.png',kind:'image',name:'first'}]} : null}))};
    ctx.nodes[0].images=[{url:'/edited-original.png',kind:'image'}];
    const before=copy(ctx.nodes[0]);
    ctx.host.applyAgentGenerationResult({...ids,operation:second,result,run});
    const output=ctx.nodes.find(n=>n.id==='agent_run-a_g2');
    assert.equal(output.runModelPrompt,explicitPrompt ? 'Frozen second prompt' : 'confirmed cat');
    assert.deepEqual(copy(output.runInputRefs).map(m=>m.url),['/first.png']);
    assert.equal(output.runSettings.videoProvider,'autodl');
    assert.equal(output.runSettings.videoDuration,6);
    assert.equal(output.agent.status,'unknown');
    assert.equal(output.images[0].url,'/output/partial.mp4');
    assert.deepEqual(copy(ctx.nodes[0]),before);
    assert.deepEqual(ctx.nodes.map(n=>n.id),['ref','agent_run-a_g2']);
    assert.equal(ctx.canvas.connections.length,0);
    assert.equal(ctx.calls.length,0,'recovery itself never submits, queries, or saves');
});

test('normal and restored output metadata match backend frozen ordered request after live edits', () => {
    const ctx=setup();
    ctx.nodes.push({id:'z',type:'smart-prompt',text:'First instruction',images:[]},
        {id:'a',type:'smart-prompt',text:'Second instruction',images:[]});
    ctx.nodes[0].inputNodeIds=['z','a'];
    const operations=[{id:'m1',op:'create_media',kind:'image',reference_node_ids:['ref']},generate];
    const run={id:ids.runId,canvas_id:ctx.canvasId,conversation_id:ids.conversationId,client_id:ctx.smartClientId,
        operations:copy(operations),reference_snapshot:[
            {id:'a',type:'smart-prompt',text:'Second instruction',images:[],input_node_ids:[]},
            {id:'ref',type:'smart-image',text:'',images:[{url:'/cat.png',kind:'image',name:''}],input_node_ids:['a','z']},
            {id:'z',type:'smart-prompt',text:'First instruction',images:[],input_node_ids:[]}],
        steps:operations.map(op=>({operation_id:op.id,status:op.op==='generate'?'generated':'completed',
            result:op.op==='generate'?{media:[{url:'/paid.png',kind:'image',name:''}]}:null}))};
    const {execFileSync}=require('node:child_process');
    const request=JSON.parse(execFileSync(path.join(__dirname,'../python/python.exe'),['-c',
        'import json,sys,os; sys.path.insert(0,os.getcwd()); import main; from canvas_agent import generation_inputs; r=json.load(sys.stdin); k,p,m=generation_inputs(r,"g1"); main.provider_env_key_value=lambda _:"fixture-key"; print(json.dumps({"context":[k,p,m],"request":main.prepare_canvas_agent_generation(k,r["operations"][-1]["settings"],p,m).model_dump()}))'],
        {cwd:path.join(__dirname,'..'),input:JSON.stringify(run),encoding:'utf8'}));
    const backend=request.context;
    assert.equal(backend[1],'Second instruction\nFirst instruction');
    ctx.host.applyAgentCanvasOperations({...ids,run,operations});
    let output=ctx.nodes.find(n=>n.id==='agent_run-a_g1');
    assert.equal(output.runModelPrompt,backend[1]);
    assert.deepEqual(copy(output.runInputRefs).map(({url,kind,name})=>({url,kind,name})),backend[2]);
    output.runModelPrompt='old incorrect placeholder metadata';
    ctx.host.applyAgentGenerationResult({...ids,run,operation:generate,result:run.steps[1].result});
    assert.equal(output.runModelPrompt,backend[1],'surviving placeholders also receive frozen accepted metadata');
    ctx.nodes.find(n=>n.id==='a').text='Edited after confirmation';
    ctx.nodes[0].inputNodeIds=['z'];
    ctx.nodes=ctx.nodes.filter(n=>n.id!==output.id);
    ctx.host.applyAgentCanvasOperations({...ids,run,operations:[generate]});
    output=ctx.nodes.find(n=>n.id==='agent_run-a_g1');
    assert.equal(output.runModelPrompt,backend[1]);
    ctx.nodes=ctx.nodes.filter(n=>n.id!==output.id);
    ctx.host.applyAgentGenerationResult({...ids,run,operation:generate,result:run.steps[1].result});
    output=ctx.nodes.find(n=>n.id==='agent_run-a_g1');
    assert.equal(output.runModelPrompt,backend[1]);
    assert.deepEqual(copy(output.runInputRefs).map(({url,kind,name})=>({url,kind,name})),backend[2]);
    assert.equal(output.runSettings.provider_id,generate.settings.provider);
    assert.equal(output.runSettings.customSize,generate.settings.size);
    assert.equal(output.runModelPrompt,request.request.prompt);
    assert.equal(output.runSettings.provider_id,request.request.provider_id);
    assert.equal(output.runSettings.model,request.request.model);
    assert.equal(output.runSettings.quality,request.request.quality);
    assert.equal(output.runSettings.resolution,request.request.resolution);
    assert.equal(output.runSettings.customRatio,request.request.aspect_ratio);
    assert.equal(output.runSettings.customSize,request.request.size);
    assert.deepEqual(copy(output.runInputRefs).map(({url,kind,name})=>({url,kind,name})),
        request.request.reference_images.map(({url,kind,name})=>({url,kind,name})));
});

test('controller and production host persist deterministic deleted dependency failure before execute', async () => {
    const ctx=setup(); graph(ctx,prefix.slice(0,2));
    const operations=copy([...prefix,generate]);
    const run={id:ids.runId,canvas_id:ctx.canvasId,conversation_id:ids.conversationId,client_id:ctx.smartClientId,
        status:'paused',operations,reference_snapshot:[],steps:operations.map((op,i)=>({operation_id:op.id,
            status:i<2?'completed':'ready',result:null,error:''}))};
    const record={id:ids.conversationId,updated_at:1,plans:[],messages:[],runs:[run],draft:'',reference_node_ids:[],
        chat_provider:'',chat_model:'',image_provider:'',image_model:'',video_provider:'',video_model:''};
    ctx.nodes=ctx.nodes.filter(n=>n.id!=='agent_run-a_p1');
    const events=[];
    const c=agent.createController({canvasId:ctx.canvasId,host:ctx.host,fetch:async (url,options={})=>{
        assert.ok(!url.endsWith('/execute'),'no provider execute for missing graph dependency');
        const body=options.body && JSON.parse(options.body);
        if(body?.type==='resume') { run.status='running'; record.updated_at++; }
        if(body?.type==='dependency_failed') {
            assert.equal(ctx.calls.at(-1).method,'PUT','deletion is saved before server validation');
            assert.ok(!ctx.calls.at(-1).body.nodes.some(n=>n.id==='agent_run-a_p1'));
            events.push(body); run.status='failed'; run.steps[2].status='failed'; record.updated_at++;
        }
        return {ok:true,json:async()=>copy({conversation:record,run})};
    }});
    await c.selectConversation(ids.conversationId);
    await assert.rejects(c.resumeRun(ids.runId),/依赖节点不存在/);
    assert.equal(events.length,1); assert.equal(events[0].operation_id,'c1');
    assert.equal(c.getSnapshot().conversation.runs[0].status,'failed');
    assert.ok(!ctx.nodes.some(n=>n.id==='agent_run-a_p1'));
    assert.equal(run.steps[0].status,'completed');
});

test('unknown error result-only recovery with production host survives failed save and fresh owner', async () => {
    const ctx=setup(), operations=copy([...prefix,generate,{id:'next',op:'create_prompt',text:'must not run'}]);
    const result={media:[{url:'/partial.png',kind:'image',name:'partial'}]};
    const run={id:ids.runId,canvas_id:ctx.canvasId,conversation_id:ids.conversationId,client_id:'old-client',
        status:'unknown',operations,reference_snapshot:[{id:'ref',text:'',type:'smart-image',images:[{url:'/cat.png',kind:'image',name:''}],input_node_ids:[]}],
        steps:operations.map(op=>({operation_id:op.id,status:op.id==='g1'?'unknown':op.id==='next'?'ready':'completed',
            result:op.id==='g1'?result:null,error:op.id==='g1'?'提交状态未知；已返回素材已保留。':''}))};
    const record={id:ids.conversationId,updated_at:1,plans:[],messages:[],runs:[run],draft:'',reference_node_ids:[],
        chat_provider:'',chat_model:'',image_provider:'',image_model:'',video_provider:'',video_model:''};
    const events=[];
    const fetch=async (url,options={})=>{
        assert.ok(!url.endsWith('/execute'));
        const body=options.body && JSON.parse(options.body);
        if(body) {
            assert.equal(body.type,'restore_results','no resume, completion, or dependency event');
            events.push(body); run.client_id=body.client_id; record.updated_at++;
        }
        return {ok:true,json:async()=>copy({conversation:record,run})};
    };
    let c=agent.createController({canvasId:ctx.canvasId,host:ctx.host,fetch});
    await c.selectConversation(ids.conversationId);
    assert.equal(ctx.nodes.length,1);
    const goodFetch=ctx.fetch;
    ctx.fetch=async()=>({ok:false,status:500,json:async()=>({detail:'disk full'})});
    await assert.rejects(c.restoreResults(ids.runId),/disk full/);
    ctx.nodes=ctx.nodes.filter(n=>n.id!=='agent_run-a_g1');
    ctx.fetch=goodFetch;
    ctx.smartClientId='fresh-client'; ctx.host.clientId='fresh-client';
    c=agent.createController({canvasId:ctx.canvasId,host:ctx.host,fetch});
    await c.selectConversation(ids.conversationId);
    await c.restoreResults(ids.runId); await c.restoreResults(ids.runId);
    assert.equal(events.length,3); assert.equal(run.client_id,'fresh-client');
    assert.equal(run.status,'unknown'); assert.equal(run.steps[3].status,'unknown');
    assert.equal(run.steps[4].status,'ready');
    const output=ctx.nodes.find(n=>n.id==='agent_run-a_g1');
    assert.equal(output.images.length,1); assert.equal(output.runModelPrompt,'confirmed cat');
    assert.deepEqual(ctx.nodes.map(n=>n.id),['ref','agent_run-a_g1']);
    assert.ok(ctx.calls.every(c=>c.method==='PUT'));
    assert.equal(ctx.calls.at(-1).body.nodes.find(n=>n.id===output.id).images[0].url,'/partial.png');
});

test('output restoration refuses completed deletions, unaccepted steps and mismatched ownership', () => {
    const result={media:[{url:'/output/paid.png',kind:'image',name:'paid'}]};
    const base={id:ids.runId,canvas_id:'canvas/one',conversation_id:ids.conversationId,client_id:'tab-one',status:'paused',
        operations:copy([...prefix,generate]),reference_snapshot:[{id:'ref',type:'smart-image',text:'',images:[],input_node_ids:[]}],
        steps:[{operation_id:'g1',status:'generated',result}]};
    for(const change of [run=>{run.steps[0].status='completed';},run=>{run.steps[0].status='ready';},
        run=>{run.canvas_id='other';},run=>{run.conversation_id='other';},run=>{run.client_id='other';}]){
        const ctx=setup(),run=copy(base); change(run);
        assert.throws(()=>ctx.host.applyAgentGenerationResult({...ids,operation:generate,result,run}));
        assert.equal(ctx.nodes.length,1); assert.equal(ctx.calls.length,0);
    }
    const ctx=setup();
    ctx.nodes.push({id:'agent_run-a_g1',type:'smart-image',images:[],agent:{canvasId:ctx.canvasId,conversationId:'other',runId:'other',operationId:'g1',role:'output'}});
    const before=copy(ctx.nodes);
    assert.throws(()=>ctx.host.applyAgentGenerationResult({...ids,operation:generate,result,run:base}));
    assert.deepEqual(copy(ctx.nodes),before);
});
