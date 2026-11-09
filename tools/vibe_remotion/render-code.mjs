import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {loadAssets} from './contract.mjs';
import {compileProgram, validateProgram, evaluateProgram} from './lib/code-program.mjs';
import {CODE_RENDERER, CODE_VERSION, CODE_LOG_PREFIX, validateCodeInput, validateTree, treeManifest, validateFrameManifest} from './lib/code-render-contract.mjs';
import {createRenderProgress} from './lib/render-progress.mjs';
import {codePreviewPlan} from './lib/code-preview-plan.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
function argumentsOf(argv){
  const args={};
  for(let i=0;i<argv.length;i++){
    if(argv[i]==='--check')args.check=true;
    else if(['--input','--output','--browser','--concurrency'].includes(argv[i])){
      if(!argv[i+1]||argv[i+1].startsWith('--'))throw new Error(`Missing ${argv[i]} value`);
      args[argv[i].slice(2)]=argv[++i];
    }else throw new Error(`Unknown argument ${argv[i]}`);
  }
  return args;
}
function sourceHash(){
  const hash=crypto.createHash('sha256');
  for(const relative of ['src/CodeScene.tsx','src/code-index.tsx','lib/code-program.mjs','lib/code-animation-progress.mjs','lib/code-render-contract.mjs','lib/code-text-visibility.mjs','lib/code-geometry-visibility.mjs','lib/layered-text-overlap.mjs','package-lock.json'])hash.update(fs.readFileSync(path.join(root,relative)));
  return hash.digest('hex').slice(0,20);
}
async function getBundle(){
  const {bundle}=await import('@remotion/bundler');
  const cache=path.join(root,'.cache');fs.mkdirSync(cache,{recursive:true});
  const bundlePath=path.join(cache,'bundle-code-'+sourceHash());
  if(fs.existsSync(path.join(bundlePath,'.ocv-complete')))return bundlePath;
  const temporary=path.join(cache,'.building-code-'+crypto.randomUUID());
  // Only this fixed, reviewed entry point is bundled. Model source is parsed
  // into data by compileProgram; it is never written into a JS module.
  await bundle({entryPoint:path.join(root,'src','code-index.tsx'),outDir:temporary,rootDir:root,
    enableCaching:true,publicPath:'/',publicDir:null,logLevel:'error'});
  fs.writeFileSync(path.join(temporary,'.ocv-complete'),CODE_RENDERER);
  if(fs.existsSync(bundlePath))return temporary;
  try{fs.renameSync(temporary,bundlePath);return bundlePath;}
  catch(error){if(['EPERM','EBUSY','EACCES'].includes(error.code))return temporary;throw error;}
}
function json(file,value){fs.writeFileSync(file,JSON.stringify(value,null,2));}
function auditFrames(poster,frames){
  const diagnostics=[];
  const posterTexts=new Map((poster.texts??[]).map(t=>[t.id,t]));
  const posterAssets=new Set((poster.assets??[]).map(a=>a.id));
  for(const frame of frames){
    for(const text of frame.texts??[]){
      const original=posterTexts.get(text.id);
      if(!original||text.text!==original.text)diagnostics.push({kind:'frame_text_changed',target:text.id,frame:frame.frame,
        explanation:'Text identity/content differs between the static poster and the actual animation frame'});
    }
    for(const asset of frame.assets??[])if(!posterAssets.has(asset.id))diagnostics.push({kind:'frame_asset_changed',target:asset.id,frame:frame.frame,explanation:'An animation-only asset is missing from the reviewed poster'});
    if(frame.id==='hold'){
      for(const [id,text] of posterTexts){
        const actual=(frame.texts??[]).find(t=>t.id===id);
        if(!actual?.visible||actual.opacity<.95)diagnostics.push({kind:'hold_text_missing',target:id,frame:frame.frame,explanation:'Poster text is not fully visible at the final reading hold'});
      }
      for(const id of posterAssets){
        const actual=(frame.assets??[]).find(a=>a.id===id);
        if(!actual?.visible||actual.opacity<.95)diagnostics.push({kind:'hold_asset_missing',target:id,frame:frame.frame,explanation:'Poster asset is not fully visible at the final reading hold'});
      }
    }
  }
  return diagnostics;
}

async function main(){
  const stage=createRenderProgress();
  const args=argumentsOf(process.argv.slice(2));
  if(!args.browser||!path.isAbsolute(args.browser)||!fs.statSync(args.browser).isFile())throw new Error('A pre-installed absolute --browser path is required; no automatic downloads');
  const concurrency=Number(args.concurrency??2);
  if(!Number.isInteger(concurrency)||concurrency<1||concurrency>2)throw new Error('Concurrency must be 1 or 2');
  const ts=(await import('typescript')).default;
  const {openBrowser,renderStill,renderMedia,selectComposition,makeCancelSignal}=await import('@remotion/renderer');
  let input,output,program,referenceManifest,previewPlan;
  if(!args.check){
    if(!args.input||!args.output)throw new Error('--input and --output are required');
    const inputPath=path.resolve(args.input);
    if(fs.statSync(inputPath).size>1024*1024)throw new Error('Input exceeds the 1 MiB budget');
    input=validateCodeInput(JSON.parse(fs.readFileSync(inputPath,'utf8')));
    previewPlan=codePreviewPlan(input);
    if(input.mode!=='measure'){
      program=input.code!==undefined?compileProgram(input.code,ts):input.program;
      validateProgram(program);
      const ctx={frame:0,fps:input.fps,width:input.canvas[0],height:input.canvas[1],duration:input.duration,poster:true,measurements:input.measurements,geometry:input.geometry};
      const posterTree=validateTree(evaluateProgram(program,ctx),input.assets,input.geometry,input.duration);
      referenceManifest=treeManifest(posterTree);
      // Full proof checks real frames; a quick layout pass only checks the poster.
      // A later full preview/video always rechecks the animation path.
      for(const frame of previewPlan.stage_frames){
        const tree=validateTree(evaluateProgram(program,{...ctx,poster:false,frame:frame.frame}),input.assets,input.geometry,input.duration);
        validateFrameManifest(tree,referenceManifest);
      }
    }
    output=path.resolve(args.output);
    if(!fs.existsSync(output))fs.mkdirSync(output,{recursive:true});
    if(fs.readdirSync(output).length)throw new Error('Output must be an empty job directory');
    if(program)json(path.join(output,'program.json'),program);
    if(referenceManifest)json(path.join(output,'reference_manifest.json'),referenceManifest);
    if(input.code!==undefined)fs.writeFileSync(path.join(output,'rendered_code.txt'),input.code);
  }else{
    const test=compileProgram('function Scene(ctx) { return <Text id="check">Ready</Text>; }',ts);
    validateProgram(test);validateTree(evaluateProgram(test,{frame:0,fps:30,width:640,height:360,duration:1,poster:true,measurements:{},geometry:{}}),{}, {},1);
  }
  const browser=await stage('browser_launch',()=>openBrowser('chrome',{browserExecutable:args.browser,chromeMode:'headless-shell',logLevel:'error',
    chromiumOptions:{disableWebSecurity:false,ignoreCertificateErrors:false}}));
  const {cancelSignal,cancel}=makeCancelSignal();const abort=()=>cancel();
  process.once('SIGTERM',abort);process.once('SIGINT',abort);
  try{
    const serveUrl=await stage('bundle',getBundle);
    if(args.check)return {ready:true,renderer:CODE_RENDERER,renderers:[CODE_RENDERER],plan_versions:[CODE_VERSION]};
    const started=Date.now();
    const {code, ...withoutSource}=input;
    const props={...withoutSource,program,assets:loadAssets(input.assets),reference_manifest:referenceManifest,collect_measurements:true};
    let lastMeasurement;
    const onBrowserLog=log=>{
      const at=log.text.indexOf(CODE_LOG_PREFIX);
      if(at>=0){try{lastMeasurement=JSON.parse(log.text.slice(at+CODE_LOG_PREFIX.length));}catch{throw new Error('OCV_CODE_MEASUREMENT: Invalid browser measurement payload');}}
    };
    const options={serveUrl,inputProps:props,browserExecutable:args.browser,chromeMode:'headless-shell',puppeteerInstance:browser,
      logLevel:'error',timeoutInMilliseconds:30000,cancelSignal,onBrowserLog};
    const composition=await stage('composition',()=>selectComposition({...options,id:'OCVCode'}));
    const still=async(file,frame,poster=false)=>{
      lastMeasurement=undefined;
      const frameProps={...props,poster};
      await stage(path.basename(file,'.png'),()=>renderStill({...options,inputProps:frameProps,composition:{...composition,props:frameProps},frame,imageFormat:'png',output:path.join(output,file)}));
      if(!lastMeasurement)throw new Error('OCV_CODE_MEASUREMENT: Browser produced no measurable evidence');
      if(input.mode!=='measure'&&lastMeasurement.poster!==poster)throw new Error('OCV_CODE_MEASUREMENT: Poster/frame identity mismatch');
      return lastMeasurement;
    };
    let measurements,frames=[];
    if(input.mode==='measure')measurements=await still('measurement_sheet.png',0);
    else{
      measurements=await still('poster.png',0,true);
      json(path.join(output,'measurements.json'),measurements);
      for(const item of previewPlan.stage_frames){
        frames.push({...await still(item.file,item.frame),...item});
        json(path.join(output,'measurements.json'),{...measurements,frames});
      }
      measurements={...measurements,frames,diagnostics:[...measurements.diagnostics,...auditFrames(measurements,frames)]};
      if(input.mode==='video'){
        const videoProps={...props,poster:false,collect_measurements:false};
        await stage('video',()=>renderMedia({...options,inputProps:videoProps,composition:{...composition,props:videoProps},outputLocation:path.join(output,'silent.mp4'),codec:'h264',pixelFormat:'yuv420p',crf:18,
          x264Preset:'fast',muted:true,overwrite:false,concurrency,mediaCacheSizeInBytes:128*1024*1024,offthreadVideoCacheSizeInBytes:64*1024*1024}));
      }
    }
    json(path.join(output,'measurements.json'),measurements);
    const count=(measurements.diagnostics??[]).length+frames.reduce((n,f)=>n+(f.diagnostics??[]).length,0);
    const metadata={renderer:CODE_RENDERER,plan_version:CODE_VERSION,mode:input.mode,preview_level:previewPlan.preview_level,proof_complete:previewPlan.proof_complete,width:composition.width,height:composition.height,fps:input.fps,
      frames:composition.durationInFrames,duration_seconds:input.duration,stage_frames:frames.map(({id,file,frame,time})=>({id,file,frame,time})),
      checks:{layout:count?'review_required':'no_detected_issues',animation:previewPlan.animation_check,diagnostic_count:count,visual_review_required:true},render_seconds:Math.round((Date.now()-started)/10)/100};
    json(path.join(output,'metadata.json'),metadata);return metadata;
  }finally{process.removeListener('SIGTERM',abort);process.removeListener('SIGINT',abort);await stage('browser_close',()=>browser.close({silent:true}));}
}
main().then(result=>console.log(JSON.stringify(result))).catch(error=>{const message=String(error?.stack??error?.message??JSON.stringify(error));console.error(message);console.log(JSON.stringify({error:message,kind:/OCV_CODE_CONTRACT:/.test(message)?'validation':'code_render_failed',renderer:CODE_RENDERER}));process.exitCode=1;});
