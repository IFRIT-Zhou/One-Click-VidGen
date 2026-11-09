"""Persistable semantic material review, independent of typography/layout.

The model must inspect the supplied image evidence. These contracts do not
infer semantic completeness from alpha values or alter/select image pixels.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .vibe_layered_retake import SourceRetake


VERSION = 'ocv-code-material-review-v3'
SubjectId = Annotated[str, Field(pattern=r'^subject_[0-7]$')]
VariantId = Annotated[str, Field(pattern=r'^subject_[0-7](?:_original)?$')]


class SourceEdgeReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    continues_outside: Annotated[bool, Field(strict=True)] | None = Field(
        description='逐边查看当前黄色裁框内侧与外侧的真实源图像素：外侧仍有同一主体可见部分为true，确认没有为false，看不清为null。若已到完整源图边界，外面没有源图像素，不要求补造源图未包含的部分。')
    evidence: str = Field(min_length=16, max_length=1600,
        description='具体描述该边内侧可见部件、外侧相邻像素/轮廓是否延续；已到完整源图边界时明确说明。只记录本次实际观察，不引用旧结论作证据。')

    @field_validator('evidence')
    @classmethod
    def detailed_edge_evidence(cls, value):
        if len(value.strip()) < 16:
            raise ValueError('裁区逐边证据必须描述边界两侧实际观察，不能只填完整/正常')
        return value.strip()


class SourceEdgesReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    top: SourceEdgeReview
    right: SourceEdgeReview
    bottom: SourceEdgeReview
    left: SourceEdgeReview


class SubjectMaterialReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    asset_id: SubjectId
    source_edges: SourceEdgesReview = Field(
        description='必须按top/right/bottom/left逐边独立检查当前裁框两侧的完整源图像素，不可省略某边、凭另一边推断或复制旧报告。')
    source_crop_complete: Annotated[bool, Field(strict=True)] | None = Field(
        description='必须先对照完整 source 原图及带当前裁框的邻域图，检查当前源裁区是否包含该主体全部可见部件。裁片与抠图看起来一致并不代表裁区完整；无法确认填 null。')
    source_crop_evidence: str = Field(min_length=16, max_length=2400,
        description='描述完整原图中主体的范围及当前裁框四边外是否仍有同一主体轮廓，特别是头顶、脚底、外围小部件；说明边界截断或夹带别的文字/边框的位置。')
    prepared_complete: Annotated[bool, Field(strict=True)] | None = Field(
        description='先确认源裁区完整，再逐项对照原始裁片，判断prepared/cutout是否保留完整原图中该主体的全部可见组成。源裁区不完整时不能为true；无法可靠判断为null。')
    original_complete: Annotated[bool, Field(strict=True)] | None = Field(
        description='提供了subject_N_original时，对照完整source原图判断该原裁片是否完整；源裁区不完整时不能为true。无法判断或没有该版本时为null。')
    recommended_asset_id: VariantId | None = Field(
        description='只能推荐本主体实际提供且判定 complete=true 的版本；均不完整时 null，issues 明确需重新取材。不是文件路径。')
    evidence: str = Field(min_length=16, max_length=2400,
        description='详细记录原裁片与 prepared 中可见组成的数量/方位、边缘/外围轮廓、具体保留或缺失位置，以及推荐依据。与字号、版式无关。')

    @field_validator('evidence', 'source_crop_evidence')
    @classmethod
    def detailed_evidence(cls, value):
        if len(value.strip()) < 16:
            raise ValueError('素材完整性证据必须详细说明实际观察，不能只填完整/正常')
        return value.strip()

    @model_validator(mode='after')
    def crop_verdict_respects_each_edge(self):
        verdicts = [getattr(self.source_edges, edge).continues_outside
                    for edge in ('top', 'right', 'bottom', 'left')]
        if any(verdict is True for verdict in verdicts):
            if self.source_crop_complete is not False:
                raise ValueError('源裁区逐边检查发现同一主体延伸到框外，source_crop_complete必须为false')
        elif any(verdict is None for verdict in verdicts):
            if self.source_crop_complete is not None:
                raise ValueError('源裁区逐边检查仍有未知边界且未确认截断，source_crop_complete必须为null')
        return self


class MaterialReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: Literal['ocv-code-material-review-v3'] = VERSION
    subjects: list[SubjectMaterialReview] = Field(max_length=8)
    issues: list[Annotated[str, Field(min_length=8, max_length=1600)]] = Field(default_factory=list, max_length=16,
        description='素材缺损/重新取材问题，逐项包含 subject_N。无任何完整版本时必须说明需重新取材；不要讨论排版或字体。')
    retakes: list[SourceRetake] = Field(default_factory=list, max_length=8,
        description='需要重裁时在此提交该主体完整source原图上的新source_crop与具体reason。只能修改已有主体的取材范围，不是prepared裁片坐标，不改身份/来源。待重新取材主体的recommended_asset_id必须为null。')

    @model_validator(mode='before')
    @classmethod
    def strip_exact_json_object_marker(cls, value):
        # Some JSON-mode providers echo this transport marker into the body.
        # Strip only that exact top-level value; all other extra fields fail.
        if isinstance(value, dict) and value.get('type') == 'json_object':
            return {key: item for key, item in value.items() if key != 'type'}
        return value

    @field_validator('issues')
    @classmethod
    def concrete_issues(cls, values):
        if any(len(value.strip()) < 8 for value in values):
            raise ValueError('素材问题不能只有空白或泛泛结论')
        return [value.strip() for value in values]


def _subjects(inventory):
    rows = inventory.get('subjects', []) if isinstance(inventory, Mapping) else getattr(inventory, 'subjects', [])
    if not isinstance(rows, (list, tuple)) or len(rows) > 8:
        raise ValueError('素材清单 subjects 无效或超过 8 项')
    return rows


def request_schema(inventory):
    """Advertise this request's exact identities, not an eight-slot example.

    The final semantic validator remains authoritative. In particular, this
    never fills missing judgments from an older response or assumes success.
    """
    ids=[f'subject_{index}' for index,_ in enumerate(_subjects(inventory))]
    schema=MaterialReview.model_json_schema()
    subjects=schema['properties']['subjects']
    subjects.update(minItems=len(ids),maxItems=len(ids),
        description='完整核查报告，不是局部补丁。必须恰好返回本轮每个主体各一项：'+', '.join(ids))
    if ids:
        schema['$defs']['SubjectMaterialReview']['properties']['asset_id']['enum']=ids
        schema['$defs']['SourceRetake']['properties']['asset_id']['enum']=ids
        subjects['allOf']=[{'contains':{'type':'object','required':['asset_id'],
            'properties':{'asset_id':{'const':identity}}},'minContains':1,'maxContains':1}
            for identity in ids]
    schema['properties']['retakes']['maxItems']=len(ids)
    return schema


def request_contract(inventory, assets, source_bindings):
    """Identity/tool checklist only. No synthetic visual observations."""
    ids=[f'subject_{index}' for index,_ in enumerate(_subjects(inventory))]
    bindings={row['asset_id']:row for row in source_bindings}
    checklist=[]
    for identity in ids:
        source=bindings.get(identity,{})
        checklist.append({'asset_id':identity,
            'available_variants':[name for name in (identity,identity+'_original') if name in assets],
            'source_id':source.get('source_id'),
            'current_source_crop':list(source.get('current_source_crop') or [])})
    return {'required_subject_ids':ids,'subject_count':len(ids),'response_mode':'complete_report',
        'subject_checklist':checklist,
        'retake_rules':{'allow_same_pixel_crop':False,'supports_mask_edit':False,
            'instruction':'retakes只能提交实际改变源图像素范围的矩形裁区。不能用相同原框登记占位、请求擦除/mask或重试。无法可靠确定可行裁区时不要猜坐标，保留该主体完整核查项，在issues说明工具限制；不能删掉主体或改判为完整。'},
        'repair_instruction':'即使只有一项报错，也必须重新返回名单内所有主体的完整报告，以及仍有效的issues和retakes；不能只返回修改项。'}


def validate_material_review(review, assets, inventory):
    """Validate identity/completeness contracts and return a detached model.

    ``assets`` is the server-owned ID mapping; this pure function does not open
    files or call providers. Persist ``result.model_dump(mode='json')``. A false
    completeness flag is durable evidence that later layout passes must not
    override without a fresh material-only review of new prepared pixels.
    """
    if not isinstance(assets, Mapping):
        raise ValueError('素材审查需要服务端已绑定的 assets ID 映射')
    # Revalidate model instances too; callers cannot bypass checks via copy(update=...).
    result = MaterialReview.model_validate(review.model_dump(mode='json') if isinstance(review, MaterialReview) else review)
    expected = {f'subject_{index}' for index, _ in enumerate(_subjects(inventory))}
    actual = [row.asset_id for row in result.subjects]
    if len(actual) != len(set(actual)):
        raise ValueError('素材语义检查存在重复 subject ID')
    if set(actual) != expected:
        raise ValueError(f'素材语义检查必须覆盖每个已有主体，不能增删身份；缺少 {sorted(expected-set(actual))}，多余 {sorted(set(actual)-expected)}')
    retake_ids = [row.asset_id for row in result.retakes]
    if len(retake_ids) != len(set(retake_ids)):
        raise ValueError('同一主体不能重复请求重新取材')
    if not set(retake_ids).issubset(expected):
        raise ValueError('重新取材只能引用本次已有主体 ID，不能新增或替换主体')
    pending_retakes = set(retake_ids)
    for row in result.subjects:
        original = row.asset_id+'_original'
        if row.source_crop_complete is not True and (
                row.prepared_complete is True or row.original_complete is True):
            raise ValueError(row.asset_id+' 源裁区未确认完整，不能把原裁片或抠图判为完整；须对照完整source原图重新取材')
        if row.prepared_complete and row.asset_id not in assets:
            raise ValueError(row.asset_id+' prepared 文件未实际提供，不能判为完整')
        if original not in assets and row.original_complete is not None:
            raise ValueError(original+' 未实际提供，original_complete 必须为 null')
        complete = {key for key, verdict in ((row.asset_id, row.prepared_complete), (original, row.original_complete))
                    if verdict is True and key in assets}
        recommendation = row.recommended_asset_id
        if row.asset_id in pending_retakes:
            if recommendation is not None:
                raise ValueError(row.asset_id+' 已请求重新取材，推荐必须为 null；新裁片核查通过前不能沿用旧结论')
        elif recommendation is not None:
            if recommendation not in (row.asset_id, original):
                raise ValueError(row.asset_id+' 不能推荐另一个主体的素材')
            if recommendation not in assets:
                raise ValueError(recommendation+' 推荐素材未实际提供')
            if recommendation not in complete:
                raise ValueError(recommendation+' 已判残缺或尚未判完整，不能推荐；透明缺失不能靠调 opacity 恢复')
        elif complete:
            raise ValueError(row.asset_id+' 已有完整版本，请明确推荐其中一个实际素材 ID')
        if not complete or row.asset_id in pending_retakes:
            issues = [issue for issue in result.issues if row.asset_id in issue]
            if not any(re.search(r'重新取材|重新裁|重取|重裁|retake|re[- ]?crop', issue, flags=re.I) for issue in issues):
                raise ValueError(row.asset_id+' 没有完整可用素材或已请求重裁；推荐须为 null，issues 必须逐主体明确说明需重新取材')
    return result


def validate_material_selection(review, measured):
    """Reject actual DOM assets previously judged incomplete/uncertain.

    This never changes a renderer ID or picks the recommended alternative.
    The caller validates the review against the current inventory/assets first.
    """
    result = MaterialReview.model_validate(review.model_dump(mode='json') if isinstance(review, MaterialReview) else review)
    decisions = {row.asset_id: row for row in result.subjects}
    if len(decisions) != len(result.subjects):
        raise ValueError('素材语义检查存在重复主体 ID')
    pending_retakes = {row.asset_id for row in result.retakes}
    if not isinstance(measured, Mapping) or not isinstance(measured.get('assets', []), list):
        raise ValueError('缺少实际浏览器素材测量列表')
    for actual in measured.get('assets', []):
        identity = actual.get('id') if isinstance(actual, Mapping) else None
        if identity == 'background':
            continue
        if not isinstance(identity, str) or not re.fullmatch(r'subject_[0-7](?:_original)?', identity):
            raise ValueError('实际渲染使用了未审查的主体素材 ID')
        canonical = identity.removesuffix('_original')
        decision = decisions.get(canonical)
        if decision is None:
            raise ValueError(identity+' 未包含在独立素材语义检查中')
        if canonical in pending_retakes or decision.source_crop_complete is not True:
            raise ValueError(identity+' 的源裁区未确认完整或仍待重新取材，不能进入排版；证据：'
                             +decision.source_crop_evidence+'；未自动替换素材')
        complete = decision.original_complete if identity.endswith('_original') else decision.prepared_complete
        if complete is not True:
            verdict = '残缺' if complete is False else '无法确认完整'
            recommendation = f'；审查建议使用 {decision.recommended_asset_id}' if decision.recommended_asset_id else '；没有已确认完整版本，需要重新取材'
            raise ValueError(f'{identity} 已在独立素材审查中判为{verdict}，不能由排版审图推翻。证据：{decision.evidence}{recommendation}；未自动替换素材')
    return True


def _read(item, key, default=None):
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _plain(value, depth=0):
    if depth > 12:
        raise ValueError('素材审查指纹说明嵌套过深')
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('素材审查指纹不能含非有限数值')
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 128:
        return [_plain(item, depth+1) for item in value]
    if isinstance(value, Mapping) and len(value) <= 128 and all(isinstance(key, str) for key in value):
        return {key: _plain(item, depth+1) for key, item in value.items()}
    raise ValueError('素材审查指纹只能包含有界 JSON 说明，不接受文件对象或其他运行时对象')


def _file_fingerprints(mapping, label):
    if not isinstance(mapping, Mapping) or len(mapping) > 64:
        raise ValueError(label+' 素材映射无效或数量过多')
    if any(not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_:-]{1,100}', identity) for identity in mapping):
        raise ValueError(label+' 包含无效素材 ID')
    result = {}
    for identity, value in sorted(mapping.items()):
        try:
            file = Path(value)
            if not file.is_file() or not 0 < file.stat().st_size <= 64*1024*1024:
                raise ValueError('missing/oversized')
            digest, length = hashlib.sha256(), 0
            with file.open('rb') as stream:
                while chunk := stream.read(1024*1024):
                    length += len(chunk)
                    if length > 64*1024*1024:
                        raise ValueError('oversized during read')
                    digest.update(chunk)
            result[identity] = {'sha256': digest.hexdigest(), 'bytes': length}
        except (OSError, TypeError, ValueError):
            raise ValueError(f'{label} 的 {identity} 文件缺失、不可读或超过64MB；未生成素材缓存指纹') from None
    return result


def fingerprint(inventory, sources, assets, notes=None):
    """Content-only cache key; includes alpha bytes, semantics and prompt version.

    Filesystem paths are neither hashed nor returned. Copying identical bound
    files to another folder keeps the key; any source/prepared bytes, crop or
    semantic requirement change invalidates it. No images are altered/opened.
    """
    subjects = []
    for index, row in enumerate(_subjects(inventory)):
        subjects.append({'asset_id': f'subject_{index}',
            'source': _read(row, 'source', _read(row, 'source_id')),
            'crop': _read(row, 'source_crop', _read(row, 'crop')),
            **{key: _read(row, key) for key in ('name', 'treatment', 'embedded_text', 'evidence_note', 'source_box')}})
    payload = {'version': VERSION, 'prompt_sha256': hashlib.sha256(PROMPT.encode('utf-8')).hexdigest(),
        'subjects': _plain(subjects), 'foreground_summary': _plain(_read(inventory, 'foreground_summary')),
        'notes': _plain(notes), 'sources': _file_fingerprints(sources, 'sources'), 'assets': _file_fingerprints(assets, 'assets')}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > 1024*1024:
        raise ValueError('素材审查指纹说明超过1MB')
    return hashlib.sha256(encoded).hexdigest()


PROMPT = '''你是独立的“素材完整性审查员”，只审查真实素材，不设计排版，不关注字号、标题、数字大小或动画。
只返回符合 MaterialReview schema 的 JSON。对 inventory 中每一个 subject_N 都必须提交一项 subjects，不增删主体、不改 ID。
response_contract是本次真实身份与工具清单，不是示例。subjects数量必须等于subject_count，required_subject_ids中的每个ID必须恰好出现一次，逐项核对subject_checklist中的源图、当前裁区和实际可用版本。首次核查和格式修正都返回完整报告，不是局部补丁；即使validation_error只点名一个主体，也不能省掉其他主体及其仍有效的issues/retakes。不要因同属一个source或画面区域重叠而合并身份。
检查顺序必须是：完整source原图 → 当前源裁框及框外邻域 → 原始裁片 → 实际抠图。绝不能只比较“原始裁片和抠图相同”就声称主体完整：它们可能一起缺了原图里的头顶、脚底或外围小部件。
先依据source_manifest/inspection报告及每项source_context.source_id把每个主体绑定到实际完整source图，查看完整原图与该主体的裁区邻域证据。core仅是海报；若subject来源是独立参考图，就对照那张完整参考图，不能把core上的位置当它的source_crop。source_context.available=false意味着缺少完整来源证据，此时source_crop_complete必须为null，不能仅凭下排裁片判为完整；报告缺失证据，不猜新裁区。
裁框/彩色标线是程序绘制的检查覆盖层，不是原画的一部分，不要抠进素材或当作主体轮廓。黄色标示当前裁区，青色标示邻域范围；source_context提供source_size、crop_pixel_window、context_pixel_window以及full_source_view/neighborhood_view的显示像素到源像素映射。邻域图包含框外像素；逐边检查当前裁框外是否还有同一个主体的延伸：最上方头顶、下方脚底/支架、左右外围小件、细线连接与自然阴影。若主体在完整源图边缘本来就已被截断，不能要求恢复源图不存在的内容；说明这是源图自身边界，目标是保留全部原本可见内容。
每个主体必须填写source_edges.top/right/bottom/left四个独立检查项。逐边先定位当前黄色裁框而非青色邻域边界，再分别看该边内侧和外侧的实际像素：内侧什么轮廓触边，外侧是否继续出现同一个部件，是否只有背景或属于另一主体的内容。bottom必须检查最下方可见脚部、底座或阴影的实际终点，不能因为上部完整而略过下边；其他三边同样逐一检查。evidence必须描述该边两侧看到什么，不可四边都重复“完整”。continues_outside=true表示完整源图中同一主体确实延伸到当前裁框外，false表示确认没有，null表示无法可靠观察。若裁边已达到完整源图自身边界，明确写出该事实；外面没有源图像素时无需凭空补全，不能将源图自身截断误当可通过重裁修复。
若提供material_edges_NN四边放大证据，优先按top/right/bottom/left逐条读取，再回完整source图核对同一主体的归属；它只供本次素材审查。report.items[].edge_evidence关联证据image_id，edge_views给出各边inside_display_side/outside_display_side、完整源图裁线位置、源窗口及source_pixel_to_sheet_affine映射。top/bottom条带保持原方向，left/right条带为放大显示逆时针旋转90度：必须按报告标示辨认框内/框外，不能把旋转后的上/下当完整源图的top/bottom，也不能拿证据拼图坐标直接重裁。outside_source_pixels_available=false表示该边已到原图边界，没有框外源像素，不应把空白说明区当缺失内容。所有retakes.source_crop始终按未旋转的完整source原图归一化[left,top,right,bottom]填写。
逐边看到轮廓延伸时还须判定归属：主体自身的肢体、支架、局部底座和附属结构需要完整保留；贯穿整张海报的环境光带、背景波纹/地面反光、其他信息框和不属于本主体的文字不算主体部件。不可因全图背景无限延伸而不断扩大裁区。若旧文本框/标签边框与主体重叠，矩形重裁无法在保留完整主体的同时排除它，就保留主体完整范围，在issues明确记录污染位置及当前矩形裁切的能力限制；不要虚构“再扩大一次”能去掉旧框，也不得为去框切掉主体。当前取材工具不提供局部擦除或mask编辑，不得用相同裁框作为mask请求的占位操作。裁区完整性仍仅按主体自身可见内容判断，prepared是否误删主体仍须独立核查；记录工具限制不等于已经修复或批准裁去主体。
逐边结论约束总评：任一continues_outside=true则source_crop_complete必须为false；没有true但任一null则必须为null；只有四边均false才可考虑true。若source_context.available=false或裁框/邻域映射看不清，不得写四边均false来绕过核查。旧检查报告和前轮重裁理由只是待核实线索，不是本轮事实；新素材返回后四边都必须重新看图，不可继承旧“完整”结论，也不可将已经修复的旧问题照抄成再次重裁理由。
source_crop_complete仅回答当前裁区是否包含完整source里该主体全部可见部分，source_crop_evidence必须描述原图与四边界的实际关系。主体触边不自动等于截断，也不能凭小缩略图看似完整就判true；看不清用null。若为false/null，prepared_complete和original_complete都不能为true，即便两张裁片完全一致。
输入 material_inspection_NN 是同一主体的一张组合证据图：上排为 Full source 完整来源和 Neighborhood beyond current crop 裁区外邻域；下排左为原始裁片，中为实际 prepared/cutout 在棋盘底上，右为 alpha 减少位置的红色提示。下排三者同像素比例，头部/四肢/器件/外围图标等边缘可直接对照；上排与下排比例不同，不要把显示位置当源坐标。报告semantic_crop_verdict=not_inferred表示程序没有替你判断裁区完整性，必须由你看图检查。
确认源裁区完整后，再看下排左侧原始裁片：清点实际可见组成的数量和方位，再看下排中侧是否全部保留。不要只看最大/中央主体；必须检查外围小组成、最上方、左右边缘、细线与轮廓连接。evidence 写清观察到的数量/位置和缺失部位，无法确定时填 null 并解释，不得无依据填写完整。
prepared_complete表示实际下排中图相对完整source的语义主体是否完整；original_complete表示实际提供的subject_N_original相对完整source是否完整。无法可靠判断的版本用null并写明不确定之处，不能默认完整。没有original版本时original_complete=null，不可把完整source海报假装成已提供的original裁片。
透明区域可能是正确移除的背景，因此红色/alpha 比例本身不能判错误；必须对照原轮廓和主体组成进行视觉语义判断。若 prepared 中主体的一部分被抠除，原像素已透明，调高 CSS opacity、放大、移动或加底板都不能补回缺失部分。不要把后续排版调整作为素材修复。
推荐只能使用实际提供的同主体 asset ID，且对应完整性必须为 true。prepared 残缺而 original 完整时，可推荐 subject_N_original 并说明其背景仍保留；不能因为 prepared 看起来更干净而忽略缺损。
若源裁框截断主体，或裁片夹带了可以通过重新取材排除的无关文字/旧卡框：在retakes提交这个subject_N的新source_crop与reason。source_crop始终是该主体冻结的完整source原图归一化[左,上,右,下]，不是当前裁片内坐标、邻域图坐标、宽高或最终排版位置。依据完整源图确定，覆盖该主体全部可见部件及必要边缘；不要盲目统一扩边把相邻文字或另一个主体纳入，也不要为躲旧字切掉脚/轮廓。只能用已有source和同一主体，不提出任意路径、新主体、新图像生成或身份替换。
每个待重裁主体recommended_asset_id=null，issues必须写“subject_N需要重新取材”及具体原因，即使主体完整只是夹带外部边框也一样。retakes只列实际需要改变取材范围的主体，禁止相同框原样重试或无必要重裁已正确素材；一次最多8个，剩余轮数以本次输入为准。请求只是待执行方案，不是已经修复；必须等新素材返回后重新对照完整原图验收，不能提前写为完整。
若两个版本均不完整或没有完整可用版本，但从当前图片无法可靠确定新裁区：recommended_asset_id=null、retakes不猜坐标，issues中写“subject_N需要重新取材”并说明缺失与不确定位置。若只是RMBG误删而原裁片完整，优先选择original；不能无故变更原始裁框。半截的原图文字不算可保留内容；不得以embedded_text伪称裁掉的半个字完整。
本报告会独立持久化。后续排版模型不得为了排版方便重新选择本报告已判残缺的版本；新材料产生后须重新做素材审查。不要因为后续字体/布局改善而撤销此处的缺损事实。
原始研究证据、人物身份与内容必须保真；本轮只输出检查结论和有限本地重裁请求，不进行图像生成、代码编辑或素材修改。'''
