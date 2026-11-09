<script setup>
import {ref,computed,onMounted} from 'vue'
import {requestJSON} from '../api'
import {h3MaximumDimensions,fitsH3Size} from '../cloudH3Limits'
const props=defineProps({modelValue:Object,disabled:Boolean,ratio:String,seconds:Number})
const emit=defineEmits(['update:modelValue'])
const capabilities=ref(null),error=ref('')
const generatedSeconds=computed(()=>{const c=capabilities.value;if(!c?.fps||!c?.frame_divisor||!props.seconds)return null;let frames=Math.ceil(props.seconds*c.fps);frames+=(c.frame_remainder-frames%c.frame_divisor+c.frame_divisor)%c.frame_divisor;return (frames/c.fps).toFixed(2)})
const requestSeconds=computed(()=>props.seconds??14)
const maximum=h3MaximumDimensions()
const minSeconds=computed(()=>Math.max(2,capabilities.value?.duration_seconds?.min??2))
const maxSeconds=computed(()=>Math.min(14,capabilities.value?.duration_seconds?.max??14))
const durationInvalid=computed(()=>!Number.isInteger(requestSeconds.value)||requestSeconds.value<minSeconds.value||requestSeconds.value>maxSeconds.value)
const sizes=computed(()=>(capabilities.value?.resolutions||[]).filter(item=>fitsH3Size(item.width,item.height)).sort((a,b)=>b.width*b.height-a.width*a.height||b.width-a.width))
const sizeDisabled=computed(()=>Boolean(props.disabled)||!sizes.value.length)
const selected=computed(()=>props.modelValue.resolution==='custom'?`${props.modelValue.width}x${props.modelValue.height}`:props.modelValue.resolution||(defaultDimensions.value?`${defaultDimensions.value.width}x${defaultDimensions.value.height}`:''))
const unavailableSelected=computed(()=>Boolean(selected.value)&&!sizes.value.some(item=>`${item.width}x${item.height}`===selected.value))
const defaultDimensions=computed(()=>{
 const portrait=props.ratio==='9:16'
 const available=sizes.value.filter(item=>(item.height>item.width)===portrait&&item.width!==item.height)
 const defaults=capabilities.value?.default_resolutions
 if(defaults!=null){
  const preferred=defaults[portrait?'portrait':'landscape']
  const advertised=(capabilities.value?.resolutions||[]).find(item=>item.width===preferred?.width&&item.height===preferred?.height)
  if(!advertised)return undefined
  if(!fitsH3Size(advertised.width,advertised.height))return available[0]
  return available.find(item=>item.width===preferred?.width&&item.height===preferred?.height)
 }
 return available.sort((a,b)=>b.width*b.height-a.width*a.height)[0]
})
const invalidDefault=computed(()=>capabilities.value?.default_resolutions!=null&&!defaultDimensions.value)
async function refresh(){try{error.value='';capabilities.value=await requestJSON('/api/video-studio/cloud/capabilities');if(!props.modelValue.resolution&&defaultDimensions.value)choose({target:{value:`${defaultDimensions.value.width}x${defaultDimensions.value.height}`}})}catch(e){error.value=e.message}}
function choose(event){
 const pair=event.target.value.split('x').map(Number)
 if(!sizes.value.some(item=>item.width===pair[0]&&item.height===pair[1])){error.value='请选择集群支持的尺寸';return}
 error.value='';emit('update:modelValue',{...props.modelValue,resolution:'custom',width:pair[0],height:pair[1]})
}
onMounted(refresh)
</script>
<template>
 <div class="cluster-h3-options">
  <strong>集群 H3</strong>
  <p v-if="capabilities?.pricing">云端当前费率：前 {{capabilities.pricing.threshold_seconds}} 秒 {{capabilities.pricing.first_10_seconds_rate}} 元/秒，超过部分 {{capabilities.pricing.additional_seconds_rate}} 元/秒。按请求时长计费，补帧不加收；总价向上取整到分。</p>
  <p v-else>尚未取得云端费率，生成前会重新获取并确认报价。</p>
  <p>尺寸上限 {{maximum[0]}} × {{maximum[1]}}，竖屏交换宽高。仅列出集群支持的尺寸。</p>
  <p>由集群排队并按需启动；模型首次加载会增加等待时间。参考配音、对口型暂不支持，原配音在导出时合成。</p>
  <label>视频尺寸 <select :value="selected" :disabled="sizeDisabled" @change="choose"><option v-if="!selected" value="" disabled>{{!capabilities?(error?'集群尺寸读取失败':'正在读取集群尺寸…'):sizes.length?'请选择视频尺寸':'集群未提供可用尺寸'}}</option><option v-if="unavailableSelected" :value="selected" disabled>{{selected}}（当前选择）</option><option v-for="item in sizes" :key="item.width+'x'+item.height" :value="item.width+'x'+item.height">{{item.width}} × {{item.height}}</option></select></label>
  <p v-if="capabilities && unavailableSelected" role="alert">已选尺寸超过集群尺寸上限或不在支持列表中。请刷新或重新选择；不会自动缩小尺寸。</p>
  <p v-if="invalidDefault && sizes.length && !selected" role="alert">集群默认尺寸与能力列表不一致，请选择支持的尺寸。</p>
  <p v-if="durationInvalid" role="alert">本镜请求 {{requestSeconds}} 秒，不在集群支持的 {{minSeconds}}–{{maxSeconds}} 秒范围内。仍可选择并保存分辨率；生成前请{{requestSeconds>maxSeconds?'拆分镜头或改用支持该时长的渠道':'调整请求时长'}}。</p>
  <p v-if="capabilities">请求时长：{{minSeconds}}–{{maxSeconds}} 秒 · {{capabilities.enabled?'可用':'暂不可用'}}。</p>
  <p v-if="seconds && !durationInvalid">本镜请求生成 {{seconds}} 秒<span v-if="generatedSeconds">，工作流帧数对齐后约 {{generatedSeconds}} 秒</span>；导出仍按配音时间轴裁切，不改变配音速度。</p>
  <p v-if="error" role="alert">{{error}}</p><button type="button" :disabled="disabled" @click="refresh">刷新集群能力</button>
 </div>
</template>
<style scoped>.cluster-h3-options{padding:12px;border:1px solid var(--border);border-radius:8px}.cluster-h3-options p{font-size:13px;color:var(--muted);line-height:1.6}</style>
