"""Shot-local material preparation and versioned poster reconstruction."""
from typing import Annotated, Literal
import re
import uuid

from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import vibe_cloud as api
from . import vibe_rmbg as rmbg
from .vibe_remotion import render_compiled
from .vibe_text_legibility import protect_text_layers
from .vibe_materials import resume_asset
from .vibe_layered_limits import MAX_LABELS, MAX_SUBJECTS, MAX_LAYERS

VERSION='ocv-layered-v1'
RENDERER='ocv-remotion-layered-v1'
MATERIAL_WORKFLOW_VERSION='material-aware-layered-v2'
CODE_WORKFLOW_VERSION='code-authored-layered-v3'
AGENT_WORKFLOW_VERSION='tool-agent-layered-v4'
WORKFLOW_VERSION=AGENT_WORKFLOW_VERSION
Unit=Annotated[float,Field(ge=0,le=1,allow_inf_nan=False)]
Box=tuple[Unit,Unit,Unit,Unit]
Color=Annotated[str,Field(pattern='^#[0-9A-Fa-f]{6}$')]


def workflow_requirements(workflow_version):
    """Keep persisted jobs on their original renderer, including unversioned jobs."""
    if workflow_version in (CODE_WORKFLOW_VERSION,AGENT_WORKFLOW_VERSION):
        return dict(required_renderer='ocv-remotion-code-v1',required_plan_version='ocv-code-program-v1')
    if workflow_version in (None,'',MATERIAL_WORKFLOW_VERSION):
        return dict(required_renderer=RENDERER,required_plan_version=VERSION)
    raise ValueError('不支持此原图分层任务的流程版本，请更新 OCV；未继续调用模型')


def preflight_workflow(workflow_version, *, cancelled=None):
    requirements=workflow_requirements(workflow_version)
    if workflow_version in (CODE_WORKFLOW_VERSION,AGENT_WORKFLOW_VERSION):
        from .vibe_code_renderer import preflight
        return preflight(cancelled=cancelled)
    options={'cancelled':cancelled} if cancelled is not None else {}
    return api.preflight_remotion(**requirements,**options)


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class Layer(Strict):
    box: Box
    at: float=Field(ge=0,le=60)
    enter: float=Field(default=.55,ge=.2,le=2)
    motion: Literal['fade','slide_left','slide_right','slide_up','scale']='fade'


class Subject(Layer):
    source: str
    crop: Box=(0,0,1,1)
    treatment: Literal['preserve','cutout']
    edge_fade_right: float=Field(default=0,ge=0,le=.25)
    embedded_text: list[Annotated[str,Field(min_length=1,max_length=180)]]=Field(default_factory=list,max_length=30)


class Plate(Strict):
    box: Box | None=None
    color: Color
    gradient_to: Color | None=None
    gradient_angle: float=Field(default=180,ge=0,le=360)
    stroke: Color | None=None
    stroke_width: float=Field(default=2,ge=0,le=12)
    radius: float=Field(default=12,ge=0,le=160)
    opacity: Unit=1
    glow: float=Field(default=0,ge=0,le=30)


class Label(Layer):
    text: str=Field(max_length=180,description='文字内容；独立无字底板填空字符串，并必须提供 plate。')
    color: Color | None=None
    font_size: float | None=Field(default=None,ge=12,le=140)
    align: Literal['left','center','right']='left'
    valign: Literal['top','middle','bottom']='middle'
    bold: bool=True
    line_height: float=Field(default=1.2,ge=1,le=1.8)
    gradient_to: Color | None=None
    plate: Color|Plate|None=None

    @model_validator(mode='after')
    def text_or_plate(self):
        if self.text.strip():
            if self.color is None or self.font_size is None:
                raise ValueError('文字图层必须提供 color 和 font_size')
        elif self.plate is None:
            raise ValueError('空文字图层必须提供 plate；不能创建既无文字也无底板的空图层')
        return self


class BaseRetainedSubject(Strict):
    name: str=Field(min_length=1,max_length=120)
    source_box: Box=Field(description='该主体在core原海报中的归一化范围；保留原位置，不是独立裁片。')
    reason: str=Field(min_length=8,max_length=600,
        description='为何保留在底图而不独立动画，例如半透明材质、透射颜色或与环境光难以无损分离；不能只说省步骤。')
    content_kind: Literal['illustration','decorative_object']=Field(
        description='仅非证据性的示意/装饰物体；真实照片、病理/实验/数据图不能用生成式底图保留，必须走原像素preserve。')

    @model_validator(mode='after')
    def valid_region(self):
        bounds(self.source_box,'background_retained.source_box')
        if not self.name.strip() or len(self.reason.strip())<8:
            raise ValueError('底图保留主体需要明确名称与具体原因')
        if (self.source_box[2]-self.source_box[0])*(self.source_box[3]-self.source_box[1])>.94:
            raise ValueError('不能把整张海报伪装为底图保留主体')
        return self


class Design(Strict):
    background: str
    background_color: Color
    background_notes: str=Field(default='',max_length=600)
    background_is_uniform: bool=False
    background_retained: list[BaseRetainedSubject]=Field(default_factory=list,max_length=MAX_SUBJECTS)
    subjects: list[Subject]=Field(max_length=MAX_SUBJECTS)
    labels: list[Label]=Field(max_length=MAX_LABELS)

    @model_validator(mode='before')
    @classmethod
    def remove_transport_marker(cls,value):
        # Compatible providers sometimes echo response_format's marker into
        # the answer. Only this exact top-level marker is transport metadata;
        # all other unknown/nested fields must still fail strict validation.
        if isinstance(value,dict) and value.get('type')=='json_object':
            return {key:item for key,item in value.items() if key!='type'}
        return value

    @model_validator(mode='after')
    def retained_requires_edit(self):
        if self.background_retained and self.background!='extract':
            raise ValueError('底图保留主体需要编辑core原图（background=extract），不能使用纯色或不含该主体的独立背景')
        return self


DIRECTOR="""你是原图分层动效导演。读取 core 海报、仅本镜绑定的参考图与最终字幕。只返回符合 schema 的 JSON。
顶层仅允许 background、background_color、background_notes、background_is_uniform、subjects、labels；不要输出 type、response_format 等接口传输配置。
复现原图构图、主体相对大小、层叠、色调、无需动画的装饰、原生文字与文字背板，不另做卡片模板。
background 填干净独立背景的素材ID；仅原图确为均匀纯色、没有渐变纹理装饰时填 solid 并设 background_is_uniform=true。
渐变、网格、粒子、建筑轮廓、分子图案、光带都属于需要保留的背景；没有独立背景文件时填 extract，绝不因此声称素材缺失。
allow_background_extraction 表示用户是否授权一次背景提取，不能为了省调用把复杂背景改为纯色。background_color 只是备用色，不代表背景细节。
background_notes 指明重建时保留哪些背景细节，不包含主体、证据图、文字。不可用 core 整图作背景。
subjects 按后到前的图层顺序，优先绑定实际独立源图；crop 为源图上的归一化 [左,上,右,下]；
本次首先作为素材拆解清单使用，实际抠图后会再次排版。不要把字和图混在同一个裁片里：应完整包含无字主体（含阴影/底座），排除下面的名称标签与文本框。无字区域无法分离时明确列出 embedded_text 并保留完整文字，不要截成半个字后声称没有字。
逐区清点整张原图的可见文字：顶部主标题、小标题、数字与单位、组名、图内标注、底部说明都应进入 labels 或 embedded_text。它们不是口播字幕，不得因与口播不同而省略；本阶段只是素材清单，不需要提前简化海报。
独立前景、医院照片、原始数据图用 preserve，照片原始像素不重画；需抠出独立物件/人物时用 cutout。
无独立参考也可从 core 局部裁取，不能拿整张海报充当前景；证据不能分离时只返回 {"missing":"具体需要补充什么"}。
所有 box 都是最终画布的归一化 [左,上,右,下]，不是宽高。人物侧缘融合可填 edge_fade_right。
每个 box/crop 必须 0<=左<右<=1 且 0<=上<下<=1。居中通过 align/valign 实现，不能把左右坐标都填0.5。
labels 的 box 是文字自身的区域，不是整张卡片；按原图位置、原有换行(用\\n)、字号（canvas像素）、字体粗细和文字颜色填写。
原图有底框时 plate 填对象，box 单独填写整个底框的范围，color/gradient_to/gradient_angle、stroke/stroke_width、radius、opacity、glow 还原原图。
独立底板也放在 labels 中：text 填 ""，必须提供 plate，可省略文字专用的 color/font_size；只渲染底板，不生成空文字。多个文字共用底板时只建一个独立底板，并放在对应文字之前，按 labels 顺序从后向前叠放；底板与文字可有各自的 at/enter/motion。
文字水平 align、垂直 valign(top/middle/bottom) 与原图一致，渐变文字可填 gradient_to。不要把文字一律贴到卡片顶部，也不要把长机构名挤成一行。
每个 subjects.embedded_text 必须逐项列出其裁取区域保留的可见文字（没有文字填[]）。不要同时裁取含文字的整块卡片又在其上叠一份同样的 labels。
有两种合法选择：只裁取无字物件/图标，文字与底框由 labels/plate 重建；或保真保留整张带字卡片、作为单层入场，并省略卡片中已包含的 labels。无法安全分离原始数据图时优先整块保真，不重复盖字、不遮挡证据。
字号不能直接沿用原图像素，需按输入 canvas 与原图的比例换算；核算中文每字约一个字号宽，行高=字号*line_height，完整文本应放得下。不要添加或删减文案。
at,enter 单位为本镜绝对秒数，贴合所给字幕，留足阅读时间，每层入场后保持至镜末。"""


def bounds(b,label='分层区域'):
    x,y,r,d=b
    if not x<r or not y<d:
        raise ValueError(f'{label}={list(b)} 为空或坐标倒置；须为 [左,上,右,下]，左<右、上<下，不是宽高或中心坐标')
    return list(b)


def resolve_background(design):
    """Core is an extraction input, never a ready-made clean background."""
    decorated=any(re.search(r'渐变|纹理|粒子|网格|网络|分子|建筑|光带|光晕|波纹|gradient|texture|particle|grid',part,re.I)
                  for part in re.split(r'[。；;，,]',design.background_notes)
                  if not re.search(r'不含|没有|无任何|without|\bno\b',part,re.I))
    if design.background=='core' or (design.background=='solid' and
            (not design.background_is_uniform or decorated)):
        return design.model_copy(update={'background':'extract'})
    return design


def compile_plan(design,context,sources):
    dur=context['duration'];w,h=context['canvas'];bg=design.background
    if bg not in {'solid','extract'} and (bg=='core' or bg not in sources):
        raise ValueError('缺少独立的干净背景；不可将核心海报整图充当背景')
    if not design.subjects and not design.labels:
        raise ValueError('没有可展示的主体或文字')
    # The model inventories text already baked into raster subjects. A label
    # placed over the same card must not render that content a second time.
    normalize=lambda text:re.sub(r'[\s，,。:：；;（）()]+','',text).casefold()
    for si,subject in enumerate(design.subjects):
        sx,sy,sr,sb=bounds(subject.box,f'subjects[{si}].box')
        for li,label in enumerate(design.labels):
            if not label.text.strip():continue
            x,y,r,b=bounds(label.box,f'labels[{li}].box')
            overlap=max(0,min(sr,r)-max(sx,x))*max(0,min(sb,b)-max(sy,y))
            if overlap<=.2*(r-x)*(b-y):continue
            text=normalize(label.text)
            if any(len(normalize(t))>=2 and (normalize(t) in text or text in normalize(t))
                   for t in subject.embedded_text):
                raise ValueError(f'labels[{li}] 与 subjects[{si}].embedded_text 重复叠字；请保留原素材文字并省略重复标签，或裁取无字主体后再重建文字')
    elements=[dict(type='image',id='background',asset_id='background',role='background',
                   box=[0,0,w,h],at=0,end=dur,enter=0,exit=0,motion='none',fit='cover')]
    for i,layer in enumerate([*design.subjects,*design.labels]):
        name=f'subjects[{i}]' if isinstance(layer,Subject) else f'labels[{i-len(design.subjects)}]'
        x,y,r,d=bounds(layer.box,name+'.box')
        box=[round(x*w),round(y*h),round(r*w)-round(x*w),round(d*h)-round(y*h)]
        if min(box[2:])<1 or layer.at+layer.enter+.1>dur:
            raise ValueError('分层方案的尺寸或入场时间超出本镜范围')
        common=dict(box=box,at=layer.at,end=dur,enter=layer.enter,exit=0,motion=layer.motion)
        if isinstance(layer,Subject):
            a,b,c,e=bounds(layer.crop,name+'.crop')
            if layer.source not in sources or (layer.source=='core' and (c-a)*(e-b)>.94 and layer.treatment=='preserve'):
                raise ValueError('主体未绑定本镜素材或试图以整张海报作前景')
            name=f'subject_{i}'
            elements.append(dict(type='image',id=name,asset_id=name,fit='contain',edge_fade_right=layer.edge_fade_right,**common))
        else:
            if layer.plate:
                style=layer.plate if isinstance(layer.plate,Plate) else Plate(color=layer.plate,stroke_width=0)
                plate_common={**common}
                if style.box:
                    a,b,c,e=bounds(style.box,name+'.plate.box')
                    plate_common['box']=[round(a*w),round(b*h),round(c*w)-round(a*w),round(e*h)-round(b*h)]
                    if min(plate_common['box'][2:])<1:raise ValueError(name+'.plate.box 太小')
                plate=dict(type='rect',id=f'plate_{i}',fill=style.color,radius=style.radius,
                           stroke_width=style.stroke_width,opacity=style.opacity,**plate_common)
                if style.stroke:plate['stroke']=style.stroke
                if style.glow:plate['glow']=style.glow
                if style.gradient_to:plate['gradient']={'color':style.gradient_to,'angle':style.gradient_angle}
                elements.append(plate)
            if not layer.text.strip():continue
            text=dict(type='text',id=f'label_{i}',text=layer.text,fill=layer.color,bold=layer.bold,
                      font_size=layer.font_size,min_font_size=min(layer.font_size,max(16,round(layer.font_size*.6))),
                      align=layer.align,valign=layer.valign,line_height=layer.line_height,**common)
            if layer.gradient_to:text['gradient']={'color':layer.gradient_to,'angle':180}
            elements.append(text)
    if len(elements)>MAX_LAYERS:
        raise ValueError(f'图层超过本地渲染上限 {MAX_LAYERS}；未截断或省略内容')
    return dict(version=VERSION,canvas=[w,h],background=design.background_color,elements=elements)


def _design(path,folder,context,sources,config,data,event,check):
    api._update(path,status='running',stage='design',message='正在核对原图、参考素材和字幕，编排图层')
    feedback={}
    for attempt in range(3):
        check()
        round_label='初次编排' if not attempt else f'第 {attempt} 轮修正（最多 2 轮）'
        api._update(path,attempt=attempt,message=f'【{round_label}】正在核对原图并设计图层' if not attempt else f'【{round_label}】正在按具体校验问题修正原图分层方案')
        raw=api._call(path,config,DIRECTOR,{**context,'instruction':data.instruction,'pace':data.pace,
                      'allow_background_extraction':bool(data.max_image_requests),
                      'schema':Design.model_json_schema(),**feedback},sources,event.is_set,max_tokens=14000)
        check()
        api._write(folder/f'design_response_{attempt}.json',raw)
        api._write(folder/'design_response.json',raw)
        if isinstance(raw,dict) and raw.get('missing'):
            raise ValueError('需要补充本镜素材：'+str(raw['missing'])[:500])
        try:
            design=resolve_background(Design.model_validate(raw))
            return design,compile_plan(design,context,sources)
        except ValueError as exc:
            if attempt==2:raise ValueError(f'【编排动效方案 · 第 2 轮修正】分层方案已修正两轮仍未通过：{exc}') from exc
            feedback={'previous_design':raw,'validation_errors':str(exc)[:3000],
                      'repair_instruction':'只修复指出的字段，重新查看原图位置与大小；不交换坐标猜测、不省略元素、不改写文字，不把装饰背景换成纯色。返回完整JSON。'}


def _inventory_design(path,folder,context,sources,config,data,event,check):
    """V2 first inventories pixels/content; it does not design layout or time."""
    from .vibe_layered_inventory import Inventory, DIRECTOR as INVENTORY_DIRECTOR
    source_sizes={}
    for name,source in sources.items():
        with Image.open(source) as im:source_sizes[name]=[im.width,im.height]
    payload={'canvas':context['canvas'],'subtitles':context['subtitles'],
             'asset_ids':list(sources),'source_sizes':source_sizes,
             'instruction':data.instruction,'pace':data.pace,
             'allow_background_extraction':bool(data.max_image_requests),
             'schema':Inventory.model_json_schema()}
    feedback={}
    for attempt in range(2):
        check()
        api._update(path,status='running',stage='design',attempt=attempt,
                    message='清点原图背景、前景主体、文字和装饰'+('' if not attempt else ' · 第 1 轮清单修正'))
        raw=api._call(path,config,INVENTORY_DIRECTOR,{**payload,**feedback},sources,
                      event.is_set,max_tokens=9000,thinking=False)
        check()
        api._write(folder/f'inventory_response_{attempt}.json',raw)
        try:
            inventory=Inventory.model_validate(raw)
            design=resolve_background(inventory.to_design(context,sources))
            api._write(folder/'inventory.json',inventory.model_dump())
            # Keep explicitly named source-space notes for reconstruction;
            # these are not prepared-image crop coordinates or final layout.
            context['inventory_notes']=_inventory_notes(inventory)
            # This adapter contains source-space placeholders, not a layout.
            # In particular, a reference crop cannot be compared with a core
            # text location by the legacy overlap/layout validator.
            return design,None
        except ValueError as exc:
            if attempt:raise ValueError(f'原图取材清单第 1 轮修正后仍未通过：{exc}') from exc
            feedback={'previous_inventory':raw,'validation_errors':str(exc)[:3500],
                      'repair_instruction':'只修正指出的清单字段；完整盘点可见主体与文字，不编排字号、画布布局或时间。禁止漏掉独立图标/物件，纯装饰不能充当主体。返回完整JSON。'}


def _inventory_notes(inventory):
    return {
        'foreground_summary':inventory.foreground_summary.model_dump(),
        'subjects':[{'id':f'subject_{i}','name':item.name,'role':item.role,'evidence_note':item.evidence_note}
                    for i,item in enumerate(inventory.subjects)],
        'background_retained':[{'id':f'base_{i}',**item.model_dump(),'coordinate_space':'core',
                                'independent_animation':False}
                               for i,item in enumerate(inventory.background_retained)],
        'decorations':[{'kind':item.kind,'description':item.description,
                        'source_box':list(item.source_box),'coordinate_space':'core'}
                       for item in inventory.decorations]}


def recoverable_material(project,task,user):
    """Check only durable, user-owned task metadata. Never contact a provider."""
    if task.get('workflow_version')==AGENT_WORKFLOW_VERSION:
        from .vibe_agent_workflow import recoverable_material as agent_recoverable
        return agent_recoverable(project,task,user)
    if task.get('template_mode')!='layered' or task.get('stage')!='materials' or task.get('status') not in {'failed','stopped'}:
        return False
    ids=(task.get('usage') or {}).get('image_task_ids',[])
    if len(ids)!=1 or not (api._job(project,task['task_id']).parent/'layout.json').is_file():return False
    from . import image_studio as studio
    try:
        record=api.previews._read_json(studio.folder(int(user),ids[0])/'record.json',128*1024)
        terminal={'FAILED','FAILURE','ERROR','CANCELLED','CANCELED','REJECTED','BLOCKED','ABORTED','TERMINATED','TIMEOUT','TIMED_OUT','EXPIRED'}
        return bool(record.get('status')=='completed' or record.get('remote_task_id') and record.get('remote_status') not in terminal)
    except (OSError,ValueError,HTTPException):return False


def _background_file(project,folder,source):
    """Only read bounded, real image files inside their owning cloud task."""
    from pathlib import Path
    from .vibe_materials import MAX_BYTES, MAX_PIXELS
    folder=api.previews._safe(project,Path(folder))
    source=api.previews._safe(project,Path(source),file=True)
    if not source.is_relative_to(folder) or source==folder:
        raise ValueError('背景素材不属于原任务')
    if source.stat().st_size>MAX_BYTES:
        raise ValueError('背景素材文件过大')
    with Image.open(source) as image:
        if image.width*image.height>MAX_PIXELS or getattr(image,'n_frames',1)!=1:
            raise ValueError('背景素材不是有效的单张图片')
        image.verify()
    return source,api.previews._stream_hash(source,MAX_BYTES)


BACKGROUND_RECIPE_VERSION='ocv-background-removal-v2'
STABLE_BACKGROUND_RECIPE_VERSION='ocv-background-stable-removal-v1'


def _background_retained_spec(design):
    """Stable image-edit meaning; free-form design rationale stays in audit."""
    retained=[]
    for row in getattr(design,'background_retained',[]):
        value=row.model_dump(mode='json') if hasattr(row,'model_dump') else dict(row)
        retained.append({key:value[key] for key in ('name','source_box','content_kind')})
    return retained


def _background_edit_request(value):
    """Validate the bounded, declarative edit intent before hashing/submission.

    Regions describe target pixels in the complete core poster, never a mask
    granting permission to erase all content inside a rectangular crop.
    """
    import math
    if not isinstance(value,dict) or set(value)-{'prompt','remove_regions','preserve_details'}:
        raise ValueError('背景编辑要求仅支持 prompt、remove_regions、preserve_details')
    prompt=value.get('prompt','')
    if not isinstance(prompt,str) or len(prompt)>2000:
        raise ValueError('背景编辑补充要求必须是最多2000字的文本')
    regions=value.get('remove_regions',[])
    details=value.get('preserve_details',[])
    if not isinstance(regions,list) or len(regions)>24:
        raise ValueError('背景定向移除区域必须是最多24项的列表')
    if not isinstance(details,list) or len(details)>24:
        raise ValueError('背景保留细节必须是最多24项的列表')
    normalized=[]
    for row in regions:
        if not isinstance(row,dict) or set(row)!={'description','region'}:
            raise ValueError('每个背景移除区域需要 description 和 region')
        description=row['description']
        if not isinstance(description,str) or not description.strip() or len(description)>600:
            raise ValueError('背景移除区域需要明确说明要删除的元素，最多600字')
        region=row['region']
        if (not isinstance(region,(tuple,list)) or len(region)!=4
                or any(type(c) not in (int,float) or not math.isfinite(c) or c<0 or c>1 for c in region)):
            raise ValueError('背景移除区域必须是归一化[左,上,右,下]数值')
        bounds(region,'edit_background.remove_regions.region')
        normalized.append(dict(description=description.strip(),region=list(region)))
    if any(not isinstance(detail,str) or not detail.strip() or len(detail)>600 for detail in details):
        raise ValueError('背景保留细节必须是非空文本，每项最多600字')
    return dict(prompt=prompt.strip(),remove_regions=normalized,
                preserve_details=[detail.strip() for detail in details])


def _background_recipe(design,notes=None,*,advanced_subject_separation=True,edit_request=None):
    """Describe a deletion-only edit, including every retained base subject.

    A recipe is a request/provenance contract, not a claim that the generated
    image preserved pixels. The later visual comparison still checks that.
    """
    import hashlib
    import json
    if type(advanced_subject_separation) is not bool:
        raise ValueError('高级主体分离选项须为布尔值')
    # Rewording why the route was chosen must not spend another image request
    # for the exact same base retention plan.
    retained=_background_retained_spec(design)
    named={row.get('id'):row.get('name') for row in (notes or {}).get('subjects',[]) if isinstance(row,dict)}
    removals=[]
    for index,row in enumerate(design.subjects):
        removal={'id':f'subject_{index}','source':row.source}
        if named.get(f'subject_{index}'):removal['name']=named[f'subject_{index}']
        if row.source=='core':removal['core_region']=list(row.crop)
        else:removal['note']='来自独立参考图，crop不是海报坐标；仅移除core中对应的前景主体，不能按参考图裁区擦除海报'
        removals.append(removal)
    texts=[{'text':row.text,'core_region':list(row.box)} for row in design.labels if row.text.strip()]
    prompt=('请直接编辑给定原海报，执行定点删除与原位补全，不要重新生成或重新设计一张背景。'
        '保持原画幅、构图、底色、渐变、纹理、建筑远景、分子/网络图案、粒子、光带、地面反射和融入背景的小装饰；'
        '不简化成纯色。除指定移除区域外，尽量保持原像素外观和全图色温、亮度、曝光、透视及光照。\n'
        '移除：海报中的文字、数字、标注文字及承载这些文字的前景文本框/卡片底板；'
        '只移除下列已选定需要后续独立编排的前景，人物或物件是否移除由该清单决定。'
        '在删去的位置按周围原背景自然补全，保留区域与修补区域应连续融合。\n'
        '需移除的独立前景清单：'+json.dumps(removals,ensure_ascii=False,separators=(',',':'))+'\n'
        '原图文字定位清单（坐标是原海报归一化左上右下，只作定位，不按整个包围框硬擦）：'
        +json.dumps(texts,ensure_ascii=False,separators=(',',':'))+'\n'
        '必须留在底图的主体清单：'+json.dumps(retained,ensure_ascii=False,separators=(',',':'))+'\n'
        '保留清单中的主体有意与底图融为一体，保持原位和原有外观；'
        '保持完整轮廓、大小、位置、材质、透明/半透明透射、反光、阴影及其与环境的连续关系。'
        '不要给它们另垫矩形色块，也不要复制第二份。清单为空时不擅自把独立主体留下。'
        '保留区域附近有字时只清除文字和其前景底板，不能擦掉主体边缘；主体不是原始照片或研究证据。'
        '不生成或重画证据/数据。去除文字后应是一张可直接承载新文字层的无字底图。\n'
        '其他需要保留的背景细节：'+design.background_notes)
    body={'version':BACKGROUND_RECIPE_VERSION,'prompt':prompt,'retained':retained}
    if not advanced_subject_separation:
        # Preserve the historical advanced recipe byte-for-byte. Stable mode
        # has its own version/hash, so an old subject-erased base cannot match
        # a request that deliberately retains complex objects in the poster.
        subjects={row.get('id'):row for row in (notes or {}).get('subjects',[]) if isinstance(row,dict)}
        for index,removal in enumerate(removals):
            role=subjects.get(removal['id'],{}).get('role')
            if role in {'person','photo','evidence','text_panel','object'}:
                removal['role']=role
            removal['treatment']=design.subjects[index].treatment
            removal['embedded_text']=list(design.subjects[index].embedded_text)
        decorations=[{key:row[key] for key in ('kind','description','source_box') if key in row}
                     for row in (notes or {}).get('decorations',[]) if isinstance(row,dict)]
        prompt=('请直接编辑给定原海报，执行定点删除与原位补全；这是原图编辑，不是重新生成背景。'
            '不得另造相似场景、重排版或改变主体构图，不能用重新生成背景替代原图编辑。\n'
            '采用稳定分层策略：复杂物体留底图，指定文字、文本底框、与文字相关的标注引线及明确登记的独立人物另层还原。'
            '保持原画幅、底色、渐变、纹理、建筑轮廓、分子/网络图案、粒子、光带、地面反射及背景小装饰；不简化成纯色。'
            '除明确删除的像素区域外，尽量保持原像素外观及全图色温、亮度、曝光、透视和光照。\n'
            '移除指定文字清单中的文字、数字、标注文字及承载这些文字的前景文本框/卡片底板，'
            '并移除与这些文字相连、后续要独立重建的标注引线。不要按文字或装饰的整个包围框硬擦。'
            '区分标注线与主体结构：不能误删血管、导管、输液管、器械轮廓或物体本身的边缘，背景光带也不是标注线。\n'
            '人物仅当出现在下列独立前景清单、且后续明确独立还原时才移除；不要因为看见人物就全部删除，'
            '清单外人物不得擅自移除。真实人物照片、医院照片、期刊封面及原始实验/病理/数据图由后续原图层保真还原，'
            '不生成重画、不改变证据内容或数据；仅清单中已指定独立还原者可从底图移除，未指定者不擅自改动。'
            'embedded_text 属于后续保真原图层，不单独改写或另造一套图中文字。\n'
            '需移除并由独立图层还原的前景清单：'+json.dumps(removals,ensure_ascii=False,separators=(',',':'))+'\n'
            '指定文字定位清单（坐标是core原海报归一化左上右下，只作定位）：'
            +json.dumps(texts,ensure_ascii=False,separators=(',',':'))+'\n'
            '需独立重建的文字底框/标注装饰线索（只移除与指定文字相关者，不把主体或背景纹理当作装饰删除）：'
            +json.dumps(decorations,ensure_ascii=False,separators=(',',':'))+'\n'
            '必须留在底图的主体清单：'+json.dumps(retained,ensure_ascii=False,separators=(',',':'))+'\n'
            '保留清单与未指明删除的器官、器械、输液袋等非证据性主体完整留在原位；保持完整轮廓、大小、位置、'
            '材质、透明/半透明透射、反光、阴影及其与环境的连续关系，不要另垫矩形色块或复制第二份。'
            '保留区域附近有文字时只清除字、文字底板及相关标注引线，不损伤主体边缘。'
            '删除区域按相邻原背景原位补全、自然融合；最终底图可承载独立文字与人物，不是把所有前景都清空。\n'
            '其他需要保留的背景细节：'+design.background_notes)
        body={'version':STABLE_BACKGROUND_RECIPE_VERSION,'advanced_subject_separation':False,
              'prompt':prompt,'retained':retained}
    if edit_request is not None:
        # Existing saved requests keep their original prompt/hash. The tool
        # path explicitly opts into this contract instead of appending a
        # deletion request to background_notes (the preservation paragraph).
        request=_background_edit_request(edit_request)
        suffix='其他需要保留的背景细节：'+design.background_notes
        if not prompt.endswith(suffix):
            raise ValueError('背景编辑保留段落缺失，未提交图片请求')
        prompt=prompt[:-len(suffix)]+(
            '【本次编辑要求】\n'
            '以下是明确的移除/修补任务，不是需要保留的背景细节。'
            '在不违反保留主体及证据保护约束的前提下，明确目标补充上文的默认删除范围；'
            '指定要代码重建的图形、图标、条带或装饰也应移除，不因它不是文字就留下。\n'
            +(request['prompt'] or '按清单和下列定位要求执行，不增加未指定的编辑。')+'\n'
            '【定向移除定位】\n'
            '坐标为完整core原海报归一化[左,上,右,下]，只定位description描述的目标像素。'
            '不是宽高、不是参考图或局部裁片坐标，也不是整块矩形硬擦蒙版。'
            '完整移除指定元素的填充、描边、渐变和残留边缘；不要只删字而留下对应图形。\n'
            +json.dumps(request['remove_regions'],ensure_ascii=False,separators=(',',':'))+'\n'
            '【原背景保留细节】\n'+design.background_notes+'\n'
            '【本次额外保留要求】\n'
            +json.dumps(request['preserve_details'],ensure_ascii=False,separators=(',',':'))+'\n'
            '【保护与修补边界】\n'
            '必须留在底图的主体和未指定删除的原始证据仍受保护。真实照片、实验/病理/原始数据证据'
            '不得生成式重画或更改事实；明确登记为独立保真还原的证据仅可整体移除后由原像素图层还原。'
            '如本次移除目标与保留主体或证据保护冲突，应先修订内容清单或编辑要求，不得默默覆盖保留规则。'
            '定位框内非目标主体和环境细节应保留；删除位置按周围原背景自然补全，使渐变、体积光、纹理'
            '与反射连续，不用纯色矩形、深色遮罩或硬边盖住残留，也不重新设计背景。'
            '此请求不代表编辑必然成功，实际结果仍须对照完整原图和局部检查。')
        body['prompt']=prompt
    digest=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    return {**body,'sha256':digest}


def _valid_background_recipe(value):
    import hashlib
    import json
    if not isinstance(value,dict) or value.get('version') not in (BACKGROUND_RECIPE_VERSION,STABLE_BACKGROUND_RECIPE_VERSION):
        return False
    if not isinstance(value.get('prompt'),str) or not isinstance(value.get('retained'),list):return False
    body={name:value[name] for name in ('version','prompt','retained')}
    if value['version']==STABLE_BACKGROUND_RECIPE_VERSION:
        if value.get('advanced_subject_separation') is not False:return False
        body['advanced_subject_separation']=False
    elif 'advanced_subject_separation' in value:
        # A policy label cannot retroactively relabel an old paid background.
        return False
    digest=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    return value.get('sha256')==digest


def _save_background_ready(project,job_id,source,sources,route,*,origin_task_id=None,image_task_id=None,recipe=None):
    """Persist a paid material independently of later design/render success."""
    path=api._job(project,job_id);task=api._read_job(project,job_id)
    key=route.get('key')
    if not key or key!=(task.get('image_route') or {}).get('key'):
        raise ValueError('背景素材渠道与任务记录不一致；不会跨渠道复用')
    source,digest=_background_file(project,path.parent,source)
    if recipe is not None and not _valid_background_recipe(recipe):
        raise ValueError('背景编辑配方记录无效，不能登记为可复用底图')
    ready={'version':2 if recipe is not None else 1,'kind':'extracted','task_id':job_id,'shot_id':task['shot_id'],
           'cloud_input_stamp':task['cloud_input_stamp'],'image_route_key':key,
           'source_file':source.relative_to(project).as_posix(),'sha256':digest,
           'core_sha256':api.previews._stream_hash(sources['core'],48*1024**2),
           'origin_task_id':origin_task_id or job_id,'image_task_id':image_task_id}
    if recipe is not None:ready.update(recipe=recipe,recipe_sha256=recipe['sha256'])
    api._write(path.parent/'background_ready.json',ready)
    return source


def _cached_background(project,task,sources,route,event,*,recipe=None):
    """Respect fresh-start isolation and immutable deletion/retention recipes.

    A fresh task may reuse its own completed edit after a continuation or a
    repeated tool choice, never any older task's generated background.
    """
    key=route.get('key')
    if not key or not _valid_background_recipe(recipe):return None
    core_hash=api.previews._stream_hash(sources['core'],48*1024**2)
    fresh=task.get('start_mode')=='fresh'
    candidates=[]
    paths=[api._job(project,task['task_id'])] if fresh else api._root(project).glob('*/task.json')
    for candidate in paths:
        try:candidates.append((candidate.stat().st_mtime,candidate))
        except OSError:continue
    for _,candidate in sorted(candidates,key=lambda item:item[0],reverse=True)[:32]:
        if event.is_set():raise InterruptedError('已停止原图分层；未提交新的背景请求')
        try:
            candidate=api.previews._safe(project,candidate,file=True)
            previous=api.previews._read_json(candidate,1024*1024)
            if ((previous.get('task_id')!=task['task_id'] if fresh else previous.get('task_id')==task['task_id'])
                    or candidate.parent.name!=previous.get('task_id')
                    or previous.get('template_mode')!='layered' or previous.get('shot_id')!=task['shot_id']
                    or previous.get('cloud_input_stamp')!=task['cloud_input_stamp']
                    or (previous.get('image_route') or {}).get('key')!=key):continue
            manifest=candidate.parent/'background_ready.json'
            if manifest.exists():
                ready=api.previews._read_json(api.previews._safe(project,manifest,file=True),64*1024)
                if (ready.get('version')!=2 or ready.get('kind')!='extracted'
                        or ready.get('task_id')!=previous['task_id'] or ready.get('shot_id')!=task['shot_id']
                        or ready.get('cloud_input_stamp')!=task['cloud_input_stamp']
                        or ready.get('image_route_key')!=key or ready.get('core_sha256')!=core_hash
                        or not _valid_background_recipe(ready.get('recipe'))
                        or ready.get('recipe_sha256')!=recipe['sha256']
                        or ready['recipe']['sha256']!=recipe['sha256']):continue
                source,digest=_background_file(project,candidate.parent,project/ready['source_file'])
                if digest!=ready.get('sha256'):continue
                return source,ready,previous['task_id']
            # Old backgrounds may have removed subjects that the new request
            # intentionally retains. They remain usable by their original
            # task, but cannot establish a matching deletion recipe here.
        except (OSError,ValueError,TypeError,KeyError,AttributeError,HTTPException,Image.DecompressionBombError):
            continue
    return None


def _background(project,job_id,design,context,sources,mats,config,event,resume,budget=1):
    path=api._job(project,job_id)
    if event.is_set():raise InterruptedError('已停止原图分层；未提交新的背景请求')
    if design.background=='solid':
        target=mats/'background.png'
        Image.new('RGB',tuple(context['canvas']),design.background_color).save(target)
        return target
    if design.background!='extract':return sources[design.background]
    task=api._read_job(project,job_id)
    route=config.get('layered_image_route') or task.get('image_route') or {}
    if route.get('key')!=(task.get('image_route') or {}).get('key'):
        raise ValueError('背景素材渠道与任务记录不一致；未提交图片请求')
    if resume:
        ids=task['usage']['image_task_ids']
        if len(ids)!=1:raise ValueError('原背景任务编号不完整，不会自动重新出图')
        receipt=path.parent/'background_request.json'
        recipe=None
        if receipt.exists():
            requested=api.previews._read_json(api.previews._safe(project,receipt,file=True),128*1024)
            recipe=requested.get('recipe')
            if (not _valid_background_recipe(recipe) or requested.get('image_task_id')!=ids[0]
                    or requested.get('image_route_key')!=route.get('key')
                    or requested.get('core_sha256')!=api.previews._stream_hash(sources['core'],48*1024**2)):
                raise ValueError('原背景请求配方或素材记录不一致；未重新提交出图，请核对原图片任务')
            if recipe['retained']!=_background_retained_spec(design):
                raise ValueError('原背景请求的保留主体与保存的分层方案不一致；未查询或重新提交图片任务，请恢复原方案或新建预览')
        elif _background_retained_spec(design):
            raise ValueError('旧背景请求没有保留主体记录，不能用于含底图保留主体的新方案；未查询或重新提交图片任务')
        api._update(path,message='继续查询原背景图片任务，不重复出图；沿用原请求的删除与保留规则')
        source=resume_asset(config['layered_owner_id'],ids[0],mats,'background',event.is_set)['path']
        # Old jobs without a route fingerprint stay usable, but must not become
        # cross-route caches. Their original studio job remains the authority.
        if not route.get('key'):return source
        return _save_background_ready(project,job_id,source,sources,route,image_task_id=ids[0],recipe=recipe)
    recipe=_background_recipe(design,context.get('inventory_notes'))
    cached=_cached_background(project,task,sources,route,event,recipe=recipe)
    if cached:
        import shutil
        source,ready,previous_id=cached
        target=api.previews._safe(project,mats/'background.png')
        # A fresh task can resume its own ready material. It is already in the
        # desired location in legacy workflows; do not copy a file onto itself.
        if source.resolve()!=target.resolve():
            with source.open('rb') as inp,target.open('xb') as out:
                shutil.copyfileobj(inp,out)
        _,digest=_background_file(project,path.parent,target)
        if digest!=ready['sha256']:raise ValueError('缓存背景在读取时发生变化；未提交新背景请求')
        _save_background_ready(project,job_id,target,sources,route,
            origin_task_id=ready.get('origin_task_id') or previous_id,image_task_id=ready.get('image_task_id'),recipe=recipe)
        api._update(path,background_reused_from=previous_id,message='复用同原图、同渠道且删除/保留规则一致的底图；不重复出图')
        return target
    if budget!=1:
        raise ValueError('原图有背景，但没有同输入、同渠道且删除/保留规则一致的可复用底图；请允许一次背景提取，未提交图片请求')
    if event.is_set():raise InterruptedError('已停止原图分层；未提交新的背景请求')
    def submitted(identity):
        api._write(path.parent/'background_request.json',{**request_receipt,'image_task_id':identity})
        usage=api._read_job(project,job_id)['usage']
        api._update(path,usage={**usage,'image_requests':1,'image_task_ids':[identity]},
            message='等待背景提取：'+route.get('label','本项目图像模型')+'；未自动切换渠道')
    request_receipt={'recipe':recipe,'image_task_id':None,'image_route_key':route.get('key'),
                     'core_sha256':api.previews._stream_hash(sources['core'],48*1024**2)}
    api._write(path.parent/'background_request.json',request_receipt)
    result=api.generate_asset(config['layered_owner_id'],recipe['prompt'],
        [sources['core']],mats,'background',event.is_set,on_submitted=submitted,background_canvas=context['canvas'],
        image_config=config.get('layered_image_config'),image_route=route)
    ids=api._read_job(project,job_id)['usage'].get('image_task_ids') or []
    return _save_background_ready(project,job_id,result['path'],sources,route,
        image_task_id=result.get('studio_task_id') or (ids[0] if len(ids)==1 else None),recipe=recipe)


def run_worker(project,sid,job_id,config,data,event,resume=False):
    path=api._job(project,job_id);folder=path.parent
    def check():
        if event.is_set():raise InterruptedError('已停止原图分层；已提交的请求可能仍计费')
    try:
        check()
        task=api._read_job(project,job_id)
        if task.get('workflow_version')==AGENT_WORKFLOW_VERSION:
            from .vibe_agent_workflow import run_worker as agent_worker
            return agent_worker(project,sid,job_id,config,data,event,resume=resume)
        snap,shot,context,sources=api._context(project,sid)
        stamp=api._cloud_stamp(snap,shot,context,sources)
        workflow_version=task.get('workflow_version')
        workflow_requirements(workflow_version)  # Reject unknown protocols before any paid work.
        material_workflow=workflow_version in (MATERIAL_WORKFLOW_VERSION,CODE_WORKFLOW_VERSION)
        if stamp!=task['cloud_input_stamp']:
            raise ValueError('本镜输入已变化，请重新开始')
        audio=folder/('narration-recovery.wav' if resume else 'narration.wav')
        digest,seek=api.previews._audio(snap[3],snap[4],snap[5],audio)
        if digest!=snap[2]:raise ValueError('最终配音已变动，请重试')
        if resume:
            design=resolve_background(Design.model_validate(api.previews._read_json(folder/'layout.json',256*1024)))
            plan=None if material_workflow else compile_plan(design,context,sources)
            if material_workflow and (folder/'inventory.json').is_file():
                from .vibe_layered_inventory import Inventory
                inventory=Inventory.model_validate(api.previews._read_json(folder/'inventory.json',256*1024))
                restored=inventory.to_design(context,sources)  # Recheck bound sources, not layout placeholders.
                if _background_retained_spec(restored)!=_background_retained_spec(design):
                    raise ValueError('恢复任务的素材清单与原背景保留方案不一致；请新建预览，未查询或重新提交图片任务')
                context['inventory_notes']=_inventory_notes(inventory)
            if design.background!='extract':raise ValueError('此方案没有可恢复的背景提取任务')
            api._update(path,status='running',stage='materials',message='复用素材清单，继续获取原背景任务；不重复提交出图，随后继续海报复原与核查' if api._read_job(project,job_id).get('workflow_version') else '复用原分层方案，继续获取原背景任务；不重新调用 DS 或提交出图')
        else:
            designer=_inventory_design if material_workflow else _design
            design,plan=designer(path,folder,context,sources,config,data,event,check)
        if design.background_retained and workflow_version!=CODE_WORKFLOW_VERSION:
            raise ValueError('底图保留主体需要当前代码分层流程，请新建预览；未提交图片请求')
        original_mats=folder/'materials'
        mats=folder/('materials-recovery-'+uuid.uuid4().hex if resume else 'materials');mats.mkdir()
        assets={}
        if not resume:api._write(folder/'layout.json',design.model_dump())
        api._update(path,stage='materials',message='先准备保留原图装饰的独立背景')
        assets['background']=_background(project,job_id,design,context,sources,mats,config,event,resume,budget=data.max_image_requests)
        api._update(path,stage='materials',message='正在提取本镜前景素材；照片和数据图仅使用原始像素')
        pending={}
        for i,s in enumerate(design.subjects):
            check()
            name=f'subject_{i}'
            cached_cutout=original_mats/(name+'-rgba.png')
            has_cutout=resume and s.treatment=='cutout' and cached_cutout.is_file()
            if has_cutout and not material_workflow:
                assets[name]=cached_cutout;continue
            cropped=(original_mats/f'crop_{i}.png' if resume and (original_mats/f'crop_{i}.png').is_file()
                     else api.crop_asset(sources,s.source,s.crop,mats,f'crop_{i}'))
            with Image.open(cropped) as im:
                alpha=im.getchannel('A').getextrema();crop_size=im.size
            if alpha[1]==0:raise ValueError('主体裁取范围完全透明，请调整素材')
            if material_workflow:
                # Preserve source pixels as a separate candidate. RMBG may
                # remove a meaningful part of a schematic; the assembly model
                # must be able to choose the original without another request.
                assets[name+'_original']=cropped
            if has_cutout:
                with Image.open(cached_cutout) as rgba:
                    if rgba.size!=crop_size:raise ValueError(f'{name} 缓存抠图与原始裁片尺寸不一致，未使用错位素材')
                assets[name]=cached_cutout;continue
            if s.treatment=='cutout' and alpha[0]==255:
                pending[name]=cropped
            assets[name]=cropped
        if pending:
            api._update(path,stage='materials',message=f'等待本机算力并使用独立 RMBG 提取 {len(pending)} 个主体；无需启动 ComfyUI')
            # Preserve the crop coordinate frame. Layout sees real dimensions
            # and alpha bounds, not an unpredictably tightened substitute.
            cutouts=rmbg.run_cutouts(pending,mats,should_stop=event.is_set,trim_padding=None)
            if material_workflow:
                for name,original in pending.items():
                    with Image.open(original) as raw,Image.open(cutouts[name]) as rgba:
                        if raw.size!=rgba.size:raise ValueError(f'{name} 抠图与原始裁片尺寸不一致，未使用错位素材')
            assets.update(cutouts)
        check()
        quality={}
        if material_workflow:
            if workflow_version==CODE_WORKFLOW_VERSION:
                from .vibe_code_workflow import finish
            else:
                from .vibe_layered_workflow import finish
            plan,assets,metrics,out,quality=finish(project,sid,job_id,design,context,sources,assets,config,data,event,audio,seek)
            _publish(project,sid,job_id,path,snap,stamp,plan,metrics,out,quality,check)
            return
        check()
        wrapper,audit=protect_text_layers(dict(plan=plan,assets={k:str(p) for k,p in assets.items()},duration=context['duration'],fps=30))
        api._write(folder/'text_legibility.json',audit)
        plan=wrapper['plan']
        api._update(path,stage='render',message='正在本机渲染原图分层动效，并合成本镜原配音')
        out=folder/('render-recovery-'+uuid.uuid4().hex if resume else 'render');out.mkdir()
        while not api._render_lock.acquire(timeout=.25):check()
        try:
            check()
            metrics=render_compiled(plan,context['canvas'],RENDERER,assets,audio,seek,context['duration'],
                                    out,api.ffmpeg_binary(),300,event.is_set)
        finally:api._render_lock.release()
        check()
        _publish(project,sid,job_id,path,snap,stamp,plan,metrics,out,quality,check)
    except Exception as exc:
        api._update(path,status='stopped' if event.is_set() else 'failed',
                    message=str(exc.detail if isinstance(exc,HTTPException) else exc)[:1400]+'；未采用或切换模式')
    finally:
        with api._lock:
            key=(str(project),sid)
            current=api._active.get(key)
            if current and current[0]==job_id:
                api._active.pop(key,None)


def _publish(project,sid,job_id,path,snap,stamp,plan,metrics,out,quality,check):
    if stamp!=api._cloud_stamp(*api._context(project,sid)):raise ValueError('本镜素材或配音已改变，未发布过期预览')
    task=api._read_job(project,job_id)
    workflow_version=task.get('workflow_version')
    expected_renderer=workflow_requirements(workflow_version)['required_renderer']
    actual_renderer=metrics.get('renderer') or RENDERER
    if actual_renderer!=expected_renderer:
        raise ValueError('原图分层渲染回执与任务流程版本不一致，未发布预览')
    quality=dict(quality)
    if workflow_version:
        if quality.get('workflow_version') not in (None,workflow_version):
            raise ValueError('原图分层质量回执与任务流程版本不一致，未发布预览')
        quality['workflow_version']=workflow_version
        quality.setdefault('quality_status','needs_review')
    identity=uuid.uuid4().hex;base=f'/api/video-studio/{project.name}/vibe-previews/{identity}/'
    receipt=dict(identity=project.name,shot_id=sid,preview_id=identity,input_stamp=snap[0],source_sha256=snap[1],
                 cloud_input_stamp=stamp,engine='cloud',renderer=actual_renderer,template_mode='layered',plan=plan,
                 advanced_subject_separation=task.get('advanced_subject_separation',True),
                 cloud_task_id=job_id,metrics=metrics,preview_url=base+'preview',silent_url=base+'silent',
                 proof_url=base+'proof',cached=False,may_charge=True,**quality)
    api._write(out/'receipt.json',receipt);store=api.previews._store(project);store.mkdir(parents=True,exist_ok=True)
    with api._lock:
        check();api.publish(out,store/identity);api.previews._publish_pointer(project,sid,identity)
        api._update(path,status='completed',stage='completed',preview=receipt,
            message='已保留待检查预览：视觉核查仍有问题，未通过验收、未采用' if quality.get('quality_status')=='needs_review' else '原图分层预览已就绪；尚未采用')
