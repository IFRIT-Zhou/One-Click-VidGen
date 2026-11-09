import json
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from backend.app import vibe_rmbg


class StandaloneBatch(unittest.TestCase):
    def setUp(self):
        stack=ExitStack()
        self.addCleanup(stack.close)
        self.enter=stack.enter_context
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.sources={}
        for name in ('subject_0','subject_1'):
            path=self.root/(name+'.png')
            Image.new('RGBA',(12,10),(14,70,231,128)).save(path)
            self.sources[name]=path
        self.output=self.root/'out'
        self.resolver=self.enter(patch('backend.app.vibe_rmbg_runtime.resolve_runtime'))
        self.lease=self.enter(patch('backend.app.managed_comfyui.standalone_gpu_lease',return_value=nullcontext()))

    def mock_process(self,invalid=False):
        def run(runtime,folder,manifest,cancelled,timeout):
            items=json.loads(manifest.read_text(encoding='utf-8'))['items']
            for i,item in enumerate(items):
                with Image.open(item['input']) as src:
                    self.assertEqual(src.mode,'RGB')
                    size=src.size
                mask=Image.new('L',size)
                if not(invalid and i==1):
                    mask.paste(255,(2,3,9,8))
                mask.save(item['output'])
            return {'status':'completed','device':'cpu','count':len(items)}
        return self.enter(patch.object(vibe_rmbg,'_invoke',side_effect=run))

    def test_one_load_per_batch_keeps_source_rgb_and_alpha(self):
        run=self.mock_process()
        result=vibe_rmbg.run_cutouts(self.sources,self.output,trim_padding=0)
        self.assertEqual(list(result),list(self.sources))
        for out in result.values():
            with Image.open(out) as cut:
                self.assertEqual(cut.size,(7,5))
                self.assertEqual(cut.getpixel((1,1)),(14,70,231,128))
        self.lease.assert_called_once()
        run.assert_called_once()

    def test_bad_second_mask_does_not_publish_first(self):
        self.mock_process(invalid=True)
        with self.assertRaisesRegex(ValueError,'未能分离'):
            vibe_rmbg.run_cutouts(self.sources,self.output)
        self.assertEqual(list(self.output.iterdir()),[])

    def test_cancel_does_not_load_dependencies(self):
        with self.assertRaises(InterruptedError):
            vibe_rmbg.run_cutouts(self.sources,self.output,should_stop=lambda:True)
        self.resolver.assert_not_called()
        self.lease.assert_not_called()
