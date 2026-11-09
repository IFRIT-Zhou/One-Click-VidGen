"""Material-aware poster reconstruction, one visual repair, then timed animation.

Model output remains bounded data. Only prepared local assets are available;
no generated code, additional image submissions, or automatic adoption here.
"""
from pathlib import Path
from typing import Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import vibe_cloud as api
from . import vibe_remotion as renderer
from .vibe_layered_retake import SourceRetake, source_manifest, apply_retakes, validate_retakes
from .vibe_layered_geometry import geometry_manifest, resolve_source_anchors
from .vibe_layered_assembly import (
    Assembly, MotionSchedule, compile_assembly, materialize_subjects, static_plan,
    apply_motion, plan_groups, validate_content, resolve_subject_asset,
    ASSEMBLY_DIRECTOR, REPAIR_DIRECTOR, MOTION_DIRECTOR,
)

VERSION='material-aware-layered-v2'


class Issue(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['missing','duplicate','overlap','crop','layout','style','timing','readability']
    target: str=Field(min_length=1,max_length=160)
    explanation: str=Field(min_length=1,max_length=600)
    correction: str=Field(min_length=1,max_length=600)


class RecoveredTextCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    text: str=Field(min_length=1,max_length=180)
    matched: bool=Field(strict=True)
    explanation: str=Field(min_length=1,max_length=500)


class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    passed: bool=Field(strict=True)
    issues: list[Issue]=Field(max_length=12)
    summary: str=Field(min_length=1,max_length=800)
    source_retakes: list[SourceRetake]=Field(default_factory=list,max_length=8,
        description='仅当已有主体的原始裁片本身截断时，按 source_retake_manifest 的实际完整源图申请重取。只在第一次海报审核后执行一次；不改变素材身份、不生成新图。无需重取则[]。')
    recovered_text_checks: list[RecoveredTextCheck]=Field(default_factory=list,max_length=30,
        description='逐项核对 assembly.recovered_text 的原图补录文字；text 原样填写，matched 表示确实可在 core 的 source_box 中看到同一原文。没有补录时为空列表。')

    @model_validator(mode='before')
    @classmethod
    def transport_marker(cls,value):
        if isinstance(value,dict) and value.get('type')=='json_object':
            return {key:item for key,item in value.items() if key!='type'}
        return value

    @model_validator(mode='after')
    def consistent(self):
        if self.passed and self.issues or not self.passed and not self.issues:
            raise ValueError('核查结论与问题清单不一致')
        if self.passed and any(not item.matched for item in self.recovered_text_checks):
            raise ValueError('补录文字未在原图核实，不能判定通过')
        if self.passed and self.source_retakes:
            raise ValueError('需要重新取材时不能判定通过')
        return self


def verify_recovered_text(review,assembly):
    expected=[item.text for item in getattr(assembly,'recovered_text',[])]
    actual=[item.text for item in review.recovered_text_checks]
    if len(actual)!=len(set(actual)) or set(actual)!=set(expected):
        raise ValueError('必须逐项核对全部 recovered_text，不能漏核、重复或加入不存在的补录项')


REVIEWER='''你是原图分层动效的严格视觉核查员。只返回符合schema的JSON。
core为原海报；poster为重建后的完整海报。素材图片只是提供检查裁片是否夹带文字。
比较实际图片，不要只相信方案字段或素材声明。检查：所有原文和数值是否保留且没有重复，
主体是否被截断、夹带半截文字、缩小过多；数字与单位是否组合正确；文本是否遮挡或重叠；
文本底板、前景箭头/标注线是否缺失；构图、主要比例、颜色、背景装饰是否接近原图。
原始照片/病理/数据图不得生成式重画。允许代码字体与原图略有差异，不要求逐像素相同。
发现问题给出具体target、explanation和可执行correction，passed=false；不要为通过而忽略问题。
若同时给了阶段截图，检查是否按字幕分阶段出现、图文入场有无闪烁/截断/重叠、末尾是否留阅读时间。
没有问题才passed=true且issues=[]。不得把缺失文字当作设计简化，不要建议改写医学事实。'''
REVIEWER+='''
assembly.recovered_text 是排版阶段认为初始清单漏掉的原图文字，绝不能仅凭声明放行。
必须逐项放大查看 core 对应 source_box，确认原文逐字存在；在 recovered_text_checks 中逐项返回相同text、matched与依据。
若它是臆造、改写或读错，matched=false，passed=false，并给出具体返工问题；没有补录则返回空列表。'''
REVIEWER+='''
修正建议必须落在提供的 Assembly schema 的实际能力内，不要求新增模型、修改原始素材像素或不存在的样式字段。
subject_N 是已抠图/保真素材，subject_N_original 是同坐标范围的原始裁片。
prepared_crop 的[0,0,1,1]代表当前这一张素材的整张，绝不是core全海报坐标。返工建议不得把core上的人物位置当作prepared_crop再次裁切。
主体因二次裁切残缺时先比较当前素材整张是否完整；若完整，应恢复prepared_crop的整张范围，再按素材自身边缘污染小幅修边。
若原始裁片完整而抠图抹掉底座、光晕或主体，prepared_crop 外扩无法恢复透明像素：可建议 prepared_variant="original" 保真沿用，
并核对原裁片矩形边缘、夹带文字与新背景是否相容。原始裁片也没有的内容不能声称能靠裁切恢复。
简单箭头是 decorations 的 polyline，不是新增 labels 文案。数字与单位、分色标题可以拆成相邻同组文字层；保持原文、字号层次、间距和基线。
source_geometry是程序核实的定位依据。placement=source_anchor的box已按实际源像素和prepared_crop换算，不能建议仅修改box来移位/缩放（程序会重新解析它）；若确需重排必须明确切manual及理由，若是抠图丢失应检查original备选，而不是反复裁透明像素。
清单里的识别文本只供定位核对，core实际可见内容才是依据；不要把误识别的装饰符号、引号或描述当作原图缺失文案要求补画。
同组分色标题的文字框边界相接并不等于字形相接。提出间距修正时考虑实际字号、文字宽度和align；不要仅让两个过宽的box贴边却保留内部空白。
允许光晕强弱、字体字形等轻微渲染差异，不要求逐像素复刻；重点拦截信息错误、文字重复/遮挡、主体损坏和明显构图/风格偏差。'''
REVIEWER+='''
当原始裁片本身就截掉主体边缘，而完整源图中确实存在缺失部分时，不要反复调整prepared_crop。
可在source_retakes请求一次本地重新取材：asset_id保持subject_N，source_crop是source_retake_manifest指定完整source_image_id中的归一化[左,上,右,下]，reason说明截断处。
先查看对应完整源图，不把已裁片的坐标当完整源图坐标；保留整个主体及必要底座，避免切掉文字或夹带半截字。不能申请新的主体、改source或重画素材。
程序会先重取真实素材，再让唯一一次视觉返工看到新素材进行排版。后续复查仍有问题则报告不通过，不再追加取材/返工。
只有原始裁片确实缺损才请求重取；若只是抠图缺损、缩放或排版问题，应使用现有prepared_variant/box等修复。'''


MOTION_REVIEWER=REVIEWER+'''
本次专门检查已验收海报的动画：poster 是完整基准，keyframes/early/middle/end 是分时截图。
请结合 motion_schedule、frame_times 和字幕判断。按时序尚未入场或正常退出的元素不是遗漏；
不能要求每张早期截图都有完整海报。只报告已到完整呈现时间却缺失、异常截断/重叠、
图文入场脱节、与叙述顺序不符或阅读时间不足的问题。不要在这一步重新设计海报或要求增加内容。'''


def asset_specs(assets):
    specs={}
    for name,path in assets.items():
        with Image.open(path) as im:
            rgba=im.convert('RGBA');alpha=rgba.getchannel('A')
            specs[name]={'width':im.width,'height':im.height,'visible_box_pixels':alpha.getbbox(),
                         'has_transparency':alpha.getextrema()[0]<255}
    return specs


def subject_variants(inventory, assets):
    """Explicit original/cutout provenance; no model-chosen filesystem paths."""
    result=[]
    for index,subject in enumerate(inventory.subjects):
        name=f'subject_{index}'
        original=name+'_original'
        entry={'asset_id':name,'source_id':subject.source,
               'coordinate_space':name+' prepared image: [0,0,1,1] is the whole supplied material, not the core poster',
               'default_variant':'cutout','variants':{'cutout':name}}
        if original in assets:
            with Image.open(assets[name]) as prepared, Image.open(assets[original]) as source:
                if prepared.size!=source.size:
                    raise ValueError(f'{name} 原裁片与抠图坐标范围不一致，不能安全替换素材')
            entry['variants']['original']=original
        result.append(entry)
    return result


def inventory_manifest(inventory, notes=None):
    """Retain original text/decor anchors without exposing legacy font defaults.

    Subject source/prepared/target geometry is separately typed and verified in
    source_geometry. Reference-source crops are never treated as core layout.
    """
    return {'background_color':inventory.background_color,'background_notes':inventory.background_notes,
            'background_retained':[{'id':f'base_{i}',**item.model_dump(),'coordinate_space':'core',
                                    'independent_animation':False}
                                   for i,item in enumerate(getattr(inventory,'background_retained',[]))],
            'subjects':[{'asset_id':f'subject_{i}','source_id':s.source,
                         'embedded_text':list(s.embedded_text)} for i,s in enumerate(inventory.subjects)],
            'labels':[{'text':label.text,'source_box':list(label.box),'coordinate_space':'core'}
                      for label in inventory.labels if label.text.strip()],
            'decorations':list((notes or {}).get('decorations',[]))}


def _request(path,folder,phase,schema,system,payload,images,config,event,validate=None,normalize=None):
    """One format repair; transport errors never trigger another request."""
    feedback={}
    for attempt in range(2):
        if event.is_set():raise InterruptedError('已停止本镜分层制作')
        api._update(path,stage=phase,attempt=attempt,
                    message=STAGES[phase]+('' if not attempt else ' · 第 1 轮格式修正'))
        raw=api._call(path,config,system,{**payload,'schema':schema.model_json_schema(),**feedback},images,
                      event.is_set,max_tokens=12000,thinking=False)
        if event.is_set():raise InterruptedError('已停止本镜分层制作')
        api._write(folder/f'{phase}_response_{attempt}.json',raw)
        try:
            value=schema.model_validate(raw)
            if normalize:value=normalize(value)
            if validate:validate(value)
            return value
        except ValueError as exc:
            if attempt:raise ValueError(f'{STAGES[phase]}格式修正仍未通过：{exc}') from exc
            feedback={'previous_response':raw,'validation_errors':str(exc)[:5000],
                      'repair_instruction':'仅修正错误定位指出的字段，返回完整JSON。文字守恒错误只修文字或装饰声明，不要为此改主体crop、位置或删除原文。装饰箭头使用polyline；不要擅自改已正确素材。'}


STAGES={'assembly':'按实际素材复原海报','poster_review':'对照原图检查海报',
        'visual_repair':'第 1 次视觉返工（最多 1 次）','poster_recheck':'返工后复查海报',
        'motion':'按最终配音编排分组动效','motion_review':'检查动效关键帧'}


def finish(project,sid,job_id,inventory,context,sources,assets,config,data,event,audio,seek):
    path=api._job(project,job_id);folder=path.parent
    def check():
        if event.is_set():raise InterruptedError('已停止本镜分层制作')
    # Bound source pixels and IDs stay immutable. One reviewed local re-take
    # may create new prepared files before the single visual repair.
    pictures={'core':sources['core'],**assets}
    variants=subject_variants(inventory,assets)
    specs=asset_specs(assets)
    manifest=inventory_manifest(inventory,context.get('inventory_notes'))
    geometry=geometry_manifest(inventory,sources,assets,canvas=context['canvas'])
    api._write(folder/'source_geometry.json',geometry)
    payload={**context,'inventory':manifest,'source_geometry':geometry,
             'material_specs':specs,'subject_variants':variants,
             'instruction':data.instruction,'pace':data.pace,
             'policy':'先完整复原静态海报。core取材且锚点可信时优先placement=source_anchor，由程序精确恢复位置和大小。prepared_crop仅表示当前裁片自身局部修边，不是core坐标；独立参考图使用manual放置。保留文字与装饰的原图定位依据，不隐藏元素或用极小字号掩盖问题。'}
    def validate(assembly):
        validate_content(assembly,inventory)
        # Validate geometry before spending a browser render. Aliases are only
        # used for validation; actual materialization below remains immutable.
        aliases={**assets,**{f'placed_subject_{i}':resolve_subject_asset(s,assets)
                            for i,s in enumerate(assembly.subjects)}}
        compile_assembly(assembly,context,aliases)
        for index,subject in enumerate(assembly.subjects):
            from .vibe_layered import bounds
            label=f'subjects[{index}].prepared_crop ({subject.asset_id})'
            left,top,right,bottom=bounds(subject.prepared_crop,label)
            with Image.open(resolve_subject_asset(subject,assets)) as im:
                crop=(round(left*im.width),round(top*im.height),round(right*im.width),round(bottom*im.height))
                if crop[0]>=crop[2] or crop[1]>=crop[3] or im.convert('RGBA').crop(crop).getchannel('A').getbbox() is None:
                    raise ValueError(f'{label} 裁片为空或完全透明：当前素材 {im.width}x{im.height}px，选择窗口 {crop}。'
                                     '坐标针对当前素材自身，默认[0,0,1,1]，不要重复使用core原图位置坐标。')
    def validate_review(review):
        verify_recovered_text(review,assembly)
        validate_retakes(review.source_retakes,inventory,sources)
    def normalize(assembly):
        return resolve_source_anchors(assembly,geometry)
    assembly=_request(path,folder,'assembly',Assembly,ASSEMBLY_DIRECTOR,payload,pictures,config,event,validate,normalize)
    reviews=[];quality='passed';issues=[];retake_audit=[]
    for visual_attempt in range(2):
        check()
        mat_dir=folder/f'assembly-assets-{visual_attempt}';mat_dir.mkdir()
        placed=materialize_subjects(assembly,assets,mat_dir)
        plan=compile_assembly(assembly,context,placed)
        api._write(folder/f'assembly_{visual_attempt}.json',assembly.model_dump())
        api._write(folder/f'assembly_plan_{visual_attempt}.json',plan)
        output=folder/f'poster-{visual_attempt}';output.mkdir()
        api._update(path,stage='poster_render',message='渲染完整海报，准备与原图对照')
        while not api._render_lock.acquire(timeout=.25):check()
        layout_error=None;poster=None
        try:
            poster=renderer.render_layered_still(static_plan(plan),context['canvas'],placed,output,cancelled=event.is_set)
        except (RuntimeError,ValueError) as exc:
            if not any(code in str(exc) for code in ('OCV_LAYOUT_OVERFLOW:','OCV_LAYOUT_OVERLAP:')):raise
            # A measured local overflow is actionable visual feedback, not a
            # reason to bypass the single rework or to submit another image.
            layout_error=str(exc)[:1600]
        finally:api._render_lock.release()
        phase='poster_review' if not visual_attempt else 'poster_recheck'
        if layout_error:
            review=Review(passed=False,summary='浏览器实测文字溢出或互相遮挡，需要修复排版',issues=[Issue(
                kind='readability',target='layout_validator',explanation=layout_error,
                correction='仅修正指出的文字层，保留原文，调整相邻字框间距、字号或换行；不能截字、隐藏文字或移动无关主体。')])
        else:
            review=_request(path,folder,phase,Review,REVIEWER,
                {'inventory':manifest,'assembly':assembly.model_dump(),'comparison':'core 对照 poster',
                 'assembly_schema':Assembly.model_json_schema(),'material_specs':specs,'subject_variants':variants,
                 'source_geometry':geometry,
                 'source_retake_manifest':source_manifest(inventory,sources),
                 'source_retake_available':not bool(visual_attempt)},
                {**pictures,'poster':poster,**{'retake_source_'+name:source
                    for name,source in sources.items() if name!='core'}},config,event,
                validate_review)
        reviews.append(review.model_dump())
        api._write(folder/'visual_reviews.json',reviews)
        if review.passed:break
        if visual_attempt:
            if layout_error:raise ValueError('第 1 次视觉返工后排版仍存在文字溢出或遮挡：'+layout_error)
            quality='needs_review';issues=review.model_dump()['issues'];break
        if review.source_retakes:
            check()
            api._update(path,stage='materials',message='按海报核查结果重新取材；仅使用原始图片和本地抠图，不提交出图')
            inventory,assets,retake_audit=apply_retakes(review.source_retakes,inventory,sources,assets,
                folder/'material-retake-1',event.is_set)
            api._write(folder/'source_retake_audit.json',retake_audit)
            pictures={'core':sources['core'],**assets}
            variants=subject_variants(inventory,assets)
            specs=asset_specs(assets)
            manifest=inventory_manifest(inventory,context.get('inventory_notes'))
            geometry=geometry_manifest(inventory,sources,assets,canvas=context['canvas'])
            api._write(folder/'source_geometry_retake.json',geometry)
            payload.update(inventory=manifest,material_specs=specs,subject_variants=variants,source_geometry=geometry)
        # No additional provider request: the single repair sees the actual
        # updated pixels, never an imagined crop it has not inspected.
        assembly=_request(path,folder,'visual_repair',Assembly,REPAIR_DIRECTOR,
            {**payload,'previous_assembly':assembly.model_dump(),'visual_issues':review.model_dump()},
            {**pictures,**({'poster':poster} if poster else {})},config,event,validate,normalize)
    if quality=='passed':
        schedule=_request(path,folder,'motion',MotionSchedule,MOTION_DIRECTOR,
            {**context,'pace':data.pace,'instruction':data.instruction,'groups':plan_groups(plan),
             'assembly':assembly.model_dump(),'subject_variants':variants,
             'policy':'只改分组时间和动效，禁止改动已验收的画面排版或内容；原裁片若含原图背景，优先原位淡入淡出，避免位移或缩放暴露矩形接缝'},
            {'core':sources['core'],'poster':poster},config,event,
            lambda value:apply_motion(plan,value,context))
        api._write(folder/'motion_schedule.json',schedule.model_dump())
        animated=apply_motion(plan,schedule,context)
    else:
        # Retain an inspectable output but never label a failed comparison as
        # accepted. Avoid further paid motion calls when the poster is wrong.
        animated=static_plan(plan)
    check()
    output=folder/'render-v2';output.mkdir()
    api._update(path,stage='render',message='渲染本镜动效和核查帧，保留原配音')
    while not api._render_lock.acquire(timeout=.25):check()
    try:
        metrics=renderer.render_compiled(animated,context['canvas'],'ocv-remotion-layered-v1',placed,
            audio,seek,context['duration'],output,api.ffmpeg_binary(),300,event.is_set)
    finally:api._render_lock.release()
    if quality=='passed':
        final=_request(path,folder,'motion_review',Review,MOTION_REVIEWER,
            {'subtitles':context['subtitles'],'groups':plan_groups(animated),
             'motion_schedule':schedule.model_dump(),'frame_times':metrics.get('detail_times',{}),
             'review_scope':'静态海报已核查；检查实际阶段截图的可读性、遮挡和信息出现顺序，不另行改写设计'},
            {'core':sources['core'],'poster':poster,'keyframes':output/'proof.jpg',
             'early':output/'detail_early.png','middle':output/'detail_middle.png','end':output/'detail_end.png'},
            config,event)
        api._write(folder/'motion_review.json',final.model_dump())
        if not final.passed:quality='needs_review';issues=final.model_dump()['issues']
    audit={'workflow_version':VERSION,'quality_status':quality,'quality_issues':issues,
           'source_geometry_version':geometry['version'],
           'placements':[{'asset_id':item.asset_id,'placement':item.placement,'box':list(item.box)} for item in assembly.subjects],
           'visual_repairs':len(reviews)-1,'static_reviews':reviews,
           'source_retakes':retake_audit,
           'subject_variants':[{'asset_id':item.asset_id,'prepared_variant':item.prepared_variant} for item in assembly.subjects],
           'recovered_text':[item.model_dump() for item in getattr(assembly,'recovered_text',[])]}
    api._write(folder/'quality.json',audit)
    metrics.update(quality_status=quality,visual_repairs=len(reviews)-1)
    check()
    return animated,placed,metrics,output,audit
