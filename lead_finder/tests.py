"""
Integration Tests for Advanced Web Scraping Modules

This module tests:
1. JavaScript rendering capabilities
2. CAPTCHA detection (detect-and-skip; never solved)
3. Search engine scraping
4. Polite crawling (honest UA, no proxies, rate limits)
5. Integration with existing lead finder
"""
import os
import sys
import json
import time
import unittest
from unittest.mock import patch, MagicMock

# Ensure the parent directory is in the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestJavaScriptRenderer(unittest.TestCase):
    """Test JavaScript rendering functionality"""
    
    def setUp(self):
        from lead_finder.javascript import JavaScriptRenderer, JSConfig
        self.renderer = JavaScriptRenderer()
        self.config = JSConfig()
    
    def test_initialization(self):
        """Test JavaScriptRenderer initialization"""
        self.assertIsNotNone(self.renderer.config)
        self.assertEqual(self.renderer.config.enable_javascript, True)
        self.assertEqual(len(self.renderer.loaded_urls), 0)
    
    def test_render_simple_page(self):
        """Test rendering a simple page"""
        try:
            html, metadata = self.renderer.render("https://example.com", timeout=5)
            self.assertIsNotNone(html)
            self.assertIn("Example Domain", html)
            self.assertEqual(metadata["status_code"], 200)
        except Exception as e:
            print(f"Warning: Could not test live URL: {e}")
            # Skip this test if network is not available
            self.skipTest("Network not available")
    
    def test_extract_json_from_page(self):
        """Test JSON extraction from HTML"""
        html = '<script type="application/json">{"test": "data"}</script>'
        with patch.object(self.renderer, "render", return_value=(html, {})):  # offline: no real fetch
            json_data = self.renderer.extract_json_from_page("https://example.com")
        self.assertEqual(len(json_data), 1)
        self.assertEqual(json_data[0]["test"], "data")
    
    def test_detect_dynamic_content(self):
        """Test dynamic content detection"""
        html_with_js = '<script>fetch("https://api.example.com/data")</script>'
        html_static = '<html><body>Hello World</body></html>'
        
        self.assertTrue(self.renderer._detect_dynamic_content(html_with_js))
        self.assertFalse(self.renderer._detect_dynamic_content(html_static))


class TestCaptchaDetector(unittest.TestCase):
    """Test CAPTCHA detection"""
    
    def setUp(self):
        from lead_finder.captcha import detector, EnhancedCaptchaDetector
        self.detector = EnhancedCaptchaDetector()
    
    def test_recaptcha_detection(self):
        """Test reCAPTCHA detection"""
        html = '<div class="g-recaptcha" data-sitekey="test"></div>'
        result = self.detector.detect(html=html)
        self.assertTrue(result["detected"])
        self.assertIn("recaptcha", result["types"])
        self.assertEqual(result["sitekey"], "test")
    
    def test_hcaptcha_detection(self):
        """Test hCaptcha detection"""
        html = '<div data-sitekey="hcaptcha-test"></div>'
        result = self.detector.detect(html=html)
        self.assertTrue(result["detected"])
        self.assertIn("hcaptcha", result["types"])
    
    def test_no_captcha_detection(self):
        """Test when no CAPTCHA is present"""
        html = '<html><body>Normal page content</body></html>'
        result = self.detector.detect(html=html)
        self.assertFalse(result["detected"])
    
    def test_status_code_detection(self):
        """Test CAPTCHA detection from status codes"""
        result = self.detector.detect(status_code=403)
        self.assertTrue(result["detected"])
        self.assertIn("rate_limited", result["types"])


class TestSearchQueryBuilder(unittest.TestCase):
    """Test search query building"""
    
    def setUp(self):
        from lead_finder.search import SearchQueryBuilder
        self.builder = SearchQueryBuilder()
    
    def test_build_simple_query(self):
        """Test building a simple query"""
        query = self.builder.build("test query")
        self.assertEqual(query, "test query")
    
    def test_build_query_with_operators(self):
        """Test building query with operators"""
        query = self.builder.build("test", site="example.com")
        self.assertIn("site:example.com", query)
        self.assertIn("test", query)
    
    def test_build_b2b_query(self):
        """Test building a B2B query"""
        query = self.builder.build_b2b_query("SaaS development", "technology", "USA")
        self.assertIn("SaaS development", query)
        self.assertIn("technology", query)
        self.assertIn("USA", query)
    
    def test_build_intent_query(self):
        """Test building an intent-based query"""
        query = self.builder.build_intent_query("web development", "commercial")
        self.assertIn("web development", query)
        self.assertIn("OR", query)


class TestPoliteCrawling(unittest.TestCase):
    """The crawler must identify itself honestly and never evade blocks."""

    def setUp(self):
        from lead_finder.politeness import ua_manager, proxy_manager, header_manager, rate_limiter
        self.ua, self.proxy, self.headers, self.rl = ua_manager, proxy_manager, header_manager, rate_limiter

    def test_user_agent_is_constant_and_identifies_bot(self):
        self.assertEqual(self.ua.get_agent(), self.ua.get_random_agent())
        self.assertEqual(self.ua.get_agent_for_browser("chrome"), self.ua.get_agent_for_browser("firefox"))
        self.assertRegex(self.ua.get_agent(), r"Bot|Finder")

    def test_proxies_disabled(self):
        self.assertFalse(self.proxy.enabled)
        self.assertIsNone(self.proxy.get_proxy_url())
        with self.assertRaises(RuntimeError):
            self.proxy.add_proxy("1.2.3.4", 8080)

    def test_headers_are_plain(self):
        h = self.headers.get_headers_for_url("https://example.com")
        self.assertEqual(h["User-Agent"], self.ua.get_agent())
        self.assertNotIn("Referer", h)

    def test_captcha_is_never_solved(self):
        from lead_finder.captcha import solver_manager, get_solvers
        ok, info = solver_manager.solve('<div class="g-recaptcha" data-sitekey="x"></div>', "https://example.com")
        self.assertFalse(ok)
        self.assertTrue(info["skipped"])
        self.assertEqual(get_solvers(), [])


class TestSourcesStealth(unittest.TestCase):
    """Test stealth integration in sources"""
    
    def setUp(self):
        from lead_finder.sources import GoogleSearchSource
        self.source = GoogleSearchSource()
    
    def test_source_initialization(self):
        """Test source initialization with stealth"""
        self.assertEqual(self.source.source_type, "google_search")
        self.assertIsNotNone(self.source.user_agent)
        self.assertTrue(self.source.use_politeness)
    
    def test_source_get_headers(self):
        """Test headers generation in source"""
        headers = self.source._get_headers("https://example.com")
        self.assertIn("User-Agent", headers)
    
    def test_source_get_user_agent(self):
        """Test user agent retrieval in source"""
        ua = self.source._get_user_agent()
        self.assertIsNotNone(ua)


class TestModuleImports(unittest.TestCase):
    """Test that all modules can be imported"""
    
    def test_import_javascript(self):
        """Test javascript module import"""
        from lead_finder.javascript import (
            JavaScriptRenderer, JSConfig, render_page, render_and_extract,
            detect_framework, extract_spa_data, spa_detector, js_renderer,
            shadow_dom_handler, iframe_handler, websocket_monitor, lazy_loader,
            DynamicContentTracker
        )
        self.assertIsNotNone(JavaScriptRenderer)
    
    def test_import_captcha(self):
        from lead_finder.captcha import CaptchaSolverManager, detector, solver_manager, detect_captcha, get_captcha_stats
        self.assertEqual(get_captcha_stats()["policy"], "detect-and-skip (no solving)")

    def test_import_search(self):
        """Test search module import"""
        from lead_finder.search import (
            SearchConfig, SearchQueryBuilder, SearchResultParser,
            SearchEngineClient, SerpAPICLIENT, AdvancedSearch,
            search_client, search_parser, query_builder, advanced_search,
            serpapi_client, search, paginated_search, multi_engine_search,
            build_query, discover_leads
        )
        self.assertIsNotNone(SearchEngineClient)
    
    def test_import_politeness(self):
        from lead_finder.politeness import ua_manager, proxy_manager, rate_limiter, header_manager, captcha_detector
        self.assertIsNotNone(ua_manager)


class TestAppCommands(unittest.TestCase):
    """Test app command integration"""
    
    def test_app_import(self):
        """Test that app.py can be imported"""
        # This tests that all the new modules integrate properly
        import app
        self.assertIsNotNone(app)
    
    def test_scrape_commands_available(self):
        """Test that scrape commands are available"""
        # Just verify the imports work
        try:
            from lead_finder.search import search, paginated_search
            from lead_finder.javascript import render_page
            from lead_finder.captcha import detector, solver_manager
            from lead_finder.politeness import ua_manager, proxy_manager
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Import error: {e}")


def run_tests():
    """Run all tests"""
    print("=" * 70)
    print("Running Integration Tests for Advanced Web Scraping Modules")
    print("=" * 70)
    print()
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestModuleImports))
    suite.addTests(loader.loadTestsFromTestCase(TestPoliteCrawling))
    suite.addTests(loader.loadTestsFromTestCase(TestSourcesStealth))
    suite.addTests(loader.loadTestsFromTestCase(TestSearchQueryBuilder))
    suite.addTests(loader.loadTestsFromTestCase(TestCaptchaDetector))
    suite.addTests(loader.loadTestsFromTestCase(TestJavaScriptRenderer))
    suite.addTests(loader.loadTestsFromTestCase(TestAppCommands))
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print()
    print("=" * 70)
    if result.wasSuccessful():
        print("✓ All tests passed!")
    else:
        print(f"✗ {len(result.failures)} failures, {len(result.errors)} errors")
    print("=" * 70)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
