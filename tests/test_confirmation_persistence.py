from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'skills'), str(ROOT / 'tests')]
from shared.confirmation_store import ConfirmationStore
from shared.stage11_bootstrap import FirstRunBootstrap, BootstrapRequest
from shared.feishu_cli import CliResponse
from test_host_onboarding import Runner

TASK = '01a01e29-a6ba-73a2-82e6-4ad1caa0f33b'


class PersistentConfirmations(unittest.TestCase):
    def test_real_second_process_creates_local_vault_without_converter(self):
        with tempfile.TemporaryDirectory(dir='/private/tmp' if sys.platform == 'darwin' else None) as tmp:
            root = Path(tmp).resolve()
            req = BootstrapRequest(TASK, '创建知识库', 'obsidian', 'Probe', obsidian_parent=str(root))
            preview = FirstRunBootstrap(confirmation_dir=root/'state').execute(req)
            self.assertEqual(preview.status, 'confirmation_required')
            script = '''from shared.stage11_bootstrap import FirstRunBootstrap, BootstrapRequest
from pathlib import Path
import json,sys
p=json.loads(sys.argv[1]); r=FirstRunBootstrap(confirmation_dir=Path(p.pop('state'))).execute(BootstrapRequest(**p)); print(r.status); assert r.status == 'created',r
'''
            data = {**req.__dict__, 'confirmation': preview.confirmation, 'state': str(root/'state')}
            proc = subprocess.run([sys.executable, '-c', script, json.dumps(data)], env={**os.environ, 'PYTHONPATH': str(ROOT/'skills')}, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout+proc.stderr)
            self.assertTrue((root/'Probe'/'06-Agent与Workflow'/'oral-structure-installation.json').is_file())

    def test_account_and_tenant_changes_invalidate_confirmation(self):
        for user, tenant in [('ou_other','tenant_probe'), ('ou_probe','other_tenant')]:
            with self.subTest(user=user, tenant=tenant), tempfile.TemporaryDirectory() as tmp:
                runner = Runner()
                bootstrap = FirstRunBootstrap(runner=runner, confirmation_dir=Path(tmp))
                req = BootstrapRequest(TASK, '创建知识库', 'feishu', 'Probe')
                preview = bootstrap.execute(req)
                runner.identity = CliResponse(0,json.dumps({'data':{'user':{'open_id':user,'tenant_key':tenant}}}))
                with mock.patch.object(bootstrap, '_create_feishu') as create:
                    result = bootstrap.execute(replace(req,confirmation=preview.confirmation))
                    self.assertEqual(result.code, 'confirmation_mismatch')
                    create.assert_not_called()

    def test_changed_target_and_task_cannot_reuse_approval(self):
        for change in [{'knowledge_base_name':'Other'}, {'task_id':'01a01e29-a6ba-73a2-82e6-4ad1caa0f33c'}]:
            with tempfile.TemporaryDirectory() as tmp:
                bootstrap = FirstRunBootstrap(runner=Runner(), confirmation_dir=Path(tmp))
                req = BootstrapRequest(TASK, '创建知识库', 'feishu', 'Probe')
                preview = bootstrap.execute(req)
                with mock.patch.object(bootstrap, '_create_feishu') as create:
                    result = bootstrap.execute(replace(req, confirmation=preview.confirmation, **change))
                    self.assertEqual(result.code, 'confirmation_mismatch')
                    create.assert_not_called()

    def test_expired_replayed_unknown_and_concurrent_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = [100.0]
            store = ConfirmationStore(Path(tmp), now=lambda: now[0])
            token = store.issue('digest')
            now[0] += 1801
            self.assertEqual(store.consume(token,'digest'), 'receipt_expired')
            token = store.issue('digest')
            def consume(_):
                return ConfirmationStore(Path(tmp), now=lambda: now[0]).consume(token,'digest')
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(consume, (1,2)))
            self.assertCountEqual(results, [None,'receipt_reused'])
            self.assertEqual(store.consume('0'*64,'digest'),'confirmation_mismatch')

    def test_broken_store_prevents_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)/'file'; state.write_text('not a directory')
            bootstrap = FirstRunBootstrap(runner=Runner(), confirmation_dir=state)
            with mock.patch.object(bootstrap, '_create_feishu') as create:
                result=bootstrap.execute(BootstrapRequest(TASK,'创建知识库','feishu','Probe'))
                self.assertEqual(result.code,'write_failed')
                create.assert_not_called()

if __name__ == '__main__': unittest.main()
