"""Offline CLI contract tests; these are not evidence of a live Feishu run."""
from __future__ import annotations

import copy
import html
import json
from pathlib import Path, PurePosixPath
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills"))

from shared.feishu_cli import CliResponse
from shared.feishu_adapter import _REQUIRED_SCOPES
from shared.oral_structure_install import install_feishu_preset
from shared.oral_structure_preset import load_preset, PresetError, RECEIPT_NAME
from shared.stage11_bootstrap import BootstrapRequest, FirstRunBootstrap
from shared.templates import ROOT_TITLES

TASK_ID = "01a01e29-a6ba-73a2-82e6-4ad1caa0f33b"


class WikiContractRunner:
    def __init__(self):
        self.nodes = {}
        self.documents = {}
        self.spaces = []
        self.calls = []
        self.fail_write_title = None
        self.corrupt_read_title = None
        self.wrong_parent_title = None
        self.bad_list_parent = None
        self.normalize_export = False
        self.apply_title_semantics = False

    @staticmethod
    def response(value):
        return CliResponse(0, json.dumps(value, ensure_ascii=False))

    def add(self, title, parent=""):
        number = len(self.nodes) + 1
        node = {"node_token": f"node{number}", "obj_token": f"doc{number}", "space_id": "123",
                "title": title, "parent_node_token": parent, "obj_type": "docx", "node_type": "origin",
                "obj_edit_time": "1"}
        self.nodes[node["node_token"]] = node
        self.documents[node["obj_token"]] = ""
        return node

    def run(self, argv, *, stdin=None):
        argv = tuple(argv)
        self.calls.append((argv, stdin))
        if argv == ("lark-cli", "--version"):
            return CliResponse(0, "lark-cli version 1.0.89")
        if argv == ("lark-cli", "auth", "status", "--verify", "--json"):
            return self.response({"identities": {"user": {"status": "ready", "tokenStatus": "valid", "verified": True,
                                                           "scope": " ".join(sorted(_REQUIRED_SCOPES))}}})
        if "+get-user" in argv:
            return self.response({"data": {"user": {"open_id": "ou_test", "tenant_key": "test_tenant"}}})
        assert argv[:3] == ("lark-cli", "--as", "user"), argv
        assert argv[-2:] == ("--format", "json"), argv
        def flag(name, default=None):
            return argv[argv.index(name) + 1] if name in argv else default
        params = json.loads(flag("--params", "{}"))
        command = argv[3:6]
        if command == ("wiki", "spaces", "list"):
            return self.response({"data": {"items": self.spaces}})
        if command == ("wiki", "spaces", "create"):
            space = {**json.loads(flag("--data")), "space_id": "123", "visibility": "private"}
            self.spaces.append(space)
            return self.response({"data": {"space": space}})
        if command == ("wiki", "spaces", "get"):
            return self.response({"data": {"space": {"space_id": "123", "visibility": "private", "open_sharing": "closed"}}})
        if command == ("wiki", "spaces", "get_node"):
            node = dict(self.nodes[params["token"]])
            if node["title"] == self.wrong_parent_title:
                node["parent_node_token"] = "wrongparent"
            return self.response({"data": {"node": node}})
        if command == ("wiki", "nodes", "list"):
            parent = params.get("parent_node_token", flag("--parent-node-token", ""))
            if parent == self.bad_list_parent:
                return self.response({"data": {"items": None, "has_more": True}})
            return self.response({"data": {"items": [n for n in self.nodes.values() if n["parent_node_token"] == parent], "has_more": False}})
        if command == ("wiki", "nodes", "create"):
            data = json.loads(flag("--data"))
            assert params["space_id"] == "123"
            assert data["obj_type"] == "docx" and data["node_type"] == "origin"
            return self.response({"data": {"node": self.add(data["title"], data.get("parent_node_token", ""))}})
        if argv[3:5] in (("docs", "+update"), ("docs", "+fetch")):
            token = flag("--doc")
            node = next(n for n in self.nodes.values() if n["obj_token"] == token)
            if argv[4] == "+update":
                assert flag("--doc-format") == "markdown" and flag("--content") == "-"
                assert flag("--command") == "overwrite"
                if node["title"] == self.fail_write_title:
                    return CliResponse(1, '{"error":{"type":"permission_denied"}}')
                if self.apply_title_semantics:
                    explicit = re.match(r"<title>(.*?)</title>", stdin)
                    heading = re.match(r"# ([^\n]+)", stdin)
                    if explicit:
                        node["title"] = html.unescape(explicit.group(1))
                    elif heading:
                        node["title"] = heading.group(1)
                self.documents[token] = stdin
                if self.apply_title_semantics and explicit:
                    body = stdin[explicit.end():].lstrip()
                    if body.startswith(f"# {node['title']}\n"):
                        self.documents[token] = body
                return self.response({"data": {"result": "success"}})
            content = self.documents[token]
            if self.normalize_export:
                fenced = False
                lines = []
                for line in content.splitlines():
                    if line.startswith("```"):
                        fenced = not fenced
                    elif not fenced and not line.startswith("<title>"):
                        # Feishu exports literal underscores escaped, plus table/list formatting.
                        segments = re.split(r"(`+[^`]*`+)", line)
                        line = "".join(segment if segment.startswith("`") else re.sub(r"(?<!\\)_", r"\\_", segment) for segment in segments)
                        if line.startswith("|"):
                            line = re.sub(r"\s*\|\s*", " | ", line).strip()
                        if line.startswith("- "):
                            line = "* " + line[2:]
                    lines.append(line)
                content = "\r\n".join(lines) + "\r\n"
            if node["title"] == self.corrupt_read_title:
                content = content.replace("https://feishu.cn/wiki/", "https://feishu.cn/wiki/broken")
            return self.response({"data": {"document": {"content": content}}})
        raise AssertionError(f"Unexpected CLI command: {argv}")


class FeishuPresetTests(unittest.TestCase):
    def test_explicit_document_title_preserves_numbered_node_names(self):
        runner = self.bare_runner()
        runner.apply_title_semantics = True
        receipt = install_feishu_preset(runner, "123", load_preset())
        self.assertEqual(len(receipt["files"]), 22)
        by_title = {node["title"]: node for node in runner.nodes.values()}
        for document in load_preset().documents:
            self.assertIn(PurePosixPath(document.path).stem, by_title)

    def test_markdown_export_escapes_are_accepted_but_code_and_links_are_checked(self):
        runner = self.bare_runner()
        runner.normalize_export = True
        receipt = install_feishu_preset(runner, "123", load_preset())
        self.assertEqual(len(receipt["files"]), 22)

    def test_comparison_preserves_link_and_code_meaning(self):
        from shared.oral_structure_install import _markdown_signature
        self.assertEqual(_markdown_signature("[a_b](https://feishu.cn/wiki/n1)"),
                         _markdown_signature(r"[a\_b](https://feishu.cn/wiki/n1)"))
        for changed in ("a_b https://feishu.cn/wiki/n1", "[a_b](https://feishu.cn/wiki/n2)", "[a_c](https://feishu.cn/wiki/n1)"):
            self.assertNotEqual(_markdown_signature("[a_b](https://feishu.cn/wiki/n1)"), _markdown_signature(changed))
        self.assertNotEqual(_markdown_signature('```json\n{"a": "b_c"}\n```'),
                            _markdown_signature('```json\n{"a": "b\\_c"}\n```'))
        self.assertNotEqual(_markdown_signature("价格：-100"), _markdown_signature("价格：100"))
        self.assertNotEqual(_markdown_signature("先执行\n再核验"), _markdown_signature("再核验\n先执行"))
        self.assertNotEqual(_markdown_signature("# 标题"), _markdown_signature(r"\# 标题"))
        self.assertNotEqual(_markdown_signature("# 标题"), _markdown_signature("## 标题"))
        self.assertNotEqual(_markdown_signature("| A | B |\n|---|---|\n| 甲 | 乙 |"),
                            _markdown_signature("| A | B |\n| 甲 | 乙 |"))
        self.assertEqual(_markdown_signature("| A | B |\n|---|---|\n| 甲 | 乙 |"),
                         _markdown_signature("|A|B|\n| :----- | -----: |\n|甲|乙|"))

    def bare_runner(self):
        runner = WikiContractRunner()
        for title in ROOT_TITLES.values():
            runner.add(title)
        return runner

    def test_first_run_builds_hierarchy_links_and_receipt(self):
        runner = WikiContractRunner()
        bootstrap = FirstRunBootstrap(runner=runner)
        request = BootstrapRequest(TASK_ID, "新建知识库", "feishu", "口播测试库")
        preview = bootstrap.execute(request)
        self.assertEqual(preview.status, "confirmation_required", preview.message)
        self.assertEqual(runner.nodes, {})
        self.assertIn("22", preview.preview["included_content"])
        result = bootstrap.execute(BootstrapRequest(TASK_ID, "新建知识库", "feishu", "口播测试库", confirmation=preview.confirmation))
        self.assertEqual(result.status, "created", result.message)
        self.assertEqual(len(result.root_refs), 9)
        preset = load_preset()
        nodes_by_title = {node["title"]: node for node in runner.nodes.values()}
        self.assertEqual(len(runner.nodes), 9 + 2 + 7 + 22 + 1)
        for relative in (*preset.directories, *(doc.path for doc in preset.documents)):
            path = PurePosixPath(relative)
            title = path.stem if path.suffix == ".md" else path.name
            expected_parent = ROOT_TITLES["04"] if path.parent.as_posix() == "." else path.parent.name
            node = nodes_by_title[title]
            self.assertEqual(node["parent_node_token"], nodes_by_title[expected_parent]["node_token"])
            content = runner.documents[node["obj_token"]]
            self.assertTrue(content.strip())
            self.assertNotIn("[[", content)
            for token in re.findall(r"https://feishu.cn/wiki/([A-Za-z0-9_-]+)", content):
                self.assertIn(token, runner.nodes)
        receipt_node = nodes_by_title[RECEIPT_NAME]
        self.assertEqual(receipt_node["parent_node_token"], nodes_by_title[ROOT_TITLES["06"]]["node_token"])
        receipt_body = runner.documents[receipt_node["obj_token"]]
        receipt = json.loads(receipt_body.partition("```json\n")[2].partition("\n```")[0])
        self.assertEqual(receipt["package_version"], "1.0.0")
        self.assertEqual(len(receipt["files"]), 22)
        before = copy.deepcopy((runner.nodes, runner.documents))
        retry = FirstRunBootstrap(runner=runner).execute(request)
        self.assertEqual(retry.code, "binding_conflict")
        self.assertEqual((runner.nodes, runner.documents), before)

    def test_existing_seed_conflict_is_zero_write(self):
        runner = self.bare_runner()
        method_root = next(n for n in runner.nodes.values() if n["title"] == ROOT_TITLES["04"])
        runner.add("口播结构", method_root["node_token"])
        before = copy.deepcopy((runner.nodes, runner.documents))
        with self.assertRaises(PresetError) as raised:
            install_feishu_preset(runner, "123", load_preset())
        self.assertEqual(raised.exception.code, "structure_conflict")
        self.assertEqual((runner.nodes, runner.documents), before)

    def test_write_node_and_link_failures_never_write_receipt(self):
        for failure in ("fail_write_title", "corrupt_read_title", "wrong_parent_title", "bad_list_parent"):
            with self.subTest(failure=failure):
                runner = self.bare_runner()
                setattr(runner, failure, "" if failure == "bad_list_parent" else "00_结构索引与AI调用规则")
                with self.assertRaises(PresetError):
                    install_feishu_preset(runner, "123", load_preset())
                self.assertFalse(any(node["title"] == RECEIPT_NAME for node in runner.nodes.values()))

    def test_first_run_reports_incomplete_on_failed_document(self):
        runner = WikiContractRunner()
        runner.fail_write_title = "00_结构索引与AI调用规则"
        bootstrap = FirstRunBootstrap(runner=runner)
        preview = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "feishu", "口播测试库"))
        result = bootstrap.execute(BootstrapRequest(TASK_ID, "创建知识库", "feishu", "口播测试库", confirmation=preview.confirmation))
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.locator, "https://feishu.cn/wiki/space/123")
        self.assertIn("未完整完成", result.message)
        self.assertFalse(any(node["title"] == RECEIPT_NAME for node in runner.nodes.values()))


if __name__ == "__main__":
    unittest.main()
