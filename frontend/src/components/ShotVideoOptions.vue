<script setup>
import {computed,ref,watch,onUnmounted} from 'vue'
import {requestJSON} from '../api'
const props=defineProps({modelValue:{type:Object,required:true},profiles:{type:Array,default:()=>[]},disabled:Boolean,loading:Boolean,apiReady:Boolean,apiResolution:{type:String,default:'720p'},ratio:{type:String,default:'16:9'},seconds:{type:Number,default:10},imageSrc:{type:String,default:''},dirty:Boolean,saved:Boolean,saveDisabled:Boolean})
const emit=defineEmits(['update:modelValue','refresh','save'])
const local=computed(()=>props.modelValue.backend==='comfyui')
const profile=computed(()=>props.profiles.find(item=>item.id===props.modelValue.profile_id))
const sourceSize=ref(null),lockRatio=ref(true)
const aspect=computed(()=>sourceSize.value?sourceSize.value[0]/sourceSize.value[1]:props.ratio==='9:16'?9/16:16/9)
watch(()=>props.imageSrc,(src,old,onCleanup)=>{
 sourceSize.value=null
 if(!src)return
 const img=new Image();let active=true
 img.onload=()=>{if(active&&img.naturalWidth&&img.naturalHeight)sourceSize.value=[img.naturalWidth,img.naturalHeight]}
 img.src=src
 onCleanup(()=>{active=false;img.onload=null})
},{immediate:true})
function chooseResolution(value){
 if(value!=='custom'){update({resolution:value});return}
 const width=Number(props.modelValue.width)||Number(dimensions.value.split(' × ')[0])||1280
 update({resolution:'custom',width,height:Math.max(32,Math.min(8192,Math.round(width/aspect.value)))})
}
function changeDimension(key,event){
 const value=Math.round(Number(event.target.value))
 if(!Number.isFinite(value)||value<32||value>8192){event.target.value=props.modelValue[key];return}
 const patch={[key]:value}
 if(lockRatio.value){
  const other=key==='width'?'height':'width'
  patch[other]=Math.round(key==='width'?value/aspect.value:value*aspect.value)
  if(patch[other]<32||patch[other]>8192){event.target.value=props.modelValue[key];return}
 }
 update(patch)
}
function toggleRatio(event){lockRatio.value=event.target.checked;if(lockRatio.value)changeDimension('width',{target:{value:props.modelValue.width}})}
const estimate=ref(null),estimateError=ref(''),checking=ref(false)
let timer,sequence=0
async function refreshEstimate(){
 if(!local.value||!profile.value)return
 const ticket=++sequence
 checking.value=true;estimateError.value=''
 try{
  const query=new URLSearchParams({profile_id:profile.value.id,ratio:props.ratio,resolution:props.modelValue.resolution||'',seconds:String(props.seconds)})
  if(props.modelValue.resolution==='custom'){query.set('width',props.modelValue.width);query.set('height',props.modelValue.height)}
  const result=await requestJSON('/api/comfyui/resource-estimate?'+query)
  if(ticket===sequence)estimate.value=result
 }catch(error){if(ticket===sequence){estimate.value=null;estimateError.value=error.message}}
 finally{if(ticket===sequence)checking.value=false}
}
watch(()=>[props.modelValue.backend,props.modelValue.profile_id,profile.value?.id,props.modelValue.resolution,props.modelValue.width,props.modelValue.height,props.ratio,props.seconds],()=>{
 clearTimeout(timer);sequence++;estimate.value=null;estimateError.value=''
 if(local.value&&profile.value)timer=setTimeout(refreshEstimate,220)
},{immediate:true})
onUnmounted(()=>{clearTimeout(timer);sequence++})
function update(patch){emit('update:modelValue',{...props.modelValue,...patch})}
function backend(value){update({backend:value,resolution:value==='api'&&['1080p','custom'].includes(props.modelValue.resolution)?'720p':props.modelValue.resolution,profile_id:props.modelValue.profile_id||props.profiles[0]?.id||''})}
const dimensions=computed(()=>{
 if(local.value&&props.modelValue.resolution==='custom')return [props.modelValue.width,props.modelValue.height].join(' × ')
 const preset=props.modelValue.resolution||(local.value?profile.value?.resolution_preset:props.apiResolution)
 let pair=({'480p':[854,480],'720p':[1280,720],'1080p':[1920,1080]})[preset]
 if(!pair&&local.value&&profile.value)pair=[profile.value.default_width,profile.value.default_height]
 if(!pair?.every(Number))return ''
 const [long,short]=[Math.max(...pair),Math.min(...pair)]
 return (props.ratio==='9:16'?[short,long]:[long,short]).join(' × ')
})
</script>

<template>
 <section class="shot-video-options" aria-label="本镜生成设置">
  <div class="options-heading"><strong>本镜生成设置</strong><span role="status">{{dirty?'有未保存配置':saved?'已保存 · 下次生成使用':'沿用项目或上次生成配置'}}</span><button type="button" class="refresh-options" :disabled="disabled||saveDisabled||(!dirty&&saved)" @click="emit('save')">保存配置</button></div>
  <div class="options-fields">
   <label><span>生成方式</span><select :value="modelValue.backend" :disabled="disabled" @change="backend($event.target.value)"><option value="comfyui">本地 ComfyUI</option><option value="api">视频 API</option></select></label>
   <label v-if="local" class="workflow-field"><span>工作流</span><select :value="modelValue.profile_id" :disabled="disabled||loading" @change="update({profile_id:$event.target.value,resolution:''})"><option value="">{{loading?'正在读取…':'请选择工作流'}}</option><option v-for="item in profiles" :key="item.id" :value="item.id">{{item.name}}</option></select></label>
   <label><span>分辨率</span><select :value="modelValue.resolution" :disabled="disabled" @change="chooseResolution($event.target.value)"><option value="">{{local?'跟随工作流':'跟随接口设置'}}</option><option value="480p">480P · 轻量</option><option value="720p">720P · 标准</option><option v-if="local" value="1080p">1080P · 高清</option><option v-if="local" value="custom">自定义尺寸</option></select></label>
   <button v-if="local" type="button" class="refresh-options" :disabled="loading||disabled" title="刷新已保存的工作流" @click="emit('refresh')">刷新</button>
  </div>
  <div v-if="local&&modelValue.resolution==='custom'" class="custom-size">
   <label>宽度 · px<input type="number" min="32" max="8192" step="1" :value="modelValue.width" :disabled="disabled" @change="changeDimension('width',$event)"></label>
   <span class="size-cross">×</span>
   <label>高度 · px<input type="number" min="32" max="8192" step="1" :value="modelValue.height" :disabled="disabled" @change="changeDimension('height',$event)"></label>
   <label class="ratio-lock"><input type="checkbox" :checked="lockRatio" :disabled="disabled" @change="toggleRatio">锁定分镜图比例</label>
   <small>{{sourceSize?'依据分镜图 '+sourceSize.join(' × '):'图片比例读取中或不可用，暂用项目比例 '+ratio}}；修改任一边后自动计算另一边，取整到像素。</small>
  </div>
  <div class="options-footer"><label v-if="local" class="h3-toggle"><input type="checkbox" :checked="modelValue.h3_prompt_agent" :disabled="disabled" @change="update({h3_prompt_agent:$event.target.checked})">H3 提示词转换</label><span v-if="dimensions">{{dimensions}} · {{ratio==='9:16'?'竖屏':'横屏'}}</span><span v-if="!local">{{apiReady?'使用已保存的视频 API，生成可能计费':'请先在「接口与服务」配置视频 API'}}</span></div>
  <div v-if="local&&profile" class="resource-estimate" :class="estimate?.risk">
   <div class="resource-title"><strong>本镜资源预估</strong><button type="button" :disabled="checking" @click="refreshEstimate">刷新余量</button></div>
   <template v-if="estimate"><span>显存预计 {{estimate.vram_estimate_gb.join('–')}} GB · 当前空闲 {{estimate.vram_free_gb??'未知'}} GB</span><span>内存预计 {{estimate.ram_estimate_gb.join('–')}} GB · 当前可用 {{estimate.ram_available_gb??'未知'}} GB</span><strong>{{({low:'余量较充足',caution:'接近资源上限',high:'溢出风险较高',unknown:'无法判断风险'})[estimate.risk]}}</strong></template>
   <span v-else>{{checking?'正在读取本机资源…':estimateError||'等待工作流参数…'}}</span>
   <small>按 {{seconds}} 秒、{{estimate?.frames||'约'}} 帧粗估；实际峰值受模型、节点和同时运行的程序影响。此预估不能保证不会溢出。</small>
  </div>
  <p>保存只影响本镜的下次生成，现有视频保持不变；单镜和批量生成都会使用各镜头配置。点击生成也会同时保存当前配置。{{disabled?'当前任务已提交或排队，结束后可调整。':''}}</p>
 </section>
</template>

<style scoped>
.custom-size{display:flex;align-items:end;gap:10px;flex-wrap:wrap;padding:12px;margin-top:12px;background:rgba(0,0,0,.12);border-radius:8px}.custom-size>label:not(.ratio-lock){display:grid;gap:6px;flex:1 1 130px;font-size:12px}.custom-size input[type=number]{width:100%;box-sizing:border-box;height:38px}.size-cross{align-self:center;padding-top:18px;color:var(--muted)}.custom-size .ratio-lock{display:flex;align-items:center;gap:7px;min-height:38px;font-size:12px;margin:0}.ratio-lock input{width:15px;height:15px;margin:0}.custom-size small{flex-basis:100%;color:var(--muted);line-height:1.6}
.shot-video-options{padding:14px 16px;margin:0 0 16px;border:1px solid var(--border,#35423f);border-radius:12px;background:color-mix(in srgb,var(--accent,#81d9bd) 5%,var(--panel,#1e2625))}.options-heading{display:flex;align-items:center;gap:12px;margin-bottom:12px;font-size:13px}.options-heading span,.options-footer,.shot-video-options p{color:var(--muted,#aab8b3);font-size:12px}.options-fields{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap}.options-fields label{display:grid;gap:6px;flex:1 1 140px;margin:0;font-size:12px}.options-fields .workflow-field{flex:2 1 230px;min-width:0}.options-fields select{width:100%;min-width:0;height:40px}.refresh-options{height:40px;font-size:12px}.options-footer{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:12px}.options-footer .h3-toggle{display:flex;align-items:center;gap:7px;margin:0}.h3-toggle input{width:15px;height:15px;margin:0;accent-color:var(--accent,#81d9bd)}.shot-video-options p{margin:10px 0 0;line-height:1.6}@media(max-width:600px){.options-fields label,.options-fields .workflow-field{flex-basis:100%}.refresh-options{margin-left:auto}}
.refresh-options{padding:0 12px;border:1px solid var(--border,#35423f);border-radius:8px;background:var(--panel,#1e2625);color:var(--text,#e7eeeb);cursor:pointer}.refresh-options:disabled{opacity:.5;cursor:default}
.resource-estimate{margin-top:12px;padding:10px 12px;border:1px solid var(--border,#35423f);border-radius:8px;display:flex;gap:7px 16px;flex-wrap:wrap;font-size:12px}.resource-estimate>span{color:var(--text,#e7eeeb)}.resource-estimate small{flex-basis:100%;color:var(--muted,#aab8b3)}.resource-estimate.high{border-color:#b87568;background:rgba(180,70,50,.09)}.resource-estimate.caution{border-color:#ad985b;background:rgba(190,150,40,.06)}.resource-title{display:flex;justify-content:space-between;align-items:center;flex-basis:100%}.resource-title button{background:none;border:0;color:var(--accent,#81d9bd);cursor:pointer;font-size:12px}.resource-title button:disabled{opacity:.5}
.options-heading{flex-wrap:wrap}.options-heading .refresh-options{margin-left:auto;flex-shrink:0}
</style>
