"""Bounded code-authoring loop: measure, preview, inspect, rework, render.

No new image request, adoption, arbitrary code execution or provider fallback.
All text/asset checks are based on the renderer's actual DOM, not declarations.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import vibe_cloud as api
from . import vibe_code_renderer as renderer
from . import vibe_code_inspection as inspection
from . import vibe_code_material_review as material_review
from . import vibe_layered_retake as source_retake
from .vibe_layered_geometry import geometry_manifest
from .vibe_layered_workflow import asset_specs, inventory_manifest, subject_variants
from .vibe_layered_assembly import RecoveredText, validate_content
from .vibe_layered import CODE_WORKFLOW_VERSION as WORKFLOW_VERSION
from .vibe_cloud_client import ModelResponseFormatError

# The task/receipt protocol is shared with the router. Material review and
# inspection have their own versions; adding tools must not change only the
# producer's receipt version and strand otherwise completed persisted tasks.
VERSION = renderer.VERSION
RENDERER = renderer.RENDERER
MAX_CALLS = 8
MAX_PREVIEWS = 3
MAX_MEASURES = 2
MAX_MATERIAL_RETAKES = 1


class ResponseContractError(ValueError):
    """Returned model JSON is repairable; transport/runtime errors are not."""
    def __init__(self, message, response=None):
        super().__init__(message)
        self.response = response


class Probe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    text: str = Field(min_length=1, max_length=500)
    font_size: float = Field(ge=12, le=512)
    font_weight: int = Field(default=600, ge=100, le=900, multiple_of=100)
    font_family: Literal['sans', 'serif'] = 'sans'
    max_width: float | None = Field(default=None, ge=8, le=1920)


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'oneOf': [
        {'properties': {'action': {'const': 'measure'}, 'code': {'maxLength': 0},
                        'probes': {'minItems': 1}}, 'required': ['action', 'probes']},
        {'properties': {'action': {'const': 'preview'}, 'code': {'minLength': 1},
                        'probes': {'maxItems': 0}}, 'required': ['action', 'code']}]})
    action: Literal['measure', 'preview']
    summary: str = Field(min_length=1, max_length=1200)
    code: str = Field(default='', max_length=45000)
    probes: list[Probe] = Field(default_factory=list, max_length=24)
    embedded_text: dict[str, list[str]] = Field(default_factory=dict,
        description='实际Asset裁片中保留的文字，键为代码使用的准确素材id；不得申报被裁掉或不存在的文字。无字素材为[]。')
    recovered_text: list[RecoveredText] = Field(default_factory=list, max_length=20)

    @model_validator(mode='before')
    @classmethod
    def marker(cls, value):
        if isinstance(value, dict) and value.get('type') == 'json_object':
            return {k: v for k, v in value.items() if k != 'type'}
        return value

    @model_validator(mode='after')
    def bounded(self):
        if self.action == 'measure' and (not self.probes or self.code):
            raise ValueError('measure只填写probes；preview填写完整Scene代码')
        if self.action == 'preview' and (not self.code.strip() or self.probes):
            raise ValueError('preview需要完整Scene代码，probes须为空')
        if len({p.id for p in self.probes}) != len(self.probes):
            raise ValueError('测量id不能重复')
        if len(self.embedded_text) > 24 or any(len(v) > 20 or any(len(t) > 500 for t in v)
                                              for v in self.embedded_text.values()):
            raise ValueError('素材文字声明过大')
        return self


class SubjectCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: str = Field(min_length=1, max_length=80)
    complete: bool = Field(strict=True)
    evidence: str = Field(min_length=8, max_length=700,
        description='对照原裁片与实际透明效果，描述外围小主体、轮廓及底座是否保留，不得只写已检查。')


class LayoutCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text_id: str = Field(min_length=1, max_length=80)
    faithful: bool = Field(strict=True)
    evidence: str = Field(min_length=8, max_length=700,
        description='对照core局部与真实文字测量，核对字号层次、颜色、上下留白和基线；不是仅检查无重叠。')


class BackgroundCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_id: str = Field(pattern=r'^base_[0-7]$')
    preserved: bool = Field(strict=True,
        description='对照core与background，保留主体的轮廓、材质、颜色、位置是否可信保留；被改画或丢失为false。')
    compositing_ok: bool = Field(strict=True,
        description='对照background与poster，该主体是否未被重复绘制、遮盖、错位或出现拼贴接缝。')
    evidence: str = Field(min_length=8,max_length=900)


class CodeReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passed: bool = Field(strict=True)
    summary: str = Field(min_length=1, max_length=1000)
    issues: list[str] = Field(default_factory=list, max_length=12)
    embedded_text_verified: bool = Field(strict=True)
    recovered_text_verified: bool = Field(strict=True)
    subject_checks: list[SubjectCheck] = Field(default_factory=list, max_length=24)
    layout_checks: list[LayoutCheck] = Field(default_factory=list, max_length=80)
    background_checks: list[BackgroundCheck] = Field(default_factory=list,max_length=8)

    @model_validator(mode='before')
    @classmethod
    def marker(cls, value):
        if isinstance(value, dict) and value.get('type') == 'json_object':
            return {k: v for k, v in value.items() if k != 'type'}
        return value

    @model_validator(mode='after')
    def consistent(self):
        if self.passed and (self.issues or not self.embedded_text_verified or not self.recovered_text_verified
                or any(not row.complete for row in self.subject_checks)
                or any(not row.faithful for row in self.layout_checks)
                or any(not row.preserved or not row.compositing_ok for row in self.background_checks)):
            raise ValueError('存在未核实原文或问题时不能判定通过')
        if not self.passed and not self.issues:
            raise ValueError('不通过时必须说明可执行问题')
        return self


DIRECTOR = '''你是OCV原图分层动效设计师，也是受限JSX场景程序员。只返回符合schema的JSON。
你的工作不是套现成模板，也不是把元素都放入大方框；根据core原图、已经准备好的真实素材、最终字幕时间轴，自行写Scene代码。
先观察原图：颜色与装饰背景、主体完整性、标题实际字形宽度、字号层次、数字单位基线、文本底板与标注线。保持事实、主体和主要构图，允许用代码更好地组织细节。
可调用measure工具获得浏览器真实字宽/字高，建议先测量关键标题、数字单位；preview工具将编译你的代码，返回完整海报、实际关键帧及DOM测量。最多3次preview（初版+2轮返工），最多2次measure，总模型预算8次含素材核查和独立审图；以remaining实际余额为准。
每次preview返回完整代码，而不是diff。返工基于真实截图和测量，不能仅声称已修复。运输错误不会自动换渠道或重试。

代码格式：function Scene(ctx) { const w=ctx.width; const h=ctx.height; return <div style={{position:'absolute',inset:0}}>...</div>; }
支持const、有限数组/对象、算术比较与三元表达式、数组.map((item,index)=>JSX)、Math.min/max/abs/round/floor/ceil/sin/cos/PI，以及clamp(x,min,max)、lerp(a,b,t)、ease(t)。可以用const draw=(参数)=>JSX写纯局部辅助函数并在本Scene内调用，也支持map(function(item){return JSX;})。辅助函数不能递归、变更状态或作为事件函数传出。
支持String(value)转换null/布尔/有限数字/字符串，以及字符串.replace(search,replacement)用两个字符串参数替换首个匹配（不支持正则或回调）。不支持import/export、let/var、for/while循环、递归、赋值、其他任意方法调用、解构、new、浏览器/Node全局、网络、文件访问、HTML注入、事件处理。不要生成这些代码。
ctx提供width,height,duration,fps,frame,measurements,geometry。尺寸以输出画布像素为单位，可用w/h比例计算，不能把参考图坐标当输出像素。
可用标签div/span/svg/g/path/polyline/line/rect/circle/ellipse，及组件Text/Asset/Group。可用CSS flex、gap、alignItems:'baseline'、grid、定位、字体、渐变、边框、圆角、阴影；不允许url()、外部字体、滤镜引用、任意HTML。
所有可见文字必须用<Text id="唯一id" style={{...}}>文字或<span style={{color:'...'}}>分色文字</span></Text>。所有id只用ASCII字母数字_:-，不能用中文内容拼id。不要裸div文字、不要SVG text。相邻分色标题优先一个Text中的inline span，数字单位可同一个Text中不同字号span；不要靠两个超宽框猜字间距。
<Asset id="subject_0" anchor="source" crop={[0,0,1,1]} />：按可信源像素原位定位。crop是当前已裁素材自身[左上右下]0到1，不是海报坐标。修边后不会拉伸回原框。独立参考图无anchor时用box={[x,y,width,height]}、fit="contain"，也可style放入flex。
subject_N_original是真实原裁片；subject_N是抠图。先看两者，抠图丢失头部/底座/光晕时可选original，需避开夹带字与背景矩形接缝。原裁片宜原位淡入，不宜大幅位移暴露接缝。所有主体必须实际出现且只保留一个版本，不能用代码猜画照片/人物。
背景已由运行时使用background铺满，不要再放一张整幅core或重复background。根div必须透明，不得添加全幅不透明background遮住已经提取好的装饰背景；局部文本底板正常绘制。可写SVG复原箭头/细线等前景装饰；勿把装饰箭头当文案。
<Group id="group_a" at={0.4} enter={0.65} exit={0.3} motion="fade">...</Group>：时间为秒，motion可fade/slide_left/slide_right/slide_up/scale。Group默认是与画布等大的绝对定位容器；内部可按画布定位，也可自行style建立flex布局。它自动生成Remotion逐帧动画，完整poster模式自动全显，无需ctx.poster分支。
让标题/主体/解释按字幕逐步出现，入场约0.5–0.9秒、退出约0.3秒，最后至少1秒全部可读。不要在开头同时把所有层亮出，也不要改变动画阶段的文字事实。
不得为通过校验而隐藏原文、缩成极小字号、用透明字凑数，不能改配音或数据。embedded_text仅申报当前实际显示裁片里可见的原文，若代码又画同一句会重复；已有文本被裁去则不要申报它。
Inventory仅为初次识别线索，原图是视觉真值。遗漏原文可以recovered_text登记原图source_box待审图，但不能凭空添加解释。要精致、克制，保持原图审美而非另起一套海报。'''
DIRECTOR += '''
工具调用示例：测量时 {"action":"measure","summary":"测关键字号","probes":[{"id":"title","text":"示例标题","font_size":80,"font_weight":700,"font_family":"sans"}]}，不要带code。
预览时 {"action":"preview","summary":"首版设计","code":"function Scene(ctx) { return <Text id=\\"title\\" style={{fontSize:80}}>示例标题</Text>; }","probes":[],"embedded_text":{},"recovered_text":[]}。
measure的font_size可12–512，font_weight为100..900整百，font_family仅sans/serif。只测量内在字宽时不填max_width；填写max_width意味着测量该固定宽度内的换行高度。
测量返回的实际宽高可从ctx.measurements[id]读取，结合原图文字锚点与字号比例决定布局，不要把所有字一律加大加亮；lineHeight至少容纳真实字高，避免大数字侵占上下文。
inventory.labels.source_box是原图里每段文字的近似区域[左,上,右,下]，可能包含留白、行框或文本底板，并非真实字形边界或目标字号。用它找位置，再看原图/同尺度局部对照中的实际字形大小，结合浏览器测量决定字号；不要把整个区域高度直接当fontSize，也不要为了填满区域而放大文字。分色/大小数字可单独测量并组合。
反馈previous_response是你上一次被拒的完整返回，请基于它修正错误，不要回退到更早错误代码。'''
DIRECTOR += '''
material_inspection图片是程序真实alpha合成的同尺度素材对照；棋盘只是透明检查底，不是要绘制到画面上的元素。透明区域里即使有残存RGB也不能显示。
先比较每个主体的原裁片与抠图：外围头部、小人物、边缘结构、底座是否被误删。缺失的像素不能通过放大、提高不透明度或外扩crop恢复；此时自主选择完整original，或在已有完整素材上合理裁剪，不要继续沿用损坏cutout。
保真原裁片可用crop去掉明显无关的下方背景，仍按anchor=source保持原位；必要时可在Asset的style用maskImage/WebkitMaskImage的linear-gradient/radial-gradient柔化背景边缘。蒙版只能淡化空白背景边缘，不能再次擦掉外围主体、字或底座；勿一刀切或靠大光晕掩盖接缝。
预览反馈含同一个core空间窗口的局部对照，左边原海报，右边实际渲染。先修主体完整性和文字比例，再谈装饰强化。大数字即使不直接相交，也不能挤占原图留给单位与下方说明的空间；同时检查整个数字+单位组合的高度、颜色、基线和上下留白。避免无依据额外加粗、放大和强光晕。
每轮只修截图确实有问题的部分，保留已正确的背景、素材和动画。summary简述你在对照图中发现的具体差异以及本轮改动；不要只宣称已优化。'''
DIRECTOR += '''
切换完整原裁片的准确语法是<Asset id="subject_0_original" anchor="source" crop={[0,0,1,1]} />，id必须来自subject_variants；Asset没有variant属性，也没有重新抠图能力。编译器指出某个属性无效时，只修正语法，不能把已经判定残缺的素材换回来。
feedback.review与fidelity_focus中的未解决问题在语法返工后仍然有效。对照数字/文字的实际高度与原图近似范围：若明显过高，优先重新测量并缩小该文字及其单位组合，而不是只把上方/下方文本推开。原图范围只是线索，最终看局部图，不能为了满足范围而裁掉字。
保持已经验证正确的文字颜色。只有明确在原图中可见的光晕才恢复，勿用更强阴影掩盖比例问题。'''
DIRECTOR += '''
material_decisions是此前独立素材核查结论，绑定当前图片/alpha/源裁切。prepared_complete=false或null的版本不能继续用；original_complete=true且推荐original时，自行在代码写对应_original素材id，保留source锚点并检查边缘。
如果保留素材自带文字，在embedded_text用实际渲染的准确id申报一次；不要再用Text画同一句。若要代码重画，必须先用crop明确裁掉素材内原字再申报为空。文字守恒错误中的“缺少字符”可能是漏了embedded_text声明，不等于应该在素材上再叠一层字。
只有当素材/原裁切确实变更并重新检查时，才能推翻先前缺损结论；不能为了通过编译自行声称坏抠图完整。'''
DIRECTOR += '''
使用original时要自己识别素材中的主体、文字牌与无关背景留边，不能默认整张矩形原裁片直接贴上去就是完成。对空背景边缘可小幅crop/柔化，保留全部头部、轮廓、底座和文字；主体紧贴的边不能盲目渐隐。相同source锚点可以保持主体位置不变，同时缩小裁片显示范围。
独立background已经移除了前景文字与文本底板。原海报中确有的圆角框、描边或卡片需要你用代码重建；不得因为上一轮框太大就直接删除原图本来就有的框。修框大小、字号、圆角与位置，而不是删掉承载结构。
先以原图文字层次和主体留白完成忠实静态复原，再增加适度动效；不要以大标题、大数字、强阴影替代原图本来的精致比例。'''
DIRECTOR += '''
独立审图意见也是待核对的观察，不是覆盖图片事实的命令。返工前在原图与实际成片的同尺度对照里重新核实每条问题：确有问题就修；若意见与可见像素相反（例如元素实际存在、前轮说太大而当前已明显偏小），保留正确部分，并在summary说明核实依据。不要连续机械缩小/放大同一元素，也不要因为局部窗口没包含它就删除或重复绘制。
优先用实际尺寸测量和截图确认，而非记忆上轮字号数字。目标是主体完整、布局协调且接近原图，不是让每次审查都改出另一种版式。'''
DIRECTOR += '''
本轮可用素材中，inventory.background_retained是你在取材阶段选择留在底图的主体，已经随background显示，不是缺失的Asset，也不用再画一份或单独入场。文字、线条、文本框仍可自主用代码制作动效。其余subjects可自主选择已核查完整的抠图或原裁片、裁边/柔化，并用measure、preview工具看效果后调整。
以可见效果决定方法：普通底框用CSS/SVG重建通常更清楚；复杂原框若完整且无接缝，也可用原裁片。检查四边、圆角、图标周围的残留框线和底色差，不要仅因文字可读就忽略破损框线。'''

REVIEWER = '''你是独立视觉验收员，只返回符合schema的JSON。core是原海报，poster是JSX重建完整画面，其余是实际动画关键帧与原始素材。
认真对比图片而非相信程序说明：检查事实文字/数值、主体完整性、重复字、字体基线、标题间隙、矩形拼贴接缝、背景装饰、文本底板、前景标注以及整体构图比例。
抠图若丢失轮廓，原裁片可作保真备选，但不能接受明显两块矩形水纹拼在背景上。文字框接触不等于字形接触，应看实际墨迹位置。
逐项核实embedded_text确实在实际显示的裁片内；recovered_text确实在core声明区域，未添加事实。无这些声明则对应verified=true。
根据字幕与关键帧时间检查依次入场、可读时间、淡入淡出；早期未出现不是遗漏，最后hold阶段必须完整。不要把装饰双箭头/光晕误认成必须补写的文字或引号。
source_geometry/material_specs是程序实际测量的像素尺寸，优先于肉眼猜尺寸。source锚点的缩放是由原图像素精确映射的，不得凭空声称宽高比失真；若报告几何错误，必须引用真实测量数据。重点比较core、original与cutout中主体轮廓是否缺失（例如边缘小主体、光晕或底座），不能只看文字有没有。
允许字体字形或光晕有轻微差异，不要求逐像素一致；不允许明显错位、遮挡、拼贴、残缺、重复或事实错误。问题应指向代码中的具体id并给出修正方式。没有问题才passed=true；不能为了跑通而放宽标准。'''
REVIEWER += '''
必须逐个填写review_targets列出的subject_checks与layout_checks，不能遗漏或重复id。
subject_checks只检查active_assets列出的本轮实际实例，在poster及原图—成片同尺度局部对照中核实。原始素材已单独核查，本轮不再发送备选素材原片，避免与应用crop/mask后的实际画面混淆。尤其核实外围小主体是否在实际poster中消失。已选完整original时，不能因为material_decisions记载其未使用cutout缺损，就认定实际画面也缺损。
layout_checks要对比原图与预览的同位置局部。不能因为无重叠就算还原合格：明显放大的标题/数字、数字与单位不协调、字号把下方说明挤走、颜色层级变化都应明确指出；不要要求补回早期尚未入场的元素。
每项evidence写出可见差异或具体一致之处。先解决完整性和构图比例，不要泛泛追求更亮更大。边缘轻微差异可接受，但实际缺失头部、肢体或其他原图可见部件不通过。'''
REVIEWER += '''
优先检查fidelity_focus的同尺度局部对照图。source_box只是模型估计的区域，可能含留白或底板；DOM高度也可能含行高。两者比值只能帮助发现候选问题，不能据此要求字号增大或缩小。layout_checks.evidence应说明截图中的实际字形、基线、上下留白和相邻元素关系；若没有可见偏差，不因粗略比例越界而判错。若数字组合明显变大挤压下方留白，应指出缩小组合，不要只建议把邻近字移开。
程序提供的dom_summary是实际文字与素材实例；没有对应Text实例时，不得虚构“又绘制一份同名文字”。原裁片里的字只出现一次且可读就可验证embedded_text，不要把素材自带字当程序重复绘制。
不强求照片级一致；先纠正大比例偏差和轮廓缺损，不因字体细微字形差异无休止返工。'''
REVIEWER += '''
material_decisions是单独查看原裁片/真实透明图得出的素材检查记录，已经绑定当前素材字节。不要推翻其中缺损结论；核查代码实际选用了哪个完整备选，并检查它是否被后续crop/mask再次截坏。不能把完整original与缺损cutout混为一谈。'''
REVIEWER += '''
只报告能在当前poster或局部对照右侧确认的问题，并说明具体位置和可见依据。不要从“原片本身不透明”推断成片必有矩形边缘：代码可能已经应用渐变mask，实际效果以截图为准。报告缺少某元素前，再在完整poster对应位置核实，局部图裁窗以外不等于缺失。报告大小问题前确认是在比较同一个元素的字形而非容器框。不能为了给出返工建议而编造差异。
对已完整的主体保持complete=true；仅在实际成片中能指出新增的裁切缺口时才改判false。不要因为字体细节偏好改变已协调的布局，优先检查错字、残缺、遮挡、明显拼贴与比例偏差。'''
REVIEWER += '''
inventory.background_retained是明确选择保留在底图的主体，不要求它独立动画。逐个填background_checks：对照core与background判断preserved，对照background与poster判断compositing_ok（是否被重复画、遮盖或留下拼贴接缝）。保留对象的材质、透射颜色、轮廓和位置应接近原图，不能只因背景存在就说主体完整；缺损/被改画要如实报告。
整体检查时也看完整文本框四边和圆角，以及图标旁不属于它的卡片残边。代码框或原裁片均可，按实际效果判断，不强制一种实现。'''


def fidelity_focus(report):
    """Soft attention cues, not guessed fonts or a case-specific layout fix."""
    result = []
    for item in report.get('items', []):
        hint = item.get('scale_hint') or {}
        if item.get('kind') != 'text' or not hint:
            continue
        height, width = hint['height_ratio'], hint['width_ratio']
        if .72 <= height <= 1.3 and .72 <= width <= 1.3:
            continue
        result.append({key: item.get(key) for key in ('id', 'text', 'image_id', 'matched_render_ids')}
                      | {'scale_hint': hint,
                         'instruction': 'Compare local pixels first; explicitly explain scale/spacing in layout_checks. Approximate source bounds are not an automatic failure.'})
    return result


def review_dom_summary(measured):
    """Avoid burying visual evidence under repeated per-frame DOM payloads."""
    return {
        'texts': measured.get('texts', []), 'assets': measured.get('assets', []),
        'groups': [{key: row.get(key) for key in ('id', 'at', 'enter', 'exit', 'motion')}
                   for row in measured.get('groups', [])],
        'diagnostics': measured.get('diagnostics', []),
        'frames': [{'id': frame.get('id'), 'time': frame.get('time'),
                    'visible_text_ids': [row.get('id') for row in frame.get('texts', []) if row.get('visible')],
                    'visible_asset_ids': [row.get('id') for row in frame.get('assets', []) if row.get('visible')],
                    'diagnostics': frame.get('diagnostics', [])} for frame in measured.get('frames', [])]}


def inspection_prompt_report(report):
    return {**report, 'items': [{key: value for key, value in item.items() if key != 'intersecting_texts'}
                               for item in report.get('items', [])]}


def validate_review_coverage(review, measured, inventory=None):
    expected_subjects = {row['id'] for row in measured.get('assets', []) if row['id'] != 'background'}
    expected_texts = {row['id'] for row in measured.get('texts', [])}
    expected_base = {f'base_{i}' for i,_ in enumerate(getattr(inventory,'background_retained',[]))}
    for name, rows, key, expected in (
        ('subject_checks', review.subject_checks, 'asset_id', expected_subjects),
        ('layout_checks', review.layout_checks, 'text_id', expected_texts),
        ('background_checks',review.background_checks,'base_id',expected_base)):
        actual = [getattr(row, key) for row in rows]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ResponseContractError(f'{name}必须逐个核验实际渲染id，不得遗漏或重复；需要：{sorted(expected)}',
                                        review.model_dump())


def _canonical(identity):
    return identity.removesuffix('_original')


def _actually_visible(row):
    # Older measurements lack geometry evidence. New browser receipts include
    # actual canvas/overflow intersection; nonzero DOM size alone is not proof.
    return bool(row.get('visible')) and (row.get('geometry_visibility') or {}).get('visible') is not False


def _visibility_error(row, kind, phase):
    geometry = row.get('geometry_visibility') or {}
    reason = {'outside_canvas': '完全位于画布之外', 'clipped': '被祖先 overflow 裁切至完全不可见',
              'empty': '没有有效尺寸'}.get(geometry.get('state'), '不可见（请检查透明度、显示状态与位置）')
    detail = f'{kind} {row.get("id")} 在{phase}{reason}；实际框={row.get("box")}；可见框={geometry.get("visible_box")}'
    if geometry.get('clipped_by'):
        detail += '；裁切容器=' + str(geometry['clipped_by'])[:700]
    if row.get('anchor') == 'source' or row.get('source_anchor'):
        source = row.get('source_anchor') or {}
        detail += ('；anchor="source" 已使用画布坐标，带 left/top/transform 的父容器会再次偏移。'
                   '原位显示请放在未偏移的全画布 Group；需要容器内自主排版时用 anchor="manual" 和 box={[x,y,width,height]}。'
                   f'可信源框={source.get("expected_canvas_box")}，实际框={source.get("actual_canvas_box", row.get("box"))}')
    return detail


def _blocking_diagnostics(rows):
    # Intentional partial asset crops and transient entrance clipping remain a
    # visual-review decision. Never downgrade text clipping or unknown errors.
    advisory = {'asset_partial_clip', 'asset_transient_clip', 'source_anchor_offset'}
    return [row for row in rows if not (isinstance(row, dict) and row.get('severity') == 'warning'
                                       and row.get('kind') in advisory)]


def validate_rendered(turn, measured, inventory, *, require_animation=True):
    """Actual browser checks; only host-selected layout drafts skip motion proof.

    The default remains strict for every existing preview/delivery caller.
    Model metadata can never disable temporal verification.
    """
    texts, subjects = measured.get('texts', []), measured.get('assets', [])
    if not isinstance(texts, list) or not isinstance(subjects, list):
        raise ValueError('浏览器文字/素材测量缺失')
    ids = [row.get('id') for row in texts]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('Text需要唯一id，便于测量与逐项返工')
    for row in texts:
        if not _actually_visible(row):
            raise ValueError(_visibility_error(row, 'Text', '完整海报中'))
        if float(row.get('font_size', 0)) < 12:
            raise ValueError(f'Text {row.get("id")} 在完整海报中隐藏或字号过小')
    subjects = [row for row in subjects if row.get('id') != 'background']
    identities = [row.get('id', '') for row in subjects]
    canonical = [_canonical(value) for value in identities]
    if len(set(canonical)) != len(canonical):
        raise ValueError('同一主体不能同时放抠图与原裁片，造成重复内容')
    if set(turn.embedded_text) - set(identities):
        raise ValueError('embedded_text声明了未实际渲染的素材')
    for row in subjects:
        if not _actually_visible(row):
            raise ValueError(_visibility_error(row, 'Asset', '完整海报中'))
    assembled = SimpleNamespace(
        labels=[SimpleNamespace(text=row['text']) for row in texts],
        subjects=[SimpleNamespace(asset_id=_canonical(row['id']),
            prepared_crop=row.get('crop') or [0, 0, 1, 1],
            embedded_text=turn.embedded_text.get(row['id'], [])) for row in subjects],
        recovered_text=turn.recovered_text)
    validate_content(assembled, inventory)
    diagnostics = _blocking_diagnostics(measured.get('diagnostics') or [])
    if diagnostics:
        raise ValueError('实际浏览器排版问题：'+str(diagnostics)[:3500])
    if not require_animation:
        return True
    baseline = {row['id']: row['text'] for row in texts}
    hold = False
    frames = measured.get('frames') or []
    if not frames:
        raise ValueError('缺少动画关键帧实测，不能只验收静态海报')
    for frame in frames:
        current = frame.get('texts') or []
        for row in current:
            if row.get('id') not in baseline or row.get('text') != baseline[row['id']]:
                raise ValueError(f'动画 {frame.get("time")} 秒的文字与完整海报不一致：{row.get("id")}')
        frame_assets = [row for row in frame.get('assets', []) if row.get('id') != 'background']
        if any(row.get('id') not in identities for row in frame_assets):
            raise ValueError('动画阶段改变了已验收主体素材')
        if frame.get('id') == 'hold' or frame.get('label') == 'hold' or frame.get('name') == 'hold':
            hold = True
            for kind, rows in (('Text', current), ('Asset', frame_assets)):
                for row in rows:
                    if not _actually_visible(row):
                        raise ValueError(_visibility_error(row, kind, f'结尾阅读阶段 {frame.get("time")} 秒'))
            if ({row['id'] for row in current if _actually_visible(row)} != set(baseline)
                    or {row['id'] for row in frame_assets if _actually_visible(row)} != set(identities)):
                raise ValueError('结尾阅读阶段没有完整呈现全部文字/主体，请提前结束入场并延后退出')
        diagnostics = _blocking_diagnostics(frame.get('diagnostics') or [])
        if diagnostics:
            raise ValueError('动画关键帧排版问题：'+str(diagnostics)[:2500])
    if not hold:
        raise ValueError('缺少完整阅读阶段hold核验')
    return True


def finish(project, sid, job_id, inventory, context, sources, assets, config, data, event, audio, seek,
           *, checkpoint=None, call_limit=MAX_CALLS):
    # Internal resume input: only the server/test harness may supply a frozen
    # previous response. It is revalidated and rerendered, never marked passed.
    if type(call_limit) is not int or not 1 <= call_limit <= MAX_CALLS:
        raise ValueError('代码制作模型预算必须为1到8次')
    if event.is_set():
        raise InterruptedError('已停止代码分层动效；未修改正式镜头')
    path = api._job(project, job_id)
    folder = path.parent
    def material_state(round_number):
        geometry = geometry_manifest(inventory, sources, assets, canvas=context['canvas'])
        runtime = renderer.runtime_geometry(geometry, assets)
        manifest = inventory_manifest(inventory, context.get('inventory_notes'))
        api._write(folder/'code-source-geometry.json', geometry)
        evidence = inspection.build_material_inspection(
            inventory, sources, assets, folder/f'code-material-inspection-{round_number}', context['canvas'])
        api._write(folder/'code-material-inspection.json', evidence['report'])
        api._write(folder/f'code-material-inspection-{round_number}.json', evidence['report'])
        return geometry, runtime, manifest, evidence

    geometry, runtime_geometry, manifest, material_evidence = material_state(0)
    # The comparison sheets show real alpha-composited pixels, not invisible
    # RGB beneath the mask. Do not resend duplicate, misleading raw variants.
    source_pictures = {'core': sources['core'], 'background': assets['background'],
                       **material_evidence['images']}
    pictures = dict(source_pictures)
    payload = {**context, 'inventory': manifest, 'source_geometry': geometry,
        'runtime_geometry': runtime_geometry, 'material_specs': asset_specs(assets),
        'subject_variants': subject_variants(inventory, assets),
        'material_inspection': material_evidence['report'],
        'instruction': data.instruction, 'pace': data.pace}
    calls = previews = measures = submissions = compile_repairs = material_retakes = 0
    material_history = []
    retake_history = []
    measured_fonts = dict((checkpoint or {}).get('measurements') or {})
    feedback = {}
    last_turn = None
    accepted = None
    last_safe_candidate = None
    reviews = []
    pending_turn = Turn.model_validate(checkpoint['turn']) if checkpoint and checkpoint.get('turn') else None
    if pending_turn and pending_turn.action != 'preview':
        raise ValueError('恢复点必须是尚待验证的完整代码预览，不是测量动作')
    if checkpoint:
        api._write(folder/'code-resume-checkpoint.json', checkpoint)

    def check():
        if event.is_set():
            raise InterruptedError('已停止代码分层动效；未修改正式镜头')

    def call(system, body, images, schema, phase):
        nonlocal calls
        check()
        if calls >= call_limit:
            raise ValueError(f'代码动效已达到{call_limit}次编排/核查预算；未自动追加请求')
        calls += 1
        repair_round = min(previews, 2) if phase == 'code_author' else max(0, previews-1)
        if phase == 'code_material_review':
            repair_round = body.get('format_attempt', 0)
        api._update(path, stage=phase, attempt=repair_round,
            source_retake_round=material_retakes,
            message=(f'代码动效 · 素材核查 · 重新取材 {material_retakes}/{MAX_MATERIAL_RETAKES} · 模型调用 {calls}/{call_limit} · 格式修正 {repair_round}/1'
                     if phase == 'code_material_review' else
                     f'代码动效 · {phase} · 模型调用 {calls}/{call_limit} · 视觉返工 {repair_round}/2'))
        try:
            response_schema=(material_review.request_schema(inventory)
                if phase=='code_material_review' else schema.model_json_schema())
            raw = api._call(path, config, system, {**body, 'schema': response_schema},
                            images, event.is_set, max_tokens=12000, thinking=False)
        except ModelResponseFormatError as exc:
            check()
            # A completed but malformed answer is a format contract error,
            # not a transport retry. Use the existing one-repair material
            # review loop and total call budget; never guess missing JSON.
            if phase != 'code_material_review':
                raise
            raise ResponseContractError(str(exc),exc.response_text) from None
        check()
        api._write(folder/f'code-response-{calls:02d}.json', raw)
        try:
            return schema.model_validate(raw)
        except ValidationError as exc:
            raise ResponseContractError(str(exc)[:5000], raw) from None

    def render(mode, code, output, probes=None):
        check()
        output.mkdir()
        while not api._render_lock.acquire(timeout=.25):
            check()
        try:
            result = renderer.run(mode=mode, code=code, canvas=context['canvas'], duration=context['duration'],
                assets=assets, geometry=runtime_geometry, output_dir=output, measurements=measured_fonts,
                measure_requests=probes or [], cancelled=event.is_set, timeout=300)
            check()
            return result
        finally:
            api._render_lock.release()

    check()
    # Freeze semantic source findings separately from typography review. A
    # later, distracted reviewer cannot rehabilitate an unchanged broken mask.
    subjects = inventory.get('subjects', []) if isinstance(inventory, dict) else inventory.subjects
    material_key = material_review.fingerprint(inventory, sources, assets, context.get('inventory_notes'))
    cached = (checkpoint or {}).get('material_decisions') or {}
    material_cached = (cached.get('version') == material_review.VERSION
        and cached.get('fingerprint') == material_key and not (cached.get('review') or {}).get('retakes'))
    if cached and not material_cached:
        pending_turn = None
        measured_fonts = {}
    while True:
        check()
        if material_cached:
            material_check = material_review.validate_material_review(
                material_review.MaterialReview.model_validate(cached['review']), assets, inventory)
        elif not subjects:
            material_check = material_review.MaterialReview(subjects=[], issues=[])
        else:
            minimum_calls = 2 if pending_turn is not None else 3
            if call_limit-calls < minimum_calls:
                raise ValueError('剩余预算不足以完成素材核查与独立审图；未发起模型请求')
            source_bindings = source_retake.source_manifest(inventory, sources)
            source_images = {row['source_image_id']: sources[row['source_id']] for row in source_bindings}
            material_body = {'inventory': manifest, 'inventory_notes': context.get('inventory_notes'),
                'material_specs': payload['material_specs'], 'subject_variants': payload['subject_variants'],
                'material_inspection': material_evidence['report'], 'source_manifest': source_bindings,
                'response_contract': material_review.request_contract(inventory,assets,source_bindings),
                'source_retake_round': material_retakes,
                'retakes_remaining': MAX_MATERIAL_RETAKES-material_retakes,
                'retake_operations': retake_history,
                'instruction': '按本轮实际图片重新核查；retake_operations只记录已执行的坐标变化，不能把旧裁区问题当作新图仍有的问题。'}
            prior_material_check = None
            for format_attempt in range(2):
                material_check = None
                try:
                    material_check = call(material_review.PROMPT, material_body,
                        {'core': sources['core'], **source_images, **material_evidence['images'],
                         **material_evidence.get('review_images', {})},
                        material_review.MaterialReview, 'code_material_review')
                    material_check = material_review.validate_material_review(material_check, assets, inventory)
                    if prior_material_check is not None:
                        previous = {row.asset_id: row for row in prior_material_check.subjects}
                        for row in material_check.subjects:
                            old = previous.get(row.asset_id)
                            if old is None:
                                continue
                            for field in ('source_crop_complete', 'prepared_complete', 'original_complete'):
                                if getattr(old, field) is not True and getattr(row, field) is True:
                                    raise ValueError(f'{row.asset_id}.{field} 的素材未变化；格式修正不能把已报告的缺损/未知改成完整。请保留观察、补齐问题和重新取材请求。')
                    if material_check.retakes:
                        source_retake.validate_retakes(material_check.retakes, inventory, sources)
                    break
                except (ResponseContractError, ValidationError, ValueError) as exc:
                    # Transport errors never enter this schema/semantic repair.
                    if format_attempt or calls >= call_limit:
                        label=(f'第 {material_retakes} 轮重新取材后核查' if material_retakes else '首次核查')
                        label+=f' · 第 {format_attempt} 轮格式修正' if format_attempt else ''
                        message=f'【主体素材完整性核查 · {label}】{exc}'
                        if isinstance(exc,ResponseContractError):
                            raise ResponseContractError(message,exc.response) from None
                        raise ValueError(message) from None
                    prior_material_check = material_check
                    material_body = {**material_body, 'validation_error': str(exc)[:4000],
                        'previous_response': (exc.response if isinstance(exc, ResponseContractError)
                                              else material_check.model_dump() if material_check is not None else None),
                        'format_attempt': format_attempt+1,
                        'instruction': '仅修正本次previous_response的JSON格式或契约错误；如果它是原始字符串，请读完原文后按schema返回完整JSON对象，不添加Markdown或解释。必须重新输出response_contract.required_subject_ids中的每一个主体，不是只输出报错项的局部补丁；保留其他主体仍有效的issues和retakes。retakes只允许真正改变像素裁区，不能提交原框占位请求mask。不重新出图、不回退旧裁区判断、不把缺损/未知改成完整；保留当前图片已核实的观察，完整逐项回答；不虚构可用版本。'}
        material_decisions = {'version': material_review.VERSION, 'fingerprint': material_key,
            'review': material_check.model_dump(), 'cached': material_cached,
            'source_retake_round': material_retakes}
        material_history.append(material_decisions)
        api._write(folder/'code-material-decisions.json', material_decisions)
        api._write(folder/'code-material-history.json', material_history)
        if not material_check.retakes:
            break
        if material_retakes >= MAX_MATERIAL_RETAKES:
            raise ValueError('重新取材后主体仍未可靠通过完整原图核查；已达到1轮取材上限，素材与证据已保留，未采用或覆盖正式镜头')
        if call_limit-calls < 3:
            raise ValueError('剩余预算不足以重新取材后核查、编写及独立审图；未执行取材，未覆盖正式镜头')
        check()
        material_retakes += 1
        api._update(path, stage='code_material_retake', source_retake_round=material_retakes,
            message=f'代码动效 · 按完整原图重新取材 {material_retakes}/{MAX_MATERIAL_RETAKES}；不重新出图')
        requests = material_check.retakes
        api._write(folder/f'code-source-retake-{material_retakes}-request.json', [row.model_dump() for row in requests])
        inventory, assets, retake_audit = source_retake.apply_retakes(
            requests, inventory, sources, assets, folder/f'code-source-retake-{material_retakes}', cancelled=event.is_set)
        check()
        api._write(folder/f'code-source-retake-{material_retakes}.json', retake_audit)
        retake_history.extend([{key: value for key, value in row.items()
            if key in ('asset_id', 'source_id', 'previous_source_crop', 'source_crop', 'treatment', 'rmbg_used')}
            for row in retake_audit])
        api._write(folder/'code-material-assets.json', {key: str(value) for key, value in assets.items()})
        api._write(folder/'code-material-inventory.json', inventory.model_dump() if hasattr(inventory, 'model_dump')
                   else inventory_manifest(inventory, context.get('inventory_notes')))
        # A saved JSX response and measured crop geometry belong to the old
        # material fingerprint. Never carry them across a successful retake.
        pending_turn = None
        measured_fonts = {}
        geometry, runtime_geometry, manifest, material_evidence = material_state(material_retakes)
        material_key = material_review.fingerprint(inventory, sources, assets, context.get('inventory_notes'))
        material_cached = False
        source_pictures = {'core': sources['core'], 'background': assets['background'], **material_evidence['images']}
        pictures = dict(source_pictures)
        payload.update(inventory=manifest, source_geometry=geometry, runtime_geometry=runtime_geometry,
            material_specs=asset_specs(assets), subject_variants=subject_variants(inventory, assets),
            material_inspection=material_evidence['report'])
    payload['material_decisions'] = material_check.model_dump()
    if any(row.recommended_asset_id is None for row in material_check.subjects):
        raise ValueError('素材完整性核查未找到可靠的完整版本；需要重新取材，未自动重画或采用：'
                         +str(material_check.model_dump())[:2500])

    while calls < call_limit and previews < MAX_PREVIEWS:
        check()
        if call_limit-calls < 2 and pending_turn is None:
            feedback = {**feedback, 'budget_error': '剩余模型预算不足以同时编写和独立审图，已停止追加请求'}
            break
        if pending_turn is not None:
            turn, pending_turn = pending_turn, None
        else:
            try:
                turn = call(DIRECTOR, {**payload, 'feedback': feedback, 'measurements': measured_fonts,
                    'previous_code': last_turn.code if last_turn else None,
                    'remaining': {'model_calls': call_limit-calls, 'previews': MAX_PREVIEWS-previews,
                                  'measure_tools': MAX_MEASURES-measures}}, pictures, Turn, 'code_author')
            except ResponseContractError as exc:
                feedback = {**feedback, 'validation_error': str(exc)[:4000], 'previous_response': exc.response,
                            'instruction': '仅修正这次previous_response的返回契约，不要回退更早错误代码。'}
                continue
        if turn.action == 'measure':
            if measures >= MAX_MEASURES:
                feedback = {**feedback, 'error': '测量次数已用完，请利用已有测量写完整Scene并preview'}
                continue
            measures += 1
            result = render('measure', '', folder/f'code-measure-{measures}',
                            [probe.model_dump(exclude_none=True) for probe in turn.probes])
            rows = result['measurements'].get('measured') or []
            if {row.get('id') for row in rows} != {p.id for p in turn.probes}:
                raise ValueError('文字测量回执缺少本次请求项目')
            measured_fonts.update({row['id']: row for row in rows})
            feedback = {**{k: v for k, v in feedback.items() if k not in ('validation_error', 'previous_response')},
                        'tool': 'measure', 'measured': rows, 'instruction': '根据真实字宽排版，下一步preview完整Scene；此前未解决的审图问题仍须修正。'}
            continue
        submissions += 1
        last_turn = turn
        feedback.pop('previous_response', None)
        api._write(folder/f'code-design-{submissions}.json', turn.model_dump())
        output = folder/f'code-preview-{submissions}'
        rendered = False
        preview_evidence = {'images': {}, 'report': {}}
        try:
            metrics = render('preview', turn.code, output)
            rendered = True
            preview_evidence = inspection.build_preview_inspection(
                inventory, sources, output/'poster.png', metrics['measurements'],
                folder/f'code-inspection-{submissions}', context['canvas'])
            api._write(folder/f'code-inspection-{submissions}.json', preview_evidence['report'])
            material_review.validate_material_selection(material_check, metrics['measurements'])
            validate_rendered(turn, metrics['measurements'], inventory)
            previews += 1
            last_safe_candidate = (turn, metrics, output)
        except ValueError as exc:
            # Incomplete/invalid DOM is a contract repair, not a visually
            # reviewable full design. Keep the same bounded repair budget.
            compile_repairs += 1
            feedback = {**feedback, 'tool': 'preview', 'validation_error': str(exc)[:6000],
                        'compile_repairs': compile_repairs,
                        'instruction': '局部修复代码中的实际错误，保留正确素材和原文。不能隐藏内容规避检查。'}
            if rendered:
                feedback.update(preview_inspection=inspection_prompt_report(preview_evidence['report']),
                                fidelity_focus=fidelity_focus(preview_evidence['report']))
                pictures = {**source_pictures, **preview_evidence['images']}
            if (output/'poster.png').is_file():
                pictures['previous_poster'] = output/'poster.png'
            if compile_repairs >= 3:
                break
            continue
        # Review the actual composite, not unmasked source alternatives. Keep
        # a bounded set of paired same-window detail images; redundant raw
        # assets can be mistaken for visible defects after CSS crop/masking.
        review_evidence = inspection.compact_preview_evidence(preview_evidence, max_images=4)
        active_ids = list(dict.fromkeys(row['id'] for row in metrics['measurements'].get('assets', [])
                                       if row['id'] != 'background'))
        active_assets = [{'asset_id': identity, 'image_id': 'poster',
                          'comparison_image_ids': [row['image_id'] for row in review_evidence['report'].get('items', [])
                                                   if identity in row.get('matched_render_ids', [])]}
                         for identity in active_ids]
        review_pictures = {'core': sources['core'], 'poster': output/'poster.png',
                           **review_evidence['images']}
        if getattr(inventory,'background_retained',[]):
            review_pictures['background']=assets['background']
        if (output/'proof.jpg').is_file():
            review_pictures['keyframes'] = output/'proof.jpg'
        review_body = {'fidelity_focus': fidelity_focus(review_evidence['report']),
            'inventory': manifest, 'subtitles': context.get('subtitles'),
            'inventory_notes': context.get('inventory_notes'),
            'source_geometry': geometry, 'runtime_geometry': runtime_geometry,
            'active_assets': active_assets,
            'preview_inspection': inspection_prompt_report(review_evidence['report']),
            'material_specs': asset_specs({identity: assets[identity] for identity in active_ids}),
            'material_decisions': material_check.model_dump(),
            'review_targets': {
                'subject_checks': [row['id'] for row in metrics['measurements'].get('assets', []) if row['id'] != 'background'],
                'layout_checks': [row['id'] for row in metrics['measurements'].get('texts', [])],
                'background_checks':[f'base_{i}' for i,_ in enumerate(getattr(inventory,'background_retained',[]))]},
            'embedded_text': turn.embedded_text,
            'recovered_text': [item.model_dump() for item in turn.recovered_text],
            'dom_summary': review_dom_summary(metrics['measurements'])}
        try:
            review = call(REVIEWER, review_body, review_pictures, CodeReview, 'code_review')
            validate_review_coverage(review, metrics['measurements'],inventory)
        except ResponseContractError as exc:
            if calls >= call_limit:
                feedback = {'validation_error': '独立验收响应契约错误：'+str(exc)}
                break
            review = call(REVIEWER, {**review_body, 'validation_error': str(exc),
                'previous_response': exc.response,
                'instruction': '仅修正验收JSON格式，重新按真实画面给出完整结论'},
                review_pictures, CodeReview, 'code_review')
            validate_review_coverage(review, metrics['measurements'],inventory)
        reviews.append(review.model_dump())
        api._write(folder/'code-reviews.json', reviews)
        if any(not row.preserved for row in review.background_checks):
            raise ValueError('底图保留主体未通过原图对照：'+
                '；'.join(f'{row.base_id}：{row.evidence}' for row in review.background_checks if not row.preserved)+
                '；需调整背景处理方案，未追加图片请求、未覆盖正式镜头')
        if review.passed:
            accepted = (turn, metrics, output)
            break
        feedback = {'tool': 'preview', 'review': review.model_dump(), 'dom_measurements': review_dom_summary(metrics['measurements']),
                    'fidelity_focus': fidelity_focus(preview_evidence['report']),
                    'preview_inspection': inspection_prompt_report(preview_evidence['report']),
                    'instruction': '请自主修改完整Scene，重点修复指出的问题，允许用flex/span/SVG更合适地组织细节。'}
        pictures = {**source_pictures, **preview_evidence['images'], 'previous_poster': output/'poster.png'}
        if (output/'proof.jpg').is_file():
            pictures['previous_keyframes'] = output/'proof.jpg'
    approved = accepted is not None
    if (accepted is None and last_safe_candidate is not None and last_turn is not None
            and last_safe_candidate[0].code == last_turn.code):
        # Keep an honest inspection preview when local facts/layout are valid
        # but visual review did not approve it. Never silently use an older
        # draft, and never make this result adoptable.
        accepted = last_safe_candidate
    if accepted is None:
        api._write(folder/'quality.json', {'workflow_version': WORKFLOW_VERSION, 'quality_status': 'needs_review',
            'quality_issues': feedback, 'model_calls': calls, 'previews': previews,
            'compile_repairs': compile_repairs, 'reviews': reviews,
            'material_retakes': material_retakes, 'material_decisions': material_decisions})
        raise ValueError(f'代码动效在 {previews} 次预览/{max(0, previews-1)} 轮返工后仍未通过；'
                         +str(feedback.get('budget_error') or feedback.get('validation_error') or feedback.get('review') or feedback)[:2200]
                         +'；预览与代码已保留，未采用或覆盖正式镜头')
    turn, preview_metrics, _ = accepted
    check()
    output = folder/'render-code-v3'
    metrics = render('video', turn.code, output)
    check()
    # Recheck the final-render DOM, not only the earlier preview.
    material_review.validate_material_selection(material_check, metrics['measurements'])
    validate_rendered(turn, metrics['measurements'], inventory)
    check()
    renderer.mux(output_dir=output, audio=audio, start=seek, duration=context['duration'],
                 ffmpeg=api.ffmpeg_binary(), cancelled=event.is_set)
    check()
    quality_status = 'passed' if approved else 'needs_review'
    quality_issues = [] if approved else (reviews[-1].get('issues', []) if reviews else
        ['预算内尚未完成独立视觉验收；仅供查看，不能采用'])
    audit = {'workflow_version': WORKFLOW_VERSION, 'quality_status': quality_status, 'quality_issues': quality_issues,
        'model_calls': calls, 'visual_repairs': previews-1, 'measure_tools': measures,
        'compile_repairs': compile_repairs, 'material_retakes': material_retakes,
        'material_decisions': material_decisions,
        'static_reviews': reviews, 'code_sha256': hashlib.sha256(turn.code.encode()).hexdigest(),
        'source_geometry_version': geometry['version']}
    api._write(folder/'quality.json', audit)
    plan = {'version': VERSION, 'code': turn.code, 'canvas': context['canvas'],
            'duration': context['duration'], 'code_sha256': audit['code_sha256']}
    metrics.update(quality_status=quality_status, visual_repairs=previews-1, duration_seconds=context['duration'])
    return plan, assets, metrics, output, audit
