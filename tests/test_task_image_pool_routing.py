import unittest
from unittest.mock import patch
from backend.app.main import GenerateRequest, _required_job_config_error

class TaskImageRoutingTests(unittest.TestCase):
    def test_personal_image_and_cloud_language_are_independent(self):
        data={'use_cloud_image_pool':False,'language_route':'cloud','image_profile_snapshot':{'id':'own'}}
        with patch('backend.app.main._api_key_status',return_value={'language':{'configured':False},'image':{'configured':False}}):
            self.assertIsNone(_required_job_config_error(data))
            self.assertIsNotNone(_required_job_config_error({**data,'image_profile_snapshot':None}))

    def test_cloud_image_does_not_bypass_explicit_personal_language(self):
        with patch('backend.app.main._api_key_status',return_value={'language':{'configured':False},'image':{'configured':False}}):
            self.assertIsNotNone(_required_job_config_error({'use_cloud_image_pool':True,'language_route':'personal'}))

if __name__=='__main__': unittest.main()
