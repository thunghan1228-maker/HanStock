"""今天在漲／跌什麼的 AI 整理：沒金鑰回 disabled、欄位清理、同一份只整理一次、每日上限、拒答、API 出錯。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import database
import move_why as module
import persistent_app

TW = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 12, 11, 30, tzinfo=TW)
BODY = {
    "code": "3008", "name": "大立光", "date": "2026-10-12", "pct": 9.98, "price": 7325, "limitUp": True,
    "path": "早盤先殺到 -4.6% 再拉上漲停", "group": {"name": "光學鏡頭", "avg": 5.5, "movers": ["台表科", "佳凌"]},
    "background": ["營收 2026/08 年增 -16.2%（衰退中）", "法人近 5 日賣超 929 張"],
    "news": [{"title": "CPO題材點火！大立光一甩早盤陰霾飆漲停", "source": "Yahoo新聞", "time": "09:49"},
             {"title": "大立光:大立光電澄清媒體報導", "source": "公司公告", "time": "11:10"}],
}
ANSWER = {"reasons": ["CPO 題材：法說會說光通訊零件已通過客戶認證", "族群一起漲：光學鏡頭台表科、佳凌也漲停"],
          "cautions": ["本業還偏弱：8 月營收年減 16%"], "one_line": "題材帶動漲停，業績還沒跟上", "has_news": True}


class FakeMessages:
    def __init__(self, answer: dict | None = None, stop: str = "end_turn", error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.answer, self.stop, self.error = answer or ANSWER, stop, error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=json.dumps(self.answer, ensure_ascii=False))]
        return SimpleNamespace(content=[] if self.stop == "refusal" else content, stop_reason=self.stop, model=module.MODEL,
                               usage=SimpleNamespace(input_tokens=900, output_tokens=300))


def fake_client(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(**kwargs)))


class MoveWhyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DATABASE_PATH", Path(self.temp_dir.name) / "test.db")
        self.db_patch.start()
        database.initialize_database()

    def tearDown(self) -> None:
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def run_it(self, body=BODY, client=None, now=NOW):
        return module.summarize(dict(body), client=client, now=now, key_enabled=lambda: True)

    def test_disabled_without_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(module.summarize(BODY)["status"], "disabled")

    def test_clean_request_and_prompt(self) -> None:
        with self.assertRaises(ValueError):
            module.clean_request({**BODY, "code": "33; DROP"})
        with self.assertRaises(ValueError):
            module.clean_request({**BODY, "pct": "abc"})
        req = module.clean_request({**BODY, "news": [{"title": "x" * 500, "source": "s"}] * 30, "background": ["  a   b  "] * 20})
        self.assertEqual((len(req["news"]), len(req["news"][0]["title"]), len(req["background"]), req["background"][0]),
                         (module.MAX_NEWS, module.MAX_TEXT, module.MAX_BACKGROUND, "a b"))
        prompt = module.build_prompt(module.clean_request(BODY))
        self.assertIn("今天漲跌：+9.98%（漲停鎖住），現價 7325", prompt)
        self.assertIn("同族群：光學鏡頭 平均 +5.5%", prompt)
        self.assertIn("<headlines>\n- [09:49] Yahoo新聞：CPO題材點火！大立光一甩早盤陰霾飆漲停", prompt)

    def test_summarize_and_cache(self) -> None:
        client = fake_client()
        out = self.run_it(client=client)
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["cached"])
        self.assertEqual(out["summary"], {"reasons": ANSWER["reasons"], "cautions": ANSWER["cautions"], "oneLine": ANSWER["one_line"], "hasNews": True})
        call = client.beta.messages.calls[0]
        self.assertEqual((call["model"], call["fallbacks"], call["betas"]), ("claude-opus-5-5", "default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        # 同一份（漲跌同一個 2% 區間、新聞一樣）不再打 API；新聞換了才重新整理
        again = self.run_it(body={**BODY, "pct": 9.5}, client=client)
        self.assertTrue(again["cached"])
        self.assertEqual(len(client.beta.messages.calls), 1)
        self.run_it(body={**BODY, "news": BODY["news"][:1]}, client=client)
        self.assertEqual(len(client.beta.messages.calls), 2)
        with patch.object(module, "_now", return_value=NOW):
            self.assertEqual(module.status()["used"], 2)

    def test_daily_cap_refusal_and_error(self) -> None:
        with patch.object(module, "DAILY_CAP", 1):
            self.run_it(client=fake_client())
            self.assertEqual(self.run_it(body={**BODY, "code": "6278"}, client=fake_client())["status"], "cap")
            self.assertEqual(self.run_it(body={**BODY, "code": "6278"}, client=fake_client(), now=NOW + timedelta(days=1))["status"], "ok")
        refused = self.run_it(body={**BODY, "code": "2301"}, client=fake_client(stop="refusal"))
        self.assertEqual(refused["status"], "refused")
        failed = self.run_it(body={**BODY, "code": "2330"}, client=fake_client(error=RuntimeError("boom")))
        self.assertEqual(failed["status"], "error")
        # 拒答、出錯都不存，下次會再試
        self.assertEqual(self.run_it(body={**BODY, "code": "2330"}, client=fake_client())["status"], "ok")

    def test_endpoint(self) -> None:
        client = TestClient(persistent_app.app)
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(client.post("/api/hub/move-why", json=BODY).json()["status"], "disabled")
            self.assertFalse(client.get("/api/hub/move-why/status").json()["enabled"])
        with patch.object(module, "enabled", return_value=True):
            self.assertEqual(client.post("/api/hub/move-why", json={**BODY, "code": "bad!"}).status_code, 400)
            with patch.object(module, "_default_client", return_value=fake_client()):
                self.assertEqual(client.post("/api/hub/move-why", json=BODY).json()["summary"]["oneLine"], ANSWER["one_line"])


if __name__ == "__main__":
    unittest.main()
