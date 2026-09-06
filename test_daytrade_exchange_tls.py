import ast
import io
import json
import ssl
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from unittest.mock import patch

# Load the HTTP helper without importing brokerage libraries or starting collectors.
module = ast.parse(Path(__file__).with_name("daytrade_flow.py").read_text(encoding="utf-8"))
helper = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_fetch_json")
exec(compile(ast.Module(body=[helper], type_ignores=[]), "daytrade_flow.py", "exec"), globals())

class ExchangeTlsTests(unittest.TestCase):
    def test_ski_compatibility_still_requires_trust_and_hostname(self):
        failure = urllib.error.URLError(ssl.SSLCertVerificationError(1, "Missing Subject Key Identifier"))
        with patch("urllib.request.urlopen", side_effect=[failure, io.BytesIO(b'{"ok":true}')]) as fetch:
            self.assertEqual(_fetch_json("https://www.twse.com.tw/history", {}), {"ok": True})
            context = fetch.call_args.kwargs["context"]
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)
            self.assertTrue(context.verify_flags & ssl.VERIFY_X509_TRUSTED_FIRST)

    def test_other_certificate_failures_and_hosts_are_not_retried(self):
        for host, message in [("www.twse.com.tw", "certificate has expired"), ("example.com", "Missing Subject Key Identifier")]:
            failure = urllib.error.URLError(ssl.SSLCertVerificationError(1, message))
            with patch("urllib.request.urlopen", side_effect=failure) as fetch:
                with self.assertRaises(urllib.error.URLError):
                    _fetch_json("https://" + host + "/history", {})
                self.assertEqual(fetch.call_count, 1)

    def test_success_uses_normal_verification_without_retry(self):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b'[]')) as fetch:
            self.assertEqual(_fetch_json("https://www.tpex.org.tw/history", {}), [])
            self.assertNotIn("context", fetch.call_args.kwargs)
            self.assertEqual(fetch.call_count, 1)

if __name__ == "__main__":
    unittest.main()
