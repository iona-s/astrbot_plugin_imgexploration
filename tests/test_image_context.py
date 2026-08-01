from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_imgexploration.core.image_context import (
    ImageContextManager,
    SessionImages,
    get_image_context_manager,
    init_image_context_manager,
)


class SessionImagesTests(unittest.TestCase):
    def test_add_image_validates_url(self) -> None:
        session = SessionImages()
        self.assertIsNone(session.add_image(""))
        self.assertIsNone(session.add_image("ftp://example.com/img.jpg"))
        self.assertIsNone(session.add_image("file:///local/path.png"))
        self.assertEqual(len(session.images), 0)

        info = session.add_image("https://example.com/valid.jpg", "msg-1", "user-1")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.url, "https://example.com/valid.jpg")
        self.assertEqual(info.message_id, "msg-1")
        self.assertEqual(info.sender_id, "user-1")

    def test_add_image_refreshes_existing_url(self) -> None:
        session = SessionImages()
        info1 = session.add_image("https://example.com/1.jpg")
        info2 = session.add_image("https://example.com/2.jpg")
        assert info1 is not None and info2 is not None

        # Re-add first URL; it should be refreshed and moved to the latest position
        info1_refreshed = session.add_image("https://example.com/1.jpg")
        assert info1_refreshed is not None
        self.assertNotEqual(info1.image_id, info1_refreshed.image_id)
        self.assertEqual(len(session.images), 2)
        self.assertEqual(
            session.get_recent_image_info().image_id, info1_refreshed.image_id
        )  # type: ignore[union-attr]

    def test_add_image_capacity_limit(self) -> None:
        session = SessionImages(max_images=2)
        session.add_image("https://example.com/1.jpg")
        session.add_image("https://example.com/2.jpg")
        session.add_image("https://example.com/3.jpg")

        self.assertEqual(len(session.images), 2)
        urls = [info.url for info in session.get_all_image_infos()]
        self.assertEqual(
            urls, ["https://example.com/2.jpg", "https://example.com/3.jpg"]
        )

    def test_prune_expired(self) -> None:
        session = SessionImages()
        info1 = session.add_image("https://example.com/1.jpg")
        info2 = session.add_image("https://example.com/2.jpg")
        assert info1 is not None and info2 is not None

        # Manually set old timestamp for info1
        info1.timestamp = datetime.now() - timedelta(seconds=100)

        pruned = session.prune_expired(ttl_seconds=60)
        self.assertEqual(pruned, 1)
        self.assertEqual(len(session.images), 1)
        self.assertEqual(
            session.get_all_image_infos()[0].url, "https://example.com/2.jpg"
        )

        self.assertEqual(session.prune_expired(ttl_seconds=0), 0)

    def test_lookups_and_clear(self) -> None:
        session = SessionImages()
        self.assertIsNone(session.get_recent_image_info())
        self.assertIsNone(session.get_image_info_by_index(0))
        self.assertIsNone(session.get_image_info_by_index(1))

        info = session.add_image("https://example.com/1.jpg")
        assert info is not None

        self.assertEqual(session.get_image_info_by_index(1), info)
        self.assertEqual(session.get_image_info_by_id(info.image_id), info)
        self.assertIsNone(session.get_image_info_by_id("non-existent"))

        session.clear()
        self.assertEqual(len(session.images), 0)
        self.assertEqual(len(session.url_index), 0)


class ImageContextManagerTests(unittest.TestCase):
    def test_session_key_resolution_fallbacks(self) -> None:
        mgr = ImageContextManager()

        e1 = SimpleNamespace(session_id="custom-session-123")
        self.assertEqual(mgr._get_session_key(e1), "custom-session-123")

        e2 = SimpleNamespace(platform="onebot", group_id=100)
        self.assertEqual(mgr._get_session_key(e2), "onebot:group:100")

        e3 = SimpleNamespace(platform="telegram", user_id=456)
        self.assertEqual(mgr._get_session_key(e3), "telegram:user:456")

        e4 = SimpleNamespace(platform="discord")
        self.assertEqual(mgr._get_session_key(e4), "discord:unknown")

    def test_session_isolation_and_lru_eviction(self) -> None:
        mgr = ImageContextManager(
            isolation_mode="session",
            max_images_per_session=10,
            max_sessions=2,
        )

        event_a = SimpleNamespace(session_id="session-a")
        event_b = SimpleNamespace(session_id="session-b")
        event_c = SimpleNamespace(session_id="session-c")

        mgr.add_image(event_a, "https://example.com/a.jpg")
        mgr.add_image(event_b, "https://example.com/b.jpg")
        self.assertEqual(len(mgr._sessions), 2)

        # Accessing event_a refreshes its LRU order
        self.assertEqual(mgr.get_recent_image(event_a), "https://example.com/a.jpg")

        # Adding third session evicts the least recently used (event_b)
        mgr.add_image(event_c, "https://example.com/c.jpg")
        self.assertEqual(len(mgr._sessions), 2)
        self.assertIn("session-a", mgr._sessions)
        self.assertIn("session-c", mgr._sessions)
        self.assertNotIn("session-b", mgr._sessions)

    def test_global_isolation_mode(self) -> None:
        mgr = ImageContextManager(isolation_mode="global")
        event1 = SimpleNamespace(session_id="session-1")
        event2 = SimpleNamespace(session_id="session-2")

        mgr.add_image(event1, "https://example.com/shared.jpg")
        self.assertEqual(mgr.get_recent_image(event2), "https://example.com/shared.jpg")
        self.assertEqual(len(mgr.get_all_images(event2)), 1)

    def test_image_retrieval_methods(self) -> None:
        mgr = ImageContextManager()
        event = SimpleNamespace(session_id="sess-1")

        self.assertIsNone(mgr.get_recent_image(event))
        self.assertIsNone(mgr.get_image_by_index(event, -1))
        self.assertIsNone(mgr.get_image_by_index(event, 1))
        self.assertIsNone(mgr.get_image_by_id(event, ""))
        self.assertIsNone(mgr.get_image_by_id(event, "fake-id"))

        mgr.add_image(event, "https://example.com/img1.jpg")
        mgr.add_image(event, "https://example.com/img2.jpg")

        self.assertEqual(mgr.get_recent_image(event), "https://example.com/img2.jpg")
        self.assertEqual(
            mgr.get_image_by_index(event, -1), "https://example.com/img2.jpg"
        )
        self.assertEqual(
            mgr.get_image_by_index(event, 1), "https://example.com/img1.jpg"
        )
        self.assertEqual(
            mgr.get_image_by_index(event, 2), "https://example.com/img2.jpg"
        )
        self.assertEqual(
            mgr.get_all_images(event),
            ["https://example.com/img1.jpg", "https://example.com/img2.jpg"],
        )

        ctx_info = mgr.get_image_context_info(event)
        self.assertTrue(ctx_info["has_images"])
        self.assertEqual(ctx_info["count"], 2)
        image_id = ctx_info["images"][0]["image_id"]
        self.assertEqual(
            mgr.get_image_by_id(event, image_id), "https://example.com/img1.jpg"
        )

    def test_context_info_url_privacy_setting(self) -> None:
        event = SimpleNamespace(session_id="privacy-sess")

        mgr_with_url = ImageContextManager(include_url_in_context=True)
        mgr_with_url.add_image(event, "https://example.com/visible.jpg")
        info_with_url = mgr_with_url.get_image_context_info(event)
        self.assertIn("url", info_with_url["images"][0])

        mgr_no_url = ImageContextManager(include_url_in_context=False)
        mgr_no_url.add_image(event, "https://example.com/hidden.jpg")
        info_no_url = mgr_no_url.get_image_context_info(event)
        self.assertNotIn("url", info_no_url["images"][0])

    def test_context_info_empty_state_and_ttl_hint(self) -> None:
        event = SimpleNamespace(session_id="empty-sess")

        mgr_empty = ImageContextManager(ttl_seconds=60)
        empty_info = mgr_empty.get_image_context_info(event)
        self.assertFalse(empty_info["has_images"])
        self.assertEqual(empty_info["count"], 0)
        self.assertIn("没有图片", empty_info["hint"])

        mgr_empty.add_image(event, "https://example.com/ttl.jpg")
        info_with_ttl = mgr_empty.get_image_context_info(event)
        self.assertIn("60 秒后会自动过期", info_with_ttl["hint"])

    def test_clear_session_and_clear_all(self) -> None:
        mgr = ImageContextManager(isolation_mode="session")
        event1 = SimpleNamespace(session_id="sess-1")
        event2 = SimpleNamespace(session_id="sess-2")

        mgr.add_image(event1, "https://example.com/1.jpg")
        mgr.add_image(event2, "https://example.com/2.jpg")

        mgr.clear_session(event1)
        self.assertIsNone(mgr.get_recent_image(event1))
        self.assertEqual(mgr.get_recent_image(event2), "https://example.com/2.jpg")

        mgr.clear_all()
        self.assertIsNone(mgr.get_recent_image(event2))

        # Test clear_session in global isolation mode
        global_mgr = ImageContextManager(isolation_mode="global")
        global_mgr.add_image(event1, "https://example.com/g.jpg")
        global_mgr.clear_session(event1)
        self.assertIsNone(global_mgr.get_recent_image(event1))

    def test_global_singleton_functions(self) -> None:
        with patch(
            "astrbot_plugin_imgexploration.core.image_context._image_context_manager",
            None,
        ):
            instance1 = get_image_context_manager()
            self.assertIsInstance(instance1, ImageContextManager)

            instance2 = init_image_context_manager(
                isolation_mode="global",
                max_images=10,
                ttl_seconds=300,
            )
            self.assertEqual(instance2.isolation_mode, "global")
            self.assertIs(get_image_context_manager(), instance2)
