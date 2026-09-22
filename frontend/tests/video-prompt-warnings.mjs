import assert from 'node:assert/strict'
import { promptWarningSeverity, shotPromptNotes, shotHasPromptWarning, promptWarningRows } from '../src/videoPromptWarnings.js'
import { dynamicTextModeDescriptions } from '../src/dynamicTextMode.js'

const format = '提示词分节标题不完整或顺序不同，内容已保留；不影响继续生成。'
for (const message of [format, '分镜图提示词结构或顺序不正确', '', null]) assert.equal(promptWarningSeverity(message), 'format')
for (const message of ['缺少既定主体名称：观众', '缺少文字归属：观众', '缺少文字容器：对话气泡', '未来版本的普通提示']) assert.equal(promptWarningSeverity(message), 'info')
for (const message of ['缺少短文字原文：危险', '规划了画面短文字，却同时要求全面禁止文字', '核心图混入未选用的阶段文字：例子', '核心图混入其他阶段文字：例子']) assert.equal(promptWarningSeverity(message), 'warning')

const shots = [
  {id:'format',image_prompt_warnings:[format],video_prompt_warnings:[format]},
  {id:'info',image_prompt_warnings:['缺少既定主体名称：观众','缺少文字归属：观众'],video_prompt_warnings:['缺少文字容器：对话气泡']},
  {id:'warning',image_prompt_warnings:[format,'缺少短文字原文：危险','缺少短文字原文：危险'],video_prompt_warnings:['缺少文字容器：对话气泡']},
]
assert.deepEqual(shotPromptNotes(shots[0]), [])
assert.equal(shotPromptNotes(shots[1]).length, 3)
assert.equal(shotHasPromptWarning(shots[1]), false)
assert.equal(shotHasPromptWarning(shots[2]), true)
assert.equal(shotPromptNotes(shots[2]).length, 2, 'Repeated notes from one source are deduplicated')
const rows = promptWarningRows(shots)
assert.equal(rows.length, 1)
assert.equal(rows[0].index, 2)
assert.equal(rows[0].notes.length, 1)
assert.equal(rows[0].notes[0].label, '核心图：缺少短文字原文：危险')
assert.deepEqual(shotPromptNotes({image_prompt_warnings:null,video_prompt_warnings:'malformed'}), [])
assert.equal(dynamicTextModeDescriptions.visual_first.includes('不用台词'), false)
assert.ok(dynamicTextModeDescriptions.visual_first.includes('简短反应'))
console.log('Warning tiers passed: format hidden, wording collapsed-only, actionable differences indexed, backward compatibility and few-text description.')
