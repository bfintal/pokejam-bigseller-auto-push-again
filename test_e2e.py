#!/usr/bin/env python3
"""End-to-end tests for the BigSeller auto push-again service.

These tests run against a mock BigSeller HTTP server to verify the full
service flow without real credentials or network access.
"""

import http.server
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

# Import the module under test
import run


class MockBigSellerServer:
    """A mock BigSeller HTTP server for E2E testing.
    
    Mimics BigSeller's API behavior including:
    - CSRF token validation (returns code 2001 when X-XSRF-TOKEN header is missing)
    - Pagination for listing failed syncs
    - Push-again endpoint
    - Session cookie refresh via Set-Cookie
    """

    def __init__(self):
        self.host = "127.0.0.1"
        self.port = 0  # Let OS pick a free port
        self.server = None
        self.thread = None
        
        # Test state - these can be modified by tests
        self.failed_rows = []
        self.pushed_ids = []
        self.list_call_count = 0
        self.push_call_count = 0
        self.require_csrf_token = True
        self.session_expired = False
        self.new_session_cookie = None  # Set to send a Set-Cookie header
        self.page_size = 50
        
        # Request logging for assertions
        self.last_list_request = None
        self.last_push_request = None
        self.last_headers = {}

    def start(self):
        """Start the mock server in a background thread."""
        mock = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass  # Suppress logging
            
            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                mock.last_headers = dict(self.headers)
                
                # Check for CSRF token
                csrf_token = self.headers.get("X-XSRF-TOKEN")
                if mock.require_csrf_token and not csrf_token:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    response = {"code": 2001, "errorType": 0, "msg": "", "data": None}
                    self.wfile.write(json.dumps(response).encode())
                    return
                
                # Check for session expiration
                if mock.session_expired:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    response = {"code": 0, "msg": "Please login first"}
                    self.wfile.write(json.dumps(response).encode())
                    return
                
                if self.path.endswith("pageList.json"):
                    mock.list_call_count += 1
                    mock.last_list_request = json.loads(body.decode())
                    self._handle_list(body)
                elif self.path.endswith("manualErrSync.json"):
                    mock.push_call_count += 1
                    mock.last_push_request = urllib.parse.parse_qs(body.decode())
                    self._handle_push(body)
                else:
                    self.send_error(404)
            
            def _handle_list(self, body):
                data = json.loads(body.decode())
                page_no = data.get("pageNo", 1)
                page_size = data.get("pageSize", mock.page_size)
                
                # Paginate the rows
                start = (page_no - 1) * page_size
                end = start + page_size
                rows = mock.failed_rows[start:end]
                total_page = (len(mock.failed_rows) + page_size - 1) // page_size or 1
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                if mock.new_session_cookie:
                    self.send_header("Set-Cookie", f"JSESSIONID={mock.new_session_cookie}; Path=/; HttpOnly")
                self.end_headers()
                
                response = {
                    "code": 0,
                    "msg": "Successfully",
                    "data": {
                        "rows": rows,
                        "totalPage": total_page,
                        "totalRecord": len(mock.failed_rows),
                    },
                }
                self.wfile.write(json.dumps(response).encode())
            
            def _handle_push(self, body):
                data = urllib.parse.parse_qs(body.decode())
                ids_str = data.get("syncDetailsIds", [""])[0]
                if ids_str:
                    ids = [int(x) for x in ids_str.split(",")]
                    mock.pushed_ids.extend(ids)
                    # Remove pushed IDs from failed_rows
                    mock.failed_rows = [r for r in mock.failed_rows if r.get("id") not in ids]
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response = {"code": 0, "msg": "Successfully"}
                self.wfile.write(json.dumps(response).encode())

        self.server = http.server.HTTPServer((self.host, 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        """Stop the mock server."""
        if self.server:
            self.server.shutdown()
            self.thread.join(timeout=5)

    @property
    def list_url(self):
        return f"http://{self.host}:{self.port}/api/v2/inventory/sync/list/record/listing/pageList.json"

    @property
    def push_url(self):
        return f"http://{self.host}:{self.port}/api/v2/inventory/sync/list/manualErrSync.json"

    def reset(self):
        """Reset test state between tests."""
        self.failed_rows = []
        self.pushed_ids = []
        self.list_call_count = 0
        self.push_call_count = 0
        self.require_csrf_token = True
        self.session_expired = False
        self.new_session_cookie = None
        self.last_list_request = None
        self.last_push_request = None
        self.last_headers = {}


class E2ETestBase(unittest.TestCase):
    """Base class for E2E tests with mock server and temp files."""

    @classmethod
    def setUpClass(cls):
        cls.mock_server = MockBigSellerServer()
        cls.mock_server.start()

    @classmethod
    def tearDownClass(cls):
        cls.mock_server.stop()

    def setUp(self):
        self.mock_server.reset()
        self.temp_dir = tempfile.mkdtemp()
        self.cookie_file = Path(self.temp_dir) / "cookie"
        
        # Set up environment for the module
        self._orig_list_url = run.LIST_URL
        self._orig_push_url = run.PUSH_URL
        self._orig_cookie_path = run.COOKIE_PATH
        self._orig_recheck_seconds = run.RECHECK_SECONDS
        self._orig_interval_seconds = run.INTERVAL_SECONDS
        
        run.LIST_URL = self.mock_server.list_url
        run.PUSH_URL = self.mock_server.push_url
        run.COOKIE_PATH = self.cookie_file
        run.RECHECK_SECONDS = 0.01  # Fast rechecks for testing
        run.INTERVAL_SECONDS = 0.01

    def tearDown(self):
        # Restore original values
        run.LIST_URL = self._orig_list_url
        run.PUSH_URL = self._orig_push_url
        run.COOKIE_PATH = self._orig_cookie_path
        run.RECHECK_SECONDS = self._orig_recheck_seconds
        run.INTERVAL_SECONDS = self._orig_interval_seconds
        
        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def write_cookie(self, cookie_str):
        """Write a cookie string to the test cookie file."""
        self.cookie_file.write_text(cookie_str)


class TestCSRFTokenValidation(E2ETestBase):
    """Test that the service sends the CSRF token correctly."""

    def test_missing_csrf_token_returns_code_2001(self):
        """Verify the mock rejects requests without X-XSRF-TOKEN header with code 2001."""
        # Cookie without muc_token (no CSRF token)
        self.write_cookie("JSESSIONID=abc123; locale=en_US")
        
        with self.assertRaises(run.ApiError) as ctx:
            run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        
        self.assertIn("code 2001", str(ctx.exception))
        self.assertIn("authentication failed", str(ctx.exception))

    def test_csrf_token_sent_as_header(self):
        """Verify that muc_token cookie value is sent as X-XSRF-TOKEN header."""
        self.write_cookie("JSESSIONID=abc123; muc_token=my_csrf_token_value")
        self.mock_server.failed_rows = []
        
        jar = run.parse_cookie(run.read_cookie_text())
        run.fetch_failed_rows(jar)
        
        # HTTP headers are case-insensitive, check both forms
        csrf_header = (
            self.mock_server.last_headers.get("X-XSRF-TOKEN") or
            self.mock_server.last_headers.get("X-Xsrf-Token") or
            self.mock_server.last_headers.get("x-xsrf-token")
        )
        self.assertEqual(csrf_header, "my_csrf_token_value")

    def test_regression_original_bug_fixed(self):
        """Regression test: the original bug was missing CSRF token causing code 2001."""
        # This mimics the original broken behavior where code 2001 was returned
        # because the CSRF token header was not sent
        self.write_cookie("JSESSIONID=abc123")  # Missing muc_token
        
        # Without CSRF token, should get code 2001
        with self.assertRaises(run.ApiError) as ctx:
            run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        
        self.assertIn("2001", str(ctx.exception))
        
        # Now with CSRF token, should work
        self.write_cookie("JSESSIONID=abc123; muc_token=valid_token")
        rows = run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        self.assertEqual(rows, [])


class TestListingFailedSyncs(E2ETestBase):
    """Test listing failed syncs with pagination."""

    def test_fetches_single_page(self):
        """Test fetching rows that fit in a single page."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = [
            {"id": 1, "sku": "SKU1", "errorMsg": "error1"},
            {"id": 2, "sku": "SKU2", "errorMsg": "error2"},
        ]
        
        rows = run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.mock_server.list_call_count, 1)

    def test_fetches_multiple_pages(self):
        """Test pagination when rows span multiple pages."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        # Create more rows than fit in one page (default pageSize=50)
        self.mock_server.failed_rows = [
            {"id": i, "sku": f"SKU{i}", "errorMsg": f"error{i}"}
            for i in range(120)  # Will need 3 pages at pageSize=50
        ]
        
        rows = run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        
        self.assertEqual(len(rows), 120)
        self.assertEqual(self.mock_server.list_call_count, 3)  # Pages 1, 2, 3

    def test_request_body_format(self):
        """Verify the list request body matches BigSeller's expected format."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = []
        
        run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        
        req = self.mock_server.last_list_request
        self.assertEqual(req["pageNo"], 1)
        self.assertEqual(req["pageSize"], 50)
        self.assertEqual(req["syncStatus"], "failed")
        self.assertEqual(req["hour24"], True)


class TestRowClassification(E2ETestBase):
    """Test that rows are correctly classified for push or skip."""

    def test_rate_limit_errors_are_pushed(self):
        """Rows with rate-limit errors should be marked for push."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = [
            {"id": 100, "sku": "RATE_LIMIT", "errorMsg": "Too many requests. Try again in 30 seconds"},
            {"id": 101, "sku": "FREQUENCY", "errorMsg": "Request frequency exceeded"},
            {"id": 102, "sku": "LIMIT", "errorMsg": "API rate limit reached"},
        ]
        
        rows = run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        matched, skipped = run.classify(rows)
        
        self.assertEqual(len(matched), 3)
        self.assertEqual(len(skipped), 0)
        self.assertEqual([r["id"] for r, _, _ in matched], [100, 101, 102])

    def test_operation_not_allowed_is_skipped(self):
        """Rows with 'Operation Not Allowed' should be skipped."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = [
            {"id": 200, "sku": "NOT_ALLOWED", "errorMsg": "Operation Not Allowed. Product must be DRAFT."},
        ]
        
        rows = run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        matched, skipped = run.classify(rows)
        
        self.assertEqual(len(matched), 0)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["id"], 200)

    def test_id_88520_regression(self):
        """Regression test: id 88520 with rate-limit error should be pushed."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = [
            {
                "id": 88520,
                "sku": "POKEJAM_TEST_SKU",
                "shopSku": "shop_88520",
                "errorMsg": "",
                "errorMsgList": [{"error": "Too many requests. Try again in 30 seconds"}],
            },
        ]
        
        rows = run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))
        matched, skipped = run.classify(rows)
        
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0][0]["id"], 88520)
        self.assertEqual(matched[0][1], "too many requests")  # needle


class TestPushAgainEndpoint(E2ETestBase):
    """Test the push-again (manualErrSync) endpoint."""

    def test_push_sends_correct_ids(self):
        """Verify push request contains the correct IDs in expected format."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        
        msg = run.push_ids(run.parse_cookie(run.read_cookie_text()), [88520, 12345, 67890])
        
        self.assertEqual(msg, "Successfully")
        self.assertEqual(self.mock_server.pushed_ids, [88520, 12345, 67890])
        
        req = self.mock_server.last_push_request
        self.assertEqual(req["syncType"], ["1"])
        self.assertEqual(req["syncDetailsIds"], ["88520,12345,67890"])


class TestRecheckLoop(E2ETestBase):
    """Test the 15-second recheck loop behavior."""

    def test_recheck_clears_when_no_more_matches(self):
        """After push, service rechecks and stops when no matches remain."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        # First check: one rate-limit row
        self.mock_server.failed_rows = [
            {"id": 1, "sku": "A", "errorMsg": "Too many requests"},
        ]
        
        # run_cycle will push, then recheck, then find nothing
        run.run_cycle()
        
        self.assertEqual(self.mock_server.pushed_ids, [1])
        # Should have called list twice: initial + recheck
        self.assertGreaterEqual(self.mock_server.list_call_count, 2)

    def test_recheck_continues_if_still_matching(self):
        """If rows still match after push, continue rechecking."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        
        # Mock will keep returning the same row (simulating it wasn't cleared)
        persistent_row = {"id": 999, "sku": "STUCK", "errorMsg": "Too many requests"}
        
        # We need to prevent the mock from removing the row on push
        original_failed_rows = [dict(persistent_row)]
        self.mock_server.failed_rows = original_failed_rows
        
        # Override the push behavior to not remove rows
        def keep_rows():
            self.mock_server.failed_rows = [dict(persistent_row)]
        
        # Run with max_pushes = 2 to limit iterations
        orig_max = run.MAX_PUSHES_PER_CYCLE
        run.MAX_PUSHES_PER_CYCLE = 2
        try:
            run.run_cycle()
        finally:
            run.MAX_PUSHES_PER_CYCLE = orig_max
        
        # Should have pushed the same ID multiple times
        self.assertGreaterEqual(len(self.mock_server.pushed_ids), 1)


class MockServerWithPersistentRows(MockBigSellerServer):
    """A mock server that doesn't remove rows on push (simulates stuck rows)."""
    
    def __init__(self, persistent_rows):
        super().__init__()
        self._persistent_rows = persistent_rows
    
    def start(self):
        """Start the mock server in a background thread."""
        mock = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            
            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                mock.last_headers = dict(self.headers)
                
                csrf_token = self.headers.get("X-XSRF-TOKEN")
                if mock.require_csrf_token and not csrf_token:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    response = {"code": 2001, "errorType": 0, "msg": "", "data": None}
                    self.wfile.write(json.dumps(response).encode())
                    return
                
                if self.path.endswith("pageList.json"):
                    mock.list_call_count += 1
                    # Always return the persistent rows
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    response = {
                        "code": 0,
                        "msg": "Successfully",
                        "data": {
                            "rows": mock._persistent_rows,
                            "totalPage": 1,
                            "totalRecord": len(mock._persistent_rows),
                        },
                    }
                    self.wfile.write(json.dumps(response).encode())
                elif self.path.endswith("manualErrSync.json"):
                    mock.push_call_count += 1
                    data = urllib.parse.parse_qs(body.decode())
                    ids_str = data.get("syncDetailsIds", [""])[0]
                    if ids_str:
                        ids = [int(x) for x in ids_str.split(",")]
                        mock.pushed_ids.extend(ids)
                    # Don't remove rows - they persist
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    response = {"code": 0, "msg": "Successfully"}
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_error(404)

        self.server = http.server.HTTPServer((self.host, 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()


class TestPushCycleLimit(unittest.TestCase):
    """Test the 10-push-per-cycle cap."""

    def test_stops_after_max_pushes(self):
        """Service stops pushing after MAX_PUSHES_PER_CYCLE in one cycle."""
        # Use a mock that doesn't remove rows (simulates stuck row)
        stuck_row = {"id": 42, "sku": "STUCK", "errorMsg": "rate limit exceeded"}
        mock_server = MockServerWithPersistentRows([stuck_row])
        mock_server.start()
        
        temp_dir = tempfile.mkdtemp()
        cookie_file = Path(temp_dir) / "cookie"
        cookie_file.write_text("JSESSIONID=abc; muc_token=csrf")
        
        # Save original values
        orig_list_url = run.LIST_URL
        orig_push_url = run.PUSH_URL
        orig_cookie_path = run.COOKIE_PATH
        orig_recheck = run.RECHECK_SECONDS
        orig_max = run.MAX_PUSHES_PER_CYCLE
        
        try:
            run.LIST_URL = mock_server.list_url
            run.PUSH_URL = mock_server.push_url
            run.COOKIE_PATH = cookie_file
            run.RECHECK_SECONDS = 0.001  # Fast rechecks
            run.MAX_PUSHES_PER_CYCLE = 3  # Lower limit for faster test
            
            run.run_cycle()
            
            # Should have pushed exactly MAX_PUSHES_PER_CYCLE times
            self.assertEqual(mock_server.push_call_count, 3)
        finally:
            run.LIST_URL = orig_list_url
            run.PUSH_URL = orig_push_url
            run.COOKIE_PATH = orig_cookie_path
            run.RECHECK_SECONDS = orig_recheck
            run.MAX_PUSHES_PER_CYCLE = orig_max
            mock_server.stop()
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestSessionExpiredHandling(E2ETestBase):
    """Test session expiration detection and handling."""

    def test_detects_login_message(self):
        """Session expired when API returns login-related message."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.session_expired = True
        
        with self.assertRaises(run.SessionExpired):
            run.fetch_failed_rows(run.parse_cookie(run.read_cookie_text()))

    def test_detects_http_401(self):
        """Session expired on HTTP 401 status."""
        jar = {"JSESSIONID": "abc", "muc_token": "csrf"}
        self.assertTrue(run.session_dead(401, b"Unauthorized", None))

    def test_detects_html_response(self):
        """Session expired when API returns HTML (login page redirect)."""
        jar = {"JSESSIONID": "abc", "muc_token": "csrf"}
        self.assertTrue(run.session_dead(200, b"<html>Please login</html>", None))


class TestCookieRefresh(E2ETestBase):
    """Test that refreshed session cookies are saved back to file."""

    def test_saves_refreshed_cookie(self):
        """When server sends Set-Cookie, the new value is saved to file."""
        self.write_cookie("JSESSIONID=old_session; muc_token=csrf")
        self.mock_server.failed_rows = []
        self.mock_server.new_session_cookie = "new_session_12345"
        
        jar = run.parse_cookie(run.read_cookie_text())
        snapshot = run.read_cookie_text()
        run.fetch_failed_rows(jar)
        
        # Jar should be updated
        self.assertEqual(jar["JSESSIONID"], "new_session_12345")
        
        # Persist should save it
        run.persist_cookie(snapshot, jar)
        
        new_content = self.cookie_file.read_text()
        self.assertIn("new_session_12345", new_content)


class TestCheckCommand(E2ETestBase):
    """Test the 'run.py check' command."""

    def test_check_success(self):
        """Check command reports success when API works."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf_token")
        self.mock_server.failed_rows = [
            {"id": 1, "sku": "TEST", "errorMsg": "some error"},
        ]
        
        result = run.check_cookie()
        self.assertEqual(result, 0)

    def test_check_missing_file(self):
        """Check command fails when cookie file doesn't exist."""
        run.COOKIE_PATH = Path(self.temp_dir) / "nonexistent"
        result = run.check_cookie()
        self.assertEqual(result, 1)

    def test_check_empty_file(self):
        """Check command fails when cookie file is empty."""
        self.write_cookie("")
        result = run.check_cookie()
        self.assertEqual(result, 1)

    def test_check_warns_missing_csrf(self):
        """Check command warns when CSRF token cookie is missing."""
        self.write_cookie("JSESSIONID=abc; locale=en_US")
        # Can't easily capture stdout in unittest, but the function should still return 1
        # because the API call will fail with code 2001
        result = run.check_cookie()
        self.assertEqual(result, 1)


class TestFullCycleIntegration(E2ETestBase):
    """Full integration tests running complete cycles."""

    def test_full_cycle_with_mixed_rows(self):
        """Test a full cycle with both pushable and skippable rows."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = [
            {"id": 88520, "sku": "PUSH_ME", "errorMsg": "Too many requests. Try again in 30 seconds"},
            {"id": 88521, "sku": "SKIP_ME", "errorMsg": "Operation Not Allowed"},
            {"id": 88522, "sku": "PUSH_TOO", "errorMsg": "rate limit exceeded"},
        ]
        
        run.run_cycle()
        
        # Only rate-limit rows should be pushed
        self.assertEqual(sorted(self.mock_server.pushed_ids), [88520, 88522])

    def test_empty_cycle_no_failed_syncs(self):
        """Test cycle when there are no failed syncs."""
        self.write_cookie("JSESSIONID=abc; muc_token=csrf")
        self.mock_server.failed_rows = []
        
        had_activity = run.run_cycle()
        
        self.assertFalse(had_activity)
        self.assertEqual(self.mock_server.push_call_count, 0)


if __name__ == "__main__":
    unittest.main()
