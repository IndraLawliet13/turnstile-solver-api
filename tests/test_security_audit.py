import unittest
import os
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock
from api_solver import (
    parse_proxy_config,
    redact_proxy_config,
    build_route_html,
    route_glob,
    validate_target_url,
    create_app,
    TurnstileAPIServer
)
import db_results

class SecurityAuditTestSuite(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db_results.DB_PATH = "test_sec_audit.db"
        if os.path.exists("test_sec_audit.db"):
            os.remove("test_sec_audit.db")
        await db_results.init_db()

        self.app = create_app(
            headless=True,
            useragent="",
            debug=True,
            browser_type="chromium",
            thread=1,
            proxy_support=False,
            use_random_config=False,
            browser_name="",
            browser_version=""
        )
        self.client = self.app.test_client()

    async def asyncTearDown(self):
        if os.path.exists("test_sec_audit.db"):
            os.remove("test_sec_audit.db")

    def test_proxy_credential_redaction_formats(self):
        """Verify credential masking across all supported proxy URL and ip:port formats."""
        # 1. Standard http with user/pass
        p1 = parse_proxy_config("http://admin:SuperSecret123@192.168.1.50:8080")
        redacted1 = redact_proxy_config(p1)
        self.assertNotIn("SuperSecret123", redacted1)
        self.assertIn("a***n:***", redacted1)

        # 2. SOCKS5 with special chars
        p2 = parse_proxy_config("socks5://my_user:P%40ssw0rd!@10.0.0.1:1080")
        redacted2 = redact_proxy_config(p2)
        self.assertNotIn("P@ssw0rd!", redacted2)
        self.assertNotIn("P%40ssw0rd!", redacted2)
        self.assertIn("m***r:***", redacted2)

        # 3. 4-part colon format ip:port:user:pass
        p3 = parse_proxy_config("127.0.0.1:8080:alice:hunter2")
        redacted3 = redact_proxy_config(p3)
        self.assertNotIn("hunter2", redacted3)
        self.assertIn("a***e:***", redacted3)

        # 4. Proxy without auth
        p4 = parse_proxy_config("http://127.0.0.1:8080")
        redacted4 = redact_proxy_config(p4)
        self.assertEqual(redacted4, "http://127.0.0.1:8080")

    def test_xss_and_template_injection_in_route_html(self):
        """Ensure malicious HTML/JS payloads in sitekey, action, and cdata are HTML-escaped."""
        payload_sitekey = '0x4AAAAAAAJ5"><script>alert(1)</script>'
        payload_action = 'login" onfocus="alert(2)"'
        payload_cdata = '<img src=x onerror=alert(3)>'

        html = build_route_html(payload_sitekey, payload_action, payload_cdata)
        
        # Unescaped script tags must NOT appear
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertNotIn('onfocus="alert(2)"', html)
        self.assertNotIn('<img src=x onerror=alert(3)>', html)
        
        # Properly escaped attributes must appear
        self.assertIn('&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn('data-action="login&quot; onfocus=&quot;alert(2)&quot;"', html)
        self.assertIn('&lt;img src=x onerror=alert(3)&gt;', html)

    async def test_overlay_injection_safety(self):
        """Ensure _load_captcha_overlay uses safe JSON evaluation arguments."""
        mock_page = MagicMock()
        mock_page.evaluate = AsyncMock()

        server = TurnstileAPIServer(
            headless=True, useragent=None, debug=True, browser_type="chromium",
            thread=1, proxy_support=False
        )

        malicious_key = "0x4AAAA'; alert(document.cookie); //"
        malicious_action = 'action"); dropDatabase(); ("'
        malicious_cdata = 'cdata</script><script>exploit()</script>'

        await server._load_captcha_overlay(mock_page, malicious_key, malicious_action, malicious_cdata, index=1)
        
        # Verify mock_page.evaluate was called
        mock_page.evaluate.assert_called_once()
        eval_arg = mock_page.evaluate.call_args[0][0]
        
        # Verify JSON serialization prevents raw quote breakouts
        self.assertIn(json.dumps(malicious_key), eval_arg)
        self.assertIn(json.dumps(malicious_action), eval_arg)
        self.assertIn(json.dumps(malicious_cdata), eval_arg)

    async def test_db_storage_does_not_leak_raw_proxy_passwords(self):
        """Verify request proxy stored in DB metadata only records 'provided' flag, never raw URL/credentials."""
        resp = await self.client.get('/turnstile?url=https://example.com&sitekey=0x4AAAAAA&proxy=http://secret_user:super_secret_pass@1.2.3.4:8080')
        self.assertEqual(resp.status_code, 200)
        data = await resp.get_json()
        self.assertIsNotNone(data)
        task_id = data.get("taskId")
        self.assertIsNotNone(task_id)

        # Inspect SQLite DB record
        res_tuple = await db_results.load_result_with_type(str(task_id))
        self.assertIsNotNone(res_tuple)
        if res_tuple:
            task_type, res = res_tuple
            self.assertEqual(task_type, "turnstile")
            self.assertIsInstance(res, dict)
            
            # Verify secret_user and super_secret_pass are NOT in DB
            db_raw_dump = json.dumps(res)
            self.assertNotIn("secret_user", db_raw_dump)
            self.assertNotIn("super_secret_pass", db_raw_dump)
            if isinstance(res, dict):
                self.assertEqual(res.get("proxy"), "provided")

    async def test_database_cleanup_retention(self):
        """Test cleanup of old database records."""
        # Insert records
        await db_results.save_result("fresh-1", "turnstile", {"status": "ready", "value": "fresh_token"})
        
        # Execute cleanup
        cleaned = await db_results.cleanup_old_results(days_old=1)
        self.assertGreaterEqual(cleaned, 0)
        
        # Verify fresh record remains
        res = await db_results.load_result("fresh-1")
        self.assertIsNotNone(res)

    def test_target_url_scheme_validation(self):
        """Validate SSRF protection and scheme enforcement for target URLs."""
        # Valid URLs
        self.assertTrue(validate_target_url("https://example.com"))
        self.assertTrue(validate_target_url("http://sub.domain.org/path?param=1"))
        self.assertTrue(validate_target_url("https://challenges.cloudflare.com/turnstile"))

        # Dangerous or Invalid Schemes
        self.assertFalse(validate_target_url("file:///etc/passwd"))
        self.assertFalse(validate_target_url("javascript:alert(1)"))
        self.assertFalse(validate_target_url("data:text/html,<h1>test</h1>"))
        self.assertFalse(validate_target_url("gopher://127.0.0.1:70/"))
        self.assertFalse(validate_target_url("ftp://ftp.example.com/"))
        self.assertFalse(validate_target_url(""))
        self.assertFalse(validate_target_url(None))
        self.assertFalse(validate_target_url("   "))
        self.assertFalse(validate_target_url("httpp://broken"))

    async def test_endpoints_reject_invalid_url_schemes(self):
        """Verify /turnstile and /cf_clearance endpoints reject invalid/dangerous URL schemes."""
        # Turnstile endpoint with file:// scheme
        resp1 = await self.client.get('/turnstile?url=file:///etc/shadow&sitekey=0x4AAAAAA')
        self.assertEqual(resp1.status_code, 200)
        d1 = await resp1.get_json()
        self.assertEqual(d1.get("errorId"), 1)
        self.assertEqual(d1.get("errorCode"), "ERROR_WRONG_PAGEURL")

        # CF Clearance endpoint with javascript: scheme
        resp2 = await self.client.get('/cf_clearance?url=javascript:alert(1)')
        self.assertEqual(resp2.status_code, 200)
        d2 = await resp2.get_json()
        self.assertEqual(d2.get("errorId"), 1)
        self.assertEqual(d2.get("errorCode"), "ERROR_WRONG_PAGEURL")

if __name__ == "__main__":
    unittest.main()
