import math
import tempfile
import unittest

from app import create_app
from text_utils import chunk_text, embed, make_cards


class TextUtilsTest(unittest.TestCase):
    def test_embedding_is_deterministic_and_normalized(self):
        first = embed("间隔重复有助于长期记忆")
        self.assertEqual(first, embed("间隔重复有助于长期记忆"))
        self.assertEqual(len(first), 128)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in first)), 1.0)

    def test_chunking_and_card_generation(self):
        chunks = chunk_text("第一段。\n\n第二段。" * 100)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 420 for chunk in chunks))
        cards = make_cards("间隔重复是一种根据记忆反馈逐步拉长复习间隔的学习方法。它能帮助知识进入长期记忆。", "学习科学")
        self.assertTrue(cards)
        self.assertIn("间隔重复", cards[0]["question"])


class ApplicationFlowTest(unittest.TestCase):
    def test_complete_learning_flow(self):
        with tempfile.TemporaryDirectory(prefix="zhishu-test-") as data_dir:
            client = create_app(data_dir).test_client()
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.post("/api/notes", json={"title": "", "content": "test"}).status_code, 400)

            response = client.post("/api/notes", json={
                "title": "测试：间隔重复",
                "subject": "学习科学",
                "source": "自动化验证",
                "content": "间隔重复是一种根据记忆反馈逐步拉长复习间隔的学习方法。与集中重读相比，它更重视主动提取。\n\n提取练习是指不借助资料主动回忆答案。它能够暴露理解缺口，并帮助学习者及时纠错。",
            })
            self.assertEqual(response.status_code, 201)
            note_id = response.get_json()["note"]["note_id"]

            response = client.post("/api/search", json={"query": "间隔重复", "mode": "hybrid"})
            self.assertEqual(response.status_code, 200)
            search = response.get_json()
            self.assertTrue(any(item["note_id"] == note_id for item in search["results"]))
            self.assertEqual(search["trace"]["fusion"], "RRF")
            self.assertTrue(search["trace"]["event_id"].startswith("event:"))

            response = client.post(f"/api/notes/{note_id}/cards")
            self.assertEqual(response.status_code, 200)
            cards = response.get_json()["cards"]
            self.assertTrue(cards)
            card_id = cards[0]["card_id"]
            response = client.post(f"/api/review/{card_id}", json={"rating": "good"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["card"]["reviews"], 1)
            self.assertEqual(response.get_json()["card"]["mastery"], 12)

            overview = client.get("/api/overview").get_json()
            self.assertEqual(overview["notes"], 1)
            self.assertEqual(overview["searches"], 1)
            self.assertEqual(overview["reviews"], 1)
            self.assertEqual(client.get("/api/export").status_code, 200)

            self.assertEqual(client.delete(f"/api/notes/{note_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/notes/{note_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
