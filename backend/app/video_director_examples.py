"""One human-approved example, scoped to the two creative video directors.

This is evidence for a method, not a topic/style template or a claim of broad
model accuracy. Finalizers receive the current plan, not these example assets.
"""

_SOURCE = (
    '那如果我说去日韩旅游呢？汉奸、不爱国。将军的炮火已经瞄准了南边。'
    '最后所有的落点都会回到一句：出国不如逛祖国的大好河山。'
)
_IMAGE = (
    '简笔火柴人，黑色钢笔轮廓线，简约手绘，一个红色衣服的小人站在演台中央，'
    '周围坐着很多观众围观他的讲话，他的脑袋上有个对话气泡框，里面是日本和韩国的标志性景观。'
    '观众表情惊讶紧张，观众们头上也有一个气泡框，里面是汉奸、不爱国，还有飞机战火'
)
_VIDEO = (
    '生成一段正常全屏、横屏16:9的连续动画，保持参考图中的白底手绘火柴人画风、'
    '红色身体的主讲人、讲台、会场和观众造型稳定。主讲人在会场中央演讲。'
    '第一阶段，主讲人开口提问日韩旅游，主讲人上方的气泡显示日本和韩国的标志性景观；'
    '随后该气泡消失。第二阶段，周围观众产生激烈反应，观众的气泡分别出现“汉奸”和“不爱国”，'
    '另一个想象气泡表现战机、炮火与城市硝烟；观众表情和肢体由疑惑变得惊恐，主讲人显得无奈；'
    '随后这些气泡全部消失。第三阶段，主讲人再次开口，上方气泡显示长城、兵马俑等祖国景观，'
    '观众转为欢呼雀跃，主讲人尴尬微笑。各阶段依次发生，不同时堆出所有气泡。'
    '动作自然连贯，允许围绕同一会场合理切换中景和观众反应镜头，不新增角色，不添加计划外文字，静音。'
)

_TRANSFER = (
    '示例的人设、画风、地点、气泡和三阶段不是通用模板，仅学习原文含义→可见关系→按时间展开。'
    '当前用户设定和source_subtitles优先；仅当当前内容适合时才采用相同手法。'
    '例如阀门控制水流可直接呈现阀门与流动变化，无需主持人或气泡；'
    '家务和照护负担可呈现同一个人被两件事同时牵制，无需转为观众恐慌或欢呼。'
)

CORE_DESIGN_EXAMPLE = {
    'source': _SOURCE,
    'user_visual_setting': '红色衣服的火柴人主讲，简约手绘风格',
    'intent': '表示当下中国普通人对于日韩旅行的刻板印象',
    'human_image_prompt': _IMAGE,
    'why_it_works': (
        '提问者与回应者处于共同现场，景观气泡归主讲人、指责和战争想象归观众，表情提供可见反应。'
        '图中可以同时保留这些有归属的素材，而不把气泡里的战争当成会场现实。'
        '核心图不用画出最后的国内景观和欢呼，那是视频后段的变化。'
    ),
    'scope': _TRANSFER,
}

MOTION_DESIGN_EXAMPLE = {
    'source': _SOURCE,
    'core_image': _IMAGE,
    'human_approved_video_body': _VIDEO,
    'why_it_works': (
        '沿核心图已有关系安排提问→受影响的观众反应→另一种提议与反应，写清气泡主体与消失顺序。'
        '核心图中缺少的国内景观由原文“大好河山”支持，可以在后段生成。'
        '保留一处会场，可按表达需要切人物中景和观众反应，不必逐字对口型。'
    ),
    'scope': _TRANSFER,
}


# A mode-specific teaching example, not a claim that these generated variants
# have been human-approved. The original text-assisted examples stay untouched.
_VISUAL_FIRST_IMAGE = (
    '简约手绘，红衣主讲人站在演台中央，侧身向台下的观众摊手提问；'
    '主讲人上方的图案想象气泡里是樱花树旁的古寺和临海的韩式传统屋顶，只有景物，没有地名或问句。'
    '台下观众面向主讲人，有人皱眉后仰，有人向主讲人伸出手掌，明确表达对这一提议的排斥。'
    '其中一位观众的短小对话气泡只写“汉奸”，保留这一政治化指责的具体含义，不再叠加整句评价。'
    '靠前一位观众的想象气泡里是战机飞过烟尘笼罩的城市，这是该观众的担忧而非会场实景。'
    '气泡尾部各自指向发言者或想象者，不添加长篇对白、口播解释或大标题。'
)
_VISUAL_FIRST_VIDEO = (
    '保持红衣主讲人、台下观众与手绘会场。先由主讲人摊手提出旅行设想，'
    '其图案气泡内浮现樱花旁的古寺和海岸边的韩式传统屋顶，不显示地名或问句。'
    '随后主讲人的图案气泡消失，台下几位观众皱眉摇头、抬掌拒绝；'
    '其中一位观众的短小对话气泡出现“汉奸”，这一指责属于该观众，不属于主讲人。'
    '前排一位观众上方的图案气泡出现战机与城市烟尘，他紧缩肩膀，邻座露出紧张神情，'
    '主讲人停住手势，显得无奈。战争图案只属于该观众的想象，不变成会场事实。'
    '指责文字和担忧气泡消失后，主讲人换一个邀请手势，上方图案气泡浮现山间长城与兵马俑，'
    '观众放松下来并拍手，主讲人尴尬微笑。各人的发言与想象关系用姿态、表情和图案归属表达，'
    '只保留该处必要短词，不画完整问句或旁白说明，气泡依次出现和消失，不累计。'
    '最后自然停在这一反应上，静音。'
)

VISUAL_FIRST_CORE_DESIGN_EXAMPLE = {
    'source': _SOURCE,
    'user_visual_setting': '红色衣服的火柴人主讲，简约手绘风格',
    'intent': CORE_DESIGN_EXAMPLE['intent'],
    'illustrative_image_prompt': _VISUAL_FIRST_IMAGE,
    'why_it_works': (
        '旅行设想用景物图案，反应用手势和表情；只保留难以用动作准确表达的关键指责短词。'
        '不要求观众读完原文；战争想象仍明确归前排观众，不作为现实事实。'
    ),
    'scope': _TRANSFER + '此处只示范画面优先，气泡数量、留白气泡和无字场景都不是强制模板。',
}

VISUAL_FIRST_MOTION_DESIGN_EXAMPLE = {
    'source': _SOURCE,
    'core_image': _VISUAL_FIRST_IMAGE,
    'illustrative_video_body': _VISUAL_FIRST_VIDEO,
    'why_it_works': (
        '提问、排斥和担忧、换一种提议后的反应依次可见；无需把口播画进气泡。'
        '图案具体写在动作中；texts 与 reference_texts 只登记选用的关键指责短词，保留观众归属。'
    ),
    'scope': _TRANSFER + '场景内确实必要的菜单、标牌、数字仍可保留，不把画面优先套成全面禁字。',
}
