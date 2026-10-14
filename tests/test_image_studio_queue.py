import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from backend.app import image_studio as studio


class ImageQueueTests(unittest.TestCase):
    def setUp(self):
        studio.ACTIVE.clear();studio.QUEUED.clear();studio.RUNNING_LANES.clear()

    def test_bounded_parallel_queue_and_drain(self):
        workers=[]
        class Thread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):workers.append(self.target)
        with tempfile.TemporaryDirectory() as tmp,patch.object(studio.threading,'Thread',Thread),patch('module4_video_render._poster_worker_count',return_value=2),patch.object(studio,'execute') as execute:
            rows=[]
            for i in range(3):
                path=Path(tmp)/str(i);path.mkdir();row={'id':str(i)};rows.append(row)
                studio.enqueue(1,path,row,{'endpoint':'a','api_key':'key'})
            self.assertEqual([r['status'] for r in rows],['running','running','queued'])
            self.assertEqual(len(workers),2)
            workers.pop(0)()
            self.assertEqual(rows[2]['status'],'running')
            self.assertEqual(len(studio.ACTIVE),2)
            for worker in list(workers):worker()
            self.assertFalse(studio.ACTIVE)
            self.assertEqual(execute.call_count,3)

    def test_distinct_outlets_and_cancel_only_waiting(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(studio.threading.Thread,'start'),patch('module4_video_render._poster_worker_count',return_value=1),patch.object(studio,'require_user',return_value={'id':1}):
            rows=[]
            for i,endpoint in enumerate(['a','a','b']):
                path=Path(tmp)/str(i);path.mkdir();row={'id':str(i)};rows.append(row)
                studio.enqueue(1,path,row,{'endpoint':endpoint,'api_key':'key'})
            self.assertEqual([r['status'] for r in rows],['running','queued','running'])
            studio.cancel_queued('1',None)
            self.assertEqual(rows[1]['status'],'cancelled')
            self.assertNotIn((1,'1'),studio.ACTIVE)
            with self.assertRaises(Exception):studio.cancel_queued('0',None)

    def tearDown(self):
        studio.ACTIVE.clear();studio.QUEUED.clear();studio.RUNNING_LANES.clear()
