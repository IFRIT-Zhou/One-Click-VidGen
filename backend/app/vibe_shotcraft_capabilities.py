"""Audited adapter capabilities, shared by model selection and catalog clients.

Editorial tags are recommendations, not new production gates. Hard limits
describe the OCV Python submission contract, using its typed fields and shared
recipe registry. No content or photo-count guessing is performed.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from .vibe_shotcraft_plan import RECIPE_RULES, minimum_frames, Item, Stage

_PATH = Path(__file__).resolve().parents[2]/'tools/vibe_remotion/lib/shotcraft-capabilities.json'
CATALOG = json.loads(_PATH.read_text(encoding='utf-8'))
VERSION = CATALOG['version']
_STAGE_FIELDS = Stage.model_json_schema()['properties']
_ITEM_FIELDS = Item.model_json_schema()['properties']


def validate_capability_catalog(catalog=CATALOG):
    if catalog.get('schema_version') != 1 or set(catalog['styles']) != set(RECIPE_RULES):
        raise ValueError('模板能力标签与运行适配器清单不一致')
    labels = catalog['tag_labels']
    for identity, row in catalog['styles'].items():
        rule = RECIPE_RULES[identity]
        for field, vocabulary in (('content_tags', 'content'), ('motion_families', 'motion_family')):
            values = row.get(field)
            if not isinstance(values, list) or not values or len(values) != len(set(values)) or any(v not in labels[vocabulary] for v in values):
                raise ValueError(f'{identity} 的 {field} 标签无效')
        max_images = 0 if rule.get('text_only') else rule['max_items']
        if type(row.get('primary_images_at_once')) is not int or not 0 <= row['primary_images_at_once'] <= max_images:
            raise ValueError(f'{identity} 的同时入画能力超出适配器')
        if row.get('evidence_support') not in labels['evidence']:
            raise ValueError(f'{identity} 缺少证据图能力说明')
        if rule.get('text_only') and row['evidence_support'] != 'unsupported':
            raise ValueError('纯文字适配器不能声明图片证据能力')
        for field in ('recommended_use', 'notes', 'image_presentation'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f'{identity} 缺少能力说明 {field}')
        if not isinstance(row.get('avoid_for'), list) or not row['avoid_for'] or any(
                not isinstance(value, str) or not value.strip() for value in row['avoid_for']):
            raise ValueError(f'{identity} 缺少不推荐用途说明')
    return True


def capabilities_for(identity, duration=None):
    if identity not in CATALOG['styles']:
        return None  # Archived upstream styles are not automatically audited.
    rule = RECIPE_RULES[identity]
    row = copy.deepcopy(CATALOG['styles'][identity])
    maximum = 0 if rule.get('text_only') else rule['max_items']
    material_tags = [] if rule.get('image_required') else ['text_only']
    if maximum:
        material_tags += ['single_image', 'mixed_text_images']
        if maximum > 1:
            material_tags.append('multiple_images')
    row.update(version=VERSION, material_tags=material_tags,
               # Slots, not a claim that every item must bind a different photo.
               image_slots={'minimum': 1 if rule.get('image_required') else 0, 'maximum': maximum,
                            'per_item_maximum': 1 if maximum else 0},
               native_data_charts=False, numeric_rendering='text_only',
               reference_inputs='multiple_for_understanding_not_display_slots',
               color_style='frozen_from_core_not_fixed_by_template')
    row['hard_constraints'] = {
        'items': {'minimum':rule['min_items'], 'maximum':rule['max_items']},
        'min_stage_seconds': minimum_frames(identity,rule['min_items'])/30,
        'min_frames':rule['min_frames'], 'per_item_frames':rule.get('per_item_frames',0),
        'max_stage_seconds':rule.get('max_seconds'),
        'image_required':bool(rule.get('image_required')), 'text_only':bool(rule.get('text_only')),
        'body_required':bool(rule.get('body_required')),
        'text_max_chars': {
            'headline':min(_STAGE_FIELDS['headline']['maxLength'], rule.get('headline_max',float('inf'))),
            'item_title':_ITEM_FIELDS['title']['maxLength'],
            # Stage.editorial_limits also applies a special short outro guard.
            'item_body':min(_ITEM_FIELDS['body']['maxLength'], rule.get('item_body_max',float('inf')),
                            22 if identity=='outro-group-photo-launch' else float('inf')),
            'stage_body':_STAGE_FIELDS['body']['maxLength'],
            'eyebrow':_STAGE_FIELDS['eyebrow']['maxLength'], 'footer':_STAGE_FIELDS['footer']['maxLength'],
        },
        'canvas':'landscape >=1.6, even pixels, <=1920x1080', 'fps':30,
    }
    if duration is not None:
        # This is an item-count budget, not a claim that this recipe can fill
        # the entire shot. E.g. outro still occupies at most six seconds.
        available = min(duration, rule.get('max_seconds', duration))
        frames = math.floor(available*30+.5)
        max_items = min(rule['max_items'],frames//rule['per_item_frames']) if rule.get('per_item_frames') else rule['max_items']
        row['max_items_within_available_time'] = max_items if frames >= rule['min_frames'] and max_items >= rule['min_items'] else 0
    return row


def selection_taxonomy():
    return {'version':VERSION,'tag_labels':copy.deepcopy(CATALOG['tag_labels']),
        'policy': {
            'hard_vs_soft':'hard_constraints are enforced; content_tags/avoid_for/evidence_support are editorial recommendations, not automatic refusals',
            'references':'All templates may read multiple reference images. image_slots counts images actually displayed, not uploaded references or required card counts.',
            'materials':'A required image may come from an original reference or a faithful rectangle of core. No independent reference does not mean no available image.',
            'data':'numeric_comparison means verified numbers as text; these adapters do not draw native bar/line/statistical charts.',
            'evidence':'contain preserves pixels but not readability. Conditional evidence use needs full axes/units/legend, readable final-size rendering and enough reading time; never omit needed evidence to fit a template.',
            'diversity':'Prefer a different motion_family when equally suitable; factual continuity and user choice take precedence. Never manufacture extra facts to fill items.',
            'timing':'max_items_within_available_time is a count ceiling before allocating stages, not permission to fill the whole shot. Respect each stage minimum/maximum and actual allocated time; total shot remains 2.5–60 seconds.',
            'scope':'Tags describe tested OCV adapters, not every capability of upstream demos. Full source cards do not override adapter constraints.',
        }}


def motion_families(recipes):
    return sorted({family for identity in recipes if identity in CATALOG['styles']
                   for family in CATALOG['styles'][identity]['motion_families']})
