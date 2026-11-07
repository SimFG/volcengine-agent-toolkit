from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "plugins/volcengine-context-search/skills/volcengine-context-search"
SCRIPT = SKILL / "scripts/ctxsearch.py"
SPEC = importlib.util.spec_from_file_location("context_search_transport", SCRIPT)
assert SPEC and SPEC.loader
CS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CS)
FAKE_CREDS = {"access_key_id": "example-ak", "secret_access_key": "example-sk", "session_token": None}
DATE = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def clean_env():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(("VOLCENGINE_", "CTXSEARCH_", "VOLC_"))}


class ContextSearchTest(unittest.TestCase):
    def test_production_endpoint_and_no_routing_flags(self):
        self.assertEqual(CS.ENDPOINT["service"], "ctxsearch")
        for flag in ("--env", "--service", "--host", "--x-tt-env", "--header"):
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                CS.parse_arguments([flag, "dev"])
            self.assertNotRegex(CS.HELP, re.escape(flag) + r"(?:\s|$)")

    def test_credentials_are_paired_and_session_token_supported(self):
        chosen = CS.credentials_from({"VOLC_ACCESSKEY": " example-ak ", "VOLC_SECRETKEY": " example-sk ",
                                      "VOLC_SESSION_TOKEN": " temporary "})
        self.assertEqual(chosen["access_key_id"], "example-ak")
        self.assertEqual(chosen["session_token"], "temporary")
        with self.assertRaises(ValueError):
            CS.credentials_from({"CTXSEARCH_ACCESS_KEY_ID": "partial", "VOLC_ACCESSKEY": "fallback", "VOLC_SECRETKEY": "fallback"})

    def test_volcengine_credentials_have_highest_priority(self):
        chosen = CS.credentials_from({
            "VOLCENGINE_ACCESS_KEY": " example-new-ak ", "VOLCENGINE_SECRET_KEY": " example-new-sk ",
            "VOLCENGINE_SESSION_TOKEN": " example-new-token ",
            "CTXSEARCH_ACCESS_KEY_ID": "example-old-ak", "CTXSEARCH_SECRET_ACCESS_KEY": "example-old-sk",
            "CTXSEARCH_TEST_AK": "example-test-ak", "CTXSEARCH_TEST_SK": "example-test-sk",
            "VOLC_ACCESSKEY": "example-volc-ak", "VOLC_SECRETKEY": "example-volc-sk",
            "CTXSEARCH_SESSION_TOKEN": "example-old-token", "VOLC_SESSION_TOKEN": "example-volc-token",
        })
        self.assertEqual(chosen, {"access_key_id": "example-new-ak", "secret_access_key": "example-new-sk",
                                  "session_token": "example-new-token"})

    def test_empty_volcengine_pair_preserves_legacy_pair_priority(self):
        pairs = CS.CREDENTIAL_PAIRS[1:]
        for start in range(len(pairs)):
            variables = {"VOLCENGINE_ACCESS_KEY": " ", "VOLCENGINE_SECRET_KEY": "", "VOLCENGINE_SESSION_TOKEN": " "}
            for index in range(start, len(pairs)):
                variables[pairs[index][0]] = f"example-ak-{index}"
                variables[pairs[index][1]] = f"example-sk-{index}"
            self.assertEqual(CS.credentials_from(variables), {
                "access_key_id": f"example-ak-{start}", "secret_access_key": f"example-sk-{start}", "session_token": None})

    def test_partial_volcengine_pair_is_rejected_before_fallback(self):
        for key in CS.CREDENTIAL_PAIRS[0]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "VOLCENGINE_ACCESS_KEY.*VOLCENGINE_SECRET_KEY"):
                CS.credentials_from({key: "example-partial", "CTXSEARCH_ACCESS_KEY_ID": "example-old-ak",
                                     "CTXSEARCH_SECRET_ACCESS_KEY": "example-old-sk"})

    def test_session_token_priority_and_whitespace_fallback(self):
        variables = {"VOLCENGINE_ACCESS_KEY": "example-ak", "VOLCENGINE_SECRET_KEY": "example-sk"}
        for start in range(4):
            tokens = {name: f" example-token-{index} " if index >= start else " "
                      for index, name in enumerate(CS.SESSION_TOKEN_NAMES)}
            expected = f"example-token-{start}" if start < 3 else None
            self.assertEqual(CS.credentials_from({**variables, **tokens})["session_token"], expected)

    def test_new_token_is_used_in_security_token_header(self):
        credentials = CS.credentials_from({"VOLCENGINE_ACCESS_KEY": "example-new-ak", "VOLCENGINE_SECRET_KEY": "example-new-sk",
                                            "VOLCENGINE_SESSION_TOKEN": "example-new-token"})
        signed = CS.build_signed_request(action="ListScene", credentials=credentials, date=DATE)
        self.assertEqual(signed["headers"]["X-Security-Token"], "example-new-token")
        self.assertIn("Credential=example-new-ak/", signed["headers"]["Authorization"])
        self.assertIn("SignedHeaders=x-date;x-security-token", signed["headers"]["Authorization"])

    def test_new_credentials_are_removed_from_local_error_messages(self):
        canary = "SYNTHETIC_ERROR_CANARY_NOT_A_REAL_SECRET"
        for name in (*CS.CREDENTIAL_PAIRS[0], "VOLCENGINE_SESSION_TOKEN"):
            with self.subTest(name=name):
                variables = {**clean_env(), name: canary}
                result = subprocess.run([sys.executable, str(SCRIPT), "--action", "ListScene", "--unknown-" + canary],
                                        env=variables, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(canary, result.stderr)
                self.assertIn("[REDACTED]", result.stderr)

    def test_v4_post_signature_matches_independent_implementation(self):
        credentials = {**FAKE_CREDS, "session_token": "temporary"}
        signed = CS.build_signed_request(action="ExampleAction", method="POST", query={"Project": "default", "Tag": ["a b", "a+b", "汉字"]},
                                         body={"Name": "测试"}, credentials=credentials, date=DATE)
        body = '{"Name":"测试"}'
        esc = lambda value: quote(value, safe="-_.~")
        values = [("Action", "ExampleAction"), ("Version", "2025-09-01"), ("Project", "default"),
                  *[("Tag", tag) for tag in ["a b", "a+b", "汉字"]]]
        query = "&".join(f"{key}={value}" for key, value in sorted((esc(key), esc(value)) for key, value in values))
        sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
        headers = "x-content-sha256:" + sha(body) + "\nx-date:20260102T030405Z\nx-security-token:temporary\n"
        names = "x-content-sha256;x-date;x-security-token"
        canonical = "\n".join(["POST", "/", query, headers, names, sha(body)])
        scope = "20260102/cn-beijing/ctxsearch/request"
        to_sign = "\n".join(["HMAC-SHA256", "20260102T030405Z", scope, sha(canonical)])
        key = b"example-sk"
        for step in ("20260102", "cn-beijing", "ctxsearch", "request"):
            key = hmac.new(key, step.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signed["headers"]["Authorization"],
                         f"HMAC-SHA256 Credential=example-ak/{scope}, SignedHeaders={names}, Signature={signature}")
        self.assertEqual(signed["payload"], body.encode())
        self.assertEqual(int(signed["headers"]["Content-Length"]), len(body.encode()))
        self.assertNotIn("x-tt-env", signed["headers"])

    def test_get_signature_without_body_and_token(self):
        signed = CS.build_signed_request(action="ListScene", query={"PageNumber": 1}, credentials=FAKE_CREDS, date=DATE)
        self.assertEqual(signed["method"], "GET")
        self.assertIsNone(signed["payload"])
        self.assertNotIn("X-Security-Token", signed["headers"])
        self.assertIn("SignedHeaders=x-date", signed["headers"]["Authorization"])

    def test_query_and_body_validation(self):
        bad = [lambda: CS.query_entries({"Action": "DeleteScene"}), lambda: CS.query_entries({"Version": "other"}),
               lambda: CS.query_entries({"Nested": {"key": "value"}}), lambda: CS.query_entries({"Items": [None]}),
               lambda: CS.query_entries({"Number": float("inf")}),
               lambda: CS.parse_arguments(["--action", "ListScene", "--action", "GetScene"]),
               lambda: CS.parse_arguments(["--action", "--method", "GET"]),
               lambda: CS.build_signed_request(action="ListScene", method="GET", body={}, credentials=FAKE_CREDS),
               lambda: CS.call_input({"--query": "{}", "--query-file": "unused"}, {}),
               lambda: CS.call_input({"--query-stdin": True, "--body-stdin": True}, {}, lambda: "{}"),
               lambda: CS.object_json('{"x":NaN}', "body"), lambda: CS.object_json("[]", "body")]
        for call in bad:
            with self.assertRaises(ValueError):
                call()

    def test_query_serialization_boolean_empty_arrays_and_repeated_values(self):
        signed = CS.build_signed_request(action="ListScene", credentials=FAKE_CREDS, date=DATE,
                                         query={"Flag": True, "No": False, "Empty": [], "Omitted": None, "Tag": ["x+y", "x y"]})
        parsed = parse_qs(urlsplit(signed["url"]).query)
        self.assertEqual(parsed["Flag"], ["true"])
        self.assertEqual(parsed["No"], ["false"])
        self.assertEqual(set(parsed["Tag"]), {"x+y", "x y"})
        self.assertNotIn("Empty", parsed)
        self.assertNotIn("Omitted", parsed)

    def test_inline_file_stdin_and_param_override(self):
        variables = {"VOLCENGINE_ACCESS_KEY": "example-ak", "VOLCENGINE_SECRET_KEY": "example-sk"}
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "body.json"
            file.write_text('{"Name":"测试"}', encoding="utf-8")
            request = CS.call_input({"--action": "ExampleAction", "--method": "POST", "--body-file": str(file),
                                     "--query-stdin": True, "--param": ["Project=chosen"]}, variables,
                                    lambda: '{"Project":"default"}')
        self.assertEqual(request["payload"], '{"Name":"测试"}'.encode())
        self.assertEqual(parse_qs(urlsplit(request["url"]).query)["Project"], ["chosen"])

    def test_response_exit_codes_and_json_redaction(self):
        sensitive = {"ResponseMetadata": {}, "Result": {"Key": "secret", "UploadUrl": "https://secret", "ObjectKey": "private/object",
                     "ApiKey": "private-key", "Password": "password", "Variables": [{"Secret": True, "Value": "secret-value"}]}}
        outputs = [CS.classify_response({"status": 200, "text": json.dumps(sensitive)}, variables={}),
                   CS.classify_response({"status": 200, "text": '{"ResponseMetadata":{"Error":{"Code":"Denied"}}}'}),
                   CS.classify_response({"status": 403, "text": "denied"}),
                   CS.classify_response({"status": 200, "text": "data: test\n\n"})]
        self.assertEqual([item["code"] for item in outputs], [0, 1, 1, 0])
        result = json.loads(outputs[0]["text"])["Result"]
        self.assertEqual(result["ApiKey"], "[REDACTED]")
        self.assertEqual(result["Variables"][0]["Value"], "[REDACTED]")
        self.assertEqual(result["UploadUrl"], "[REDACTED]")

    def test_free_text_sse_and_configured_values_are_redacted(self):
        canary = "SYNTHETIC_CANARY_NOT_A_REAL_SECRET"
        text = 'data: ' + json.dumps({"ApiKey": canary, "Secret": True, "Value": canary}) + "\n\n"
        self.assertNotIn(canary, CS.classify_response({"status": 200, "text": text}, variables={})["text"])
        self.assertNotIn(canary, CS.redact({"Message": f"Authorization: {canary}"}, {})["Message"])
        self.assertNotIn(canary, CS.redact({"Message": f"ApiKey={canary}"}, {})["Message"])
        self.assertNotIn(canary, CS.redact_text(f"free text {canary}", {"VOLCENGINE_SECRET_KEY": canary}))

    def test_missing_credentials_does_not_make_network_request(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--action", "ListScene"],
                                env=clean_env(), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("缺少凭据", result.stderr)

    def test_private_output_file_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "response.json"
            CS.write_private_new_file(target, "first")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                CS.write_private_new_file(target, "second")
            self.assertEqual(target.read_text(), "first")
            link = Path(directory) / "link.json"
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                CS.write_private_new_file(link, "bad")
            self.assertEqual(target.read_text(), "first")

    def test_upload_fixed_put_headers_and_no_openapi_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "url.txt").write_text("https://example.invalid/presigned")
            (root / "asset.bin").write_bytes(b"\x00\x01")
            (root / "headers.json").write_text('{"x-amz-acl":"private","content-length":"999"}')
            options = {"--url-file": str(root / "url.txt"), "--file": str(root / "asset.bin"), "--headers-file": str(root / "headers.json")}
            request = CS.upload_input(options)
            self.assertEqual(request["method"], "PUT")
            self.assertEqual(request["headers"]["Content-Length"], "2")
            self.assertNotIn("content-length", request["headers"])
            (root / "headers.json").write_text('{"Authorization":"forbidden"}')
            with self.assertRaises(ValueError):
                CS.upload_input(options)

    def test_transport_https_no_redirect_response_size_and_timeout(self):
        with self.assertRaises(ValueError):
            CS.send({"url": "http://example.invalid", "method": "GET", "headers": {}}, 1000)
        connection = mock.MagicMock()
        response = connection.getresponse.return_value
        response.status = 302
        response.read1.side_effect = [b"redirect", b""]
        with mock.patch.object(CS.http.client, "HTTPSConnection", return_value=connection):
            result = CS.send({"url": "https://example.invalid/a", "method": "GET", "headers": {}}, 1000)
        self.assertEqual(result["status"], 302)
        connection.request.assert_called_once()
        connection.close.assert_called_once()
        connection.reset_mock()
        response.read1.side_effect = [b"12345"]
        with mock.patch.object(CS.http.client, "HTTPSConnection", return_value=connection), mock.patch.object(CS, "MAX_RESPONSE_BYTES", 4):
            with self.assertRaisesRegex(ValueError, "32 MiB"):
                CS.send({"url": "https://example.invalid", "method": "GET", "headers": {}}, 1000)
        with mock.patch.object(CS.http.client, "HTTPSConnection", return_value=connection), mock.patch.object(CS.time, "monotonic", side_effect=[0, 2]):
            with self.assertRaises(TimeoutError):
                CS.send({"url": "https://example.invalid", "method": "GET", "headers": {}}, 1000)

    def test_main_output_and_exit_code(self):
        for response, expected in [({"status": 200, "text": '{"ResponseMetadata":{},"Result":{"Items":[]}}'}, 0),
                                   ({"status": 200, "text": '{"ResponseMetadata":{"Error":{"Code":"Denied"}}}'}, 1)]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(CS, "call_input", return_value={}), mock.patch.object(CS, "send", return_value=response), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(CS.main(["--action", "ListScene"]), expected)
            self.assertTrue((stdout if expected == 0 else stderr).getvalue())


class ContextSearchPackageTest(unittest.TestCase):
    def test_catalog_contains_optional_plugin(self):
        catalog = json.loads((ROOT / "skills/core/volcengine-find-skills/references/catalog.json").read_text())
        entry = next(item for item in catalog["plugins"] if item["name"] == "volcengine-context-search")
        self.assertFalse(entry["default"])
        self.assertEqual(entry["skills"][0]["path"], SKILL.relative_to(ROOT).as_posix())

    def test_no_internal_service_or_node_dependency(self):
        for path in SKILL.rglob("*"):
            if path.is_file() and path.suffix != ".pyc":
                content = path.read_text(encoding="utf-8")
                for item in ("ctxsearch_dev", "ctxsearch_stg", "ark-context-search-cli", "--env", "--x-tt-env", "ctxsearch.mjs", "Node.js", "node scripts/"):
                    self.assertNotIn(item, content, str(path))
        self.assertFalse((SKILL / "scripts/ctxsearch.mjs").exists())


if __name__ == "__main__":
    unittest.main()
