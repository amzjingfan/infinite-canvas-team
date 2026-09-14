const test = require('node:test');
const assert = require('node:assert/strict');
const {slashQuery, filterSkills, insertSkill} = require('../static/js/skills.js');

test('slash starts only at a token boundary and ignores URLs, paths and completed tokens', () => {
    assert.deepEqual(slashQuery('/', 1), {start:0, end:1, query:''});
    assert.deepEqual(slashQuery('请用 /品牌', 6), {start:3, end:6, query:'品牌'});
    for(const text of ['https://site/path', 'C:/Users/name', '/folder/file', 'a/skill', '/skill done', '/skill ']) {
        assert.equal(slashQuery(text, text.length), null, text);
    }
});

test('candidate search includes only selected skills and matches names or descriptions', () => {
    const skills = [{id:'image', name:'Image Maker', description:'产品 图片', selected:true},
        {id:'notes', name:'Notes', description:'Product notes', selected:false},
        {id:'review', name:'Review', description:'Review product code', selected:true}];
    assert.deepEqual(filterSkills(skills, '').map(s=>s.id), ['image', 'review']);
    assert.deepEqual(filterSkills(skills, '图片').map(s=>s.id), ['image']);
    assert.deepEqual(filterSkills(skills, 'IMAGE').map(s=>s.id), ['image']);
    assert.deepEqual(filterSkills(skills, 'notes'), []);
});

test('selection replaces just the slash token without losing trailing draft or reference text', () => {
    const text = '@[商品](node:node1) /im 原有文字';
    const caret = text.indexOf(' 原有');
    assert.deepEqual(insertSkill(text, slashQuery(text, caret), 'image'), {
        text:'@[商品](node:node1) /image 原有文字', caret:25
    });
    assert.deepEqual(insertSkill('/im', slashQuery('/im', 3), 'image'), {text:'/image ', caret:7});
});
