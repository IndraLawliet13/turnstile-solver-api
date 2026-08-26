import unittest
import asyncio
import os
import json
from typing import Dict, Any, cast
import db_results
from api_solver import (
    parse_proxy_config,
    redact_proxy_config,
    build_route_html,
    route_glob,
    create_app
)


class ProxyConfigTests(unittest.TestCase):
    def test_plain_host_port_defaults_to_http(self):
        self.assertEqual(
            parse_proxy_config("127.0.0.1:8080"),
            {"server": "http://127.0.0.1:8080"},
        )

    def test_plain_host_port_auth_defaults_to_http(self):
        self.assertEqual(
            parse_proxy_config("127.0.0.1:8080:user:pass"),
            {
                "server": "http://127.0.0.1:8080",
                "username": "user",
                "password": "pass",
            },
        )

    def test_scheme_url_without_auth(self):
        self.assertEqual(
            parse_proxy_config("socks5://127.0.0.1:9050"),
            {"server": "socks5://127.0.0.1:9050"},
        )

    def test_scheme_url_with_auth(self):
        self.assertEqual(
            parse_proxy_config("http://user:pass@127.0.0.1:8080"),
            {
                "server": "http://127.0.0.1:8080",
                "username": "user",
                "password": "pass",
            },
        )

    def test_backward_compatible_scheme_colon_format(self):
        self.assertEqual(
            parse_proxy_config("http:127.0.0.1:8080:user:pass"),
            {
                "server": "http://127.0.0.1:8080",
                "username": "user",
                "password": "pass",
            },
        )

    def test_redaction_hides_auth_secret(self):
        self.assertEqual(
            redact_proxy_config({
                "server": "http://127.0.0.1:8080",
                "username": "user",
                "password": "pass",
            }),
            "http://127.0.0.1:8080 (auth: u***r:***)",
        )

    def test_invalid_proxy_fails_cleanly(self):
        with self.assertRaises(ValueError):
            parse_proxy_config("bad")


class RouteInterceptionHelperTests(unittest.TestCase):
    def test_build_route_html_basic(self):
        html = build_route_html(sitekey="0x4AAAAAAAJ5XXXXXXXXX")
        self.assertIn('data-sitekey="0x4AAAAAAAJ5XXXXXXXXX"', html)
        self.assertIn('challenges.cloudflare.com/turnstile/v0/api.js', html)
        self.assertIn('onTurnstileSuccess', html)

    def test_build_route_html_with_action_and_cdata(self):
        html = build_route_html(sitekey="0x4AAAAAAAJ5XXXXXXXXX", action="login", cdata="session123")
        self.assertIn('data-sitekey="0x4AAAAAAAJ5XXXXXXXXX"', html)
        self.assertIn('data-action="login"', html)
        self.assertIn('data-cdata="session123"', html)

    def test_route_glob(self):
        glob1 = route_glob("https://example.com/login?param=1")
        self.assertIn("login", glob1)

        glob2 = route_glob("https://sub.domain.org/path/to/page/")
        self.assertIn("path/to/page", glob2)


class DatabaseResultsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db_results.DB_PATH = "test_results.db"
        if os.path.exists("test_results.db"):
            os.remove("test_results.db")
        await db_results.init_db()

    async def asyncTearDown(self):
        if os.path.exists("test_results.db"):
            os.remove("test_results.db")

    async def test_save_and_load_turnstile_result(self):
        task_id = "test-task-123"
        payload = {"value": "0.test_token_xyz", "elapsed_time": 2.45}
        await db_results.save_result(task_id, "turnstile", payload)

        res = await db_results.load_result(task_id)
        self.assertIsNotNone(res)
        self.assertTrue(isinstance(res, dict))
        if isinstance(res, dict):
            self.assertEqual(res.get("value"), "0.test_token_xyz")
            self.assertEqual(res.get("type"), "turnstile")

        res_tuple = await db_results.load_result_with_type(task_id)
        self.assertIsNotNone(res_tuple)
        if res_tuple:
            task_type, data = res_tuple
            self.assertEqual(task_type, "turnstile")
            self.assertTrue(isinstance(data, dict))
            if isinstance(data, dict):
                self.assertEqual(data.get("value"), "0.test_token_xyz")

    async def test_save_and_load_cf_clearance_result(self):
        task_id = "test-cf-clearance-456"
        payload = {
            "status": "ready",
            "cf_clearance": "v1.clearance_sample_cookie_val",
            "cookies": [{"name": "cf_clearance", "value": "v1.clearance_sample_cookie_val"}],
            "user_agent": "Mozilla/5.0 TestUA",
            "headers": {"User-Agent": "Mozilla/5.0 TestUA", "Accept-Language": "en-US,en;q=0.9"},
            "elapsed_time": 3.8
        }
        await db_results.save_result(task_id, "cf_clearance", payload)

        res_tuple = await db_results.load_result_with_type(task_id)
        self.assertIsNotNone(res_tuple)
        if res_tuple:
            task_type, data = res_tuple
            self.assertEqual(task_type, "cf_clearance")
            self.assertTrue(isinstance(data, dict))
            if isinstance(data, dict):
                self.assertEqual(data.get("cf_clearance"), "v1.clearance_sample_cookie_val")
                self.assertEqual(data.get("status"), "ready")

    async def test_get_pending_and_cleanup(self):
        await db_results.save_result("pending-1", "turnstile", {"status": "CAPTCHA_NOT_READY"})
        pending_count = await db_results.get_pending_count()
        self.assertEqual(pending_count, 1)

        await db_results.delete_result("pending-1")
        pending_after = await db_results.get_pending_count()
        self.assertEqual(pending_after, 0)


class EndpointRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db_results.DB_PATH = "test_endpoint_results.db"
        if os.path.exists("test_endpoint_results.db"):
            os.remove("test_endpoint_results.db")
        await db_results.init_db()

        self.app = create_app(
            headless=True,
            useragent="",
            debug=False,
            browser_type="chromium",
            thread=1,
            proxy_support=False,
            use_random_config=False,
            browser_name="",
            browser_version=""
        )
        self.client = self.app.test_client()

    async def asyncTearDown(self):
        if os.path.exists("test_endpoint_results.db"):
            os.remove("test_endpoint_results.db")

    async def test_index_landing_page(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        data = await response.get_data(as_text=True)
        self.assertIn("Turnstile", data)
        self.assertIn("/turnstile", data)

    async def test_turnstile_missing_params(self):
        response = await self.client.get("/turnstile")
        self.assertEqual(response.status_code, 200)
        json_data = await response.get_json()
        self.assertEqual(json_data.get("errorId"), 1)
        self.assertEqual(json_data.get("errorCode"), "ERROR_WRONG_PAGEURL")

    async def test_cf_clearance_missing_params(self):
        response = await self.client.get("/cf_clearance")
        self.assertEqual(response.status_code, 200)
        json_data = await response.get_json()
        self.assertEqual(json_data.get("errorId"), 1)
        self.assertEqual(json_data.get("errorCode"), "ERROR_WRONG_PAGEURL")

    async def test_result_missing_id(self):
        response = await self.client.get("/result")
        self.assertEqual(response.status_code, 200)
        json_data = await response.get_json()
        self.assertEqual(json_data.get("errorId"), 1)
        self.assertEqual(json_data.get("errorCode"), "ERROR_WRONG_CAPTCHA_ID")

    async def test_result_cf_clearance_ready(self):
        task_id = "test-cf-ready-1"
        payload = {
            "status": "ready",
            "cf_clearance": "v1.mock_clearance_token",
            "cookies": [{"name": "cf_clearance", "value": "v1.mock_clearance_token"}],
            "user_agent": "Mozilla/5.0 TestBrowser",
            "headers": {"User-Agent": "Mozilla/5.0 TestBrowser"},
            "elapsed_time": 2.1
        }
        await db_results.save_result(task_id, "cf_clearance", payload)

        response = await self.client.get(f"/result?id={task_id}")
        self.assertEqual(response.status_code, 200)
        json_data = await response.get_json()
        self.assertEqual(json_data.get("errorId"), 0)
        self.assertEqual(json_data.get("status"), "ready")
        solution = json_data.get("solution", {})
        self.assertEqual(solution.get("cf_clearance"), "v1.mock_clearance_token")
        self.assertEqual(solution.get("user_agent"), "Mozilla/5.0 TestBrowser")

    async def test_result_turnstile_ready(self):
        task_id = "test-turnstile-ready-1"
        payload = {
            "value": "0.mock_turnstile_token_12345",
            "elapsed_time": 1.8
        }
        await db_results.save_result(task_id, "turnstile", payload)

        response = await self.client.get(f"/result?id={task_id}")
        self.assertEqual(response.status_code, 200)
        json_data = await response.get_json()
        self.assertEqual(json_data.get("errorId"), 0)
        self.assertEqual(json_data.get("status"), "ready")
        solution = json_data.get("solution", {})
        self.assertEqual(solution.get("token"), "0.mock_turnstile_token_12345")


if __name__ == "__main__":
    unittest.main()
