// Trusted host lifecycle only. Never include model code, asset paths or errors.
export const RENDER_STAGE_PREFIX='OCV_RENDER_STAGE:';
export function createRenderProgress({clock=()=>performance.now(),write=line=>console.log(line)}={}){
  const began=clock();
  const seconds=ms=>Math.max(0,Math.round(ms)/1000);
  const emit=(stage,status,started)=>{
    const now=clock();
    write(RENDER_STAGE_PREFIX+JSON.stringify({stage,status,elapsed_seconds:seconds(now-began),
      ...(started===undefined?{}:{stage_seconds:seconds(now-started)})}));
  };
  return async(stage,operation)=>{
    const started=clock();emit(stage,'started');
    try{const result=await operation();emit(stage,'completed',started);return result;}
    catch(error){emit(stage,'failed',started);throw error;}
  };
}
