# Recovered ranking force history

`2026-09-04.json` contains genuine completed trade-day amount aggregates for all 325 distinct members needed by the September 4 strong/weak stock and group rankings. It is an immutable recovery archive, not generated sample data. The application imports a requested trade date into D1 and continues to fetch new dates from its existing historical endpoint.

282 records were recovered from `https://hanstock.xyz/api/hub/daytrade-flow-ranking` with explicit `codes`, `date=2026-09-04`, and `include_all=true`. The remaining 43 were recovered using the project's existing Shioaji market-data account and `TicksQueryType.AllDay`; no trading operations were performed. These raw trades were processed with the existing `daytrade_flow.summarize_historical_ticks` implementation, which uses the MarketDataHub main-order threshold (20 lots or TWD 1,000,000), the original buy/sell direction, and the formal 09:00–13:30 session including the closing auction.

Independent direct queries for 2426, 3324, 3441, and 6290 produced exactly the same buy, sell, and turnover amounts as the Hub. No credentials, account details, or private trading records are included. Missing/placeholder upstream values were rejected; genuine zero totals were retained.

The ranking percentage is `(large_buy_amount - large_sell_amount) / total_turnover_amount * 100`. Group force is the equal-weight average of every distinct configured member, including members with a genuine zero.
