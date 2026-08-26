import os
import sys
import time
import uuid
import random
import logging
import asyncio
import html
import json
from typing import Optional, Union, Dict, Any, List, cast
import argparse
from urllib.parse import urlparse, unquote
from quart import Quart, request, jsonify
from camoufox.async_api import AsyncCamoufox
from patchright.async_api import async_playwright
from db_results import init_db, save_result, load_result, load_result_with_type, cleanup_old_results
from browser_configs import browser_config
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich import box

# Ensure shared Playwright browser binaries directory is discovered in all environments
if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
    for default_path in ["/root/.cache/ms-playwright", os.path.expanduser("~/.cache/ms-playwright")]:
        if os.path.isdir(default_path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = default_path
            break

COLORS = {
    'MAGENTA': '\033[35m',
    'BLUE': '\033[34m',
    'GREEN': '\033[32m',
    'YELLOW': '\033[33m',
    'RED': '\033[31m',
    'RESET': '\033[0m',
}


class CustomLogger(logging.Logger):
    @staticmethod
    def format_message(level, color, message):
        timestamp = time.strftime('%H:%M:%S')
        return f"[{timestamp}] [{COLORS.get(color)}{level}{COLORS.get('RESET')}] -> {message}"

    def debug(self, message, *args, **kwargs):
        super().debug(self.format_message('DEBUG', 'MAGENTA', message), *args, **kwargs)

    def info(self, message, *args, **kwargs):
        super().info(self.format_message('INFO', 'BLUE', message), *args, **kwargs)

    def success(self, message, *args, **kwargs):
        super().info(self.format_message('SUCCESS', 'GREEN', message), *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        super().warning(self.format_message('WARNING', 'YELLOW', message), *args, **kwargs)

    def error(self, message, *args, **kwargs):
        super().error(self.format_message('ERROR', 'RED', message), *args, **kwargs)


logging.setLoggerClass(CustomLogger)
logger: CustomLogger = cast(CustomLogger, logging.getLogger("TurnstileAPIServer"))
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)


def _mask_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if len(value) <= 2:
        return "**"
    return f"{value[:1]}***{value[-1:]}"


def parse_proxy_config(proxy: str) -> dict:
    """Convert supported proxy formats into Patchright/Playwright context config.

    Supported formats:
    - ip:port
    - ip:port:username:password
    - scheme://ip:port
    - scheme://username:password@ip:port
    - scheme:ip:port:username:password, kept for backward compatibility
    """
    raw_proxy = (proxy or "").strip()
    if not raw_proxy:
        raise ValueError("Invalid proxy format")

    if "://" in raw_proxy:
        parsed = urlparse(raw_proxy)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            raise ValueError("Invalid proxy format")

        config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username is not None:
            config["username"] = unquote(parsed.username)
        if parsed.password is not None:
            config["password"] = unquote(parsed.password)
        return config

    parts = raw_proxy.split(":")
    if len(parts) == 2:
        host, port = parts
        if not host or not port:
            raise ValueError("Invalid proxy format")
        return {"server": f"http://{host}:{port}"}

    if len(parts) == 4:
        host, port, username, password = parts
        if not host or not port or not username:
            raise ValueError("Invalid proxy format")
        return {
            "server": f"http://{host}:{port}",
            "username": username,
            "password": password,
        }

    if len(parts) == 5:
        scheme, host, port, username, password = parts
        if not scheme or not host or not port or not username:
            raise ValueError("Invalid proxy format")
        return {
            "server": f"{scheme}://{host}:{port}",
            "username": username,
            "password": password,
        }

    raise ValueError("Invalid proxy format")


def redact_proxy_config(proxy_config: Optional[dict]) -> str:
    if not proxy_config:
        return "none"
    username = proxy_config.get("username")
    if username:
        return f"{proxy_config.get('server')} (auth: {_mask_secret(username)}:***)"
    return str(proxy_config.get("server"))


def validate_target_url(url: Optional[str]) -> bool:
    """Validate that the target URL uses http or https scheme to prevent SSRF and protocol misuse."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ('http', 'https') and bool(parsed.netloc)
    except Exception:
        return False


def build_route_html(sitekey: str, action: Optional[str] = None, cdata: Optional[str] = None) -> str:
    """Generate lightweight synthetic HTML stub with explicit Turnstile widget and callback."""
    action_attr = f' data-action="{html.escape(action)}"' if action else ''
    cdata_attr = f' data-cdata="{html.escape(cdata)}"' if cdata else ''
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Turnstile Verification</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>
</head>
<body style="margin:0; padding:10px; background:#fff;">
    <div class="cf-turnstile" data-sitekey="{html.escape(sitekey)}"{action_attr}{cdata_attr} data-callback="onTurnstileSuccess"></div>
    <script>
        function onTurnstileSuccess(token) {{
            window.__turnstile_token = token;
            let el = document.getElementById('cf-turnstile-response');
            if (!el) {{
                el = document.createElement('input');
                el.type = 'hidden';
                el.name = 'cf-turnstile-response';
                el.id = 'cf-turnstile-response';
                document.body.appendChild(el);
            }}
            el.value = token;
        }}
    </script>
</body>
</html>"""


def route_glob(url: str) -> str:
    """Generate route glob pattern for intercepting the target URL."""
    try:
        parsed = urlparse(url)
        path = parsed.path if parsed.path and parsed.path != "/" else "/*"
        return f"*{path}*"
    except Exception:
        return "**/turnstile-synthetic-intercept*"


class TurnstileAPIServer:

    def __init__(self, headless: bool, useragent: Optional[str], debug: bool, browser_type: str, thread: int, proxy_support: bool, use_random_config: bool = False, browser_name: Optional[str] = None, browser_version: Optional[str] = None):
        self.app = Quart(__name__)
        self.debug = debug
        self.browser_type = browser_type
        self.headless = headless
        self.thread_count = thread
        self.proxy_support = proxy_support
        self.browser_pool = asyncio.Queue()
        self.use_random_config = use_random_config
        self.browser_name = browser_name
        self.browser_version = browser_version
        self.console = Console()
        self.login_address = os.getenv("TURNSTILE_LOGIN_ADDRESS", "").strip()
        
        # Initialize useragent and sec_ch_ua attributes
        self.useragent = useragent
        self.sec_ch_ua = None
        
        if self.browser_type in ['chromium', 'chrome', 'msedge']:
            if browser_name and browser_version:
                config = browser_config.get_browser_config(browser_name, browser_version)
                if config:
                    useragent, sec_ch_ua = config
                    self.useragent = useragent
                    self.sec_ch_ua = sec_ch_ua
            elif useragent:
                self.useragent = useragent
            else:
                browser, version, useragent, sec_ch_ua = browser_config.get_random_browser_config(self.browser_type)
                self.browser_name = browser
                self.browser_version = version
                self.useragent = useragent
                self.sec_ch_ua = sec_ch_ua
        
        self.browser_args = []
        if self.useragent:
            self.browser_args.append(f"--user-agent={self.useragent}")

        self._setup_routes()

    def display_welcome(self):
        """Displays welcome screen with logo."""
        self.console.clear()
        
        combined_text = Text()
        combined_text.append("\nHigh-throughput Turnstile & Cloudflare Solver API", style="bold white")
        combined_text.append("\nEndpoints: /turnstile | /cf_clearance | /result | /docs", style="green")
        combined_text.append("\nFeatures: Fast Route-Intercept, Physical Mouse Clicks, IUAM Clearances", style="yellow")
        combined_text.append("\nRuntime: Quart + Patchright/Camoufox", style="cyan")
        combined_text.append("\nStorage: SQLite (WAL mode)", style="cyan")
        combined_text.append("\n")

        info_panel = Panel(
            Align.left(combined_text),
            title="[bold blue]Turnstile Solver API[/bold blue]",
            subtitle="[bold magenta]Production Build[/bold magenta]",
            box=box.ROUNDED,
            border_style="bright_blue",
            padding=(0, 1),
            width=60
        )

        self.console.print(info_panel)
        self.console.print()

    def _setup_routes(self) -> None:
        """Set up the application routes."""
        self.app.before_serving(self._startup)
        self.app.route('/turnstile', methods=['GET'])(self.process_turnstile)
        self.app.route('/cf_clearance', methods=['GET'])(self.process_cf_clearance)
        self.app.route('/result', methods=['GET'])(self.get_result)
        self.app.route('/')(self.index)
        self.app.route('/docs')(self.index)
        self.app.route('/docs/')(self.index)

    async def _startup(self) -> None:
        """Initialize the browser and page pool on startup."""
        self.display_welcome()
        logger.info("Starting browser initialization")
        try:
            await init_db()
            await self._initialize_browser()
            
            # Periodic cleanup of old results
            asyncio.create_task(self._periodic_cleanup())
            
        except Exception as e:
            logger.error(f"Failed to initialize browser: {str(e)}")
            raise

    async def _initialize_browser(self) -> None:
        """Initialize the browser and create the page pool."""
        playwright = None
        camoufox = None

        if self.browser_type in ['chromium', 'chrome', 'msedge']:
            playwright = await async_playwright().start()
        elif self.browser_type == "camoufox":
            camoufox = AsyncCamoufox(headless=self.headless)

        browser_configs = []
        for _ in range(self.thread_count):
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                if self.use_random_config:
                    browser, version, useragent, sec_ch_ua = browser_config.get_random_browser_config(self.browser_type)
                elif self.browser_name and self.browser_version:
                    config = browser_config.get_browser_config(self.browser_name, self.browser_version)
                    if config:
                        useragent, sec_ch_ua = config
                        browser = self.browser_name
                        version = self.browser_version
                    else:
                        browser, version, useragent, sec_ch_ua = browser_config.get_random_browser_config(self.browser_type)
                else:
                    browser = getattr(self, 'browser_name', 'custom')
                    version = getattr(self, 'browser_version', 'custom')
                    useragent = self.useragent
                    sec_ch_ua = getattr(self, 'sec_ch_ua', '')
            else:
                # Camoufox defaults
                browser = self.browser_type
                version = 'custom'
                useragent = self.useragent
                sec_ch_ua = getattr(self, 'sec_ch_ua', '')

            browser_configs.append({
                'browser_name': browser,
                'browser_version': version,
                'useragent': useragent,
                'sec_ch_ua': sec_ch_ua
            })

        for i in range(self.thread_count):
            config = browser_configs[i]
            
            browser_args = [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
            if config['useragent']:
                browser_args.append(f"--user-agent={config['useragent']}")
            
            browser = None
            if self.browser_type in ['chromium', 'chrome', 'msedge'] and playwright:
                launch_kwargs = {
                    "headless": self.headless,
                    "args": browser_args
                }
                if self.browser_type != 'chromium':
                    launch_kwargs["channel"] = self.browser_type
                browser = await playwright.chromium.launch(**launch_kwargs)
            elif self.browser_type == "camoufox" and camoufox:
                browser = await camoufox.start()

            if browser:
                await self.browser_pool.put((i+1, browser, config))

            if self.debug:
                logger.info(f"Browser {i + 1} initialized successfully with {config['browser_name']} {config['browser_version']}")

        logger.info(f"Browser pool initialized with {self.browser_pool.qsize()} browsers")
        
        if self.use_random_config:
            logger.info("Each browser in pool received random configuration")
        elif self.browser_name and self.browser_version:
            logger.info(f"All browsers using configuration: {self.browser_name} {self.browser_version}")
        else:
            logger.info("Using custom configuration")
            
        if self.debug:
            for i, config in enumerate(browser_configs):
                logger.debug(f"Browser {i+1} config: {config['browser_name']} {config['browser_version']}")
                logger.debug(f"Browser {i+1} User-Agent: {config['useragent']}")
                logger.debug(f"Browser {i+1} Sec-CH-UA: {config['sec_ch_ua']}")

    async def _periodic_cleanup(self):
        """Periodic cleanup of old results every hour"""
        while True:
            try:
                await asyncio.sleep(3600)
                deleted_count = await cleanup_old_results(days_old=7)
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old results")
            except Exception as e:
                logger.error(f"Error during periodic cleanup: {e}")

    async def _antishadow_inject(self, page):
        await page.add_init_script("""
          (function() {
            const originalAttachShadow = Element.prototype.attachShadow;
            Element.prototype.attachShadow = function(init) {
              const shadow = originalAttachShadow.call(this, init);
              if (init.mode === 'closed') {
                window.__lastClosedShadowRoot = shadow;
              }
              return shadow;
            };
          })();
        """)

    async def _optimized_route_handler(self, route):
        """Optimized route handler to save resources."""
        url = route.request.url
        resource_type = route.request.resource_type

        allowed_types = {'document', 'script', 'xhr', 'fetch'}

        allowed_domains = [
            'challenges.cloudflare.com',
            'static.cloudflareinsights.com',
            'cloudflare.com'
        ]
        
        if resource_type in allowed_types:
            await route.continue_()
        elif any(domain in url for domain in allowed_domains):
            await route.continue_() 
        else:
            await route.abort()

    async def _block_rendering(self, page):
        """Resource-saving route filter"""
        try:
            await page.route("**/*", self._optimized_route_handler)
        except Exception as e:
            if self.debug:
                logger.debug(f"Block rendering route registration error: {e}")

    async def _unblock_rendering(self, page):
        """Unblock routes safely without raising exceptions if not registered"""
        try:
            await page.unroute("**/*", self._optimized_route_handler)
        except Exception:
            pass

    async def _find_turnstile_elements(self, page, index: int):
        """Check all possible Turnstile elements safely"""
        selectors = [
            '.cf-turnstile',
            '[data-sitekey]',
            'iframe[src*="turnstile"]',
            'iframe[title*="widget"]',
            'div[id*="turnstile"]',
            'div[class*="turnstile"]'
        ]
        
        elements = []
        for selector in selectors:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    elements.append((selector, count))
                    if self.debug:
                        logger.debug(f"Browser {index}: Found {count} elements with selector '{selector}'")
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Selector '{selector}' failed: {str(e)}")
                continue
        
        return elements

    async def _click_physical_bounding_box(self, page, index: int) -> bool:
        """Physical mouse click on Turnstile iframe bounding box (box.x + 30, box.y + box.h/2)."""
        try:
            iframe_selectors = [
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]',
                'iframe[title*="widget"]',
                'iframe[title*="Cloudflare"]'
            ]
            for selector in iframe_selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0:
                        box_model = await locator.bounding_box()
                        if box_model and box_model['width'] > 0 and box_model['height'] > 0:
                            click_x = box_model['x'] + 30
                            click_y = box_model['y'] + (box_model['height'] / 2)
                            await page.mouse.click(click_x, click_y)
                            if self.debug:
                                logger.debug(f"Browser {index}: Physical bounding box click executed at ({click_x}, {click_y}) for {selector}")
                            return True
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Bounding box click attempt failed for '{selector}': {str(e)}")
                    continue
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: General bounding box click error: {str(e)}")
        return False

    async def _find_and_click_checkbox(self, page, index: int):
        """Find and click Turnstile CAPTCHA checkbox inside iframe."""
        try:
            iframe_selectors = [
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]',
                'iframe[title*="widget"]'
            ]
            
            iframe_locator = None
            for selector in iframe_selectors:
                try:
                    test_locator = page.locator(selector).first
                    try:
                        iframe_count = await test_locator.count()
                    except Exception:
                        iframe_count = 0
                        
                    if iframe_count > 0:
                        iframe_locator = test_locator
                        if self.debug:
                            logger.debug(f"Browser {index}: Found Turnstile iframe with selector: {selector}")
                        break
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Iframe selector '{selector}' failed: {str(e)}")
                    continue
            
            if iframe_locator:
                try:
                    iframe_element = await iframe_locator.element_handle()
                    frame = await iframe_element.content_frame()
                    
                    if frame:
                        checkbox_selectors = [
                            'input[type="checkbox"]',
                            '.cb-lb input[type="checkbox"]',
                            'label input[type="checkbox"]',
                            '#challenge-stage input[type="checkbox"]',
                            '.ctp-checkbox-label input'
                        ]
                        
                        for selector in checkbox_selectors:
                            try:
                                checkbox = frame.locator(selector).first
                                await checkbox.click(timeout=2000)
                                if self.debug:
                                    logger.debug(f"Browser {index}: Successfully clicked checkbox in iframe with selector '{selector}'")
                                return True
                            except Exception as click_e:
                                if self.debug:
                                    logger.debug(f"Browser {index}: Direct checkbox click failed for '{selector}': {str(click_e)}")
                                continue
                    
                        # Fallback direct iframe click
                        try:
                            if self.debug:
                                logger.debug(f"Browser {index}: Trying to click iframe directly as fallback")
                            await iframe_locator.click(timeout=1000)
                            return True
                        except Exception as e:
                            if self.debug:
                                logger.debug(f"Browser {index}: Iframe direct click failed: {str(e)}")
                
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Failed to access iframe content: {str(e)}")
            
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: General iframe search failed: {str(e)}")
        
        return False

    async def _try_click_strategies(self, page, index: int):
        strategies = [
            ('physical_bbox_click', lambda: self._click_physical_bounding_box(page, index)),
            ('checkbox_click', lambda: self._find_and_click_checkbox(page, index)),
            ('direct_widget', lambda: self._safe_click(page, '.cf-turnstile', index)),
            ('iframe_click', lambda: self._safe_click(page, 'iframe[src*="turnstile"]', index)),
            ('js_click', lambda: page.evaluate("document.querySelector('.cf-turnstile')?.click()")),
            ('sitekey_attr', lambda: self._safe_click(page, '[data-sitekey]', index)),
            ('any_turnstile', lambda: self._safe_click(page, '*[class*="turnstile"]', index)),
            ('xpath_click', lambda: self._safe_click(page, "//div[@class='cf-turnstile']", index))
        ]
        
        for strategy_name, strategy_func in strategies:
            try:
                result = await strategy_func()
                if result is True or result is None:
                    if self.debug:
                        logger.debug(f"Browser {index}: Click strategy '{strategy_name}' succeeded")
                    return True
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Click strategy '{strategy_name}' failed: {str(e)}")
                continue
        
        return False

    async def _safe_click(self, page, selector: str, index: int):
        """Safe element click with timeout protection"""
        try:
            locator = page.locator(selector).first
            await locator.click(timeout=1000)
            return True
        except Exception as e:
            if self.debug and "Can't query n-th element" not in str(e):
                logger.debug(f"Browser {index}: Safe click failed for '{selector}': {str(e)}")
            return False

    async def _load_captcha_overlay(self, page, websiteKey: str, action: str = '', cdata: str = '', index: int = 0):
        # Use JSON serialization to safely pass parameters into the browser context without injection risks
        script = """
        (function(sitekey, action, cdata) {
            const existing = document.querySelector('#captcha-overlay');
            if (existing) existing.remove();

            const overlay = document.createElement('div');
            overlay.id = 'captcha-overlay';
            overlay.style.position = 'absolute';
            overlay.style.top = '0';
            overlay.style.left = '0';
            overlay.style.width = '100vw';
            overlay.style.height = '100vh';
            overlay.style.backgroundColor = 'rgba(0, 0, 0, 0.5)';
            overlay.style.display = 'block';
            overlay.style.justifyContent = 'center';
            overlay.style.alignItems = 'center';
            overlay.style.zIndex = '1000';

            const captchaDiv = document.createElement('div');
            captchaDiv.className = 'cf-turnstile';
            captchaDiv.setAttribute('data-sitekey', sitekey);
            captchaDiv.setAttribute('data-callback', 'onCaptchaSuccess');
            if (action) captchaDiv.setAttribute('data-action', action);
            if (cdata) captchaDiv.setAttribute('data-cdata', cdata);

            overlay.appendChild(captchaDiv);
            document.body.appendChild(overlay);

            const scriptEl = document.createElement('script');
            scriptEl.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
            scriptEl.async = true;
            scriptEl.defer = true;
            document.head.appendChild(scriptEl);
        })
        """

        await page.evaluate(f"({script})({json.dumps(websiteKey)}, {json.dumps(action)}, {json.dumps(cdata)})")
        if self.debug:
            logger.debug(f"Browser {index}: Created CAPTCHA overlay with sitekey: {websiteKey}")

    async def _poll_for_turnstile_token(self, page, index: int, max_time_s: float, start_time: float, sitekey: str, action: Optional[str] = None, cdata: Optional[str] = None) -> Optional[str]:
        """Loop and check for Turnstile token generated in page or hidden inputs."""
        locator = page.locator('input[name="cf-turnstile-response"]')
        elapsed_loop_start = time.time()
        attempt = 0
        
        while (time.time() - elapsed_loop_start) < max_time_s:
            attempt += 1
            try:
                # 1. Check window.__turnstile_token first (synthetic fast-path hook)
                try:
                    js_token = await page.evaluate("window.__turnstile_token || null")
                    if js_token:
                        return js_token
                except Exception:
                    pass

                # 2. Check input elements
                try:
                    count = await locator.count()
                except Exception:
                    count = 0

                if count == 1:
                    try:
                        token = await locator.input_value(timeout=300)
                        if token:
                            return token
                    except Exception:
                        pass
                elif count > 1:
                    for i in range(count):
                        try:
                            token = await locator.nth(i).input_value(timeout=300)
                            if token:
                                return token
                        except Exception:
                            continue

                # Periodic click strategies
                if attempt > 1 and attempt % 3 == 0:
                    await self._try_click_strategies(page, index)

                # Fallback overlay injection if needed
                if attempt == 8 and sitekey:
                    try:
                        if count == 0:
                            if self.debug:
                                logger.debug(f"Browser {index}: Creating overlay as fallback strategy")
                            await self._load_captcha_overlay(page, sitekey, action or '', cdata or '', index)
                    except Exception as e:
                        if self.debug:
                            logger.debug(f"Browser {index}: Fallback overlay creation error: {str(e)}")

                wait_time = min(0.3 + (attempt * 0.05), 1.0)
                await asyncio.sleep(wait_time)
            except Exception as e:
                if self.debug:
                    logger.debug(f"Browser {index}: Poll attempt {attempt} error: {str(e)}")
                await asyncio.sleep(0.5)

        return None

    async def _solve_turnstile_fastpath(self, page, index: int, url: str, sitekey: str, action: Optional[str] = None, cdata: Optional[str] = None, timeout_s: float = 6.0) -> Optional[str]:
        """Try Fast-Path Route-Interception: serve synthetic stub on the target URL domain."""
        target_glob = route_glob(url)
        handle_synthetic_route = None

        try:
            stub_html = build_route_html(sitekey, action=action, cdata=cdata)
            
            if self.debug:
                logger.debug(f"Browser {index}: Attempting fast-path route interception on {target_glob}")

            async def route_handler(route):
                try:
                    req_url = route.request.url
                    if self.debug:
                        logger.debug(f"Browser {index}: Intercepted navigation route to {req_url}")
                    await route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=stub_html
                    )
                except Exception as r_err:
                    if self.debug:
                        logger.debug(f"Browser {index}: Route fulfill error: {r_err}")
                    try:
                        await route.continue_()
                    except Exception:
                        pass

            handle_synthetic_route = route_handler
            await page.route(target_glob, handle_synthetic_route)

            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=5000)
            except Exception as nav_e:
                if self.debug:
                    logger.debug(f"Browser {index}: Fast-path navigation warning: {str(nav_e)}")

            token = await self._poll_for_turnstile_token(page, index, max_time_s=timeout_s, start_time=time.time(), sitekey=sitekey, action=action, cdata=cdata)
            return token
        except Exception as e:
            if self.debug:
                logger.debug(f"Browser {index}: Fast-path route-interception failed: {str(e)}")
            return None
        finally:
            # Always clean up route handler so fallback realpage navigation is never blocked or poisoned
            if handle_synthetic_route:
                try:
                    await page.unroute(target_glob, handle_synthetic_route)
                except Exception:
                    pass
            try:
                await page.unroute(target_glob)
            except Exception:
                pass

    async def _solve_turnstile(self, task_id: str, url: str, sitekey: str, action: Optional[str] = None, cdata: Optional[str] = None, request_proxy: Optional[str] = None):
        """Solve the Turnstile challenge with Fast-Path Route Interception and fallback to full real-page navigation."""
        proxy = None
        index, browser, b_config = await self.browser_pool.get()
        
        try:
            if hasattr(browser, 'is_connected') and not browser.is_connected():
                if self.debug:
                    logger.warning(f"Browser {index}: Browser disconnected, skipping")
                await self.browser_pool.put((index, browser, b_config))
                await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": 0})
                return
        except Exception as e:
            if self.debug:
                logger.warning(f"Browser {index}: Cannot check browser state: {str(e)}")

        start_time = time.time()
        context = None
        proxy_file_path = os.path.join(os.getcwd(), "proxies.txt")

        try:
            if request_proxy:
                proxy = request_proxy.strip()
                if self.debug:
                    logger.debug("Browser %s: Using request-level proxy override", index)
            elif self.proxy_support:
                try:
                    with open(proxy_file_path) as proxy_file:
                        proxies = [line.strip() for line in proxy_file if line.strip()]
                    proxy = random.choice(proxies) if proxies else None
                except FileNotFoundError:
                    if self.debug:
                        logger.warning(f"Proxy file not found: {proxy_file_path}")
                    proxy = None
                except Exception as e:
                    logger.error(f"Error reading proxy file: {str(e)}")
                    proxy = None

            context_options = {"user_agent": b_config['useragent']}

            if b_config.get('sec_ch_ua') and b_config['sec_ch_ua'].strip():
                context_options['extra_http_headers'] = {
                    'sec-ch-ua': b_config['sec_ch_ua']
                }

            if proxy:
                proxy_config = parse_proxy_config(proxy)
                context_options["proxy"] = proxy_config
                if self.debug:
                    logger.debug(f"Browser {index}: Creating context with proxy {redact_proxy_config(proxy_config)}")
            elif self.debug:
                logger.debug(f"Browser {index}: Creating context without proxy")

            context = await browser.new_context(**context_options)
            page = await context.new_page()
            
            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                await page.set_viewport_size({"width": 500, "height": 240})

        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": elapsed_time})
            logger.error(f"Browser {index}: Failed to create browser context: {str(e)}")
            try:
                if hasattr(browser, 'is_connected') and browser.is_connected():
                    await self.browser_pool.put((index, browser, b_config))
            except Exception as pool_error:
                if self.debug:
                    logger.warning(f"Browser {index}: Error returning browser after context failure: {str(pool_error)}")
            return

        token_found = None

        try:
            # 1. ATTEMPT FAST-PATH ROUTE INTERCEPTION (Target 2-3s)
            if self.debug:
                logger.debug(f"Browser {index}: Attempting Fast-Path Route-Interception for {url}")
            token_found = await self._solve_turnstile_fastpath(page, index, url, sitekey, action, cdata, timeout_s=5.0)

            # 2. FALLBACK TO REAL-PAGE NAVIGATION IF FAST-PATH FAILS
            if not token_found:
                if self.debug:
                    logger.info(f"Browser {index}: Fast-path inconclusive/failed, falling back to full real-page navigation: {url}")
                
                await self._block_rendering(page)
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
                await self._unblock_rendering(page)

                # Optional auto-login for sites that gate Turnstile behind an address/email step
                try:
                    login_input = page.locator('input[name="address"]')
                    if self.login_address and await login_input.count() > 0 and await login_input.is_visible():
                        if self.debug:
                            logger.debug(f"Browser {index}: Login page detected, submitting configured TURNSTILE_LOGIN_ADDRESS")
                        await login_input.fill(self.login_address)
                        await asyncio.sleep(0.5)
                        await page.locator('button[type="submit"]').click()
                        await page.wait_for_load_state('domcontentloaded')
                        await asyncio.sleep(3)
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Optional login flow info: {e}")

                # Optional helper for trigger buttons
                try:
                    buttons = [
                        '#load-turnstile-btn',
                        'button:has-text("Load Security Verification")',
                        'button:has-text("Click to verify")',
                        '.btn-primary-modern:has-text("Security")'
                    ]
                    for btn in buttons:
                        if await page.locator(btn).count() > 0:
                            if await page.locator(btn).is_visible():
                                if self.debug:
                                    logger.debug(f"Browser {index}: Verification trigger found ({btn}), clicking...")
                                await page.locator(btn).click(force=True)
                                await asyncio.sleep(3)
                                break
                except Exception as e:
                    if self.debug:
                        logger.debug(f"Browser {index}: Optional click flow info: {e}")

                # Poll on real page
                token_found = await self._poll_for_turnstile_token(page, index, max_time_s=25.0, start_time=start_time, sitekey=sitekey, action=action, cdata=cdata)

            if token_found:
                elapsed_time = round(time.time() - start_time, 3)
                logger.success(f"Browser {index}: Successfully solved captcha - {COLORS.get('MAGENTA')}{token_found[:10]}...{COLORS.get('RESET')} in {COLORS.get('GREEN')}{elapsed_time}{COLORS.get('RESET')}s")
                await save_result(task_id, "turnstile", {"value": token_found, "elapsed_time": elapsed_time})
            else:
                elapsed_time = round(time.time() - start_time, 3)
                logger.error(f"Browser {index}: Failed solving Turnstile in {COLORS.get('RED')}{elapsed_time}{COLORS.get('RESET')}s")
                await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": elapsed_time})

        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            await save_result(task_id, "turnstile", {"value": "CAPTCHA_FAIL", "elapsed_time": elapsed_time})
            if self.debug:
                logger.error(f"Browser {index}: Error solving Turnstile: {str(e)}")
        finally:
            try:
                if context:
                    await context.close()
            except Exception as e:
                if self.debug:
                    logger.warning(f"Browser {index}: Error closing context: {str(e)}")
            
            try:
                if hasattr(browser, 'is_connected') and browser.is_connected():
                    await self.browser_pool.put((index, browser, b_config))
            except Exception as e:
                if self.debug:
                    logger.warning(f"Browser {index}: Error returning browser to pool: {str(e)}")

    async def _solve_cf_clearance(self, task_id: str, url: str, request_proxy: Optional[str] = None, max_wait_s: float = 35.0):
        """Solve Cloudflare Interstitials (IUAM, Managed Challenge, JS Challenge) and extract full clearance bundle."""
        proxy = None
        index, browser, b_config = await self.browser_pool.get()
        
        try:
            if hasattr(browser, 'is_connected') and not browser.is_connected():
                if self.debug:
                    logger.warning(f"Browser {index}: Browser disconnected, skipping")
                await self.browser_pool.put((index, browser, b_config))
                await save_result(task_id, "cf_clearance", {"value": "CAPTCHA_FAIL", "error": "Browser disconnected", "elapsed_time": 0})
                return
        except Exception as e:
            if self.debug:
                logger.warning(f"Browser {index}: Cannot check browser state: {str(e)}")

        start_time = time.time()
        context = None
        proxy_file_path = os.path.join(os.getcwd(), "proxies.txt")

        try:
            if request_proxy:
                proxy = request_proxy.strip()
                if self.debug:
                    logger.debug("Browser %s: Using request-level proxy override for cf_clearance", index)
            elif self.proxy_support:
                try:
                    with open(proxy_file_path) as proxy_file:
                        proxies = [line.strip() for line in proxy_file if line.strip()]
                    proxy = random.choice(proxies) if proxies else None
                except FileNotFoundError:
                    if self.debug:
                        logger.warning(f"Proxy file not found: {proxy_file_path}")
                    proxy = None
                except Exception as e:
                    logger.error(f"Error reading proxy file: {str(e)}")
                    proxy = None

            context_options = {
                "user_agent": b_config['useragent'],
                "locale": "en-US",
                "extra_http_headers": {
                    "Accept-Language": "en-US,en;q=0.9",
                }
            }

            if b_config.get('sec_ch_ua') and b_config['sec_ch_ua'].strip():
                context_options['extra_http_headers']['sec-ch-ua'] = b_config['sec_ch_ua']

            if proxy:
                proxy_config = parse_proxy_config(proxy)
                context_options["proxy"] = proxy_config
                if self.debug:
                    logger.debug(f"Browser {index}: Creating clearance context with proxy {redact_proxy_config(proxy_config)}")
            elif self.debug:
                logger.debug(f"Browser {index}: Creating clearance context without proxy")

            context = await browser.new_context(**context_options)
            page = await context.new_page()

            if self.browser_type in ['chromium', 'chrome', 'msedge']:
                await page.set_viewport_size({"width": 1280, "height": 720})

        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            await save_result(task_id, "cf_clearance", {"value": "CAPTCHA_FAIL", "error": str(e), "elapsed_time": elapsed_time})
            logger.error(f"Browser {index}: Failed to create clearance browser context: {str(e)}")
            try:
                if hasattr(browser, 'is_connected') and browser.is_connected():
                    await self.browser_pool.put((index, browser, b_config))
            except Exception as pool_error:
                if self.debug:
                    logger.warning(f"Browser {index}: Error returning browser after context failure: {str(pool_error)}")
            return

        try:
            if self.debug:
                logger.debug(f"Browser {index}: Navigating to CF-protected URL: {url}")

            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            except Exception as nav_e:
                if self.debug:
                    logger.debug(f"Browser {index}: Initial navigation warning on CF URL: {str(nav_e)}")

            clearance_found = False
            loop_start = time.time()
            attempt = 0

            while (time.time() - loop_start) < max_wait_s:
                attempt += 1
                cookies = await context.cookies()
                cf_clearance_cookie = next((c for c in cookies if c.get('name') == 'cf_clearance'), None)

                if cf_clearance_cookie and cf_clearance_cookie.get('value'):
                    clearance_found = True
                    elapsed_time = round(time.time() - start_time, 3)
                    
                    bundle = {
                        "cf_clearance": cf_clearance_cookie.get('value'),
                        "cookies": cookies,
                        "user_agent": b_config['useragent'],
                        "headers": {
                            "User-Agent": b_config['useragent'],
                            "Accept-Language": "en-US,en;q=0.9",
                            "sec-ch-ua": b_config.get('sec_ch_ua', '')
                        },
                        "elapsed_time": elapsed_time
                    }

                    logger.success(f"Browser {index}: Solved CF Clearance - {COLORS.get('MAGENTA')}{bundle['cf_clearance'][:12]}...{COLORS.get('RESET')} in {COLORS.get('GREEN')}{elapsed_time}{COLORS.get('RESET')}s")
                    await save_result(task_id, "cf_clearance", bundle)
                    return

                # Check if Cloudflare challenge elements exist and try solving/clicking
                cf_challenge_selectors = [
                    '#challenge-form',
                    '#challenge-stage',
                    '.cf-browser-verification',
                    '#cf-challenge-running',
                    'iframe[src*="challenges.cloudflare.com"]'
                ]
                
                has_cf_challenge = False
                for sel in cf_challenge_selectors:
                    try:
                        if await page.locator(sel).count() > 0:
                            has_cf_challenge = True
                            break
                    except Exception:
                        continue

                if has_cf_challenge or attempt > 2:
                    # Attempt physical click and checkbox click strategies
                    await self._click_physical_bounding_box(page, index)
                    await self._find_and_click_checkbox(page, index)

                wait_step = min(0.5 + (attempt * 0.1), 2.0)
                await asyncio.sleep(wait_step)

            # If loop finished without clearance cookie, check if page actually cleared or failed
            cookies = await context.cookies()
            cf_clearance_cookie = next((c for c in cookies if c.get('name') == 'cf_clearance'), None)
            elapsed_time = round(time.time() - start_time, 3)

            if cf_clearance_cookie and cf_clearance_cookie.get('value'):
                bundle = {
                    "cf_clearance": cf_clearance_cookie.get('value'),
                    "cookies": cookies,
                    "user_agent": b_config['useragent'],
                    "headers": {
                        "User-Agent": b_config['useragent'],
                        "Accept-Language": "en-US,en;q=0.9",
                        "sec-ch-ua": b_config.get('sec_ch_ua', '')
                    },
                    "elapsed_time": elapsed_time
                }
                await save_result(task_id, "cf_clearance", bundle)
            else:
                logger.error(f"Browser {index}: Failed to obtain cf_clearance in {elapsed_time}s")
                await save_result(task_id, "cf_clearance", {
                    "value": "CAPTCHA_FAIL",
                    "error": "Timeout waiting for cf_clearance cookie",
                    "elapsed_time": elapsed_time
                })

        except Exception as e:
            elapsed_time = round(time.time() - start_time, 3)
            await save_result(task_id, "cf_clearance", {
                "value": "CAPTCHA_FAIL",
                "error": str(e),
                "elapsed_time": elapsed_time
            })
            if self.debug:
                logger.error(f"Browser {index}: Error in cf_clearance solver: {str(e)}")
        finally:
            try:
                if context:
                    await context.close()
            except Exception as e:
                if self.debug:
                    logger.warning(f"Browser {index}: Error closing clearance context: {str(e)}")
            
            try:
                if hasattr(browser, 'is_connected') and browser.is_connected():
                    await self.browser_pool.put((index, browser, b_config))
            except Exception as e:
                if self.debug:
                    logger.warning(f"Browser {index}: Error returning browser to pool: {str(e)}")

    async def process_turnstile(self):
        """Handle the /turnstile endpoint requests."""
        url = request.args.get('url')
        sitekey = request.args.get('sitekey')
        action = request.args.get('action')
        cdata = request.args.get('cdata')
        request_proxy = request.args.get('proxy')

        if not url or not sitekey or not validate_target_url(url):
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_WRONG_PAGEURL",
                "errorDescription": "Both 'url' and 'sitekey' are required, and 'url' must have a valid http/https scheme"
            }), 200

        task_id = str(uuid.uuid4())
        await save_result(task_id, "turnstile", {
            "status": "CAPTCHA_NOT_READY",
            "createTime": int(time.time()),
            "url": url,
            "sitekey": sitekey,
            "action": action,
            "cdata": cdata,
            "proxy": "provided" if request_proxy else None
        })

        try:
            asyncio.create_task(self._solve_turnstile(task_id=task_id, url=url, sitekey=sitekey, action=action, cdata=cdata, request_proxy=request_proxy))

            if self.debug:
                logger.debug(f"Request completed with taskid {task_id}.")
            return jsonify({
                "errorId": 0,
                "taskId": task_id
            }), 200
        except Exception as e:
            logger.error(f"Unexpected error processing request: {str(e)}")
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_UNKNOWN",
                "errorDescription": str(e)
            }), 200

    async def process_cf_clearance(self):
        """Handle the /cf_clearance endpoint requests for Cloudflare Interstitials."""
        url = request.args.get('url')
        request_proxy = request.args.get('proxy')

        if not url or not validate_target_url(url):
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_WRONG_PAGEURL",
                "errorDescription": "'url' parameter is required and must have a valid http/https scheme"
            }), 200

        task_id = str(uuid.uuid4())
        await save_result(task_id, "cf_clearance", {
            "status": "CAPTCHA_NOT_READY",
            "createTime": int(time.time()),
            "url": url,
            "proxy": "provided" if request_proxy else None
        })

        try:
            asyncio.create_task(self._solve_cf_clearance(task_id=task_id, url=url, request_proxy=request_proxy))

            if self.debug:
                logger.debug(f"CF Clearance request queued with taskid {task_id}.")
            return jsonify({
                "errorId": 0,
                "taskId": task_id
            }), 200
        except Exception as e:
            logger.error(f"Unexpected error processing cf_clearance request: {str(e)}")
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_UNKNOWN",
                "errorDescription": str(e)
            }), 200

    async def get_result(self):
        """Return solved token or clearance session bundle."""
        task_id = request.args.get('id')

        if not task_id:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_WRONG_CAPTCHA_ID",
                "errorDescription": "Invalid task ID/Request parameter"
            }), 200

        res_data = await load_result_with_type(task_id)
        if not res_data:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                "errorDescription": "Task not found"
            }), 200

        task_type, result = res_data

        if result == "CAPTCHA_NOT_READY" or (isinstance(result, dict) and result.get("status") == "CAPTCHA_NOT_READY"):
            return jsonify({"status": "processing"}), 200

        # Check for failure
        if isinstance(result, dict) and (result.get("value") == "CAPTCHA_FAIL" or result.get("status") == "CAPTCHA_FAIL"):
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                "errorDescription": result.get("error") or "Workers could not solve the Captcha"
            }), 200

        # Handle CF Clearance Result Bundle
        if task_type == "cf_clearance" or (isinstance(result, dict) and "cf_clearance" in result):
            if isinstance(result, dict) and result.get("cf_clearance"):
                return jsonify({
                    "errorId": 0,
                    "status": "ready",
                    "solution": {
                        "cf_clearance": result["cf_clearance"],
                        "cookies": result.get("cookies", []),
                        "user_agent": result.get("user_agent", ""),
                        "headers": result.get("headers", {}),
                        "elapsed_time": result.get("elapsed_time", 0)
                    }
                }), 200

        # Handle Turnstile Token Solution
        if isinstance(result, dict) and result.get("value") and result.get("value") != "CAPTCHA_FAIL":
            return jsonify({
                "errorId": 0,
                "status": "ready",
                "solution": {
                    "token": result["value"],
                    "elapsed_time": result.get("elapsed_time", 0)
                }
            }), 200
        else:
            return jsonify({
                "errorId": 1,
                "errorCode": "ERROR_CAPTCHA_UNSOLVABLE",
                "errorDescription": "Workers could not solve the Captcha"
            }), 200

    @staticmethod
    async def index():
        """Serve the API documentation page."""
        return """
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Turnstile Solver API</title>
                <script src="https://cdn.tailwindcss.com"></script>
            </head>
            <body class="bg-gray-900 text-gray-200 min-h-screen flex items-center justify-center p-6">
                <div class="bg-gray-800 p-8 rounded-lg shadow-xl max-w-3xl w-full border border-blue-500">
                    <h1 class="text-3xl font-bold mb-2 text-center text-blue-400">Turnstile & Cloudflare Solver API</h1>
                    <p class="text-center text-gray-400 mb-6 text-sm">High-throughput automated solver for Cloudflare Turnstile & Interstitial Challenges</p>

                    <div class="space-y-6">
                        <!-- Turnstile Section -->
                        <div class="bg-gray-700/60 p-5 rounded-lg border border-gray-600">
                            <h2 class="text-xl font-semibold text-blue-300 mb-2 flex items-center gap-2">
                                <span class="bg-blue-600 text-white text-xs px-2 py-0.5 rounded">GET</span> /turnstile
                            </h2>
                            <p class="text-sm text-gray-300 mb-3">Solve Cloudflare Turnstile widgets using fast-path route-interception or full-page execution.</p>
                            <div class="bg-gray-900 p-3 rounded text-xs font-mono text-gray-300 break-all border border-gray-700">
                                /turnstile?url=https://example.com&sitekey=0x4AAAAAAA...&action=login&proxy=socks5://127.0.0.1:9050
                            </div>
                        </div>

                        <!-- CF Clearance Section -->
                        <div class="bg-gray-700/60 p-5 rounded-lg border border-gray-600">
                            <h2 class="text-xl font-semibold text-green-300 mb-2 flex items-center gap-2">
                                <span class="bg-green-600 text-white text-xs px-2 py-0.5 rounded">GET</span> /cf_clearance
                            </h2>
                            <p class="text-sm text-gray-300 mb-3">Bypass Cloudflare IUAM, JS Challenge, and Managed Challenges. Extracts clearance cookies and browser fingerprint headers.</p>
                            <div class="bg-gray-900 p-3 rounded text-xs font-mono text-gray-300 break-all border border-gray-700">
                                /cf_clearance?url=https://protected-site.com&proxy=http://user:pass@ip:port
                            </div>
                        </div>

                        <!-- Result Section -->
                        <div class="bg-gray-700/60 p-5 rounded-lg border border-gray-600">
                            <h2 class="text-xl font-semibold text-yellow-300 mb-2 flex items-center gap-2">
                                <span class="bg-yellow-600 text-white text-xs px-2 py-0.5 rounded">GET</span> /result
                            </h2>
                            <p class="text-sm text-gray-300 mb-3">Poll the solved token or clearance session bundle by taskId.</p>
                            <div class="bg-gray-900 p-3 rounded text-xs font-mono text-gray-300 break-all border border-gray-700">
                                /result?id=&lt;taskId&gt;
                            </div>
                        </div>
                    </div>

                    <div class="mt-6 pt-4 border-t border-gray-700 text-center text-xs text-gray-500">
                        Zero Cold-Start Worker Pool • SQLite WAL Mode • Physical Mouse Coordinate Traversal
                    </div>
                </div>
            </body>
            </html>
        """


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Turnstile API Server")

    parser.add_argument('--no-headless', action='store_true', help='Run the browser with GUI (disable headless mode). By default, headless mode is enabled.')
    parser.add_argument('--useragent', type=str, help='User-Agent string (if not specified, random configuration is used)')
    parser.add_argument('--debug', action='store_true', help='Enable or disable debug mode for additional logging and troubleshooting information (default: False)')
    parser.add_argument('--browser_type', type=str, default='chromium', help='Specify the browser type for the solver. Supported options: chromium, chrome, msedge, camoufox (default: chromium)')
    parser.add_argument('--thread', type=int, default=2, help='Set the number of browser threads to use for multi-threaded mode. (default: 2)')
    parser.add_argument('--proxy', action='store_true', help='Enable proxy support for the solver (Default: False)')
    parser.add_argument('--random', action='store_true', help='Use random User-Agent and Sec-CH-UA configuration from pool')
    parser.add_argument('--browser', type=str, help='Specify browser name to use (e.g., chrome, firefox)')
    parser.add_argument('--version', type=str, help='Specify browser version to use (e.g., 139, 141)')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='Specify the IP address where the API solver runs. (Default: 0.0.0.0)')
    parser.add_argument('--port', type=str, default='5072', help='Set the port for the API solver to listen on. (Default: 5072)')
    return parser.parse_args()


def create_app(headless: bool, useragent: str, debug: bool, browser_type: str, thread: int, proxy_support: bool, use_random_config: bool, browser_name: str, browser_version: str) -> Quart:
    server = TurnstileAPIServer(headless=headless, useragent=useragent, debug=debug, browser_type=browser_type, thread=thread, proxy_support=proxy_support, use_random_config=use_random_config, browser_name=browser_name, browser_version=browser_version)
    return server.app


if __name__ == '__main__':
    args = parse_args()
    browser_types = [
        'chromium',
        'chrome',
        'msedge',
        'camoufox',
    ]
    if args.browser_type not in browser_types:
        logger.error(f"Unknown browser type: {COLORS.get('RED')}{args.browser_type}{COLORS.get('RESET')} Available browser types: {browser_types}")
    else:
        app = create_app(
            headless=not args.no_headless, 
            debug=args.debug, 
            useragent=args.useragent, 
            browser_type=args.browser_type, 
            thread=args.thread, 
            proxy_support=args.proxy,
            use_random_config=args.random,
            browser_name=args.browser,
            browser_version=args.version
        )
        app.run(host=args.host, port=int(args.port))
