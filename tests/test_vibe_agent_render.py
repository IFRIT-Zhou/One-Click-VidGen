"""Opt-in local browser proof of AgentSession -> preview -> delivery.

No provider calls, RMBG, real project mutations, or adopted shots. Run with
OCV_TEST_REAL_AGENT_RENDER=1 when the bundled Node/Chrome/FFmpeg are installed.
"""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import wave

from PIL import Image, ImageDraw, ImageFont

from backend.app import vibe_agent_workflow as workflow
from backend.app import vibe_cloud as cloud
from backend.app import vibe_code_renderer as renderer
from backend.app.vibe_agent_contract import parse_tool_call, VERSION


@unittest.skipUnless(os.environ.get('OCV_TEST_REAL_AGENT_RENDER') == '1',
                     'requires installed local Node/Chrome/FFmpeg')
class AgentRenderSmokeTest(unittest.TestCase):
    def test_measure_preview_mock_review_and_real_delivery(self):
        self.render_fixture(retain_subject=False)

    def test_stable_retained_subject_real_browser_and_delivery(self):
        self.render_fixture(retain_subject=True)

    def render_fixture(self, retain_subject):
        renderer.preflight()
        if os.environ.get('OCV_TEST_KEEP_AGENT_RENDER') == '1':
            root = Path(tempfile.mkdtemp(prefix='agent-render-smoke-')).resolve()
        else:
            temporary = tempfile.TemporaryDirectory(prefix='agent-render-smoke-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name).resolve()
        project = root/('a'*32)
        project.mkdir()
        (project/'record.json').write_text('{"test_only":true}', encoding='utf-8')
        before = (project/'record.json').read_bytes()
        job_id = uuid.uuid4().hex
        folder = cloud._root(project)/job_id
        folder.mkdir()
        cloud._write(folder/'task.json', dict(task_id=job_id, shot_id='synthetic',
            status='running', stage='agent_plan', workflow_version=VERSION,
            advanced_subject_separation=False,
            max_model_calls=16, max_image_requests=0,
            usage=dict(model_calls=[], image_requests=0, image_task_ids=[])))
        core = project/'core.png'
        canvas = (640, 360)
        image = Image.new('RGB', canvas, '#102030')
        draw = ImageDraw.Draw(image)
        font_path = Path(os.environ.get('SystemRoot', 'C:/Windows'))/'Fonts'/'arial.ttf'
        title_font = ImageFont.truetype(str(font_path), 30) if font_path.is_file() else ImageFont.load_default()
        caption_font = ImageFont.truetype(str(font_path), 42) if font_path.is_file() else ImageFont.load_default()
        draw.text((40, 40), 'AUTONOMOUS TOOL TEST', fill='#FFFFFF', font=title_font)
        draw.text((190, 160), '28 samples', fill='#70EAFF', font=caption_font)
        if retain_subject:
            draw.ellipse((50,250,110,310), fill='#70EAFF')
        image.save(core)
        audio = project/'voice.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0'*22400)
        context = dict(canvas=list(canvas), duration=1.4,
                       subtitles=[dict(id='s1', start=0, end=1.4, text='28 samples')])
        data = cloud.CloudRequest(confirm_cloud=True, template_mode='layered', max_image_requests=0)
        session = workflow.AgentSession(project, 'synthetic', job_id, {'model': 'offline-test'},
                                       data, threading.Event(), context, {'core': core})
        def tool(name, arguments):
            name, args = parse_tool_call(dict(tool=name, summary='Offline integration test', arguments=arguments))
            result = session.execute(name, args, 'Offline integration test')
            session.record(name, 'Offline integration test', result['receipt'], result.get('images'))
            return result['receipt']

        # A useful creative tool works before the content inventory is chosen.
        measured = tool('measure_text', dict(probes=[dict(id='title_width', text='AUTONOMOUS TOOL TEST',
                                                         font_size=30, font_weight=700)]))
        self.assertGreater(measured['measured'][0]['width'], 0)
        inventory = dict(background='solid', background_color='#102030',
            background_is_uniform=True,
            foreground_summary=dict(has_nontext_subjects=False, subject_count=0,
                description='Synthetic text-only fixture', text_only_reason='No photographic or object subjects'),
            subjects=[], decorations=[], texts=[
                dict(text='AUTONOMOUS TOOL TEST', source_box=[.0625,.11,.9,.24]),
                dict(text='28 samples', source_box=[.296,.44,.7,.6])])
        if retain_subject:
            inventory.update(background='extract', background_is_uniform=False,
                background_retained=[dict(name='Synthetic round object', source_box=[.078,.694,.172,.862],
                    reason='Keep the simple test object as original background pixels', content_kind='illustration')])
            inventory['foreground_summary'].update(has_nontext_subjects=True, background_retained_count=1,
                description='Retained circle and independent text',text_only_reason=None)
        tool('set_inventory', dict(inventory=inventory))
        if retain_subject:
            base=folder/'synthetic-base.png'
            base_image=Image.new('RGB',canvas,'#102030')
            ImageDraw.Draw(base_image).ellipse((50,250,110,310),fill='#70EAFF')
            base_image.save(base)
            recipe=workflow.layered._background_recipe(session.workspace.design,
                workflow.layered._inventory_notes(session.workspace.inventory),advanced_subject_separation=False)
            identity=session.workspace.bind_background(base,recipe)['receipt']['asset_id']
            tool('select_background',dict(asset_id=identity))
        else:
            tool('select_background', dict(color='#102030'))
        code = '''function Scene(ctx) {
          const heading = 'AUTONOMOUS TOOL TEST';
          const titleWidth = ctx.measurements.title_width.width;
          return <div style={{position:'absolute',inset:0}}>
            <Group id="intro" at={0} enter={0.4} exit={0.2} motion="slide_left">
              <Text id="title" style={{position:'absolute',left:40,top:40,fontSize:30,fontWeight:700,
                 width:titleWidth+8,color:'#FFFFFF'}}>{heading}</Text>
            </Group>
            <Group id="value" at={0.25} enter={0.4} exit={0.2} motion="scale">
              <Text id="samples" style={{position:'absolute',left:190,top:160,fontSize:42,
                fontWeight:700,color:'#FFFFFF'}}><span style={{color:'#70EAFF'}}>28</span> samples</Text>
              <svg style={{position:'absolute',left:190,top:224}} width={240} height={8} viewBox="0 0 240 8">
                <path d="M 0 4 L 240 4" stroke="#70EAFF" strokeWidth={2} fill="none" />
              </svg>
            </Group>
          </div>;
        }'''
        level = 'layout' if retain_subject else 'full'
        preview = tool('preview_scene', dict(code=code, embedded_text={}, recovered_text=[], preview_level=level))
        self.assertTrue(preview['ok'], preview)
        candidate = session.state['previews'][preview['preview_id']]
        self.assertEqual(candidate['preview_level'], level)
        self.assertEqual(len(candidate['metrics']['stage_frames']), 0 if retain_subject else 4)
        poster = project/candidate['output']/'poster.png'
        self.assertTrue(poster.is_file())
        # Only the independent cloud review is faked; both browser passes and
        # the final video+audio mux are the real installed implementations.
        with patch.object(session, 'invoke', return_value=dict(passed=True, summary='Offline fixture approved', issues=[])) as review:
            result = tool('finish', dict(preview_id=preview['preview_id']))
        self.assertTrue(result['ok'])
        self.assertEqual(review.call_args.args[-1], 'agent_review')
        self.assertEqual(session.accepted['preview_level'], 'full')
        self.assertEqual(len(session.accepted['metrics']['stage_frames']), 4)
        if retain_subject:
            self.assertNotEqual(candidate['preview_id'], session.accepted['preview_id'])
            self.assertEqual(candidate['turn']['code'], session.accepted['turn']['code'])
        plan, metrics, output, quality = session.render_delivery(session.accepted, audio, 0)
        self.assertEqual(plan['version'], renderer.VERSION)
        self.assertEqual(metrics['frames'], 42)
        self.assertEqual(quality['workflow_version'], VERSION)
        self.assertEqual(quality['quality_status'], 'passed')
        for name in ('poster.png', 'silent.mp4', 'preview.mp4', 'proof.jpg'):
            self.assertGreater((output/name).stat().st_size, 100)
        self.assertEqual((project/'record.json').read_bytes(), before)
        if retain_subject:
            self.assertEqual(set(session.workspace.assets()),{'background'})
            with Image.open(output/'poster.png') as poster:
                self.assertEqual(poster.convert('RGB').getpixel((80,280)),(112,234,255))
        if os.environ.get('OCV_TEST_KEEP_AGENT_RENDER') == '1':
            print('Agent render fixture:', str(output))


if __name__ == '__main__':
    unittest.main()
