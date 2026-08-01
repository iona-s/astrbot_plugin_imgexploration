"""搜索结果发送与降级契约测试"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from astrbot.core.message.components import Image, Nodes, Plain
from astrbot_plugin_imgexploration.core.models import SearchResultItem
from astrbot_plugin_imgexploration.core.result_sender import send_search_results


def make_item(**overrides: object) -> SearchResultItem:
    values = {
        "title": "示例标题",
        "url": "https://source.example/page",
        "source": "SauceNAO",
        "similarity": "92%",
        "domain": "source.example",
        "thumbnail": "https://source.example/thumb.jpg",
    }
    values.update(overrides)
    return SearchResultItem(**values)


def make_event(platform: str, side_effect: object = None) -> SimpleNamespace:
    return SimpleNamespace(
        platform=platform,
        get_self_id=Mock(return_value="10001"),
        chain_result=Mock(side_effect=lambda chain: chain),
        plain_result=Mock(side_effect=lambda text: text),
        send=AsyncMock(side_effect=side_effect),
    )


def get_nodes(payload: object) -> list[object]:
    assert isinstance(payload, list)
    assert len(payload) == 1
    forward = payload[0]
    assert isinstance(forward, Nodes)
    return forward.nodes


def get_plain_texts(components: list[object]) -> list[str]:
    return [comp.text for comp in components if isinstance(comp, Plain)]


class ResultSenderContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_aiocqhttp_sends_forward_message_with_all_result_fields(
        self,
    ) -> None:
        event = make_event("aiocqhttp")
        item = make_item(thumbnail="", thumbnail_bytes=b"thumbnail")

        await send_search_results(event, [item])

        event.send.assert_awaited_once()
        nodes = get_nodes(event.send.await_args.args[0])
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].uin, "10001")
        texts = get_plain_texts(nodes[0].content)
        self.assertEqual(texts[0], "1. 示例标题")
        self.assertIn("来源: SauceNAO", texts[1])
        self.assertIn("相似度: 92%", texts[2])
        self.assertIn("域名: source.example", texts[3])
        self.assertIn("链接: https://source.example/page", texts[-1])
        images = [comp for comp in nodes[0].content if isinstance(comp, Image)]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].file.startswith("base64://"))

    async def test_forward_retry_removes_only_suspicious_source_image(
        self,
    ) -> None:
        event = make_event("aiocqhttp", [RuntimeError("rejected"), None])
        items = [
            make_item(source="Ascii2d"),
            make_item(
                title="第二条目",
                url="https://other.example/page",
                thumbnail="https://other.example/thumb.jpg",
            ),
        ]

        await send_search_results(event, items)

        self.assertEqual(event.send.await_count, 2)
        retry_nodes = get_nodes(event.send.await_args_list[1].args[0])
        self.assertNotIn(
            Image,
            [type(comp) for comp in retry_nodes[0].content],
        )
        self.assertIn(
            Image,
            [type(comp) for comp in retry_nodes[1].content],
        )
        for node in retry_nodes:
            texts = get_plain_texts(node.content)
            self.assertIn("链接:", texts[-1])

    async def test_forward_failure_without_removable_image_uses_normal_chain(
        self,
    ) -> None:
        event = make_event("aiocqhttp", [RuntimeError("rejected"), None])

        await send_search_results(event, [make_item()])

        self.assertEqual(event.send.await_count, 2)
        get_nodes(event.send.await_args_list[0].args[0])
        normal_payload = event.send.await_args_list[1].args[0]
        self.assertIsInstance(normal_payload, list)
        self.assertFalse(any(isinstance(comp, Nodes) for comp in normal_payload))
        normal_text = "".join(get_plain_texts(normal_payload))
        self.assertIn("1. 示例标题", normal_text)
        self.assertIn("链接: https://source.example/page", normal_text)

    async def test_normal_failure_uses_plain_text_with_titles_and_urls(self) -> None:
        event = make_event(
            "aiocqhttp",
            [RuntimeError("forward rejected"), RuntimeError("chain rejected"), None],
        )
        items = [
            make_item(),
            make_item(
                title="第二条目",
                url="https://other.example/page",
                domain=None,
            ),
        ]

        await send_search_results(event, items)

        self.assertEqual(event.send.await_count, 3)
        text = event.send.await_args_list[2].args[0]
        self.assertIsInstance(text, str)
        self.assertIn("1. 示例标题", text)
        self.assertIn("2. 第二条目", text)
        self.assertIn("链接: https://source.example/page", text)
        self.assertIn("链接: https://other.example/page", text)
        self.assertIn("来源: SauceNAO", text)
        self.assertIn("相似度: 92%", text)
        self.assertIn("域名: source.example", text)
        self.assertIn("\n---\n", text)
        self.assertFalse(text.endswith("---"))

    async def test_non_aiocqhttp_sends_normal_chain_directly(self) -> None:
        event = make_event("telegram")

        await send_search_results(event, [make_item()])

        event.send.assert_awaited_once()
        payload = event.send.await_args.args[0]
        self.assertIsInstance(payload, list)
        self.assertFalse(any(isinstance(comp, Nodes) for comp in payload))
        text = "".join(get_plain_texts(payload))
        self.assertIn("1. 示例标题", text)
        self.assertIn("链接: https://source.example/page", text)


if __name__ == "__main__":
    unittest.main()
