const {test}=require('node:test');
const assert=require('node:assert/strict');
const {agent,document,flatten,plan,record,setup}=require('./image_design_frontend_fixtures.cjs');

test('v3 generation reads only frozen actual input and does not append research media',()=>{
    const p=plan();p.reference_snapshot[0].images=[{url:'/changed.png',kind:'image'}];
    p.image_workflow.cases[0].media.url='/poison.png';
    const result=agent.generationInputs(p,'image1');
    assert.deepEqual(result.media.map(m=>m.url),['/product.png']);
    assert.equal(result.prompt,'唯一冻结的完整实际提示词');
    result.media[0].url='/caller-mutation.png';
    assert.equal(p.operations[0].generation_inputs[0].url,'/product.png');
});

test('design card clearly separates research, actual input and nonblocking notes',()=>{
    const p=plan(),conversation={...record(),plans:[p]};
    const nodes=flatten(agent.renderMessage({role:'assistant',content:'已定稿',plan_id:'plan',plan_version:1},document,
        {conversation,controller:{confirmPlan(){}}}));
    const text=nodes.map(n=>n.textContent).join(' ');
    for(const label of ['设计决定','实际图片输入','研究参考','分区与引线','已定稿，含检查关注项','成图检查关注主体数量'])
        assert.ok(text.includes(label),label);
    assert.doesNotMatch(text,/OLD DIRECTION|主风格\/构图参考/);
    const actual=nodes.find(n=>n.className==='agent-generation-inputs');
    assert.ok(actual,'actual inputs must have their own visible group');
    assert.deepEqual(flatten(actual).filter(n=>n.tag==='img').map(n=>n.src),['/product.png']);
});

test('retired case settings are normalized while the draft and model persist',async()=>{
    const {c,calls}=setup();await c.open();
    assert.equal(c.getSnapshot().conversation.case_input_mode,'design_only');
    assert.deepEqual(c.getSnapshot().conversation.recipe_overrides,[]);
    c.updatePreferences({case_input_mode:'single_case',draft:'使用产品图生成海报'});
    await c.sendMessage();
    const sent=calls.find(call=>call.url.endsWith('/messages')).body;
    assert.equal(sent.case_input_mode,'design_only');assert.equal(sent.recipe_id,'');
    assert.deepEqual(sent.recipe_overrides,[]);
    assert.equal(sent.generation_defaults.image.model,'image');
});

test('retired case strategy cannot invalidate the current proposal',async()=>{
    const f=setup(),p=plan();f.stored.plans=[p];f.stored.runs=[];await f.c.open();
    f.c.updatePreferences({case_input_mode:'single_case'});
    assert.equal(f.c.getSnapshot().conversation.plans[0].status,'proposed');
    assert.equal(f.c.getSnapshot().conversation.plans[0].case_input_mode,'design_only');
});
