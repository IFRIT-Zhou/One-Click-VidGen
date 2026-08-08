---
name: ocv-generate-test-scripts
description: "Generate Chinese test scripts optimized for stable One-Click VidGen (OCV) demonstrations, together with the three UI visual-setting fields: unified visual style, global character settings, and story-world/environment settings. Use when creating or expanding OCV demo copy for story, narration/commentary, popular-science, serious educational, suspense, emotional, or general custom modes, especially when the result should minimize character inconsistency, costume changes, reflection errors, excessive time jumps, crowded scenes, and other image-generation failure points."
---

# Generate OCV Test Scripts

Create engaging Chinese test material that demonstrates OCV reliably. Optimize for visual continuity without making the writing bland.

## Determine the request

Use the mode requested by the user. If unspecified, ask for a mode only when the choice materially matters; otherwise choose one and state it. Support at least:

- 故事叙事：都市、悬疑、温情、轻奇幻等具有连续事件的短故事。
- 口播分享：观点、关系沟通、生活经验、社会观察等有具体生活场景支撑的口播。
- 科普教育：自然科学、医学常识、历史、地理、技术或其他学科的严肃科普。
- 通用自定义：按用户指定题材、受众和语气生成。

When the user requests a batch covering multiple modes, create separate self-contained cases rather than mixing modes in one script.

## Default scope

Unless the user specifies otherwise:

- Write 600–900 Chinese characters, suitable for a short but meaningful test.
- Use natural paragraphs and complete punctuation. Do not place every short phrase on a separate line.
- Keep the main event within one continuous period, preferably several minutes to one day.
- Use 1–2 recurring characters; allow at most 3 only when necessary.
- Give each recurring character one fixed outfit for the entire script.
- Use 2–4 visually distinguishable but connected locations.
- Give each scene a concrete action, object, spatial relationship, or environmental change that can become a new image.

Respect explicit user length, mode, subject, audience, and tone requirements over these defaults.

## Stability guardrails

Apply these rules unless the user explicitly requests an exception:

1. Avoid mirrors, mirror reflections, water reflections, glass reflections, duplicated shadows, portraits within portraits, and other compositions that can duplicate or distort characters.
2. Avoid long time spans, repeated flashbacks, rapid season changes, childhood-to-adulthood transitions, aging, body transformations, frequent day/night switches, and repeated costume changes.
3. Keep recurring characters visually stable: same age band, hairstyle, clothing, accessories, and body type. Do not write plot beats that require removing a signature item.
4. Avoid crowds, many similar-looking people, complicated fights, dense hand interactions, simultaneous multi-person actions, split screens, collages, comic panels, montage grids, and picture-in-picture.
5. Use phones, tablets, computers, letters, photographs, charts, and exact on-screen text only when essential. If their content is not narratively explicit, show the person naturally using the device instead of inventing screen content.
6. Avoid exact brand logos, long readable text, dense formulas, intricate maps, and visually ambiguous pronouns. Give recurring characters distinct names or stable role labels.
7. Avoid many simultaneous character states. Prefer one clear emotional or physical state per shot-worthy sentence.
8. Do not repeat the same visual beat with slightly different wording. Make adjacent paragraphs distinguishable through action, location, prop, framing opportunity, or information layer.
9. Keep safety and platform suitability: no graphic gore, sexualized minors, hateful dehumanization, or instructions for wrongdoing.

## Mode-specific writing

### 故事叙事

- Build a clear opening hook, escalating discovery or conflict, and a meaningful ending.
- Let causally connected sentences stay together; do not fragment one event into slogan-like lines.
- Prefer a single outing, visit, conversation, investigation, work shift, or evening.
- Use recurring props and locations to strengthen continuity.

### 口播分享

- State the tension early, then explain it through one or two concrete everyday examples.
- Turn abstract claims into observable behavior, setting, and consequences without becoming a PPT outline.
- Keep any example couple, family, colleague, or narrator in one stable appearance and time period.
- End with a grounded conclusion or actionable perspective rather than empty motivational slogans.

### 科普教育

- Prioritize correctness and clarity. For unstable or high-stakes facts, verify them with authoritative primary sources before writing.
- Move from a question or familiar phenomenon to mechanism, example, misconception correction, and concise takeaway.
- Prefer visually expressible processes, scale comparisons, objects, environments, and stepwise changes.
- Do not force a recurring host character. Leave the character field empty when people are unnecessary.
- Avoid relying on large blocks of generated text, exact equations, or complex charts to carry the explanation.

## Produce the deliverable

Return the following sections in Chinese. Put each copyable field in its own fenced text block.

### 1. 基本信息

Include:

- 标题
- 推荐模式
- 预计字数
- 主要测试点（3–6 concise items）

### 2. 测试文案

Write the complete narration. Do not insert shot numbers, image prompts, production notes, or bracketed stage directions into the narration unless requested.

### 3. 统一画面风格

Write one coherent OCV-ready paragraph that defines:

- visual medium and degree of realism;
- palette, lighting, texture, and atmosphere;
- horizontal 2:1 composition and clean image quality;
- exclusions such as multi-panel collage, dense text, watermark, noisy smearing, or unintended photorealism when an illustration style is requested.

Do not put character identity or costume details here.

### 4. 全局人物设定

For every recurring character, define a unique stable role label or name, approximate age, gender presentation when relevant, hairstyle, face/body cues, one complete outfit, and one optional signature object. Avoid duplicate generic labels.

For people-free science or environment cases, output `留空（本案例不需要固定人物）`.

End non-empty character settings with exactly:

`当画面中没有这些角色的时候则本段不作为参考。`

### 5. 故事世界与环境设定

Define the narrow time period, region or setting, connected recurring locations, weather/light continuity, technology era, and key recurring props. Explicitly constrain the world to avoid accidental time, season, architecture, or technology jumps.

## Final quality check

Before responding, silently verify:

- The narration is coherent and interesting rather than a list of shots.
- The time span, locations, cast, and clothing stay within the selected limits.
- No mirror/reflection-dependent scene or accidental multi-panel instruction remains.
- Every recurring character in the narration appears in the global character field.
- Character details do not leak into the unified style field.
- The three UI fields agree with the narration and can be pasted directly into OCV.
- A science case does not invent a host merely to fill the character field.
- The script uses normal sentence and paragraph boundaries suitable for OCV segmentation.

If any check fails, revise before returning the result.
