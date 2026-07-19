# GESH Financial — Data-Licensing Matrix
**Date:** 2026-07-19 · **Status:** operational risk map, not legal advice. Confirm each provider's *current* ToS with counsel before scaling paid customers.

The risk pattern: GESH pulls data for free/personal use, derives signals, and **resells** them to paying customers. Several of these licenses permit personal use but restrict commercial redistribution of raw or derived data. "Derived signal" resale is a grey area that varies by provider.

| Provider | Used for | Commercial-resale status | Risk | Action |
|---|---|---|---|---|
| **Yahoo / `yfinance`** | Primary price/OHLCV feed | Unofficial/undocumented endpoints; Yahoo's API ToS restricts commercial use & redistribution. Personal use only in practice. | **HIGH** (both legal + reliability — endpoints break/block) | **Replace before scaling.** Move price data to a commercially-licensed feed. Candidates: **Polygon.io** (reliable, real-time on paid), **Tiingo** (cheap, EOD+fundamentals), **Twelve Data**, **Alpha Vantage**, **Finnhub paid**, **IEX/other**. `price_source.py` already has a provider-adapter shape → swap is a contained change I can implement. |
| **Robinhood** (`robin_stocks` / MCP) | Owner's live portfolio + fallback quotes | Governed by Robinhood customer ToS; automating a **personal** account is a grey area, using it as the data spine of a **commercial** product is not contemplated by the ToS. | **MEDIUM–HIGH** for anything customer-facing; LOW-ish for owner's own account personal use | Keep Robinhood strictly for the **owner's own** account/snapshot. Do **not** build customer-facing data on it. The masked model book is derived from the owner's account — confirm with counsel this is "publishing about my own portfolio," not redistributing RH data. |
| **Finnhub** | News, sentiment | Free tier is for personal/eval; **commercial use requires a paid/commercial license.** | **MEDIUM** | Move to Finnhub's **commercial tier** or drop the dependency; confirm the paid plan permits displaying to your customers. |
| **FRED (St. Louis Fed)** | Macro (rates, curve, CPI) | Generally **permissive**; most FRED series are public-domain-ish, but some underlying series have their own source restrictions and FRED requires attribution + an API key. | **LOW** | Keep API key + attribution; verify no restricted third-party series are redistributed. Lowest-risk source you have. |
| **Reddit (PRAW)** | r/stocks etc. sentiment | Reddit's API terms **tightened** (2023+); commercial use and data redistribution are restricted and can require a paid data agreement. | **MEDIUM** | Currently the node returns "unavailable" anyway. Either license properly, keep it purely as an internal signal (not resold/displayed as Reddit data), or drop it. Don't advertise "Reddit sentiment" as a customer feature without checking terms. |
| **CoinDesk / crypto feeds** | Crypto prices/indices | Varies; CoinDesk indices and API have commercial-use terms and attribution requirements. | **MEDIUM** | Confirm commercial terms for any crypto data shown to customers; the crypto product is paper-only, which lowers stakes but not licensing. |
| **alternative.me (Fear & Greed)** | Sentiment index | Free API, attribution expected; lighter terms. | **LOW** | Attribute; keep as an internal/secondary signal. |
| **SEC EDGAR** | 10-K/10-Q/8-K | **Public domain**, US government; free to use with fair-access rate limits. | **LOW** | Respect rate limits + declared User-Agent. Safe. |
| **Anthropic Claude / Google Gemini** | AI analysis | Commercial API use is *permitted* under their API terms — but **you** are responsible for what you send: customer portfolio data flows to these third parties. | **MEDIUM (privacy, not licensing)** | Disclose in the Privacy Policy that inputs go to third-party LLMs; check each provider's data-use/retention terms for API traffic (generally not trained on, but confirm) and whether you need a DPA. |

## Recommended near-term actions (I can implement the engineering parts)
1. **Price feed swap** off `yfinance` → a licensed provider (contained change in `price_source.py`). *Highest-leverage licensing fix.*
2. **Finnhub** → commercial tier, or remove.
3. **Reddit** → license or stop presenting it as a customer-facing "Reddit sentiment" feature.
4. **Attribution** where required (FRED, alternative.me, CoinDesk).
5. **Privacy Policy** discloses the LLM data flow (see the privacy draft).
6. Have counsel confirm the **"publishing about my own Robinhood account"** framing for the masked model book.

## Sources
- [MarketXLS — Yahoo Finance API guide + licensed alternatives](https://marketxls.com/blog/yahoo-finance-api-ultimate-guide)
- [Yahoo Developer API Terms of Use](https://legal.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.html)
- [yfinance project (unofficial Yahoo endpoints)](https://github.com/ranaroussi/yfinance)
