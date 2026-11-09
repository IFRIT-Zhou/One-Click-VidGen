"""Manual checkpoint continuation executes a new decision, not an old tool.

All paid calls, renderers and audio operations are mocked. Temporary workspaces
are real so the persisted checkpoint/asset boundaries still run.
"""
import copy
import json
import unittest
from unittest.mock import patch

from backend.app import vibe_agent_workflow as workflow
import test_vibe_agent_workflow as fixture


class StepWorkflowTests(unittest.TestCase):
    setUp = fixture.AgentWorkflowTests.setUp
    mock = fixture.AgentWorkflowTests.mock
    update_task = fixture.AgentWorkflowTests.update_task
    model_call = fixture.AgentWorkflowTests.model_call
    render = fixture.AgentWorkflowTests.render
    saved_candidate = fixture.AgentWorkflowTests.saved_candidate
    setup_commands = fixture.AgentWorkflowTests.setup_commands
    preview = fixture.AgentWorkflowTests.preview

    def run_saved(self, *, images=False, **flags):
        self.task['cloud_input_stamp'] = 'same-input'
        snap = (None, None, 'audio-digest', None, None, None)
        captured = []

        def next_decision(session):
            captured.append(copy.deepcopy(session.state))
            raise workflow.CreatorBlocked('offline stop after restore, before any model request')

        with patch.object(workflow.api, '_context', return_value=(snap, {}, self.session.context, {'core': self.core})), \
             patch.object(workflow.api, '_cloud_stamp', return_value='same-input'), \
             patch.object(workflow.api.previews, '_audio', return_value=('audio-digest', 0)), \
             patch.object(workflow.AgentSession, 'run', next_decision), \
             patch.object(workflow.AgentSession, 'execute', side_effect=AssertionError('must not replay tool')), \
             patch.object(workflow.AgentSession, 'resume_image', side_effect=AssertionError('must not poll image')), \
             patch.object(workflow.AgentSession, 'continue_saved_preview', side_effect=AssertionError('must not render')):
            workflow.run_worker(self.project, 'shot_generic', 'a'*32, {'new_images_allowed':images},
                self.session.data, self.event, continue_steps=True, **flags)
        self.model.assert_not_called()
        self.image_call.assert_not_called()
        return captured

    def test_early_empty_response_checkpoint_restores_without_inventory_or_preview(self):
        self.session.state['events'] = [dict(tool='inspect_image',summary='已完成',receipt={'asset_id':'core'})]
        self.session.state['observations'] = {'inspect_image':{'asset_id':'core'}}
        self.session.state['sequence'] = 1
        self.session.save()
        self.task['usage']['model_calls'] = [{'error_code':'model_empty_completed'}]
        usage = copy.deepcopy(self.task['usage'])
        states = self.run_saved()
        self.assertEqual(len(states),1)
        restored = states[0]
        self.assertIsNone(restored['latest_code'])
        self.assertFalse(restored['previews'])
        self.assertEqual(restored['sequence'],1)
        self.assertEqual(restored['events'],self.session.state['events'])
        self.assertFalse(restored['new_images_allowed'])
        self.assertEqual(self.task['usage'],usage)
        self.assertFalse(self.renders)

    def test_preview_code_material_and_previous_review_are_restored_not_replayed(self):
        candidate = self.saved_candidate()
        review = dict(**workflow.binding(candidate),passed=False,summary='需要核查',issues=['标注来源待核实'])
        self.session.state['delivery_reviews'][candidate['preview_id']] = review
        self.session.save()
        before = copy.deepcopy(self.session.state)
        count = len(self.renders)
        states = self.run_saved()
        restored = states[0]
        for key in ('previews','latest_code','delivery_reviews','workspace','sequence','events'):
            self.assertEqual(restored[key],before[key],key)
        self.assertEqual(len(self.renders),count)
        self.assertFalse(restored['new_images_allowed'])

    def test_explicit_remaining_image_confirmation_never_revives_revoked_permission(self):
        for state_allowed, task_allowed, expected in ((True,True,True),(False,True,False),(True,False,False)):
            with self.subTest(state_allowed=state_allowed,task_allowed=task_allowed):
                self.session.state['new_images_allowed'] = state_allowed
                self.task['new_images_allowed'] = task_allowed
                self.session.save()
                self.assertEqual(self.run_saved(images=True)[0]['new_images_allowed'],expected)

    def test_corrupt_checkpoint_or_mixed_resume_mode_does_not_run_or_overwrite(self):
        self.session.save()
        path = self.folder/'agent-state.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state['version'] = 999
        workflow.api._write(path,state)
        before = path.read_bytes()
        self.assertEqual(self.run_saved(),[])
        self.assertIn('版本不一致',self.task['message'])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.run_saved(continue_preview=True),[])
        self.assertIn('一种续接方式',self.task['message'])
        self.assertEqual(path.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
