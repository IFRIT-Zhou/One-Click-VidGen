"""V2 source-material inventory, deliberately separate from layout and timing.

The model names what is visible and where to take it from. Only the adapter
fills legacy Design placeholders for existing material preparation and audit;
those placeholders must never become the assembly design contract.
"""
from typing import Annotated, Literal
import re
import unicodedata

from pydantic import Field, field_validator, model_validator

from .vibe_layered import Box, Color, Design, Strict, BaseRetainedSubject, bounds, resolve_background
from .vibe_layered_limits import MAX_INVENTORY_TEXTS, MAX_SUBJECTS, MAX_DECORATIONS


NonemptyText = Annotated[str, Field(min_length=1, max_length=180)]


def _nonempty(value, field):
    if value is not None and not value.strip():
        raise ValueError(field+' 不能仅为空白')
    return value


def _text_key(text):
    return re.sub(r'[\s，,。；;！？!?（）()【】\[\]“”"「」『』、|｜]+', '', unicodedata.normalize('NFKC', text))


class InventorySubject(Strict):
    role: Literal['object','person','photo','evidence','text_panel'] = Field(default='object',
        description='person为独立人物/人像（只分离、不重画脸或改身份）；object为其他实物/语义图标；photo为完整真实照片；evidence为原始研究/病理/数据图；text_panel为带字底框。默认稳定模式下object须留在background_retained，开启进阶后才可独立取材。')
    name: str = Field(min_length=1, max_length=120,
        description='原图可见主体的简短具体名称，包含人物、物件、成组图标、照片、原始图表等；不能用“背景元素”含混省略。')
    source: str = Field(min_length=1, max_length=80,
        description='实际提供的素材ID：优先本镜独立参考，缺少独立参考时可用core，不是文件路径。')
    source_crop: Box = Field(default=(0, 0, 1, 1),
        description='在上述 source 原图中的归一化 [左,上,右,下] 取材范围；这是源图取材，不是目标位置。保留完整主体边缘，尽量排除外部文字/底板。')
    treatment: Literal['preserve', 'cutout'] = Field(
        description='照片、原始实验/病理/数据图必须preserve原像素；仅需分离普通物件/图标/人物时cutout。')
    embedded_text: list[NonemptyText] = Field(default_factory=list, max_length=30,
        description='本裁片确实保留的可见文字，逐项填写；该同一处文字不能又列入texts重建。没有则[]，不得猜测半截字。')
    evidence_note: str | None = Field(default=None, min_length=1, max_length=400,
        description='可选：说明为何这是必须保真的照片/研究证据或为何需要抠图；提供时不能空白。')

    @field_validator('name', 'source', 'evidence_note')
    @classmethod
    def nonempty_fields(cls, value, info):
        return _nonempty(value, info.field_name)

    @model_validator(mode='after')
    def valid_source_region(self):
        if self.role in ('photo','evidence') and self.treatment!='preserve':
            raise ValueError('photo/evidence必须使用preserve保留原始像素，不能自动抠图或生成式重画')
        bounds(self.source_crop, 'subjects.source_crop')
        for text in self.embedded_text:
            _nonempty(text, 'subjects.embedded_text')
        return self


class InventoryText(Strict):
    text: NonemptyText = Field(description='需要代码重建的原图文字，逐字转写；保留数字/单位/换行。不是旁白改写，也不是装饰箭头。')
    source_box: Box = Field(description='这段文字在 core 原海报中的归一化范围 [左,上,右,下]，仅用于定位核对，不是新布局。')

    @model_validator(mode='after')
    def original_text(self):
        _nonempty(self.text, 'texts.text')
        bounds(self.source_box, 'texts.source_box')
        # Do not generally exempt symbols from conservation. Pure drawn arrows
        # belong to decoration inventory; actual textual math still stays text.
        stripped = re.sub(r'\s', '', self.text)
        arrow_characters = set('«»‹›←→↔⇐⇒⇔⇦⇨⇄⇆⟵⟶⟷➜➝➞➟➠➤➥➦➧➨➩➪➫➬➭➮➯➱➡➔─━—–-_│┃|')
        if stripped and all(char in arrow_characters for char in stripped):
            raise ValueError('texts.text 仅含装饰箭头/分隔线；请将其放入 decorations 描述，不作为新增文字')
        return self


class InventoryDecoration(Strict):
    kind: Literal['arrow', 'line', 'frame', 'other']
    description: str = Field(min_length=1, max_length=400,
        description='需独立重建的简单前景装饰及原图样式；不要把人物/物件/图标/原始图表当装饰省略。不要在此阶段生成路径或代码。')
    source_box: Box = Field(description='装饰在 core 原海报中的归一化范围，仅作识别定位；后续排版再决定几何/路径。')

    @model_validator(mode='after')
    def valid_description(self):
        _nonempty(self.description, 'decorations.description')
        bounds(self.source_box, 'decorations.source_box')
        return self


class ForegroundSummary(Strict):
    has_nontext_subjects: bool = Field(strict=True,
        description='原图是否存在人物/物件/语义图标/照片/图表，包括独立subjects及显式background_retained；抽象光带不算主体。')
    subject_count: int = Field(strict=True, ge=0, le=MAX_SUBJECTS,
        description='按主体素材组计数，必须等于subjects数量；不要把左右两个语义不同的图标组都省略为背景。')
    background_retained_count: int = Field(default=0,strict=True,ge=0,le=MAX_SUBJECTS,
        description='显式保留在底图、不单独入场的主体数量，必须等于background_retained长度；不是删除主体。')
    description: str = Field(min_length=1, max_length=800,
        description='清点原图可见前景及大致区域，明确哪些独立取材、哪些保留底图；与subjects和background_retained两表相符。')
    text_only_reason: str | None = Field(default=None, min_length=1, max_length=500,
        description='仅当subjects与background_retained均为空时必填，说明确为纯文字/抽象装饰；任一表有主体则为null。不能以无参考图/为省步骤为由省略主体。')

    @field_validator('description', 'text_only_reason')
    @classmethod
    def nonempty_fields(cls, value, info):
        return _nonempty(value, info.field_name)


class Inventory(Strict):
    background: str = Field(min_length=1, max_length=80,
        description='可用独立背景素材ID；没有独立背景用extract；仅均匀纯色用solid。有background_retained时必须extract，按移除清单编辑core；不能暗中把主体归入背景。')
    background_color: Color
    background_notes: str = Field(default='', max_length=600,
        description='背景装饰的保留要求，例如渐变、粒子、光带、远景轮廓；语义主体另在subjects或background_retained明确登记，文字单独处理。')
    background_is_uniform: bool = Field(default=False, strict=True)
    background_retained: list[BaseRetainedSubject] = Field(default_factory=list,max_length=MAX_SUBJECTS,
        description='难无损分离且不需要独立入场的非证据性物体，可明确保留在底图。仍参与完整性审图，不列subjects、不重复贴图；不含文字/文本框/真实照片/研究证据。')
    foreground_summary: ForegroundSummary
    subjects: list[InventorySubject] = Field(default_factory=list, max_length=MAX_SUBJECTS)
    texts: list[InventoryText] = Field(default_factory=list, max_length=MAX_INVENTORY_TEXTS,
        description=f'仅需要独立重建的原文字块，最多{MAX_INVENTORY_TEXTS}段；裁片保真的同一处文字已经在embedded_text内，不要重复计入。按原图独立位置和样式分别记录，不得为了凑数量删字、删数据或把不同区域强行合并。')
    decorations: list[InventoryDecoration] = Field(default_factory=list, max_length=MAX_DECORATIONS)

    @model_validator(mode='before')
    @classmethod
    def remove_transport_marker(cls, value):
        if isinstance(value, dict) and value.get('type') == 'json_object':
            return {key: item for key, item in value.items() if key != 'type'}
        return value

    @model_validator(mode='after')
    def complete_inventory(self):
        summary = self.foreground_summary
        count = len(self.subjects)
        retained_count=len(self.background_retained)
        if summary.subject_count != count or summary.background_retained_count != retained_count or summary.has_nontext_subjects != bool(count+retained_count):
            raise ValueError('foreground_summary 的主体判断/数量与subjects、background_retained两表不一致；请清点独立取材和底图保留项，不能遗漏主体')
        if self.background_retained and self.background!='extract' and not self.background.startswith('background_version_'):
            raise ValueError('background_retained需要background="extract"，或已登记的background_version_底图版本ID；不能使用core冒充干净底图。复用时保留原底图recipe.retained清单，由素材工具核对来源，不需要再次出图。')
        if not count and not retained_count and not summary.text_only_reason:
            raise ValueError('subjects 为空时必须明确 text_only_reason：确认原图确为纯文字/抽象装饰，没有独立人物、物件、图标、照片或图表')
        if count+retained_count and summary.text_only_reason is not None:
            raise ValueError('有主体时不能声明 text_only_reason；请保留正确的主体清单')
        if not count and not retained_count and not self.texts:
            raise ValueError('取材清单不能既没有主体也没有原文；抽象背景不能代替分镜内容')
        names=[]
        for retained in self.background_retained:
            if retained.name.strip() in names:
                raise ValueError('background_retained不能重复登记同一主体')
            names.append(retained.name.strip())
            for subject in self.subjects:
                a,b,c,d=retained.source_box
                x,y,r,s=subject.source_crop
                overlap=max(0,min(c,r)-max(a,x))*max(0,min(d,s)-max(b,y))
                union=(c-a)*(d-b)+(r-x)*(s-y)-overlap
                if subject.name.strip()==retained.name.strip() or (subject.source=='core' and union>0 and overlap/union>.85):
                    raise ValueError('同一主体不能同时列入background_retained和subjects，避免底图上重复贴图')
        for subject_index, subject in enumerate(self.subjects):
            if subject.source != 'core': continue
            left, top, right, bottom = subject.source_crop
            for text_index, label in enumerate(self.texts):
                if _text_key(label.text) not in {_text_key(t) for t in subject.embedded_text}: continue
                x1, y1, x2, y2 = label.source_box
                intersection = max(0, min(right, x2)-max(left, x1))*max(0, min(bottom, y2)-max(top, y1))
                if intersection > .5*(x2-x1)*(y2-y1):
                    raise ValueError(f'texts[{text_index}] 与 subjects[{subject_index}].embedded_text 在同一core区域重复计入原文；'
                                     '保真裁片保留则只列embedded_text，需独立重建则裁掉旧字后仅列texts')
        return self

    def to_design(self, context, sources):
        """Convert only for legacy material workers/conservation, not layout.

        at/font are server placeholders, not styling. Label boxes retain core
        locations; reference-image subject boxes are NOT core locations.
        Reconstruction receives explicitly labelled provenance, not this
        adapter wholesale or ambiguous crop/box fields.
        """
        if 'core' not in sources:
            raise ValueError('取材清单缺少 core 原图，不能定位或核对素材')
        if self.background not in ('solid', 'extract', 'core') and self.background not in sources:
            raise ValueError('独立背景未绑定本镜素材：'+self.background)
        subjects = []
        for index, subject in enumerate(self.subjects):
            if subject.source not in sources:
                raise ValueError(f'subjects[{index}] {subject.name} 的 source={subject.source} 未绑定本镜素材；不能读取任意路径')
            left, top, right, bottom = subject.source_crop
            if subject.source == 'core' and subject.treatment == 'preserve' and (right-left)*(bottom-top) > .94:
                raise ValueError(f'subjects[{index}] 不能以整张 core 海报充当保真前景；请定位独立主体范围')
            subjects.append(dict(source=subject.source, crop=subject.source_crop, box=subject.source_crop,
                treatment=subject.treatment, embedded_text=list(subject.embedded_text), at=0))
        labels = [dict(text=label.text, box=label.source_box, color='#FFFFFF', font_size=32, at=0)
                  for label in self.texts]
        return resolve_background(Design(background=self.background, background_color=self.background_color,
            background_notes=self.background_notes, background_is_uniform=self.background_is_uniform,
            background_retained=self.background_retained,
            subjects=subjects, labels=labels))


DIRECTOR = '''你是原图分层动效的“取材盘点员”，不是排版师或动画导演。只返回符合 Inventory schema 的 JSON。
本轮只做：看清core原海报→盘点背景/前景/原文→选择素材来源及源图取材范围。不要输出字体大小、最终box、at、enter、motion、代码或新排版。
先通览整张core（左/中/右、上/中/下），在foreground_summary逐项清点所有需保留的人物、物件、语义图标、成组图标、照片、原始数据图。
所有语义主体必须在subjects或background_retained中明确登记，不能因为简单、像图标、没有独立参考或想省步骤而删掉。summary.subject_count等于subjects数，background_retained_count等于保留底图数，has_nontext_subjects按两者合计判断。
只有core确实仅有文字和抽象装饰才允许两表皆空；此时has_nontext_subjects=false，两个count=0，并填text_only_reason。只要任一表有主体就不填text_only_reason。普通文本框本身不是语义主体。
背景制作是编辑core，优先“移除指定元素”，不是另造一张相似背景：background=extract，保留原图色调、纹理、光带、粒子、建筑轮廓及未指明删除的装饰。有合适独立底图才可绑定它；真正均匀纯色才用solid。core整张不能直接冒充已编辑背景。
对半透明、玻璃、液体、烟雾或与环境光紧密融合的非证据性示意物体，若不需要单独运动，可选择background_retained：给name、core中的source_box、具体reason及content_kind(illustration/decorative_object)。该物体保留原位在底图中，贯穿全镜；其他文字/标注仍按字幕动画。不能再列入subjects或在代码里重复绘制。不能把真实人物/医院照片、病理切片、实验/数据图列为此类，它们需要原像素保真。不得保留旧文字/普通文本框来逃避重建。不是强制所有物体都留底图：需要独立入场且能可靠分离的仍进subjects。
主体：每项给role、name、实际source素材ID、该source上的source_crop、treatment、embedded_text。role为object/photo/evidence/text_panel。优先对应的本镜独立参考；无参考就从core局部取材，不能报“没有参考所以无主体”。
source_crop是源图片的归一化[左,上,右,下]，不是宽高、更不是最终放置位置。完整保留主体及必要底座/边缘，尽量排除外侧旧文字和独立文本底板；不能切掉主体头顶、手脚或左右边缘。
照片、病理切片、原始实验/数据图用photo/evidence+preserve保真，禁止生成式重绘；普通物件/人物/图标需去背景时cutout。带旧色底的原矩形不等于干净主体，不能把它当无缝素材；透明物体无法无损分离时优先上述显式底图保留。evidence_note可简要解释处理理由，提供时不能为空。
texts逐字记录core所有需代码重建的文字与其原图source_box，包括主标题、数字、单位、分组名称、脚注，不能仅盯着正文漏主标题；不改写，不增加旁白中原图没有的文字。
裁片里完整保留的同一处文字放subjects.embedded_text，不要同时再计入texts；不同位置确实重复出现的原文可分别记录。半截残字不算“保留原文”，应调整源裁区把旧字完整排除。
普通文本框、按钮底板、发光框线通常更适合texts+decorations(kind=frame)代码重建，边框四边和圆角一起恢复。如果原框样式复杂且完整裁取效果更好，也可用role=text_panel保真保留，文字只登记embedded_text，不再叠一份。你自行选择，评价标准是原文完整、框线不截断、没有拼贴接缝。真实期刊封面、数据图、照片内部文字仍优先原像素保真。
纯装饰箭头、双箭头、分隔线、独立框线放decorations的kind/description/source_box，不用«、»、→等字符冒充文字，也不要输出polyline路径；这些由后续排版重建。语义图标与其外侧文本框尽量分离，不能把邻接卡片残边当作图标轮廓。
无需动画的抽象背景装饰写background_notes；独立控制的简单线框写decorations；语义图标/人物/物件明确登记subjects或符合条件的background_retained，不能用decorations代替取材。
最后复核：所有语义主体均在两表之一且不重复；所有原文在texts或embedded_text；保留底图不是漏记主体，decorations没有吞掉文字。不要发起额外自检调用。'''
