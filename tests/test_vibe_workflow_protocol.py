"""Real module protocol parity, without a renderer, model, or network call."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.app import vibe_code_workflow as workflow
from backend.app import vibe_layered as layered
from backend.app import vibe_agent_contract as agent_contract


class WorkflowProtocolTests(unittest.TestCase):
    def test_real_router_and_quality_producer_share_persisted_protocol(self):
        # Route tests intentionally stub the implementation module. Check its
        # real value here so an independently bumped producer cannot pass them.
        self.assertEqual(workflow.WORKFLOW_VERSION, layered.CODE_WORKFLOW_VERSION)
        self.assertEqual(layered.WORKFLOW_VERSION, agent_contract.VERSION)
        self.assertEqual(layered.AGENT_WORKFLOW_VERSION, agent_contract.VERSION)
        self.assertEqual(workflow.WORKFLOW_VERSION, 'code-authored-layered-v3')
        requirements = layered.workflow_requirements(workflow.WORKFLOW_VERSION)
        self.assertEqual(requirements['required_renderer'], workflow.RENDERER)
        self.assertEqual(requirements['required_plan_version'], workflow.VERSION)
        self.assertEqual(layered.workflow_requirements(agent_contract.VERSION), requirements)

    def test_real_producer_version_can_publish_but_unknown_version_still_cannot(self):
        for quality_version, succeeds in ((workflow.WORKFLOW_VERSION, True),
                                          ('future-unknown-version', False)):
            with self.subTest(quality_version=quality_version), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp)/'project'
                output = project/'task'/'render'
                output.mkdir(parents=True)
                record = project/'record.json'
                record.write_bytes(b'{"unchanged":true}')
                before = record.read_bytes()
                path = project/'task'/'task.json'
                quality = {'workflow_version': quality_version,
                           'quality_status': 'passed', 'quality_issues': []}
                check = Mock()
                api = layered.api
                with patch.object(api, '_read_job', return_value={
                        'workflow_version': layered.CODE_WORKFLOW_VERSION}), \
                     patch.object(api, '_context', return_value=()), \
                     patch.object(api, '_cloud_stamp', return_value='stamp'), \
                     patch.object(api.previews, '_store', return_value=project/'previews'), \
                     patch.object(api.previews, '_publish_pointer') as pointer, \
                     patch.object(api, 'publish', wraps=api.publish) as publish, \
                     patch.object(api, '_update') as update:
                    args = (project, 'shot', 'task', path, ('input', 'source', 'audio'),
                            'stamp', {'version': workflow.VERSION},
                            {'renderer': workflow.RENDERER}, output, quality, check)
                    if succeeds:
                        layered._publish(*args)
                        preview_id = update.call_args.kwargs['preview']['preview_id']
                        receipt = json.loads((project/'previews'/preview_id/'receipt.json').read_text(encoding='utf-8'))
                        self.assertEqual(receipt['workflow_version'], layered.CODE_WORKFLOW_VERSION)
                        self.assertEqual(receipt['quality_status'], 'passed')
                        self.assertEqual(publish.call_count, 2)  # Atomic receipt, then preview directory.
                        pointer.assert_called_once()
                        self.assertEqual(update.call_args.kwargs['status'], 'completed')
                    else:
                        with self.assertRaisesRegex(ValueError, '质量回执与任务流程版本不一致'):
                            layered._publish(*args)
                        self.assertFalse((output/'receipt.json').exists())
                        publish.assert_not_called()
                        pointer.assert_not_called()
                        update.assert_not_called()
                self.assertEqual(record.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
