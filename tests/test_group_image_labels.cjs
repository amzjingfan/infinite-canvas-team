const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/js/smart-canvas.js'),'utf8');
function context(){
    const ctx={imageForDisplay:x=>x,smartRecoverableImageTask:()=>null,
        MEDIA_GROUP_MAX_VISIBLE_ROWS:4,selectedImage:{},escapeHtml:String,escapeAttr:String,tr:String,
        mediaKindForItem:()=> 'image',thumbMediaHtml:x=>`<img src="${x.url}">`,
        singleMediaHtml:x=>`<img src="${x.url}">`};
    vm.createContext(ctx);
    for(const name of ['imageNameLabel','imageNameBadgeHtml','imageResolutionLabel','imageResolutionBadgeHtml','nodeBodyHtml']){
        vm.runInContext(source.match(new RegExp(`^function ${name}\\([^]*?^}`,'m'))[0],ctx);
    }
    return ctx;
}
test('multi-row group keeps each filename inside its own thumbnail, including after reordering',()=>{
    const ctx=context();
    const images=Array.from({length:10},(_,i)=>({url:`/image-${i}.png`,name:`original-${i}.png`}));
    for(const items of [images,[...images].reverse()]){
        const html=ctx.nodeBodyHtml({id:'group',images:items},{cols:3,rows:4,visibleRows:3,thumb:180});
        const cards=[...html.matchAll(/<div class="thumb-item[^]*?<\/div>/g)].map(m=>m[0]);
        assert.equal(cards.length,10);
        cards.forEach((card,i)=>{
            assert.ok(card.includes(`src="${items[i].url}"`));
            assert.ok(card.includes(`title="${items[i].name}"`));
            assert.doesNotMatch(card,/image-name-badge-outside|has-outside-image-name/,
                'Outside labels are clipped on the first row and appear below the preceding row');
        });
    }
});
test('standalone image preserves its existing outside filename layout',()=>{
    const html=context().nodeBodyHtml({images:[{url:'/single.png',name:'single.png'}]},{width:180,height:180});
    assert.match(html,/image-name-badge-outside/);
});
