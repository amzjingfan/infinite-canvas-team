const {test}=require('node:test');
const assert=require('node:assert/strict');
const {setup}=require('./image_design_frontend_fixtures.cjs');

test('legacy recipe choices cannot leak through a loaded conversation into new requests',async()=>{
    const f=setup();
    Object.assign(f.stored,{case_input_mode:'single_case',recipe_id:'recipe_a',recipe_overrides:['background'],draft:'本次新产品'});
    await f.c.open();
    const record=f.c.getSnapshot().conversation;
    assert.equal(record.case_input_mode,'design_only');
    assert.equal(record.recipe_id,'');
    assert.deepEqual(record.recipe_overrides,[]);
    assert.equal(record.draft,'本次新产品');
    await f.c.sendMessage();
    const sent=f.calls.find(call=>call.url.endsWith('/messages')).body;
    assert.equal(sent.case_input_mode,'design_only');
    assert.equal(sent.recipe_id,'');
    assert.deepEqual(sent.recipe_overrides,[]);
    assert.equal(f.calls.filter(call=>call.url.includes('/recipes')).length,0);
});

test('stale preference writers cannot invalidate a fresh proposal or overwrite the draft and model',async()=>{
    const f=setup();
    f.stored.plans=[{id:'plan',version:1,status:'proposed',contract_version:3,case_input_mode:'design_only'}];
    await f.c.open();
    f.c.updatePreferences({draft:'只改文案',case_input_mode:'single_case',recipe_id:'recipe_a',recipe_overrides:['palette']});
    await f.c.savePreferences();
    const record=f.c.getSnapshot().conversation;
    assert.equal(record.plans[0].status,'proposed');
    assert.equal(record.draft,'只改文案');
    assert.equal(record.image_model,'image');
    assert.equal(record.case_input_mode,'design_only');
    assert.equal(record.recipe_id,'');
});
