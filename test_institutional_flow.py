from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from institutional_flow import (
    fetch_tpex_institutional_latest,
    fetch_tpex_institutional_official,
    load_tpex_institutional_snapshot,
    normalize_tpex_institutional_payload,
)


FOREIGN_KEY = "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"
TRUST_KEY = "SecuritiesInvestmentTrustCompanies-Difference"
DEALER_KEY = "Dealers-Difference"


def sample_row(index: int, date: str = "1150819") -> dict[str, str]:
    return {
        "Date": date,
        "SecuritiesCompanyCode": f"{1000 + index:04d}",
        "CompanyName": f"測試{index}",
        FOREIGN_KEY: "1,500",
        TRUST_KEY: "-2,000",
        DEALER_KEY: "300",
    }


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class InstitutionalFlowTest(unittest.TestCase):
    def test_normalizes_latest_complete_tpex_day(self) -> None:
        payload = [sample_row(index) for index in range(650)] + [sample_row(900, "1150818")]
        result = normalize_tpex_institutional_payload(payload)

        self.assertEqual(result["data_date"], "2026/08/19")
        self.assertEqual(result["count"], 650)
        self.assertEqual(result["rows"][0]["foreign"], 1500)
        self.assertEqual(result["rows"][0]["trust"], -2000)
        self.assertEqual(result["rows"][0]["dealer"], 300)

    def test_rejects_incomplete_market_payload(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "incomplete_20"):
            normalize_tpex_institutional_payload([sample_row(index) for index in range(20)])

    @patch("institutional_flow.urllib.request.urlopen")
    def test_fetch_uses_official_payload(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([sample_row(index) for index in range(620)])
        result = fetch_tpex_institutional_official(timeout=5)

        self.assertEqual(result["data_date"], "2026/08/19")
        self.assertEqual(result["count"], 620)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)

    def test_loads_verified_github_snapshot(self) -> None:
        rows = normalize_tpex_institutional_payload([sample_row(index) for index in range(620)])["rows"]
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "data_date": "2026/08/19",
                        "generated_at": "2026-08-19T09:35:00Z",
                        "rows": rows,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            result = load_tpex_institutional_snapshot(snapshot_path)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data_date"], "2026/08/19")
        self.assertEqual(result["count"], 620)

    @patch("institutional_flow.fetch_tpex_institutional_official")
    def test_falls_back_to_snapshot_when_datacenter_is_blocked(self, fetch_official) -> None:
        fetch_official.side_effect = RuntimeError("tpex_institutional_fetch_failed_HTTPError")
        rows = normalize_tpex_institutional_payload([sample_row(index) for index in range(620)])["rows"]
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "snapshot.json"
            snapshot_path.write_text(
                json.dumps({"data_date": "2026/08/19", "rows": rows}, ensure_ascii=False),
                encoding="utf-8",
            )
            result = fetch_tpex_institutional_latest(timeout=5, snapshot_path=snapshot_path)

        self.assertEqual(result["data_date"], "2026/08/19")
        self.assertEqual(result["count"], 620)


if __name__ == "__main__":
    unittest.main()
