<script setup>
import { computed, ref } from 'vue'
import RawLog from './RawLog.vue'
const page = ref('home'), tab = ref('文案'), drawer = ref(''), search = ref(''), filter = ref('全部'), logs = ref(false), selected = ref(0), selectedSentence = ref(0)
const title = ref('爱与生活之间'), script = ref('成年人的感情，很少只败给不爱。更多时候，两个人明明还在乎彼此，却被房租、工作、父母、未来和日复一日的疲惫推到了桌子的两端。\n\n晚上十点，周屿和许宁坐在出租屋的餐桌旁。饭菜已经凉了，桌上放着续租通知和几张尚未整理的账单。许宁希望换一套离公司更近的房子，因为她每天通勤接近三个小时。'), step = ref(true), preset = ref('日常叙事'), voice = ref('温柔女声'), style = ref('电影写实'), resolution = ref('2K'), emotion = ref('平静'), strength = ref(65), bgm = ref(false), running = ref(false), progress = ref(7), notice = ref(''), stage = ref('配音'), kind = ref('图文视频'), dirty = ref(false)
const sentences = ref(['成年人的感情，很少只败给不爱。','更多时候，两个人明明还在乎彼此，','却被房租、工作、父母、未来，','和日复一日的疲惫推到了桌子的两端。','爱情希望我们忠于感受，','现实却不断追问：谁来承担，谁先让步？'])
const shots = ref(['关系的距离','深夜餐桌','城市的疲惫','生活的重量','说出心里的担忧','一起走下去'])
const projects = ref([{name:'爱与生活之间',type:'图文视频',status:'待确认',date:'刚刚',detail:'配音已完成，等待试听确认'}, {name:'地球的缓慢呼吸',type:'图文视频',status:'已完成',date:'昨天',detail:'12 个画面 · 02:14'}, {name:'一段关于秋天的旁白',type:'仅配音',status:'已完成',date:'昨天',detail:'8 句 · 01:26'}, {name:'访谈字幕整理',type:'仅字幕识别',status:'草稿',date:'9 月 2 日',detail:'等待上传音视频'}])
const visible = computed(()=>projects.value.filter(p=>(filter.value==='全部'||p.type===filter.value)&&p.name.includes(search.value)))
const tabs = computed(()=>kind.value==='仅配音'?['文案','配音','导出']:kind.value==='仅字幕识别'?['字幕','导出']:['文案','配音','画面与字幕','导出'])
const logProject = ref('爱与生活之间'), logLevel = ref('全部'), logSearch = ref('')
const rawLogs = computed(()=>logProject.value==='访谈字幕整理'?[]:[
 '[14:30:00] 任务启动：'+logProject.value,
 '[14:30:01] 模块 1：使用 IndexTTS-2.5 配音',
 '[14:30:02] 开始：断句、配音、原始字幕',
 '[14:30:03] [TTS] 开始配音：共 6 句，并行数 2',
 ...Array.from({length:6},(_,i)=>`[14:31:${String(i*8).padStart(2,'0')}] 配音进度 ${i+1}/6：原文第 ${i+1} 句已生成`),
 '[14:31:49] [TTS] 6 句配音全部完成，正在合并音频与字幕',
 '[14:31:52] 完成：断句、配音、原始字幕',
 '[14:31:53] 模块 1 检查点已保存；后续失败断点续跑不会重配音',
 '[14:31:54] 开始：ASR 短字幕与页面断句',
 '[14:32:04] 完成：ASR 短字幕与页面断句',
 '[14:32:05] 开始：原文对齐与字幕校准',
 '[14:32:08] 完成：原文对齐与字幕校准',
 ...(logProject.value==='地球的缓慢呼吸'?[
 '[14:32:09] Agent 1：故事规划已保存',
 '[14:32:14] Agent 2：已规划 12 张画面',
 '[14:32:15] [POSTER_PROGRESS] 0/12',
 '[14:32:21] [WARN] poster_007 查询网络超时，5 秒后重试查询原任务',
 '[14:32:26] poster_007 原任务查询成功，图片已保存',
 '[14:33:01] [POSTER_PROGRESS] 12/12',
 '[14:33:02] 开始：视频渲染',
 '[14:34:10] 完成：视频渲染，成片已保存',
 ]:['[14:32:09] 分步模式：配音与字幕校对已完成，请试听、精修并确认。'])
])
const logEntries = [
 {time:'14:32:08',project:'爱与生活之间',level:'完成',stage:'配音',message:'6 句配音已完成，音频与字幕已保存。'},
 {time:'14:32:09',project:'爱与生活之间',level:'提示',stage:'逐步确认',message:'等待试听确认，确认后继续生成画面。'},
 {time:'14:25:16',project:'地球的缓慢呼吸',level:'提醒',stage:'画面',message:'第 7 张画面请求暂时超时，已保留成功图片，正在查询原任务。'},
 {time:'14:25:23',project:'地球的缓慢呼吸',level:'完成',stage:'画面',message:'原任务查询成功，12 张画面全部完成。'},
 {time:'14:27:41',project:'地球的缓慢呼吸',level:'完成',stage:'导出',message:'视频导出完成，可以打开项目查看作品。'},
 {time:'13:40:05',project:'一段关于秋天的旁白',level:'完成',stage:'配音',message:'音频已导出至项目输出目录。'},
]
const filteredLogs = computed(()=>logEntries.filter(e=>(logProject.value==='全部项目'||e.project===logProject.value)&&(logLevel.value==='全部'||e.level===logLevel.value)&&(`${e.message} ${e.project} ${e.stage}`).includes(logSearch.value)))
async function copyLogs(){try{await navigator.clipboard.writeText(filteredLogs.value.map(e=>`[${e.time}] [${e.project}] ${e.message}`).join('\n'));flash('已复制当前筛选的演示日志。')}catch{flash('浏览器未允许复制，请选择日志文字复制。')}}
function flash(text){notice.value=text}
function edit(p){title.value=p.name;kind.value=p.type;page.value='editor';tab.value=p.type==='仅字幕识别'?'字幕':p.status==='已完成'?'导出':'配音';selected.value=0}
function create(type='图文视频'){kind.value=type;page.value='new';title.value='';script.value='';drawer.value=''}
function start(){if(!title.value.trim()||(!script.value.trim()&&kind.value!=='仅字幕识别')){flash('请先填写项目名称与文案。');return} projects.value.unshift({name:title.value,type:kind.value,status:'待确认',date:'刚刚',detail:'原型演示项目'});page.value='editor';tab.value=kind.value==='仅字幕识别'?'字幕':'配音';stage.value='配音';flash('演示：已进入编辑工作区，未调用生成服务。')}
function advance(){if(stage.value==='配音'){stage.value='画面';tab.value='画面与字幕';running.value=true;flash('演示：画面生成中，可以返回首页查看其他项目。')}else{stage.value='导出';tab.value='导出';running.value=false}}
</script>

<template>
 <div class="studio">
  <aside class="rail">
   <button class="brand" @click="page='home'"><img src="/one-click-vidgen-logo.png" alt="OCV"/><span>OCV<small>创作工作台</small></span></button>
   <button class="new-button" @click="create()"><span>＋</span> 新建图文视频</button>
   <nav><button :class="{active:page==='home'}" @click="page='home'"><span>▦</span> 项目首页</button><button :class="{active:page==='logs'}" @click="page='logs'" aria-label="任务日志"><span>≋</span> 任务日志</button><p>附加功能</p><button @click="create('仅配音')"><span>◉</span> 配音工作室</button><button @click="create('仅字幕识别')"><span>≡</span> 字幕识别</button></nav>
   <div v-if="running" class="running"><span class="dot"/> 正在生成画面 <b>{{progress}} / 15</b><progress :value="progress" max="15"/><button @click="page='editor';tab='画面与字幕'">返回运行项目 →</button></div>
   <div class="rail-bottom"><button @click="drawer='接口与服务'">⚙ <span>接口与服务</span><i class="dot"/></button><button @click="drawer='插件与设置'">⊞ <span>插件与设置</span></button><div class="local"><span class="avatar">本</span><div>本地工作空间<small>文件保存在这台电脑</small></div></div></div>
  </aside>
  <main>
   <header class="topbar"><div>工作空间 <span>/</span> {{page==='home'?'项目首页':page==='logs'?'任务日志':page==='new'?'新建'+kind:title}}</div><div class="top-actions"><span class="prototype-label">交互原型 · 演示数据</span><button @click="drawer='使用说明'">帮助</button></div></header>
   <div v-if="notice" class="toast" role="status">{{notice}}<button aria-label="关闭提示" @click="notice=''">×</button></div>
   <section v-if="page==='home'" class="home content">
    <div class="page-heading"><div><p class="eyebrow">YOUR CREATIVE SPACE</p><h1>让想法，成为作品。</h1><p class="muted">从一段文字开始，继续上次的创作。</p></div><button class="primary" @click="create()">＋ 新建图文视频</button></div>
    <div class="resume"><div class="resume-mark">Ⅱ</div><div><small>继续创作</small><h3>爱与生活之间</h3><p>配音已完成，试听并调整后即可生成画面。</p></div><button @click="edit(projects[0])">继续编辑 <span>↗</span></button></div>
    <div class="section-heading"><h2>我的项目 <small>{{projects.length}}</small></h2><input v-model="search" class="search" placeholder="搜索项目…" aria-label="搜索项目"/></div>
    <div class="filters"><button v-for="f in ['全部','图文视频','仅配音','仅字幕识别']" :class="{selected:filter===f}" @click="filter=f">{{f}}</button></div>
    <div class="project-table"><div class="table-head"><span>项目名称</span><span>状态</span><span>最近修改</span><span/></div><div v-for="p in visible" class="project-row"><button class="project-title" @click="edit(p)"><span class="project-icon">{{p.type==='图文视频'?'▧':p.type==='仅配音'?'◉':'≡'}}</span><span><b>{{p.name}}</b><small>{{p.type}} · {{p.detail}}</small></span></button><span class="status" :class="{waiting:p.status==='待确认'}">{{p.status}}</span><span class="muted">{{p.date}}</span><button @click="edit(p)">打开 ↗</button></div><p v-if="!visible.length" class="empty">没有符合条件的项目</p></div>
    <p class="home-note">创作由你掌握。配音、画面与字幕，都可以在生成后继续打磨。</p>
   </section>
   <section v-else-if="page==='logs'" class="content logs-page">
    <div class="page-heading"><div><p class="eyebrow">TASK CONSOLE</p><h1>任务日志</h1><p class="muted">查看后台执行过程、当前进度与报错原文。</p></div><button @click="edit(projects.find(p=>p.name===logProject)||projects[0])">前往项目 ↗</button></div>
    <div class="log-toolbar"><label>查看任务<select v-model="logProject"><option v-for="p in projects" :key="p.name">{{p.name}}</option></select></label><span class="muted">{{rawLogs.length ? '演示状态：'+(logProject==='地球的缓慢呼吸'?'已完成':'等待配音确认') : '等待任务启动'}}</span></div>
    <RawLog :lines="rawLogs"/>
    <div class="log-help"><span>遇到问题？诊断包方便定位失败原因。</span><button @click="flash('当前是原型演示，没有真实任务诊断文件。')">导出诊断包 ↗</button></div>
   </section>
   <section v-else-if="page==='new'" class="content creation">
    <div class="page-heading"><div><p class="eyebrow">NEW PROJECT</p><h1>{{kind==='图文视频'?'从文字，开始一部作品。':kind==='仅配音'?'让文字，有自己的声音。':'让每一句话，都清晰可见。'}}</h1><p class="muted">{{kind==='仅字幕识别'?'上传素材，识别后即可校对文字与时间。':'先写下内容，其他细节可以慢慢调整。'}}</p></div><select v-model="preset" aria-label="套用预设"><option>日常叙事</option><option>知识科普</option><option>不使用预设</option></select></div>
    <label class="name-label">项目名称<input v-model="title" placeholder="为这个作品起个名字"/></label>
    <div v-if="kind!=='仅字幕识别'" class="writing"><div><b>口播文案</b><button @click="flash('原型阶段暂不导入真实文件。')">↥ 导入文案</button></div><textarea v-model="script" placeholder="在这里写下你的故事、观点或知识…"/><footer><span>{{script.length}} 字</span><span>生成后仍可调整发音与字幕</span></footer></div>
    <button v-else class="upload-zone" @click="flash('原型阶段暂不上传真实素材。')">＋ 选择音频或视频<small>拖入素材也可以 · 识别完成后支持逐句校对</small></button>
    <div class="setting-summaries"><button v-if="kind!=='仅字幕识别'" @click="drawer='声音设置'"><span>◉</span><div><small>配音</small><b>{{voice}} · {{emotion}}</b></div><span>›</span></button><button v-if="kind==='图文视频'" @click="drawer='画面设置'"><span>▧</span><div><small>画面</small><b>{{style}} · {{resolution}}</b></div><span>›</span></button><button @click="drawer='我的预设'"><span>⊞</span><div><small>我的预设</small><b>{{preset}}</b></div><span>›</span></button></div>
    <div class="create-footer"><label v-if="kind==='图文视频'"><input v-model="step" type="checkbox"/> 逐步确认 <small>配音、画面完成后，由你确认再继续</small></label><span v-else class="muted">文件保存在本地工作空间</span><button class="primary" @click="start">{{kind==='仅字幕识别'?'开始识别':'开始生成'}} →</button></div>
   </section>
   <section v-else class="editor">
    <div class="editor-heading"><div><h1>{{title}}</h1><span class="muted">{{kind}} <span class="save-state">· 原型草稿</span></span></div><button @click="drawer='我的预设'">预设 · {{preset}}⌄</button></div>
    <div class="editor-tabs"><button v-for="t in tabs" :class="{active:tab===t}" @click="tab=t">{{t}}</button><span class="tab-spacer"/><button @click="drawer=tab==='配音'?'声音设置':'画面设置'">调整参数 ⚙</button></div>
    <div v-if="tab==='文案'" class="editor-writing"><label>口播文案<textarea v-model="script"/></label><p class="muted">修改文案后可选择需要重配的句子。</p></div>
    <div v-else-if="tab==='配音'||tab==='字幕'" class="audio-workspace">
     <div class="audio-transport"><button class="play" @click="flash('此处为试听交互示意，原型不播放实际项目音频。')">▶</button><div class="wave"><i v-for="n in 70" :style="{height:(8+(n*17)%27)+'px'}"/></div><span>00:00 / 02:14</span><button @click="drawer='声音设置'">{{voice}} ⚙</button></div>
     <div class="split-editor"><div class="sentence-list"><div class="list-heading">全部句子 <span>{{sentences.length}} 句</span></div><button v-for="(s,i) in sentences" :class="{selected:i===selectedSentence}" @click="selectedSentence=i"><small>{{String(i+1).padStart(2,'0')}}</small><span>{{s}}</span><span class="duration">{{i*4}}s</span></button></div><div class="sentence-detail"><div class="detail-title"><span class="eyebrow">{{tab==='字幕'?'字幕校对':'配音精修'}}</span><span class="muted">第 {{selectedSentence+1}} 句</span></div><h2>{{sentences[selectedSentence]}}</h2><button @click="flash('演示：局部试听')">▶ 试听这一句</button><label>{{tab==='字幕'?'字幕文字':'朗读文本'}}<textarea v-model="sentences[selectedSentence]"/></label><p class="muted">{{tab==='字幕'?'修改文字后，可以继续调整相邻字幕的边界。':'可用拼音修正读音；字幕保留原文。'}}</p><div class="inline-settings"><label>{{tab==='字幕'?'开始时间':'句后停顿（秒）'}}<input type="number" min="0" step="0.1" value="0.6"/></label><button @click="flash('演示：打开相邻句边界调整')">{{tab==='字幕'?'调整边界':'断句 / 合并'}}</button></div><button class="primary" @click="flash(tab==='字幕'?'演示：字幕修改已记录':'演示：仅重新生成当前句')">{{tab==='字幕'?'应用字幕修改':'重新配音当前句'}}</button></div></div>
    </div>
    <div v-else-if="tab==='画面与字幕'" class="visual-workspace"><div class="shot-list"><div class="list-heading">画面 <span>{{shots.length}} 张</span></div><button v-for="(s,i) in shots" :class="{selected:selected===i}" @click="selected=i"><div class="mini-art" :class="'art-'+i"><span>{{String(i+1).padStart(2,'0')}}</span></div><div><b>{{s}}</b><small>{{i*8}}:00 — {{i*8+8}}:00</small></div></button></div><div class="shot-detail"><div class="detail-title"><h2>{{shots[selected]}}</h2><button @click="drawer='画面设置'">画面设置 ↗</button></div><div class="art-preview" :class="'art-'+selected"><div class="art-shape"/><span>画面预览位置 · 原型示意</span></div><div class="image-actions"><button @click="flash('演示：打开替换画面')">↥ 替换画面</button><button @click="flash('演示：重新生成当前画面')">↻ 重新出图</button><button @click="flash('演示：调整画面覆盖的字幕范围')">调整覆盖范围</button></div><div class="subtitle-heading"><h3>这一画面的字幕</h3><small>点击文字编辑</small></div><div v-for="(s,i) in sentences.slice(0,3)" class="subtitle-row"><span>00:{{String(selected*8+i*2).padStart(2,'0')}}</span><input :value="s" aria-label="字幕文字"/><button @click="flash('演示：打开带试听的边界调整')">边界</button></div></div></div>
    <div v-else class="export-workspace"><div class="export-preview"><span>▷</span><h2>{{kind==='图文视频'?'准备好，让作品见面。':'最后一步，带走你的作品。'}}</h2><p>原型预览 · 导出后在这里查看结果</p></div><div class="export-settings"><h2>导出设置</h2><label>成片版本<select><option>仅字幕版</option><option>双版本</option><option>纯净版</option></select></label><label v-if="kind==='图文视频'">背景音乐<button @click="drawer='背景音乐'">{{bgm?'已启用':'未添加'}} ＋</button></label><p class="muted">导出后仍可以返回各编辑区域继续修改。</p><button class="primary" @click="flash('演示：导出操作已确认，原型不会执行渲染。')">{{kind==='图文视频'?'导出视频':kind==='仅配音'?'导出音频':'导出 SRT'}} ↗</button></div></div>
    <div class="task-strip"><button @click="logs=!logs">{{logs?'⌄':'›'}} 任务日志</button><span v-if="running" class="muted">正在生成画面 {{progress}}/15</span><span v-else class="muted">{{tab==='配音'?'配音已就绪，试听满意后继续':'编辑内容仅用于原型演示'}}</span><span class="tab-spacer"/><button v-if="kind==='图文视频' && tab!=='导出'" class="primary" @click="advance">{{stage==='配音'?'确认配音，生成画面':'确认画面，前往导出'}} →</button></div><div v-if="logs" class="log-panel">[演示] 配音与字幕处理完成。<br/>[演示] 当前原型不会调用 API 或修改真实任务。<div><button @click="flash('原型没有真实诊断数据。')">导出诊断包</button><button @click="running=false;flash('演示任务已停止')">停止任务</button></div></div>
   </section>
  </main>
  <div v-if="drawer" class="drawer-backdrop" @click.self="drawer=''"><aside class="drawer"><header><div><p class="eyebrow">PROJECT SETTINGS</p><h2>{{drawer}}</h2></div><button aria-label="关闭设置" @click="drawer=''">×</button></header><div class="drawer-body"><template v-if="drawer==='声音设置'"><label>执行方式<select><option>本地 IndexTTS-2.5</option><option>集群配音</option></select></label><label>参考声音<select v-model="voice"><option>温柔女声</option><option>沉稳男声</option></select></label><label>情绪<select v-model="emotion"><option>平静</option><option>开心</option><option>悲伤</option></select></label><label>情绪强度 <span>{{strength}}%</span><input type="range" v-model="strength"/></label><label>语速<input type="number" value="1" step="0.1"/></label></template><template v-else-if="drawer==='画面设置'"><label>内容模式<select><option>通用自定义</option><option>纯科普</option><option>口播科普</option><option>都市惊悚</option></select></label><label>统一画风<select v-model="style"><option>电影写实</option><option>手绘插画</option><option>自定义</option></select></label><label>导演策略<select><option>叙事增强（测试）</option><option>稳定策略</option></select></label><label>出图分辨率<select v-model="resolution"><option>1K</option><option>2K</option></select></label><label>人物设定<textarea placeholder="描述人物的外貌与服装"/></label><label>故事环境<textarea placeholder="描述时代、场景与氛围"/></label></template><template v-else-if="drawer==='我的预设'"><p class="muted">预设方便新作品复用。修改项目不会自动覆盖预设。</p><label>当前预设<select v-model="preset"><option>日常叙事</option><option>知识科普</option></select></label><button @click="flash('演示：更新当前预设')">更新当前预设</button><button @click="flash('演示：另存为新预设')">＋ 另存为新预设</button></template><template v-else-if="drawer==='接口与服务'"><p class="muted">在这里统一管理配音、语言模型与出图服务。</p><label>语言模型<select><option>官方 API</option><option>自定义兼容接口</option><option>号池</option></select></label><label>API Base URL<input placeholder="未配置 · 输入接口地址" autocomplete="off"/></label><label>API Key<input type="password" placeholder="输入密钥" autocomplete="new-password"/></label><label>图像服务<select><option>号池</option><option>自定义接口</option></select></label><button @click="flash('原型不保存真实接口配置，请勿填写真实密钥。')">保存接口配置</button></template><template v-else-if="drawer==='背景音乐'"><label><input v-model="bgm" type="checkbox"/> 添加背景音乐</label><button @click="flash('演示：选择本地音乐')">＋ 选择音乐</button><label>音量<input type="range" value="20"/></label></template><template v-else><p class="muted">这是新工作台的交互原型，可体验导航、编辑布局与设置面板。真实任务接入将在布局确认后进行。</p><button @click="page='home';drawer=''">返回项目首页</button></template></div><footer><span class="muted">原型设置仅在本次页面内保留</span><button class="primary" @click="drawer=''">完成</button></footer></aside></div>
 </div>
</template>
