import copy
import unittest
from backend.app.vibe_annotations import clean_illustration_labels

class AnnotationTests(unittest.TestCase):
    def test_only_standalone_labels_without_images(self):
        scene={'subtitle':'医学示意', 'stages':[{'footer':'DOI:123 医学示意', 'items':[{'note':'医学示意', 'body':'医学示意不代表临床结果'}]}]}
        before=copy.deepcopy(scene)
        result=clean_illustration_labels(scene)
        self.assertEqual(result['subtitle'],'')
        self.assertEqual(result['stages'][0]['items'][0]['note'],'')
        self.assertEqual(result['stages'][0]['footer'],'DOI:123 医学示意')
        self.assertEqual(result['stages'][0]['items'][0]['body'],'医学示意不代表临床结果')
        self.assertEqual(scene,before)
    def test_images_preserve_labels(self):
        for stage in [{'visual':{'asset_id':'photo'},'items':[]}, {'items':[{'asset_id':'photo'}]}]:
            scene={'subtitle':'医学示意','stages':[stage]}
            self.assertEqual(clean_illustration_labels(scene),scene)
    def test_questions_only_response(self):
        self.assertIsNone(clean_illustration_labels(None))
