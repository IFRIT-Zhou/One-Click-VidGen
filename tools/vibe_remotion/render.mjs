import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {loadAssets,proofFrames,RENDERER,validateInput} from './contract.mjs';
import {SHOTCRAFT_VERSION,SHOTCRAFT_RENDERER,validateShotcraftInput,shotcraftProofFrames,shotcraftItemFrames} from './lib/shotcraft-contract.mjs';
import {LAYERED_VERSION,LAYERED_RENDERER,validateLayeredInput,layeredProofFrames} from './lib/layered-contract.mjs';

const root=path.dirname(fileURLToPath(import.meta.url));
function argumentsOf(argv) {
  const out={};
  for(let i=0;i<argv.length;i++) {
    if(['--check','--stills-only','--still-only'].includes(argv[i])) out[argv[i]]=true;
    else if(['--input','--output','--browser','--concurrency'].includes(argv[i])) {
      if(!argv[i+1]||argv[i+1].startsWith('--')) throw new Error('Missing argument: '+argv[i]);
      out[argv[i]]=argv[++i];
    } else throw new Error('Unknown argument: '+argv[i]);
  }
  return out;
}
function sourceHash() {
  const hash=crypto.createHash('sha256');
  const walk=dir=>fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).forEach(e=> {
    const file=path.join(dir,e.name);if(e.isDirectory()) walk(file);else hash.update(fs.readFileSync(file));
  });
  walk(path.join(root,'src'));
  walk(path.join(root,'lib'));
  hash.update(fs.readFileSync(path.join(root,'timing.mjs')));
  hash.update(fs.readFileSync(path.join(root,'layout.mjs')));
  hash.update(fs.readFileSync(path.join(root,'palette.mjs')));
  hash.update(fs.readFileSync(path.join(root,'background.mjs')));
  hash.update(fs.readFileSync(path.join(root,'package-lock.json')));
  return hash.digest('hex').slice(0,20);
}

async function getBundle() {
  // Import on every preflight too: a stale cache must not hide a missing runtime.
  const {bundle}=await import('@remotion/bundler');
  const cache=path.join(root,'.cache');fs.mkdirSync(cache,{recursive:true});
  const bundlePath=path.join(cache,'bundle-v2-'+sourceHash());
  const complete=path.join(bundlePath,'.ocv-complete');
  if(fs.existsSync(complete)) return bundlePath;
  const temporary=path.join(cache,'.building-'+crypto.randomUUID());
  try {
    await bundle({entryPoint:path.join(root,'src','index.tsx'),outDir:temporary,rootDir:root,
      enableCaching:true,publicPath:'/',publicDir:null,logLevel:'error'});
    fs.writeFileSync(path.join(temporary,'.ocv-complete'),RENDERER);
    if(fs.existsSync(complete)) return bundlePath;
    // Old, incomplete bundles are not used or overwritten by a render job.
    if(fs.existsSync(bundlePath)) return temporary;
    try {fs.renameSync(temporary,bundlePath);return bundlePath;}
    catch(error) {
      if(fs.existsSync(complete))return bundlePath;
      // Windows scanners may briefly lock a newly built directory. Its own
      // completion marker is sufficient to use this invocation's immutable
      // bundle without deleting, overwriting or retrying another job's cache.
      if(['EPERM','EBUSY','EACCES'].includes(error.code)&&fs.existsSync(path.join(temporary,'.ocv-complete')))return temporary;
      throw error;
    }
  } finally {
    // Keep a fully built temporary bundle if an older incomplete cache occupies
    // its fingerprint; otherwise remove only this invocation's private temp.
    if(fs.existsSync(temporary)&&fs.existsSync(complete)) fs.rmSync(temporary,{recursive:true,force:true});
  }
}

async function main() {
  const args=argumentsOf(process.argv.slice(2));
  if(args['--still-only']&&args['--stills-only'])throw new Error('Choose one still rendering mode');
  const browserExecutable=args['--browser'];
  if(!browserExecutable||!path.isAbsolute(browserExecutable)||!fs.statSync(browserExecutable).isFile()) throw new Error('A pre-installed absolute --browser path is required; no automatic downloads');
  const concurrency=Number(args['--concurrency']??2);
  if(!Number.isInteger(concurrency)||concurrency<1||concurrency>2) throw new Error('Concurrency must be 1 or 2');
  const {openBrowser,renderMedia,renderStill,selectComposition,makeCancelSignal}=await import('@remotion/renderer');
  const browser=await openBrowser('chrome',{browserExecutable,chromeMode:'headless-shell',logLevel:'error',
    chromiumOptions:{disableWebSecurity:false,ignoreCertificateErrors:false}});
  const {cancelSignal,cancel}=makeCancelSignal();
  const abort=()=>cancel();
  process.once('SIGTERM',abort);process.once('SIGINT',abort);
  try {
    const serveUrl=await getBundle();
    if(args['--check']) {console.log(JSON.stringify({ready:true,renderer:RENDERER,renderers:[RENDERER,SHOTCRAFT_RENDERER,LAYERED_RENDERER],plan_versions:['ocv-motion-v1',SHOTCRAFT_VERSION,LAYERED_VERSION]}));return;}
    if(!args['--input']||!args['--output']) throw new Error('--input and --output are required');
    const inputPath=path.resolve(args['--input']);
    if(fs.statSync(inputPath).size>1024*1024) throw new Error('Input plan too large');
    const raw=JSON.parse(fs.readFileSync(inputPath,'utf8'));
    const shotcraft=raw?.plan?.version===SHOTCRAFT_VERSION;
    const layered=raw?.plan?.version===LAYERED_VERSION;
    const input=layered?validateLayeredInput(raw):shotcraft?validateShotcraftInput(raw):validateInput(raw);
    if(args['--still-only']) {
      if(!layered)throw new Error('Single poster still requires a layered plan');
      if(input.plan.elements.some(e=>e.at!==0||e.enter!==0||e.exit!==0))throw new Error('Poster still requires all layers visible without animation');
    }
    const output=path.resolve(args['--output']);
    if(!fs.existsSync(output)) fs.mkdirSync(output,{recursive:true});
    if(fs.readdirSync(output).length) throw new Error('Output must be an empty job directory');
    const props={...input,assets:loadAssets(input.assets)};
    const options={serveUrl,inputProps:props,browserExecutable,chromeMode:'headless-shell',puppeteerInstance:browser,
      logLevel:'error',timeoutInMilliseconds:30000,cancelSignal};
    const composition=await selectComposition({...options,id:'OCVVibe'});
    if(args['--still-only']) {
      const started=Date.now();
      // LayeredScene performs the same measured text overflow audit as video
      // renders. All layers are visible; no audio, stage frames or encoding.
      await renderStill({...options,composition,frame:0,imageFormat:'png',output:path.join(output,'poster.png')});
      const result={renderer:LAYERED_RENDERER,plan_version:LAYERED_VERSION,
        width:composition.width,height:composition.height,fps:input.fps,frames:1,
        composition_frames:composition.durationInFrames,still_only:true,
        checks:{layout:'passed'},render_seconds:Math.round((Date.now()-started)/10)/100};
      fs.writeFileSync(path.join(output,'metadata.json'),JSON.stringify(result,null,2));
      console.log(JSON.stringify(result));
      return;
    }
    const frames=layered?layeredProofFrames(input.plan,input.duration,input.fps):shotcraft?shotcraftProofFrames(input.plan,input.duration,input.fps):proofFrames(input.plan,input.duration,input.fps);
    const itemFrames=shotcraft?shotcraftItemFrames(input.plan,input.fps):[];
    const started=Date.now();
    const detailTimes={};
    // All stage finals are inspected before spending the full-frame encoding cost.
    // The React component raises OCV_LAYOUT_OVERFLOW for text exceeding its box.
    for(const shot of [...frames.stages,...itemFrames]) {
      await renderStill({...options,composition,frame:shot.frame,imageFormat:'png',output:path.join(output,shot.file)});
    }
    for(const [name,frame] of Object.entries(frames.details)) {
      await renderStill({...options,composition,frame,imageFormat:'png',output:path.join(output,`detail_${name}.png`)});
      detailTimes[name]=frame/input.fps;
    }
    if(!args['--stills-only']) await renderMedia({...options,composition,outputLocation:path.join(output,'silent.mp4'),
      codec:'h264',pixelFormat:'yuv420p',crf:18,x264Preset:'fast',muted:true,overwrite:false,concurrency,
      mediaCacheSizeInBytes:128*1024*1024,offthreadVideoCacheSizeInBytes:64*1024*1024});
    const result={renderer:layered?LAYERED_RENDERER:shotcraft?SHOTCRAFT_RENDERER:RENDERER,width:composition.width,height:composition.height,fps:input.fps,
      frames:composition.durationInFrames,duration_seconds:input.duration,detail_times:detailTimes,
      stage_frames:frames.stages,...(layered?{layer_frames:frames.stages,plan_version:LAYERED_VERSION}:shotcraft?{item_frames:itemFrames,plan_version:SHOTCRAFT_VERSION}:{}),checks:{layout:'passed',stage_count:frames.stages.length},
      render_seconds:Math.round((Date.now()-started)/10)/100,stills_only:!!args['--stills-only']};
    fs.writeFileSync(path.join(output,'metadata.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  } finally {
    process.removeListener('SIGTERM',abort);process.removeListener('SIGINT',abort);
    await browser.close({silent:true});
  }
}

main().catch(error=>{
  const text=error?.stack??error?.message??JSON.stringify(error);
  console.error(text);
  if(/OCV_LAYOUT_(?:OVERFLOW|OVERLAP):/.test(String(text))) console.log(JSON.stringify({error:String(text),kind:'validation'}));
  process.exitCode=1;
});
