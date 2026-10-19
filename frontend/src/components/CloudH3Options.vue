<script setup>
import {ref,computed,onMounted} from 'vue'
import {requestJSON} from '../api'
import {h3MaximumDimensions,fitsH3Dimensions} from '../cloudH3Limits'
const props=defineProps({modelValue:Object,disabled:Boolean,ratio:String,seconds:Number})
const emit=defineEmits(['update:modelValue'])
const capabilities=ref(null),error=ref('')
const generatedSeconds=computed(()=>{const c=capabilities.value;if(!c?.fps||!c?.frame_divisor||!props.seconds)return null;let frames=Math.ceil(props.seconds*c.fps);frames+=(c.frame_remainder-frames%c.frame_divisor+c.frame_divisor)%c.frame_divisor;return (frames/c.fps).toFixed(2)})
const requestSeconds=computed(()=>props.seconds??14)
const maximum=computed(()=>h3MaximumDimensions(requestSeconds.value))
const durationInvalid=computed(()=>!Number.isFinite(requestSeconds.value)||requestSeconds.value<=0||requestSeconds.value>14)
const sizes=computed(()=>(capabilities.value?.resolutions||[]).filter(item=>fitsH3Dimensions(item.width,item.height,requestSeconds.value)).sort((a,b)=>b.width*b.height-a.width*a.height||b.width-a.width))
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
  if(!fitsH3Dimensions(advertised.width,advertised.height,requestSeconds.value))return available[0]
  return available.find(item=>item.width===preferred?.width&&item.height===preferred?.height)
 }
 return available.sort((a,b)=>b.width*b.height-a.width*a.height)[0]
})
const invalidDefault=computed(()=>capabilities.value?.default_resolutions!=null&&!defaultDimensions.value)
async function refresh(){try{error.value='';capabilities.value=await requestJSON('/api/video-studio/cloud/capabilities');if(!props.modelValue.resolution&&defaultDimensions.value)choose({target:{value:`${defaultDimensions.value.width}x${defaultDimensions.value.height}`}})}catch(e){error.value=e.message}}
function choose(event){
 const pair=event.target.value.split('x').map(Number)
 if(!sizes.value.some(item=>item.width===pair[0]&&item.height===pair[1])){error.value='请选择符合本镜时长限制、且集群支持的尺寸';return}
 error.value='';emit('update:modelValue',{...props.modelValue,resolution:'custom',width:pair[0],height:pair[1]})
}
onMounted(refresh)
</script>
<template>
 <div class="cluster-h3-options">
  <strong>集群 H3</strong>
  <p>请求最长 14 秒，尺寸上限 1664 × 936。竖屏交换宽高；列表只显示符合当前时长限制、且集群支持的尺寸。</p>
  <p>本镜尺寸上限：{{maximum[0]}} × {{maximum[1]}}（长边 × 短边）。</p>
  <p>由集群排队并按需启动；模型首次加载会增加等待时间。参考配音、对口型暂不支持，原配音在导出时合成。</p>
  <label>视频尺寸 <select :value="selected" :disabled="disabled||durationInvalid||!capabilities" @change="choose"><option v-if="!selected" value="" disabled>{{capabilities?'没有符合限制的尺寸':'正在读取集群尺寸…'}}</option><option v-if="unavailableSelected" :value="selected" disabled>{{selected}}（超过本镜限制或集群未提供）</option><option v-for="item in sizes" :key="item.width+'x'+item.height" :value="item.width+'x'+item.height">{{item.width}} × {{item.height}}</option></select></label>
  <p v-if="unavailableSelected" role="alert">当前保存的尺寸超过本镜时长对应的上限，或集群尚未提供，请重新选择；不会自动缩小已保存尺寸。</p>
  <p v-if="invalidDefault" role="alert">集群默认尺寸与能力列表不一致，请刷新配置或明确选择支持的尺寸；不会自动降低分辨率。</p>
  <p v-if="durationInvalid" role="alert">本镜请求时长无效或超过 14 秒，请先拆分镜头。</p>
  <p v-if="capabilities">请求时长：{{capabilities.duration_seconds?.min||2}}–{{Math.min(14,capabilities.duration_seconds?.max||14)}} 秒 · {{capabilities.enabled?'可用':'暂不可用'}}；超限镜头可使用“自动拆分”，补帧后可能略长于请求时长。</p>
  <p v-if="seconds">本镜请求生成 {{seconds}} 秒<span v-if="generatedSeconds">，工作流帧数对齐后约 {{generatedSeconds}} 秒</span>；导出仍按配音时间轴裁切，不改变配音速度。</p>
  <p v-if="error" role="alert">{{error}}</p><button type="button" :disabled="disabled" @click="refresh">刷新集群能力</button>
 </div>
</template>
<style scoped>.cluster-h3-options{padding:12px;border:1px solid var(--border);border-radius:8px}.cluster-h3-options p{font-size:13px;color:var(--muted);line-height:1.6}</style>
