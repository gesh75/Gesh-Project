package com.gesh.financial.model

import com.google.gson.annotations.SerializedName

// ─── Portfolio ────────────────────────────────────────────────────────────────
data class PortfolioResponse(val status: String, val data: PortfolioData)

data class PortfolioData(
    @SerializedName("equity_summary") val equitySummary: EquitySummary,
    val holdings: List<Holding>,
    val watchlist: List<WatchlistItem>
)

data class EquitySummary(
    @SerializedName("portfolio_equity") val portfolioEquity: Double = 0.0,
    @SerializedName("day_change") val dayChange: Double = 0.0,
    @SerializedName("day_change_pct") val dayChangePct: Double = 0.0,
    val cash: Double = 0.0,
    @SerializedName("buying_power") val buyingPower: Double = 0.0
)

data class Holding(
    val symbol: String,
    val quantity: Double = 0.0,
    @SerializedName("average_buy_price") val avgBuyPrice: Double = 0.0,
    @SerializedName("current_price") val currentPrice: Double = 0.0,
    val equity: Double = 0.0,
    @SerializedName("percent_change") val percentChange: Double = 0.0,
    @SerializedName("portfolio_percentage") val portfolioPct: Double = 0.0
)

data class WatchlistItem(
    val symbol: String,
    @SerializedName("last_trade_price") val lastPrice: Double = 0.0,
    @SerializedName("bid_price") val bidPrice: Double = 0.0,
    @SerializedName("ask_price") val askPrice: Double = 0.0,
    @SerializedName("change_pct") val changePct: Double = 0.0
)

// ─── Deep Analysis ────────────────────────────────────────────────────────────
data class DeepAnalysisResponse(val status: String, val model: String, val analysis: DeepAnalysis)

data class DeepAnalysis(
    @SerializedName("portfolio_score") val portfolioScore: Int = 0,
    @SerializedName("portfolio_verdict") val portfolioVerdict: String = "",
    @SerializedName("portfolio_type") val portfolioType: String = "",
    @SerializedName("risk_score") val riskScore: Int = 5,
    @SerializedName("market_context") val marketContext: String = "",
    @SerializedName("diversification_score") val diversificationScore: Int = 0,
    @SerializedName("holdings_analysis") val holdingsAnalysis: List<HoldingAnalysis> = emptyList(),
    @SerializedName("portfolio_risks") val portfolioRisks: List<RiskItem> = emptyList(),
    @SerializedName("portfolio_recommendations") val recommendations: List<Recommendation> = emptyList(),
    @SerializedName("sector_breakdown") val sectorBreakdown: List<SectorItem> = emptyList()
)

data class HoldingAnalysis(
    val symbol: String = "",
    @SerializedName("company_type") val companyType: String = "",
    val scores: Scores = Scores(),
    @SerializedName("snowflake_summary") val snowflakeSummary: String = "",
    @SerializedName("fair_value") val fairValue: FairValue? = null,
    val risks: List<RiskItem> = emptyList(),
    val rewards: List<RewardItem> = emptyList(),
    val prediction: Prediction = Prediction(),
    val verdict: String = "HOLD",
    @SerializedName("analyst_note") val analystNote: String = ""
)

data class Scores(
    val valuation: Int = 0, val growth: Int = 0,
    val health: Int = 0, val momentum: Int = 0, val dividend: Int = 0
)

data class FairValue(
    val estimate: Double = 0.0, val current: Double = 0.0,
    val status: String = "fair",
    @SerializedName("pct_diff") val pctDiff: Double = 0.0
)

data class RiskItem(val text: String = "", val severity: String = "medium")
data class RewardItem(val text: String = "")

data class Prediction(
    @SerializedName("3m") val threeMonth: String = "NEUTRAL",
    @SerializedName("12m") val twelveMonth: String = "NEUTRAL",
    val rationale: String = ""
)

data class Recommendation(
    val action: String = "", val priority: String = "medium", val rationale: String = ""
)

data class SectorItem(
    val sector: String = "",
    @SerializedName("allocation_pct") val allocationPct: Double = 0.0,
    val assessment: String = "neutral"
)
