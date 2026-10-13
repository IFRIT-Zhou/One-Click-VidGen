import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../src/components/VideoStudio.vue',import.meta.url),'utf8');
const code=source.slice(source.indexOf('async function changeLanguageRoute('),source.indexOf('async function plan('));
function fixture(fail=false){
 const calls=[];let logins=0;
 const project={value:{id:'old-project',revision:5,creation_parameters:{use_cloud_image_pool:false}}};
 const context=vm.createContext({project,dirty:{value:false},save:async()=>{},run:async fn=>{try{await fn()}catch{}},requestCloudLogin:()=>{logins++},call:async(...args)=>{calls.push(args);if(fail)throw Object.assign(new Error('login'),{status:401});return {...project.value,revision:6,creation_parameters:{...project.value.creation_parameters,language_route:'cloud',dynamic_auto_advance:false}}}});
 vm.runInContext(code,context);return {context,calls,project,logins:()=>logins};
}
test('existing project selection persists route without starting generation',async()=>{
 const f=fixture(),event={target:{value:'cloud'}};await f.context.changeLanguageRoute(event);
 assert.equal(f.calls.length,1);assert.equal(f.calls[0][0],'/old-project/language-route');assert.equal(f.calls[0][2].revision,5);assert.equal(f.calls[0][2].route,'cloud');assert.equal(event.target.value,'cloud');assert.equal(f.project.value.creation_parameters.use_cloud_image_pool,false);
});
test('failed cloud login preserves previous project route and opens login',async()=>{
 const f=fixture(true),event={target:{value:'cloud'}};await f.context.changeLanguageRoute(event);
 assert.equal(f.logins(),1);assert.equal(f.project.value.revision,5);assert.equal(event.target.value,'project');
});
