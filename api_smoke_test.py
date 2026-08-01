"""不啟動伺服器的 HanStock API 基本測試。"""

from api_server import health, list_groups


def main() -> None:
    health_result = health()
    groups_result = list_groups(include_stocks=False)

    assert health_result["status"] == "ok"
    assert health_result["group_count"] == groups_result["group_count"]
    assert groups_result["group_count"] >= 69

    print("HanStock API 基本測試成功！")
    print(f"族群數量：{groups_result['group_count']}")
    print(
        "Rule1 結果檔："
        + ("存在" if health_result["rule1_result_exists"] else "尚未建立")
    )


if __name__ == "__main__":
    main()
