import asyncio
import socket
import types
import unittest
from typing import Optional
from unittest.mock import patch

import webtools_server.robots as robots
import webtools_server.security as security


class FakeRobotsResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class FakeRobotsClient:
    def __init__(
        self, response: Optional[FakeRobotsResponse] = None, exc: Exception = None
    ) -> None:
        self.response = response
        self.exc = exc
        self.calls = 0

    async def get(self, url: str, headers: Optional[dict[str, str]] = None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.response is None:
            raise AssertionError("Response not configured")
        return self.response


class TestSecurityHelpers(unittest.TestCase):
    def test_is_ip_private_or_local_returns_true_for_private(self) -> None:
        self.assertTrue(security._is_ip_private_or_local("127.0.0.1"))

    def test_is_ip_private_or_local_returns_false_for_public(self) -> None:
        self.assertFalse(security._is_ip_private_or_local("8.8.8.8"))

    def test_hostname_points_to_blocked_ip_for_dns_failure(self) -> None:
        with patch(
            "webtools_server.security.socket.getaddrinfo", side_effect=socket.gaierror
        ):
            self.assertTrue(security._hostname_points_to_blocked_ip("no-such-host"))

    def test_hostname_points_to_blocked_ip_for_private_ip(self) -> None:
        infos = [(None, None, None, None, ("127.0.0.1", 0))]
        with patch("webtools_server.security.socket.getaddrinfo", return_value=infos):
            self.assertTrue(security._hostname_points_to_blocked_ip("example.com"))

    def test_hostname_points_to_blocked_ip_allows_public_ip(self) -> None:
        infos = [(None, None, None, None, ("8.8.8.8", 0))]
        with patch("webtools_server.security.socket.getaddrinfo", return_value=infos):
            self.assertFalse(security._hostname_points_to_blocked_ip("example.com"))


class TestRobotsHandling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._original_cache = robots._robots_cache
        robots._robots_cache = {}

    async def asyncTearDown(self) -> None:
        robots._robots_cache = self._original_cache

    async def test_can_fetch_uses_cache(self) -> None:
        base = "https://example.com"
        fake_rp = types.SimpleNamespace(can_fetch=lambda *_args, **_kwargs: False)
        now = asyncio.get_event_loop().time()
        robots._robots_cache[base] = robots.RobotsCacheEntry(rp=fake_rp, fetched_at=now)

        client = FakeRobotsClient()
        allowed = await robots._can_fetch("https://example.com/path", client)

        self.assertFalse(allowed)
        self.assertEqual(client.calls, 0)

    async def test_can_fetch_parses_disallow(self) -> None:
        response = FakeRobotsResponse(200, "User-agent: *\nDisallow: /")
        client = FakeRobotsClient(response=response)

        allowed = await robots._can_fetch("https://example.com/path", client)

        self.assertFalse(allowed)
        self.assertEqual(client.calls, 1)

    async def test_can_fetch_allows_on_error(self) -> None:
        client = FakeRobotsClient(exc=Exception("boom"))

        allowed = await robots._can_fetch("https://example.com/path", client)

        self.assertTrue(allowed)
        self.assertEqual(client.calls, 1)
