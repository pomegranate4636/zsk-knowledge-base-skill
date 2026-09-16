from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))
sys.path.insert(0, str(ROOT))
import install
from shared.feishu_adapter import FeishuAdapter, _REQUIRED_SCOPES
from shared.feishu_cli import CliResponse
from shared import markdown_converter as converter
from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap


class Runner:
    def __init__(self, *, version="lark-cli version v1.0.94+5092114", auth=None, identity=None):
        self.calls = []
        self.version = version
        self.auth = auth or CliResponse(2, '', 'Error: unknown command "auth" for "lark-cli"')
        self.identity = identity or CliResponse(0, json.dumps({"ok": True, "data": {"user": {"open_id": "ou_probe", "tenant_key": "tenant_probe"}}}))

    def run(self, argv, *, stdin=None):
        self.calls.append(tuple(argv))
        if argv == ("lark-cli", "--version"):
            return CliResponse(0, self.version)
        if "auth" in argv:
            return self.auth
        if "+get-user" in argv:
            return self.identity
        if "list" in argv:
            return CliResponse(0, '{"ok":true,"data":{"items":[]}}')
        raise AssertionError(f"Unexpected operation: {argv}")


class HostAuthTests(unittest.TestCase):
    def test_hosted_auth_only_proves_identity_not_scopes(self):
        runner = Runner()
        result = FeishuAdapter(runner).doctor()
        self.assertEqual(result.status, "ok")
        self.assertNotIn("required_scopes", result.checked)
        self.assertEqual(result.metadata["write_permissions"], "not_preverified")
        self.assertTrue(all("create" not in argv and "login" not in argv for argv in runner.calls))

    def test_structured_missing_command_selects_hosted_identity(self):
        error = CliResponse(2, json.dumps({"ok": False, "error": {"message": 'unknown command "auth" for "lark-cli"'}}))
        self.assertEqual(FeishuAdapter(Runner(auth=error)).doctor().status, "ok")

    def test_hosted_auth_reaches_preview_without_creating(self):
        runner = Runner()
        result = FirstRunBootstrap(runner=runner).execute(BootstrapRequest(
            "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b", "创建知识库", "feishu", "Probe"))
        self.assertEqual(result.status, "confirmation_required")
        self.assertIn("权限", result.preview["permission_check"])
        self.assertTrue(all("create" not in argv for argv in runner.calls))

    def test_standalone_keeps_scope_validation(self):
        for scopes, expected in [(list(_REQUIRED_SCOPES), "ok"), ([], "blocked")]:
            with self.subTest(scopes=scopes):
                auth = CliResponse(0, json.dumps({"identities": {"user": {
                    "status": "ready", "tokenStatus": "valid", "verified": True, "scope": " ".join(scopes)}}}))
                runner = Runner(auth=auth)
                self.assertEqual(FeishuAdapter(runner).doctor().status, expected)
                self.assertFalse(any("+get-user" in argv for argv in runner.calls))

    def test_auth_failure_is_never_treated_as_hosted(self):
        for response in [CliResponse(1, '{"ok":false,"error":{"type":"permission_denied"}}'),
                         CliResponse(1, '', 'network timeout'), CliResponse(0, '{}'),
                         CliResponse(0, '{"identities":{"user":null}}')]:
            with self.subTest(response=response):
                runner = Runner(auth=response)
                self.assertNotEqual(FeishuAdapter(runner).doctor().status, "ok")
                self.assertFalse(any("+get-user" in argv for argv in runner.calls))

    def test_missing_identity_or_permission_stops(self):
        for response in [CliResponse(0, '{"ok":true,"data":{}}'),
                         CliResponse(1, '{"ok":false,"error":{"type":"permission_denied"}}')]:
            self.assertNotEqual(FeishuAdapter(Runner(identity=response)).doctor().status, "ok")

    def test_old_or_malformed_version_still_stops(self):
        for version in ['lark-cli version v1.0.88+build', 'lark-cli version garbage']:
            runner = Runner(version=version)
            self.assertNotEqual(FeishuAdapter(runner).doctor().status, "ok")
            self.assertEqual(len(runner.calls), 1)


class ConverterCompatibilityTests(unittest.TestCase):
    def tearDown(self):
        converter._version.cache_clear()

    def test_missing_version_does_not_prevent_actual_conversion(self):
        def run(argv, **kwargs):
            if '--version' in argv:
                return subprocess.CompletedProcess(argv, 2, '', 'unrecognized arguments: --version')
            if '--help' in argv:
                return subprocess.CompletedProcess(argv, 0, 'usage: markitdown ...', '')
            Path(argv[-1]).write_text('converted document', encoding='utf-8')
            return subprocess.CompletedProcess(argv, 0, '', '')
        with mock.patch.object(converter, '_executable', return_value='/probe/markitdown'), mock.patch.object(converter.subprocess, 'run', side_effect=run):
            result = converter.convert_to_markdown(b'probe', '.docx')
        self.assertIn('unreported', result.version)
        self.assertIn('converted document', result.text)

    def test_missing_version_never_hides_conversion_failure(self):
        with mock.patch.object(converter, '_executable', return_value='/probe/markitdown'), mock.patch.object(converter, '_version', return_value='unreported'), mock.patch.object(converter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'missing dependency')):
            with self.assertRaises(converter.ConversionFailed):
                converter.convert_to_markdown(b'probe', '.docx')

    def test_reuse_performs_no_install(self):
        with mock.patch.object(install, 'probe_formats', return_value={'docx': True}), mock.patch.object(install.subprocess, 'run') as run:
            self.assertTrue(install.install_converter(('docx',)))
            run.assert_not_called()

    def test_installs_only_failed_format_and_preserves_version(self):
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / 'markitdown'
            exe.touch()
            metadata = {'venvs': {'markitdown': {'metadata': {'main_package': {'app_paths': [str(exe)]}}}}}
            responses = [subprocess.CompletedProcess([], 0, json.dumps(metadata), ''),
                         subprocess.CompletedProcess([], 0, 'Name: markitdown\nVersion: 0.1.7\n', ''),
                         subprocess.CompletedProcess([], 0, '', '')]
            with mock.patch.dict('os.environ', {}, clear=True), mock.patch.object(install, 'probe_formats', side_effect=[{'docx': True, 'pdf': False}, {'docx': True, 'pdf': True}]), mock.patch.object(install.shutil, 'which', side_effect=lambda name: '/bin/pipx' if name == 'pipx' else str(exe)), mock.patch.object(install.subprocess, 'run', side_effect=responses) as run:
                self.assertTrue(install.install_converter(('docx', 'pdf')))
                self.assertEqual(run.call_args_list[-1].args[0], ('/bin/pipx', 'runpip', 'markitdown', 'install', 'markitdown[pdf]==0.1.7'))

    def test_other_python_environment_is_not_modified(self):
        with mock.patch.dict('os.environ', {}, clear=True), mock.patch.object(install, 'probe_formats', return_value={'docx': False}), mock.patch.object(install.shutil, 'which', side_effect=lambda name: '/bin/pipx' if name == 'pipx' else '/another/markitdown'), mock.patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"venvs":{"markitdown":{}}}', '')) as run:
            self.assertFalse(install.install_converter(('docx',)))
            self.assertEqual(len(run.call_args_list), 1)  # inventory only, no pip install

    def test_malformed_pipx_metadata_stops_before_install(self):
        with mock.patch.dict('os.environ', {}, clear=True), mock.patch.object(install, 'probe_formats', return_value={'docx': False}), mock.patch.object(install.shutil, 'which', return_value='/fake/executable'), mock.patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{"venvs":{"markitdown":{"metadata":null}}}', '')) as run:
            self.assertFalse(install.install_converter(('docx',)))
            self.assertEqual(len(run.call_args_list), 1)

    def test_read_only_flags_cannot_be_combined_with_install(self):
        for flags in [('--check', '--dependencies-only'), ('--doctor', '--dependencies-only'), ('--package-check', '--install-markitdown'), ('--check', '--install-markitdown')]:
            with self.subTest(flags=flags), mock.patch.object(sys, 'argv', ['install.py', *flags, '--formats', 'docx']), mock.patch.object(install, 'install_converter') as dep, mock.patch.object(install, 'install') as copy:
                with self.assertRaises(SystemExit):
                    install.main()
                dep.assert_not_called()
                copy.assert_not_called()

    def test_dependencies_only_does_not_reinstall_skill(self):
        with mock.patch.object(sys, 'argv', ['install.py', '--dependencies-only', '--formats', 'docx']), mock.patch.object(install, 'install_converter', return_value=True) as dep, mock.patch.object(install, 'install') as copy:
            self.assertEqual(install.main(), 0)
            dep.assert_called_once_with(('docx',))
            copy.assert_not_called()

    def test_non_codex_needs_explicit_destination(self):
        with mock.patch.object(sys, 'argv', ['install.py', '--host', 'doubao']), mock.patch.object(install, 'install') as copy:
            with self.assertRaises(SystemExit):
                install.main()
            copy.assert_not_called()

    def test_bootstrap_doctor_does_not_require_converter(self):
        with mock.patch.object(sys, 'argv', ['install.py', '--doctor', '--dest', str(ROOT / 'skills')]), mock.patch.object(install, 'converter_version', return_value=None), mock.patch.object(install, 'page_evidence_status', return_value={'pdf': False, 'pptx': False}), mock.patch.object(install, 'local_ocr_status', return_value={'ready': False, 'languages': ()}), mock.patch.object(install, 'probe_formats') as probe:
            self.assertEqual(install.main(), 0)
            probe.assert_not_called()

if __name__ == '__main__':
    unittest.main()
