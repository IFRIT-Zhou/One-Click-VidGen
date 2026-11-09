"""Explicit, shot-local material tools for the Vibe authoring agent.

An inventory is a plan, not a command to run RMBG or an image provider.  Every
material operation below is explicit and versioned.  Renderer aliases may move
to a new version, but source files and previous versions are never overwritten.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import uuid

from PIL import Image, ImageOps

from . import vibe_rmbg as rmbg
from .vibe_layered_inventory import Inventory, InventorySubject
from .vibe_materials import MaterialError, _image, crop_asset
from .vibe_agent_policy import validate_subject_policy


_ID = re.compile(r'^[A-Za-z0-9_:-]{1,80}$')
_SUBJECT = re.compile(r'^subject_(0|[1-9][0-9]*)$')
_RESERVED = re.compile(r'^(?:background|subject_(?:0|[1-9][0-9]*)(?:_original)?)$')
_VERSION = 'ocv-agent-materials-v2'
_LEGACY_VERSION = 'ocv-agent-materials-v1'
_PIXEL_VERSION = 'original-rgb-exif-crop-rmbg-untrimmed-v1'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _subject_signature(subject, source_entry):
    """Pixel-producing inputs only; labels/claims never change the image bytes.

    Inventory still validates evidence roles and text claims on every update,
    and the full inventory remains in the preview fingerprint. This cache does
    not approve semantic changes; it only avoids running the same crop twice.
    """
    return dict(source=subject.source, source_sha256=source_entry['sha256'],
                source_crop=list(subject.source_crop), treatment=subject.treatment,
                processing=_PIXEL_VERSION)


def _signature_key(spec):
    return hashlib.sha256(_json(spec).encode('utf-8')).hexdigest()


def _background_signature(inventory):
    if inventory is None:
        return None
    return {key: value for key, value in inventory.model_dump(mode='json').items()
            if key.startswith('background')}


class MaterialWorkspace:
    def __init__(self, project, folder, sources, canvas, cancelled=None, *, advanced_subject_separation=True):
        self.project = Path(project).resolve()
        self.folder = Path(folder).resolve()
        if self.folder == self.project or not self.folder.is_relative_to(self.project):
            raise MaterialError('素材工作目录必须位于当前项目的独立任务目录内')
        if (not isinstance(canvas, (tuple, list)) or len(canvas) != 2 or
                any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 8192 for n in canvas)):
            raise MaterialError('素材画布尺寸无效')
        self.canvas = tuple(canvas)
        self.cancelled = cancelled
        if type(advanced_subject_separation) is not bool:
            raise MaterialError('主体分离策略须为布尔值，未执行素材处理')
        # Legacy callers keep their original permissions. New cloud tasks pass
        # the user's persisted (default False) choice explicitly.
        self.advanced_subject_separation = advanced_subject_separation
        self.sources = {}
        self._entries = {}
        self._aliases = {}
        self.prepared = {}
        self._subject_history = {}
        self.inventory_delta = dict(retained=[], reused=[], invalidated=[], missing=[], reasons={})
        self.inventory = None
        self.design = None
        self.epoch = 0
        self._background = None
        self._background_history = {}
        for key, path in sources.items():
            self._valid_id(key)
            if _RESERVED.fullmatch(key):
                raise MaterialError('源素材 ID 与渲染别名冲突：' + key)
            path = self._scoped(path, self.project)
            self.sources[key] = path
            self._entries[key] = self._entry(path, 'source')
        if 'core' not in self.sources:
            raise MaterialError('素材工作区缺少本镜 core 原图')

    @staticmethod
    def _valid_id(identity):
        if not isinstance(identity, str) or not _ID.fullmatch(identity):
            raise MaterialError('素材只能使用已登记的 ID，不能使用文件路径')

    def _check_cancelled(self):
        if self.cancelled and self.cancelled():
            raise InterruptedError('Vibe 素材操作已取消')

    @staticmethod
    def _scoped(path, root):
        path = Path(path).resolve()
        if path == root or not path.is_relative_to(root):
            raise MaterialError('素材路径超出当前镜头授权的工作范围')
        return _image(path)

    def _entry(self, path, kind):
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image)
            width, height = oriented.size
        return dict(path=Path(path), sha256=_sha(path), width=width, height=height, kind=kind)

    @property
    def registry(self):
        result = {key: value['path'] for key, value in self._entries.items()}
        result.update({key: self._entries[value]['path'] for key, value in self._aliases.items()})
        return result

    def _resolve(self, identity):
        self._valid_id(identity)
        target = self._aliases.get(identity, identity)
        entry = self._entries.get(target)
        if entry is None:
            raise MaterialError('素材未绑定到本镜任务：' + identity)
        root = self.project if entry['kind'] == 'source' else self.folder
        path = self._scoped(entry['path'], root)
        if _sha(path) != entry['sha256']:
            raise MaterialError('素材内容已改变，请重新读取本镜素材：' + identity)
        return target, entry

    def assets(self):
        """Only current renderer aliases; inspection and source IDs stay private."""
        return {key: self._resolve(key)[1]['path'] for key in self._aliases}

    def fingerprint(self):
        current = {key: self._resolve(key)[1]['sha256'] for key in sorted(self._aliases)}
        body = dict(inventory=self.inventory.model_dump(mode='json') if self.inventory else None,
                    assets=current, canvas=self.canvas)
        return hashlib.sha256(_json(body).encode('utf-8')).hexdigest()

    def _directory(self):
        self._check_cancelled()
        # A new name for every call, including retries; never overwrite old output.
        directory = self.folder / 'agent-materials' / uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def _register(self, path, prefix='material'):
        path = self._scoped(path, self.folder)
        identity = prefix + '_' + uuid.uuid4().hex
        self._entries[identity] = self._entry(path, 'derived')
        return identity

    def _result(self, receipt, image_ids=()):
        receipt.update(epoch=self.epoch, fingerprint=self.fingerprint())
        return dict(receipt=receipt, images={key: self._resolve(key)[1]['path'] for key in image_ids})

    def _spec(self, subject):
        return _subject_signature(subject, self._resolve(subject.source)[1])

    def _validate_prepared(self, item):
        for field in ('original', 'prepared'):
            _, entry = self._resolve(item[field])
            if entry['kind'] != 'derived':
                raise MaterialError('取材缓存不能把源图冒充已处理版本')
        ready = self._resolve(item['prepared'])[1]
        if (item['width'], item['height']) != (ready['width'], ready['height']):
            raise MaterialError('取材缓存尺寸与实际素材不一致')

    def material_status(self):
        wanted = [f'subject_{i}' for i in range(len(self.inventory.subjects))] if self.inventory else []
        return dict(prepared=sorted(self.prepared), missing=[key for key in wanted if key not in self.prepared],
                    background_ready='background' in self._aliases,
                    selected_background_asset_id=self._aliases.get('background'),
                    selectable_background_asset_ids=sorted(self._background_history),
                    inventory_delta=copy.deepcopy(self.inventory_delta),
                    reusable_subjects=[dict(source=item['spec']['source'],
                        source_crop=item['spec']['source_crop'], treatment=item['spec']['treatment'],
                        asset_id=item['prepared'], original_asset_id=item['original'],
                        width=item['width'], height=item['height']) for item in self._subject_history.values()])

    def _inventory_background(self, inventory, *, entries=None, aliases=None, history=None):
        """Resolve only the requested, already-owned background, not all assets.

        Initial source IDs retain the legacy inventory contract. Generated or
        imported background versions belong to this workspace, and may be
        named explicitly without pretending they were an original reference.
        """
        entries = self._entries if entries is None else entries
        aliases = self._aliases if aliases is None else aliases
        history = self._background_history if history is None else history
        requested = inventory.background
        if requested in ('extract', 'solid', 'core') or requested in self.sources:
            return None
        target = aliases.get('background') if requested == 'background' else requested
        if target not in history or target not in entries or entries[target]['kind'] != 'derived':
            available = ', '.join(sorted(history)) or '暂无已登记底图版本'
            raise MaterialError('清单背景未绑定为本任务底图：' + requested +
                '。background 只表示当前已选择底图；可使用已登记版本：' + available +
                '。需要编辑原图时声明 extract 后调用 edit_background；复用已生成底图可声明其版本ID或先 select_background。')
        item = history[target]
        if not isinstance(item, dict) or item.get('asset_id') != target:
            raise MaterialError('清单背景版本与登记记录不一致')
        recipe = item.get('recipe')
        if not isinstance(recipe, dict):
            raise MaterialError('清单背景版本制作记录格式无效')
        retained = [{key: subject.model_dump(mode='json')[key] for key in ('name', 'source_box', 'content_kind')}
                    for subject in inventory.background_retained]
        recipe_retained = recipe.get('retained') or []
        if recipe_retained != retained:
            raise MaterialError('所选背景版本保留的主体与当前清单不一致，不能静默替换底图；请先核对保留主体清单')
        path = self._scoped(entries[target]['path'], self.folder)
        if _sha(path) != entries[target]['sha256']:
            raise MaterialError('已登记底图内容改变，未绑定该版本')
        return target

    def _inventory_design(self, inventory, *, entries=None, aliases=None, history=None):
        # Only original bound sources can be subject inputs. Do not grant the
        # entire image/preview registry to Inventory.to_design's source lookup.
        for subject in inventory.subjects:
            if subject.source not in self.sources:
                raise MaterialError('主体 source 必须是本镜原始绑定素材 core/ref，不能使用派生背景或预览ID：' + subject.source)
        target = self._inventory_background(inventory, entries=entries, aliases=aliases, history=history)
        sources = dict(self.sources)
        if target:
            selected_entries = self._entries if entries is None else entries
            sources[inventory.background] = selected_entries[target]['path']
        return inventory.to_design({'canvas': self.canvas}, sources), target

    def set_inventory(self, inventory):
        self._check_cancelled()
        raw = inventory.model_dump(mode='json') if isinstance(inventory, Inventory) else copy.deepcopy(inventory)
        updated = Inventory.model_validate(raw)
        validate_subject_policy(updated, self.advanced_subject_separation)
        design, requested_background = self._inventory_design(updated)
        old = self.inventory
        if (old is not None and old.model_dump(mode='json') == updated.model_dump(mode='json')
                and (not requested_background or self._aliases.get('background') == requested_background)):
            self.inventory_delta = dict(retained=sorted(self.prepared), reused=[], invalidated=[],
                                        missing=[f'subject_{i}' for i in range(len(updated.subjects))
                                                 if f'subject_{i}' not in self.prepared], reasons={})
            return self.design
        # Resolve every source before mutating aliases, including cached hits.
        specs = [self._spec(subject) for subject in updated.subjects]
        keep = {}
        delta = dict(retained=[], reused=[], invalidated=[], missing=[], reasons={})
        for index, spec in enumerate(specs):
            identity = f'subject_{index}'
            prepared = self.prepared.get(identity)
            cached = self._subject_history.get(_signature_key(spec))
            selected = prepared if prepared and prepared['spec'] == spec else cached
            if selected:
                self._validate_prepared(selected)
                keep[identity] = copy.deepcopy(selected)
                delta['retained' if selected is prepared else 'reused'].append(identity)
            else:
                delta['missing'].append(identity)
            if prepared and prepared['spec'] != spec:
                delta['invalidated'].append(identity)
                changed = [key for key in spec if prepared['spec'].get(key) != spec[key]]
                delta['reasons'][identity] = '取材参数改变：' + ', '.join(changed)
        for identity in set(self.prepared) - {f'subject_{i}' for i in range(len(specs))}:
            delta['invalidated'].append(identity)
            delta['reasons'][identity] = '已从独立主体清单移除；历史版本仍保留'
        for identity in self.prepared:
            self._aliases.pop(identity, None)
            self._aliases.pop(identity + '_original', None)
        for identity, prepared in keep.items():
            self._aliases[identity] = prepared['prepared']
            self._aliases[identity + '_original'] = prepared['original']
        previous_background = self._aliases.get('background')
        if requested_background:
            # The inventory explicitly selected this registered version. Bind
            # that exact version and report it, without a copy or new image call.
            self._aliases['background'] = requested_background
            self._background = copy.deepcopy(self._background_history[requested_background])
            delta['background'] = dict(requested=updated.background, selected=requested_background,
                previous=previous_background, changed=previous_background != requested_background,
                reason='显式复用已登记底图版本；没有出图或替换素材内容')
        elif _background_signature(old) != _background_signature(updated):
            self._aliases.pop('background', None)
            self._background = None
            if previous_background:
                delta['background'] = dict(previous=previous_background, selected=None, changed=True,
                    reason='背景制作要求改变；旧版本仍可检查并显式选择复用')
        self.prepared = keep
        self.inventory_delta = delta
        self.inventory, self.design = updated, design
        self.epoch += 1
        return design

    def prepare_subject(self, subject_id, source_crop=None, treatment=None):
        self._check_cancelled()
        match = _SUBJECT.fullmatch(str(subject_id))
        if not match or self.inventory is None or int(match[1]) >= len(self.inventory.subjects):
            raise MaterialError('请先在取材清单登记该主体，再准备素材：' + str(subject_id))
        index = int(match[1])
        raw = self.inventory.model_dump(mode='json')
        if source_crop is not None:
            raw['subjects'][index]['source_crop'] = source_crop
        if treatment is not None:
            raw['subjects'][index]['treatment'] = treatment
        candidate = Inventory.model_validate(raw)
        validate_subject_policy(candidate, self.advanced_subject_separation)
        candidate_design, _ = self._inventory_design(candidate)
        subject = candidate.subjects[index]
        source = self._resolve(subject.source)[1]['path']
        spec = self._spec(subject)
        cached = self._subject_history.get(_signature_key(spec))
        if cached:
            self._validate_prepared(cached)
            self.inventory, self.design = candidate, candidate_design
            self.prepared[subject_id] = copy.deepcopy(cached)
            self._aliases[subject_id] = cached['prepared']
            self._aliases[subject_id + '_original'] = cached['original']
            self.epoch += 1
            return self._result(dict(subject_id=subject_id, asset_id=cached['prepared'],
                original_asset_id=cached['original'], render_asset_id=subject_id,
                original_render_asset_id=subject_id + '_original', source=subject.source,
                source_crop=list(subject.source_crop), treatment=subject.treatment,
                width=cached['width'], height=cached['height'], rmbg_used=False, reused=True),
                (subject_id, subject_id + '_original'))
        directory = self._directory()
        original = crop_asset({subject.source: source}, subject.source, subject.source_crop, directory, 'original')
        prepared = original
        rmbg_used = False
        if subject.treatment == 'cutout':
            self._check_cancelled()
            output = rmbg.run_cutouts({subject_id: original}, directory / 'rmbg',
                                     should_stop=self.cancelled, trim_padding=None)
            prepared = self._scoped(output[subject_id], directory)
            rmbg_used = True
            with Image.open(original) as before, Image.open(prepared) as after:
                if before.size != after.size or after.mode != 'RGBA':
                    raise MaterialError('抠图返回尺寸或透明通道无效，未替换当前素材')
                if after.getchannel('A').getextrema()[1] == 0:
                    raise MaterialError('抠图结果完全透明，未替换当前素材；可重新取材或选择保真裁片')
        self._check_cancelled()
        # Publish only after all work succeeds. An interrupted crop/RMBG cannot
        # leave the inventory geometry describing a result that does not exist.
        original_id = self._register(original)
        prepared_id = original_id if prepared == original else self._register(prepared)
        self.inventory, self.design = candidate, candidate_design
        self._aliases[subject_id + '_original'] = original_id
        self._aliases[subject_id] = prepared_id
        entry = self._entries[prepared_id]
        self.prepared[subject_id] = dict(spec=spec, original=original_id,
                                         prepared=prepared_id, width=entry['width'], height=entry['height'])
        self._subject_history[_signature_key(spec)] = copy.deepcopy(self.prepared[subject_id])
        self.epoch += 1
        receipt = dict(subject_id=subject_id, asset_id=prepared_id, original_asset_id=original_id,
                       render_asset_id=subject_id, original_render_asset_id=subject_id + '_original',
                       source=subject.source, source_crop=list(subject.source_crop), treatment=subject.treatment,
                       width=entry['width'], height=entry['height'], rmbg_used=rmbg_used, reused=False)
        return self._result(receipt, (subject_id, subject_id + '_original'))

    def inspect_image(self, asset_id, region=None):
        self._check_cancelled()
        target, entry = self._resolve(asset_id)
        if region is not None:
            directory = self._directory()
            path = crop_asset({asset_id: entry['path']}, asset_id, region, directory, 'inspection')
            target = self._register(path, 'inspection')
            entry = self._entries[target]
        return self._result(dict(asset_id=target, requested_asset_id=asset_id, region=region,
                                 width=entry['width'], height=entry['height'], sha256=entry['sha256']), (target,))

    def register_image(self, asset_id, path):
        """Expose a job-local screenshot for inspection, not as a render alias."""
        self._check_cancelled()
        self._valid_id(asset_id)
        if _RESERVED.fullmatch(asset_id) or asset_id in self._entries:
            raise MaterialError('图片 ID 已被占用，请给每轮预览使用不同的版本 ID：' + asset_id)
        path = self._scoped(path, self.folder)
        directory = self._directory()
        version = crop_asset({'preview_source': path}, 'preview_source', (0, 0, 1, 1), directory, 'preview')
        self._entries[asset_id] = self._entry(version, 'derived')
        entry = self._entries[asset_id]
        return self._result(dict(asset_id=asset_id, width=entry['width'], height=entry['height'],
                                 sha256=entry['sha256']), (asset_id,))

    def select_background(self, asset_id=None, color=None):
        self._check_cancelled()
        if (asset_id is None) == (color is None):
            raise MaterialError('请明确选择一个背景素材 ID 或一种纯色，不能同时指定')
        if color is not None:
            if not isinstance(color, str) or not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
                raise MaterialError('背景颜色须为 #RRGGBB')
            if self.inventory and self.inventory.background_retained:
                raise MaterialError('当前清单有保留在底图的主体，纯色会丢失主体；请先调整清单')
            directory = self._directory()
            path = directory / 'solid.png'
            Image.new('RGB', self.canvas, color).save(path)
            return self.bind_background(path, recipe=dict(kind='solid', color=color.upper()))
        target, entry = self._resolve(asset_id)
        historical = self._background_history.get(target)
        retained = [{key: item.model_dump(mode='json')[key] for key in ('name', 'source_box', 'content_kind')}
                    for item in self.inventory.background_retained] if self.inventory else []
        recipe = (historical or {}).get('recipe') or {}
        if retained and recipe.get('retained') != retained:
            raise MaterialError('当前清单有保留在底图的主体，请使用按此清单编辑的底图，不能直接换成参考图')
        if recipe.get('retained') and recipe['retained'] != retained:
            raise MaterialError('历史底图保留的主体与当前清单不一致，请先调整清单或重新编辑背景')
        if historical:
            return self.bind_background(entry['path'], recipe=historical['recipe'])
        # Copy even a bound source into an immutable job-local background version.
        directory = self._directory()
        path = crop_asset({target: entry['path']}, target, (0, 0, 1, 1), directory, 'background')
        return self.bind_background(path, recipe=dict(kind='selected', source=target))

    def bind_background(self, path, recipe=None):
        """Bind a completed same-job image edit, never start an image request."""
        self._check_cancelled()
        if recipe is None:
            recipe = {}
        elif not isinstance(recipe, dict):
            raise MaterialError('背景制作记录须为对象，未绑定或复制素材')
        recipe = copy.deepcopy(recipe)
        path = self._scoped(path, self.folder)
        # A caller may reuse its result path on a later edit; bind a snapshot so
        # the previous registered version remains immutable even in that case.
        directory = self._directory()
        version = crop_asset({'edited_background': path}, 'edited_background', (0, 0, 1, 1), directory, 'background')
        identity = self._register(version, 'background_version')
        self._aliases['background'] = identity
        self._background = dict(asset_id=identity, recipe=recipe)
        self._background_history[identity] = copy.deepcopy(self._background)
        self.epoch += 1
        entry = self._entries[identity]
        return self._result(dict(asset_id=identity, render_asset_id='background',
                                 width=entry['width'], height=entry['height'],
                                 background=copy.deepcopy(self._background)), ('background',))

    def snapshot(self):
        """Persist resume state without granting access to any new source path."""
        entries = {}
        for key, entry in self._entries.items():
            self._resolve(key)
            entries[key] = {**entry, 'path': entry['path'].relative_to(self.project).as_posix()}
        return dict(version=_VERSION, folder=self.folder.relative_to(self.project).as_posix(),
                    canvas=list(self.canvas), entries=entries, source_ids=sorted(self.sources),
                    inventory=self.inventory.model_dump(mode='json') if self.inventory else None,
                    aliases=dict(self._aliases), prepared=copy.deepcopy(self.prepared),
                    subject_history=copy.deepcopy(self._subject_history), inventory_delta=copy.deepcopy(self.inventory_delta),
                    background=copy.deepcopy(self._background), background_history=copy.deepcopy(self._background_history),
                    epoch=self.epoch, fingerprint=self.fingerprint())

    def restore(self, snapshot):
        """Validate an entire snapshot before replacing state; never execute tools."""
        self._check_cancelled()
        raw = copy.deepcopy(snapshot)
        if (raw.get('version') not in (_VERSION, _LEGACY_VERSION) or raw.get('folder') != self.folder.relative_to(self.project).as_posix()
                or raw.get('canvas') != list(self.canvas) or raw.get('source_ids') != sorted(self.sources)):
            raise MaterialError('素材快照不属于本镜任务或画布已改变，不能恢复')
        if type(raw.get('epoch')) is not int or raw['epoch'] < 0:
            raise MaterialError('素材快照版本无效')
        restored = {}
        for identity, saved in raw['entries'].items():
            self._valid_id(identity)
            relative = Path(saved['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise MaterialError('素材快照不能包含绝对路径或跨目录路径')
            root = self.project if identity in self.sources else self.folder
            path = self._scoped(self.project / relative, root)
            actual = self._entry(path, 'source' if identity in self.sources else 'derived')
            expected = {**saved, 'path': path}
            if actual != expected:
                raise MaterialError('素材快照文件或校验值已改变：' + identity)
            if identity in self.sources and actual != self._entries[identity]:
                raise MaterialError('素材快照与当前绑定源图不一致：' + identity)
            restored[identity] = actual
        if not set(self.sources).issubset(restored):
            raise MaterialError('素材快照缺少当前镜头绑定源图')
        aliases = raw.get('aliases')
        prepared = raw.get('prepared')
        if not isinstance(aliases, dict) or not isinstance(prepared, dict):
            raise MaterialError('素材快照别名格式无效')
        history = raw.get('background_history', {})
        if not isinstance(history, dict):
            raise MaterialError('素材快照背景历史无效')
        inventory = Inventory.model_validate(raw['inventory']) if raw.get('inventory') is not None else None
        if inventory is not None:
            validate_subject_policy(inventory, self.advanced_subject_separation)
        design = (self._inventory_design(inventory, entries=restored, aliases=aliases,
            history=history)[0] if inventory else None)
        expected_aliases = {}
        for identity, item in prepared.items():
            match = _SUBJECT.fullmatch(identity)
            if not match or not inventory or int(match[1]) >= len(inventory.subjects):
                raise MaterialError('素材快照的主体身份或裁区与清单不一致')
            subject = inventory.subjects[int(match[1])]
            expected = _subject_signature(subject, restored[subject.source])
            if raw['version'] == _LEGACY_VERSION:
                if item['spec'] != subject.model_dump(mode='json'):
                    raise MaterialError('旧素材快照的主体身份或裁区与清单不一致')
                item['spec'] = expected
            elif item['spec'] != expected:
                raise MaterialError('素材快照的主体身份或裁区与清单不一致')
            for field, alias in (('prepared', identity), ('original', identity + '_original')):
                key = item[field]
                if key not in restored or restored[key]['kind'] != 'derived':
                    raise MaterialError('素材快照的主体版本缺失')
                expected_aliases[alias] = key
            ready = restored[item['prepared']]
            if item['width'] != ready['width'] or item['height'] != ready['height']:
                raise MaterialError('素材快照的主体尺寸不一致')
        background = raw.get('background')
        for identity, item in history.items():
            if (identity not in restored or restored[identity]['kind'] != 'derived'
                    or not isinstance(item, dict) or item.get('asset_id') != identity
                    or not isinstance(item.get('recipe'), dict)):
                raise MaterialError('素材快照背景历史与素材版本不一致')
        if background:
            key = background['asset_id']
            if key not in restored or restored[key]['kind'] != 'derived':
                raise MaterialError('素材快照的背景版本缺失')
            if history.get(key) != background:
                raise MaterialError('素材快照的当前背景与历史记录不一致')
            expected_aliases['background'] = key
        if aliases != expected_aliases:
            raise MaterialError('素材快照出现未登记或错配的渲染别名')
        body = dict(inventory=inventory.model_dump(mode='json') if inventory else None,
                    assets={key: restored[value]['sha256'] for key, value in sorted(aliases.items())}, canvas=self.canvas)
        if hashlib.sha256(_json(body).encode('utf-8')).hexdigest() != raw.get('fingerprint'):
            raise MaterialError('素材快照指纹校验失败')
        subject_history = raw.get('subject_history') or {}
        if not isinstance(subject_history, dict):
            raise MaterialError('素材快照主体历史无效')
        for key, item in subject_history.items():
            self._validate_saved_material(item, restored)
            if key != _signature_key(item['spec']):
                raise MaterialError('素材快照主体历史签名无效')
        for item in prepared.values():
            subject_history[_signature_key(item['spec'])] = copy.deepcopy(item)
        self._entries, self._aliases, self.prepared = restored, aliases, prepared
        self._subject_history = subject_history
        self.inventory, self.design = inventory, design
        self._background, self.epoch = background, raw['epoch']
        self._background_history = history
        self.inventory_delta = copy.deepcopy(raw.get('inventory_delta') or dict(
            retained=sorted(prepared), reused=[], invalidated=[], missing=[], reasons={}))
        return self.design

    @staticmethod
    def _validate_saved_material(item, entries):
        try:
            spec = item['spec']
            source = entries[spec['source']]
            subject = InventorySubject(name='cached subject', source=spec['source'],
                source_crop=spec['source_crop'], treatment=spec['treatment'])
            if spec != _subject_signature(subject, source) or source['kind'] != 'source':
                raise MaterialError('取材历史的源图或处理参数不一致')
            original, prepared = entries[item['original']], entries[item['prepared']]
            if original['kind'] != 'derived' or prepared['kind'] != 'derived':
                raise MaterialError('取材历史不能引用未处理源图')
            if (item['width'], item['height']) != (prepared['width'], prepared['height']):
                raise MaterialError('取材历史尺寸不一致')
            if (original['width'], original['height']) != (prepared['width'], prepared['height']):
                raise MaterialError('取材历史不保留裁片尺寸')
            if spec['treatment'] == 'preserve' and original['sha256'] != prepared['sha256']:
                raise MaterialError('保真取材历史不能使用经抠图或修改的版本')
        except (KeyError, TypeError, ValueError) as exc:
            raise MaterialError('取材历史参数或版本无效') from exc

    def import_materials(self, snapshot, prior_folder, history_receipts=()):
        """Copy verified same-project material versions, never prior code/answers.

        The caller must additionally bind the old task to this shot/input stamp.
        Legacy v1 snapshots forgot inactive prepared aliases. Successful tool
        receipts can recover their exact source/crop/treatment, not their prose.
        """
        self._check_cancelled()
        prior_folder = Path(prior_folder).resolve()
        if prior_folder == self.folder:
            raise MaterialError('复用导入必须使用不同的独立任务目录')
        old = MaterialWorkspace(self.project, prior_folder, self.sources, self.canvas)
        old.restore(snapshot)
        history = copy.deepcopy(old._subject_history)
        rejected = []
        for receipt in history_receipts:
            try:
                if isinstance(receipt, dict) and 'tool' in receipt:
                    if receipt['tool'] != 'prepare_subject':
                        continue
                    receipt = receipt.get('receipt')
                if not isinstance(receipt, dict) or receipt.get('ok') is False or receipt.get('error'):
                    raise MaterialError('不是已完成取材回执')
                subject = InventorySubject(name='cached subject', source=receipt['source'],
                    source_crop=receipt['source_crop'], treatment=receipt['treatment'])
                item = dict(spec=old._spec(subject), original=receipt['original_asset_id'],
                    prepared=receipt['asset_id'], width=receipt['width'], height=receipt['height'])
                old._validate_saved_material(item, old._entries)
                # Verify the source crop's actual pixel extent, not just claims.
                source = old._entries[subject.source]
                x, y, right, bottom = subject.source_crop
                expected_size = (math.ceil(right*source['width'])-math.floor(x*source['width']),
                                 math.ceil(bottom*source['height'])-math.floor(y*source['height']))
                original = old._entries[item['original']]
                if expected_size != (original['width'], original['height']):
                    raise MaterialError('回执裁区与原裁片尺寸不一致')
                with Image.open(source['path']) as full, Image.open(original['path']) as saved:
                    oriented = ImageOps.exif_transpose(full).convert('RGBA')
                    pixels = (math.floor(x*oriented.width), math.floor(y*oriented.height),
                              math.ceil(right*oriented.width), math.ceil(bottom*oriented.height))
                    if oriented.crop(pixels).tobytes() != saved.convert('RGBA').tobytes():
                        raise MaterialError('回执裁区与原裁片像素不一致')
                history[_signature_key(item['spec'])] = item
            except (MaterialError, KeyError, TypeError, ValueError) as exc:
                rejected.append(str(exc)[:300])
        selected = {item[field] for item in history.values() for field in ('original', 'prepared')}
        selected.update(old._background_history)
        mapping, copied = {}, {}
        for identity in sorted(selected):
            self._check_cancelled()
            _, entry = old._resolve(identity)
            directory = self._directory()
            target = directory / ('reused' + entry['path'].suffix)
            shutil.copyfile(entry['path'], target)
            if _sha(target) != entry['sha256']:
                raise MaterialError('复用素材复制校验失败')
            new_id = ('background_version_' if identity in old._background_history else 'material_') + uuid.uuid4().hex
            mapping[identity] = new_id
            copied[new_id] = self._entry(target, 'derived')
        imported_history = {}
        for key, item in history.items():
            item['original'], item['prepared'] = mapping[item['original']], mapping[item['prepared']]
            imported_history[key] = item
        backgrounds = {}
        for identity, value in old._background_history.items():
            new_id = mapping[identity]
            backgrounds[new_id] = dict(asset_id=new_id, recipe=copy.deepcopy(value['recipe']))
        self._entries.update(copied)
        self._subject_history.update(imported_history)
        self._background_history.update(backgrounds)
        imported_inventory = copy.deepcopy(snapshot.get('inventory'))
        if imported_inventory:
            requested = imported_inventory['background']
            old_target = old._aliases.get('background') if requested == 'background' else requested
            if old_target in mapping and old_target in old._background_history:
                imported_inventory['background'] = mapping[old_target]
        return dict(imported_subject_versions=len(imported_history), imported_background_versions=len(backgrounds),
                    rejected_receipts=rejected, materials=self.material_status()['reusable_subjects'],
                    backgrounds=list(backgrounds.values()),
                    inventory=imported_inventory,
                    previous_background_asset_id=mapping.get((old._background or {}).get('asset_id')))
