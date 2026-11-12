"""Offline scheduling and non-destructive output selection regressions."""
import copy
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import vibe_adoption as adoption, vibe_remotion as render
from backend.app import vibe_cloud as cloud


class ProductionControlsTests(unittest.TestCase):
    def test_task_summary_is_read_only_and_project_scoped(self):
        with patch.object(cloud,'owned_project',return_value=Path('project')), \
             patch.object(cloud,'_active',{('project','s1'):('job1',None),('other','s2'):('job2',None)}), \
             patch.object(cloud,'_read_job',return_value=dict(shot_id='s1',task_id='job1',status='running',stage='render')), \
             patch.object(cloud,'_update') as update:
            result=cloud.active_tasks('id',None)
            self.assertEqual([t['shot_id'] for t in result['items']],['s1'])
            self.assertEqual((result['design_concurrency'],result['render_concurrency']),(2,1))
            update.assert_not_called()

    def test_output_selection_preserves_media_and_prompts(self):
        shot = dict(id='s', kind='video', duration=8, image='core.png',
                    video='paid.mp4', video_status='completed', video_request={'backend':'api'},
                    action='motion', video_prompt='prompt', video_history=[{'video':'old.mp4'}])
        original = copy.deepcopy(shot)
        record = dict(shots=[shot], revision=1)
        with patch.object(adoption, 'owned_project', return_value=Path('test')), \
             patch.object(adoption, '_target', return_value=(record, shot)), \
             patch.object(adoption.studio, 'discard_planning_resume'), \
             patch.object(adoption, '_finish', side_effect=lambda p,r,n:r):
            for kind in ('static', 'video'):
                adoption.output_kind('p','s',adoption.OutputKind(revision=1,kind=kind),None)
                self.assertEqual(shot['kind'],kind)
                for key in ('image','video','video_status','video_request','action','video_prompt','video_history'):
                    self.assertEqual(shot[key],original[key])

    def test_remotion_shared_lane_serializes_and_releases_after_failure(self):
        active=0
        peak=0
        gate=threading.Lock()
        def process(*a, **kw):
            nonlocal active, peak
            with gate:
                active+=1;peak=max(peak,active)
            time.sleep(.04)
            with gate:active-=1
            return 'ok'
        with patch.object(render,'_run_process',side_effect=process):
            threads=[threading.Thread(target=render._run,args=(['node','render-code.mjs'],),kwargs={'timeout':1}) for _ in range(3)]
            for t in threads:t.start()
            for t in threads:t.join(2)
        self.assertEqual(peak,1)
        with patch.object(render,'_run_process',side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):render._run(['node','render.mjs'],timeout=1)
        self.assertTrue(render._render_slot.acquire(blocking=False))
        render._render_slot.release()

    def test_waiting_render_is_cancelable_without_launching(self):
        render._render_slot.acquire()
        try:
            with patch.object(render,'_run_process') as process:
                with self.assertRaises(InterruptedError):
                    render._run(['node','render-code.mjs'],timeout=1,cancelled=lambda:True)
                process.assert_not_called()
        finally:render._render_slot.release()


if __name__=='__main__':unittest.main()
