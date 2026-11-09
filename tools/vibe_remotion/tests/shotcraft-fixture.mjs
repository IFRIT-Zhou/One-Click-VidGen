export function fixture(){
  const duration=404/30;
  return {duration,fps:30,assets:{},plan:{version:'ocv-shotcraft-v1',canvas:[960,540],duration,fps:30,
    palette:{background:'#EAF3F8',surface:'#F5FAFD',ink:'#123048',muted:'#48677C',line:'#B8CCD9',accent:'#167C9C'},background:{kind:'technical_grid'},typography:'sans',stages:[
      {id:'open',recipe:'spotlight-hero-card',start:0,end:4.4,eyebrow:'从问题出发',headline:'把线索变成\n可接续的工作',body:'保持原始信息的完整性，让问题有一个清晰的起点。',footer:'内容与原始材料保持一致',items:[{id:'hero',title:'一份清晰的记录',body:'从已有材料中整理线索，保留上下文。',motif:'document'}]},
      {id:'relay',recipe:'word-relay-filmstrip',start:4.4,end:299/30,eyebrow:'循序推进',headline:'围绕同一个问题',body:'每一步，都有可以回看的依据。',footer:'过程展示，不添加未经支持的数据',items:[{id:'read',title:'理解背景',body:'阅读材料，确认问题的来处。',motif:'book'},{id:'inspect',title:'核对证据',body:'回到原始信息，检查每个关键表述。',motif:'inspection'},{id:'refine',title:'继续完善',body:'把已知与未知分别记录。',motif:'refinement'}]},
      {id:'outro',recipe:'outro-group-photo-launch',start:299/30,end:duration,eyebrow:'保持完整，继续推进',headline:'让已有信息\n继续产生意义',body:'从清晰的起点，走向下一步。',footer:'统一视觉，完整呈现',items:[{id:'a',title:'原始材料',body:'保留原貌',motif:'document'},{id:'b',title:'问题背景',body:'保留上下文',motif:'book'},{id:'c',title:'证据核对',body:'明确边界',motif:'inspection'},{id:'d',title:'后续完善',body:'持续修订',motif:'refinement'}]},
    ]}};
}
