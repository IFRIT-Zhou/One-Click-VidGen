import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import HTTPException
from backend.app import main
from backend.app.tts_editor import _build_regeneration_plan

class BoundarySuggestionsTests(unittest.TestCase):
    def test_preview_is_read_only_and_preserves_all_words(self):
        job=SimpleNamespace(request={},id='preview')
        with (patch.object(main,'_owned_completed_job',return_value=(job,1)),
              patch.object(main.tts_editor,'status',return_value={'status':'idle'}),
              patch('backend.app.language_routing.project_language_scope',return_value=nullcontext()),
              patch('backend.app.tts_segmentation.segment_indextts25_text',return_value=(['前半句','后半句'],'voice_segmentation_agent',140)),
              patch.object(main.tts_editor,'resegment') as submit):
            result=main.suggest_tts_boundaries('preview',main.TtsTokenCountRequest(texts=['前半句后半句']),None)
            self.assertEqual(result['parts'],['前半句','后半句'])
            submit.assert_not_called()

    def test_agent_rewrite_is_rejected(self):
        with (patch.object(main,'_owned_completed_job',return_value=(SimpleNamespace(request={}),1)),
              patch.object(main.tts_editor,'status',return_value={}),
              patch('backend.app.language_routing.project_language_scope',return_value=nullcontext()),
              patch('backend.app.tts_segmentation.segment_indextts25_text',return_value=(['改写'],'voice_segmentation_agent',140))):
            with self.assertRaises(HTTPException):
                main.suggest_tts_boundaries('preview',main.TtsTokenCountRequest(texts=['原文']),None)

    def test_cloud_long_reading_is_safely_synthesized_and_rejoined(self):
        with patch('backend.app.tts_segmentation.build_indextts25_token_counter',return_value=len):
            text='甲'*111+'。'+'乙'*15
            chunks,positions,_=_build_regeneration_plan({3:text},'cluster')
            self.assertEqual(''.join(chunks),text)
            self.assertTrue(all(len(c)<=110 for c in chunks))
            self.assertEqual(positions[3],list(range(len(chunks))))

    def test_multiple_proposed_parts_are_accepted_by_schema(self):
        request=main.TtsResegmentRequest(start_index=1,replace_count=2,parts=[{'text':x} for x in '甲乙丙丁'])
        self.assertEqual(len(request.parts),4)

if __name__=='__main__': unittest.main()
