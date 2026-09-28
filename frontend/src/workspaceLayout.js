import {reactive,watch} from 'vue'

const layouts=new Map()
const defaults=scope=>({width:248,order:scope==='illustrated-visuals'?['preview','prompts','subtitles','logs']:['preview','prompts','logs'],collapsed:{logs:true}})
export function useWorkspaceLayout(scope){
 if(!layouts.has(scope)){
  let value=defaults(scope)
  try{
   const saved=JSON.parse(localStorage.getItem('ocv.layout.v1.'+scope)||'null')
   if(saved&&typeof saved==='object'){
    const order=Array.isArray(saved.order)?[...new Set(saved.order.filter(id=>value.order.includes(id)))]:[]
    value={width:Math.min(440,Math.max(180,Number(saved.width)||248)),order:[...order,...value.order.filter(id=>!order.includes(id))],collapsed:Object.fromEntries(value.order.map(id=>[id,saved.collapsed?.[id]===true]))}
   }
  }catch{}
  const state=reactive(value)
  watch(state,()=>{try{localStorage.setItem('ocv.layout.v1.'+scope,JSON.stringify(state))}catch{}},{deep:true})
  layouts.set(scope,state)
 }
 const state=layouts.get(scope)
 return {state,reset:()=>Object.assign(state,defaults(scope))}
}
