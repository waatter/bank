'use strict';
/* Focus and inert state transitions in a controlled DOM model; not browser QA. */
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../assets/demo.js'),'utf8');
const modalSource=source.slice(source.indexOf('function modal('),source.indexOf('/* ------------------------------------------------------------------ 三扇窗'));
function setup(){
 const nodes=new Map(),listeners=new Map();let document;
 const make=name=>{if(!nodes.has(name))nodes.set(name,{name,hidden:true,inert:false,isConnected:true,value:'',focus(){document.activeElement=this}});return nodes.get(name)};
 const first=make('cancel'),last=make('confirm'),origin=make('origin');
 document={activeElement:origin,addEventListener:(k,fn)=>listeners.set(k,fn),removeEventListener:k=>listeners.delete(k)};
 const context=vm.createContext({document,$:s=>s==='[data-r="1"]'?last:make(s),$$:()=>[first,last],esc:v=>v,setTimeout:fn=>fn()});
 vm.runInContext(modalSource+'\nglobalThis.runModal=modal;',context);
 function key(name,shiftKey=false){let prevented=false;listeners.get('keydown')?.({key:name,shiftKey,preventDefault(){prevented=true}});return prevented}
 function click(accept){make('#modal').onclick({target:{closest:()=>({dataset:{r:accept?'1':'0'}})}})}
 return {context,make,document,first,last,origin,key,click,listeners};
}
test('opening a confirmation isolates background and focuses its confirm control',async()=>{
 const x=setup();x.make('#profileGate').inert=true;
 const pending=x.context.runModal({title:'检查',body:'合成数据'});
 assert.equal(x.make('.top').inert,true);assert.equal(x.make('.content').inert,true);assert.equal(x.document.activeElement,x.last);
 x.click(false);assert.equal(await pending,false);
 assert.equal(x.make('.top').inert,false);assert.equal(x.make('#profileGate').inert,true);assert.equal(x.document.activeElement,x.origin);
});
test('Tab and Shift+Tab remain inside the modal controls',async()=>{
 const x=setup(),pending=x.context.runModal({title:'检查',body:''});
 assert.equal(x.key('Tab'),true);assert.equal(x.document.activeElement,x.first);
 assert.equal(x.key('Tab',true),true);assert.equal(x.document.activeElement,x.last);
 x.click(true);assert.equal(await pending,true);
});
test('Escape cancels without a second resolve and removes keyboard handling',async()=>{
 const x=setup(),pending=x.context.runModal({title:'检查',body:''});
 assert.equal(x.key('Escape'),true);assert.equal(await pending,false);
 assert.equal(x.make('#modal').hidden,true);assert.equal(x.listeners.has('keydown'),false);assert.equal(x.make('#modal').onclick,null);
});
test('confirm restores background and safely ignores a removed focus origin',async()=>{
 const x=setup(),pending=x.context.runModal({title:'检查',body:''});x.origin.isConnected=false;
 x.click(true);assert.equal(await pending,true);assert.equal(x.make('.content').inert,false);
});
