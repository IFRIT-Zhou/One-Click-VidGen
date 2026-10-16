<script setup>
import {ref,computed,onMounted} from 'vue'
import {requestJSON} from '../api'
const props=defineProps({modelValue:Object,disabled:Boolean,ratio:String,seconds:Number})
const emit=defineEmits(['update:modelValue'])
const capabilities=ref(null),error=ref('')
const generatedSeconds=computed(()=>{const c=capabilities.value;if(!c?.fps||!c?.frame_divisor||!props.seconds)return null;let frames=Math.ceil(props.seconds*c.fps);frames+=(c.frame_remainder-frames%c.frame_divisor+c.frame_divisor)%c.frame_divisor;return (frames/c.fps).toFixed(2)})
const sizes=computed(()=>capabilities.value?.resolutions||[])
const selected=computed(()=>props.modelValue.resolution==='custom'?`${props.modelValue.width}x${props.modelValue.height}`:props.modelValue.resolution||'')
const unavailableSelected=computed(()=>Boolean(selected.value)&&!sizes.value.some(item=>`${item.width}x${item.height}`===selected.value))
const defaultDimensions=computed(()=>{
 const portrait=props.ratio==='9:16'
 const available=sizes.value.filter(item=>(item.height>item.width)===portrait&&item.width!==item.height)
 const defaults=capabilities.value?.default_resolutions
 if(defaults!=null){
  const preferred=defaults[portrait?'portrait':'landscape']
  return available.find(item=>item.width===preferred?.width&&item.height===preferred?.height)
 }
 return available.sort((a,b)=>b.width*b.height-a.width*a.height)[0]
})
const invalidDefault=computed(()=>capabilities.value?.default_resolutions!=null&&!defaultDimensions.value)
async function refresh(){try{error.value='';capabilities.value=await requestJSON('/api/video-studio/cloud/capabilities')}catch(e){error.value=e.message}}
function choose(event){const pair=event.target.value.split('x').map(Number);emit('update:modelValue',{...props.modelValue,resolution:event.target.value?'custom':'',...(event.target.value?{width:pair[0],height:pair[1]}:{})})}
onMounted(refresh)
</script>
<template>
 <div class="cluster-h3-options">
  <strong>集群 H3（测试免费）</strong>
  <p>由集群排队并按需启动；模型首次加载会增加等待时间。参考配音、对口型暂不支持，原配音在导出时合成。</p>
  <label>视频尺寸 <select :value="selected" :disabled="disabled" @change="choose"><option value="">跟随集群默认尺寸与项目画幅{{defaultDimensions?`（${defaultDimensions.width} × ${defaultDimensions.height}）`:""}}</option><option v-if="unavailableSelected" :value="selected" disabled>已保存 {{selected}}，请明确重新选择尺寸</option><option v-for="item in sizes" :key="item.width+'x'+item.height" :value="item.width+'x'+item.height">{{item.width}} × {{item.height}}</option></select></label>
  <p v-if="unavailableSelected" role="alert">当前保存的尺寸未出现在集群能力列表，请选择实际支持的尺寸；不会自动降低分辨率。</p>
  <p v-if="invalidDefault" role="alert">集群默认尺寸与能力列表不一致，请刷新配置或明确选择支持的尺寸；不会自动降低分辨率。</p>
  <p v-if="capabilities">允许时长：{{capabilities.duration_seconds?.min}}–{{capabilities.duration_seconds?.max}} 秒 · {{capabilities.enabled?'可用':'暂不可用'}}；超限镜头需要先拆分。</p>
  <p v-if="seconds">本镜请求生成 {{seconds}} 秒<span v-if="generatedSeconds">，工作流帧数对齐后约 {{generatedSeconds}} 秒</span>；导出仍按配音时间轴裁切，不改变配音速度。</p>
  <p v-if="error" role="alert">{{error}}</p><button type="button" :disabled="disabled" @click="refresh">刷新集群能力</button>
 </div>
</template>
<style scoped>.cluster-h3-options{padding:12px;border:1px solid var(--border);border-radius:8px}.cluster-h3-options p{font-size:13px;color:var(--muted);line-height:1.6}</style>
