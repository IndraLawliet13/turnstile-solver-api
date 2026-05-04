import unittest

from api_solver import parse_proxy_config, redact_proxy_config


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


if __name__ == "__main__":
    unittest.main()
