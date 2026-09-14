const {test} = require('node:test');
const assert = require('node:assert/strict');
const agent = require('../static/js/smart-canvas-agent.js');

const doc = () => ({createElement(tag) { return {tag, children:[], textContent:'',
    append(...children) { this.children.push(...children); }, setAttribute() {}}; }});
const flatten = element => [element, ...element.children.flatMap(flatten)];
function fixture() {
    const context = {corpus_total:541, queries:['香水 perfume'], max_revisions:2,
        template:'产品摄影', direction:'琥珀色侧光', reference_instruction:'图1为用户身份；图2仅为风格案例。',
        identity_media:[{url:'/assets/product.png',kind:'image',name:'产品'}],
        cases:[{id:358,title:'案例标题<script>bad</script>',prompt:'Complete case prompt',purpose:'主风格/构图参考',
            source_url:'https://example.org/case358',media:{url:'/assets/skill-references/case.png',kind:'image'}}],
        candidates:[{id:358,title:'候选',observation:'瓶身居中、侧光、上方留白。'}],unavailable:[]};
    const plan = {id:'plan',version:1,summary:'海报',status:'proposed',reference_snapshot:[],image_workflow:context,
        operations:[{id:'p',op:'create_prompt',text:'原始提示词'},
            {id:'m',op:'create_media',kind:'image',reference_node_ids:[]},
            {id:'c',op:'connect',from:'p',to:'m'},
            {id:'g',op:'generate',node:'m',settings:{provider:'tugo',model:'gpt-image-2',count:1,
                size:'768x1024',aspect_ratio:'3:4',resolution:'1k',quality:'high'}}]};
    const conversation = {id:'conv',plans:[plan],runs:[]};
    const message = {role:'assistant',content:'研究完成',plan_id:'plan',plan_version:1};
    return {context,plan,conversation,message};
}

test('creative plan presents frozen asset task, attributed requirements and full copy without a graph', () => {
    const f=fixture();
    f.plan.mode='creation'; f.plan.preflight={status:'passed',model:'deepseek-fixture'};
    f.plan.operations=[{id:'poster',op:'generate',kind:'image',prompt:'Exact confirmed prompt including input roles',
        reference_node_ids:['product'],settings:f.plan.operations[3].settings,
        contract:{requirements:[{id:'subject',source:'user',text:'Keep real product appearance',evidence:'保留产品外观'},
            {id:'detail',source:'uncertain',text:'Small control markings unreadable',evidence:'product'}],
            copy:{exact_text:[{text:'Complete Copy',placement:'subtitle',source:'proposal'}],allow_additional_text:true,forbidden_text:['SALE']},
            reference_roles:{product:'identity'}}}];
    f.plan.reference_snapshot=[{id:'product',title:'Product',text:'',images:[{url:'/product.png',kind:'image'}]}];
    const nodes=flatten(agent.renderMessage(f.message,doc(),{conversation:f.conversation,controller:{confirmPlan(){}}}));
    const text=nodes.map(n=>n.textContent).join(' ');
    for(const fragment of ['独立创作','图片任务 poster','Exact confirmed prompt including input roles','用户明确要求','Complete Copy','允许补充文案','尚未确认'])
        assert.ok(text.includes(fragment),fragment);
    assert.doesNotMatch(text,/undefined|连接：|生成 .*→/);
    assert.ok(nodes.some(n=>n.tag==='img' && n.src==='/product.png'));
});

test('single version separates execution completion from unresolved issues and shows counts without comparison claims', () => {
    const f=fixture();
    f.conversation.runs=[{id:'run',plan_id:'plan',version:1,status:'completed',steps:[{operation_id:'g',status:'completed',
        result:{media:[{url:'/output/0.png',kind:'image'}],best_url:'/output/0.png',
            quality:{status:'needs_review',best_round:0,generation_count:1,edit_count:0,detail:'缺少可靠修图依据'},
            attempts:[{round:0,media:[{url:'/output/0.png'}],prompt:'Actual initial instruction',
                input_media:[{url:'/product.png',kind:'image'}],input_roles:[{url:'/product.png',purpose:'主体身份参考'}],edit_targets:[],
                review:{score:85,summary:'整体可用，控制标识待核验',checks:[{category:'identity',status:'uncertain',detail:'仍看不清控制文字',fix:'UNSAFE GUESS'}],issues:[
                    {id:'control',category:'identity',status:'uncertain',severity:'hard',location:'底部控制面板',
                        evidence:'字符过小，无法分辨',fix:'UNSAFE GUESS',preserve:['真实标识'],can_edit:false}]}}]}}]}];
    const text=flatten(agent.renderMessage(f.message,doc(),{conversation:f.conversation,controller:{}})).map(n=>n.textContent).join(' ');
    for(const fragment of ['执行完成','图片调用 1 次','修图 0 次','底部控制面板','字符过小','主体身份参考','Actual initial instruction'])
        assert.ok(text.includes(fragment),fragment);
    assert.doesNotMatch(text,/推荐|最佳|UNSAFE GUESS|检查通过/);
});

test('pending edit retains actual submitted instruction and exact targets even without a returned new version', () => {
    const f=fixture();
    f.conversation.runs=[{id:'run',plan_id:'plan',version:1,status:'unknown',steps:[{operation_id:'g',status:'unknown',
        result:{media:[{url:'/output/0.png',kind:'image'}],attempts:[],quality:{status:'editing',generation_count:2,edit_count:1,
            pending_revision:{round:1,prompt:'Submitted local title correction',input_media:[{url:'/output/0.png',kind:'image'}],
                input_roles:[{url:'/output/0.png',purpose:'待修版本'}],edit_targets:[{id:'title',location:'顶部标题',fix:'Correct exact title'}]}}}}]}];
    const text=flatten(agent.renderMessage(f.message,doc(),{conversation:f.conversation,controller:{}})).map(n=>n.textContent).join(' ');
    assert.ok(text.includes('Submitted local title correction'));
    assert.ok(text.includes('Correct exact title'));
    assert.ok(text.includes('尚未返回'));
});

test('enhanced plan displays actual research, frozen image inputs and maximum calls before confirmation', () => {
    const f = fixture();
    const nodes = flatten(agent.renderMessage(f.message,doc(),{conversation:f.conversation,controller:{confirmPlan(){}}}));
    const text = nodes.map(n=>n.textContent).join(' ');
    for(const item of ['541','香水 perfume','358','侧光','最多修图 2 次','最多图片调用 3 次','Complete case prompt'])
        assert.ok(text.includes(item), `missing confirmation evidence ${item}`);
    assert.ok(nodes.some(n=>n.tag==='img' && n.src==='/assets/skill-references/case.png'));
    assert.ok(nodes.some(n=>n.tag==='a' && n.href==='https://example.org/case358'));
    assert.ok(nodes.every(n=>n.innerHTML===undefined));
    const inputs = agent.generationInputs(f.plan,'g');
    assert.equal(inputs.prompt,'原始提示词\n\n图1为用户身份；图2仅为风格案例。');
    assert.deepEqual(inputs.media.map(m=>m.url),['/assets/product.png','/assets/skill-references/case.png']);
});

test('quality evidence names every retained version and highlights the best without claiming all passed', () => {
    const f = fixture();
    f.conversation.runs.push({id:'run',plan_id:'plan',version:1,status:'completed',steps:[{
        operation_id:'g',status:'completed',result:{media:[{url:'/output/0.png',kind:'image'},{url:'/output/1.png',kind:'image'}],
            best_url:'/output/0.png',quality:{status:'limit_reached',best_round:0,detail:'仍有文字问题'},
            attempts:[0,1].map(round=>({round,media:[{url:`/output/${round}.png`,kind:'image'}],prompt:`编辑提示词${round}`,
                review:{summary:'标题需要修正',score:80-round,checks:[{category:'text',status:'fail',detail:'顶部标题第二个字错误。',fix:'改为夏日香气'}]}}))}}]});
    const nodes = flatten(agent.renderMessage(f.message,doc(),{conversation:f.conversation,controller:{}}));
    const text = nodes.map(n=>n.textContent).join(' ');
    for(const item of ['达到修图上限','推荐版本 1','版本 2','顶部标题第二个字错误','编辑提示词1','仍有文字问题'])
        assert.ok(text.includes(item),item);
    assert.ok(!text.includes('检查通过'));
    assert.equal(nodes.filter(n=>n.tag==='img' && n.src.startsWith('/output/')).length,2);
});

test('failed review is visible and unsafe research links cannot become active elements', () => {
    const f=fixture();
    f.context.cases[0].source_url='javascript:alert(1)';
    f.context.cases[0].media.url='javascript:alert(2)';
    f.conversation.runs.push({id:'run',plan_id:'plan',version:1,status:'completed',steps:[{
        operation_id:'g',status:'completed',result:{media:[{url:'/output/0.png',kind:'image'}],
            quality:{status:'review_failed',detail:'检查未完成，未修图'},attempts:[]}}]});
    const nodes=flatten(agent.renderMessage(f.message,doc(),{conversation:f.conversation,controller:{}}));
    assert.ok(nodes.map(n=>n.textContent).join(' ').includes('检查未完成'));
    assert.ok(!nodes.some(n=>String(n.href||n.src||'').startsWith('javascript:')));
});

test('best version supplies its actual edit prompt and inputs rather than first generation metadata', () => {
    const f=fixture();
    assert.equal(typeof agent.generationResultContext,'function','best-version metadata must be resolved');
    const run={...f.plan,steps:[{operation_id:'g',result:{best_url:'/output/best.png',attempts:[
        {round:0,prompt:'最初的提示词',input_media:[],media:[{url:'/output/first.png'}]},
        {round:1,prompt:'只修改标题',input_media:[{url:'/output/first.png',kind:'image'}],media:[{url:'/output/best.png'}]}
    ]}}]};
    const result=agent.generationResultContext(run,'g');
    assert.equal(result.prompt,'只修改标题');
    assert.deepEqual(result.media,[{url:'/output/first.png',kind:'image'}]);
});
