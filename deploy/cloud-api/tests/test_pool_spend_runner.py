import os
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
from contextlib import ExitStack
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock
from app import pool_spend_runner as runner
from app.pool_balance_alerts import DeliveryResult


class SpendRunnerTests(unittest.TestCase):
    def test_baseline_then_twenty_no_history_no_duplicate_mail(self):
        with TemporaryDirectory() as folder,ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,{"POOL_SPEND_STATE_DIR":folder,"POOL_SPEND_EMAIL_ENABLED":"true"}))
            stack.enter_context(patch.dict("sys.modules",{"fcntl":SimpleNamespace(flock=lambda *args:None,LOCK_EX=1,LOCK_NB=2)}))
            stack.enter_context(patch.object(runner,"settings",SimpleNamespace(ican_image_api_keys=("key",))))
            source=stack.enter_context(patch.object(runner,"ican_total_usd",side_effect=[Decimal("100"),Decimal("120"),Decimal("120")]))
            sessions=stack.enter_context(patch.object(runner,"SessionLocal"))
            sessions.return_value.__enter__.return_value.scalars.return_value=[]
            stack.enter_context(patch.object(runner.pool_spend_email,"configured",return_value=True))
            send=stack.enter_context(patch.object(runner.pool_spend_email,"send",return_value=DeliveryResult("accepted")))
            stack.enter_context(patch("builtins.print"))
            runner.run();self.assertEqual(send.call_count,0)
            runner.run();self.assertEqual(send.call_count,1)
            runner.run();self.assertEqual(send.call_count,1)
            report=json.loads((Path(folder)/"status.json").read_text())
            self.assertEqual(Decimal(report["total_usd"]),20)
            self.assertIn("仅包括已确认",send.call_args.args[1])
