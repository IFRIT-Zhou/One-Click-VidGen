import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
from contextlib import ExitStack
from decimal import Decimal
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from app import pool_balance_runner as runner
from app.pool_balance_alerts import DeliveryResult


class RunnerTests(unittest.TestCase):
    def execute(self, enabled, currency):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"POOL_BALANCE_STATE_DIR":folder,"POOL_BALANCE_EMAIL_ENABLED":enabled}))
            stack.enter_context(patch.dict("sys.modules", {"fcntl":SimpleNamespace(flock=lambda *a:None,LOCK_EX=1,LOCK_NB=2)}))
            stack.enter_context(patch.object(runner,"settings",SimpleNamespace(runninghub_image_api_keys=(),ican_image_api_keys=("secret-test",))))
            stack.enter_context(patch.object(runner,"query_ican",return_value=(Decimal("20"),currency)))
            stack.enter_context(patch.object(runner.pool_balance_email,"configured",return_value=True))
            send=stack.enter_context(patch.object(runner.pool_balance_email,"send",return_value=DeliveryResult("accepted")))
            stack.enter_context(patch("builtins.print"))
            runner.run()
            runner.run()
            report=json.loads((Path(folder)/"status.json").read_text())
            self.assertNotIn("secret-test",json.dumps(report))
            return report,send.call_count

    def test_disabled_never_sends(self):
        report,count=self.execute("false","CNY")
        self.assertEqual(count,0)
        self.assertEqual(report["accounts"][0]["status"],"low")

    def test_currency_mismatch_never_sends(self):
        report,count=self.execute("true","USD")
        self.assertEqual(count,0)
        self.assertEqual(report["accounts"][0]["status"],"currency_mismatch")

    def test_enabled_low_balance_sends_once(self):
        report,count=self.execute("true","CNY")
        self.assertEqual(count,1)
