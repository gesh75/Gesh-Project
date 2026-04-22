package com.gesh.financial.ui.analysis

import android.graphics.Color
import android.os.Bundle
import android.view.*
import android.widget.*
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import com.github.mikephil.charting.charts.RadarChart
import com.github.mikephil.charting.components.Legend
import com.github.mikephil.charting.data.*
import com.github.mikephil.charting.formatter.IndexAxisValueFormatter
import com.gesh.financial.R
import com.gesh.financial.databinding.FragmentAnalysisBinding
import com.gesh.financial.model.*
import com.gesh.financial.viewmodel.PortfolioViewModel
import com.gesh.financial.viewmodel.UiState
import java.text.NumberFormat
import java.util.Locale

class AnalysisFragment : Fragment() {
    private var _b: FragmentAnalysisBinding? = null
    private val b get() = _b!!
    private val vm: PortfolioViewModel by activityViewModels()
    private val usd = NumberFormat.getCurrencyInstance(Locale.US)

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?) =
        FragmentAnalysisBinding.inflate(inflater, container, false).also { _b = it }.root

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        b.btnRunAnalysis.setOnClickListener { vm.runDeepAnalysis() }
        observeViewModel()
    }

    private fun observeViewModel() {
        vm.analysis.observe(viewLifecycleOwner) { state ->
            when (state) {
                is UiState.Loading -> {
                    b.btnRunAnalysis.isEnabled = false
                    b.btnRunAnalysis.text = "Analyzing with AI…"
                    b.progressBar.visibility = View.VISIBLE
                    b.tvStatusMsg.text = "Running deep analysis — this may take 30–60 seconds"
                    b.tvStatusMsg.visibility = View.VISIBLE
                    b.scrollContent.visibility = View.GONE
                }
                is UiState.Success -> {
                    b.btnRunAnalysis.isEnabled = true
                    b.btnRunAnalysis.text = "↻ Re-analyze"
                    b.progressBar.visibility = View.GONE
                    b.tvStatusMsg.visibility = View.GONE
                    b.scrollContent.visibility = View.VISIBLE
                    renderAnalysis(state.data)
                    vm.analysisModel.value?.let { b.tvModelBadge.text = it }
                }
                is UiState.Error -> {
                    b.btnRunAnalysis.isEnabled = true
                    b.btnRunAnalysis.text = "▶ Run Deep Analysis"
                    b.progressBar.visibility = View.GONE
                    b.tvStatusMsg.text = "Error: ${state.message}"
                    b.tvStatusMsg.setTextColor(ContextCompat.getColor(requireContext(), R.color.red))
                    b.tvStatusMsg.visibility = View.VISIBLE
                }
                else -> {
                    b.progressBar.visibility = View.GONE
                }
            }
        }
    }

    private fun renderAnalysis(a: DeepAnalysis) {
        // ── Portfolio score gauge ──
        b.tvPortfolioScore.text = a.portfolioScore.toString()
        b.tvPortfolioScore.setTextColor(scoreColor(a.portfolioScore))
        b.tvPortfolioVerdict.text = a.portfolioVerdict
        b.tvPortfolioType.text = a.portfolioType
        b.tvRiskScore.text = "Risk: ${a.riskScore}/10"
        b.tvRiskScore.setTextColor(if (a.riskScore >= 7) color(R.color.red)
            else if (a.riskScore >= 4) color(R.color.yellow) else color(R.color.green))
        b.tvDiversification.text = "Diversification: ${a.diversificationScore}/100"
        b.tvMarketContext.text = a.marketContext

        // Score progress bar
        b.scoreProgressBar.progress = a.portfolioScore
        b.scoreProgressBar.progressTintList = android.content.res.ColorStateList.valueOf(scoreColor(a.portfolioScore))

        // ── Portfolio risks ──
        b.llPortfolioRisks.removeAllViews()
        a.portfolioRisks.forEach { risk -> b.llPortfolioRisks.addView(buildRiskChip(risk)) }

        // ── Recommendations ──
        b.llRecommendations.removeAllViews()
        a.recommendations.forEach { rec -> b.llRecommendations.addView(buildRecView(rec)) }

        // ── Stock cards ──
        b.llStockCards.removeAllViews()
        a.holdingsAnalysis.forEach { h -> b.llStockCards.addView(buildStockCard(h)) }
    }

    private fun buildRiskChip(risk: RiskItem): View {
        val tv = TextView(requireContext()).apply {
            text = "● ${risk.text}"
            setTextColor(when (risk.severity) {
                "critical" -> color(R.color.red)
                "high" -> color(R.color.orange)
                "medium" -> color(R.color.yellow)
                else -> color(R.color.accent)
            })
            textSize = 12f
            setPadding(0, 6, 0, 6)
        }
        return tv
    }

    private fun buildRecView(rec: Recommendation): View {
        val ll = LinearLayout(requireContext()).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(8), dp(12), dp(8))
            setBackgroundColor(color(R.color.card_hover))
            val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            lp.bottomMargin = dp(6)
            layoutParams = lp
        }
        ll.addView(TextView(requireContext()).apply {
            text = "▸ ${rec.action}"
            setTextColor(color(R.color.text))
            textSize = 12f
        })
        if (rec.rationale.isNotEmpty()) {
            ll.addView(TextView(requireContext()).apply {
                text = rec.rationale
                setTextColor(color(R.color.muted))
                textSize = 11f
                setPadding(0, 3, 0, 0)
            })
        }
        return ll
    }

    private fun buildStockCard(h: HoldingAnalysis): View {
        val ctx = requireContext()
        val card = com.google.android.material.card.MaterialCardView(ctx).apply {
            radius = dp(8).toFloat()
            strokeWidth = dp(1)
            strokeColor = color(R.color.border)
            setCardBackgroundColor(color(R.color.card))
            val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            lp.bottomMargin = dp(14)
            layoutParams = lp
        }

        val root = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL }

        // Header
        val header = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(dp(14), dp(12), dp(14), dp(12))
        }
        header.addView(LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }.also { vg ->
            vg.addView(TextView(ctx).apply { text = h.symbol; setTextColor(color(R.color.accent)); textSize = 16f; setTypeface(null, android.graphics.Typeface.BOLD) })
            vg.addView(TextView(ctx).apply { text = h.companyType; setTextColor(color(R.color.muted)); textSize = 11f })
        })
        header.addView(buildVerdictBadge(h.verdict))
        root.addView(header)

        // Divider
        root.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(1))
            setBackgroundColor(color(R.color.border))
        })

        // Body: radar + bars side by side
        val body = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL }

        // Radar chart
        val radarWrap = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(dp(170), LinearLayout.LayoutParams.WRAP_CONTENT)
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(8), dp(10), dp(4), dp(8))
        }
        val radar = buildRadarChart(h.scores)
        radarWrap.addView(radar)
        radarWrap.addView(TextView(ctx).apply {
            text = h.snowflakeSummary
            setTextColor(color(R.color.muted))
            textSize = 10f
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, dp(4), 0, 0)
        })
        body.addView(radarWrap)

        // Right side: score bars + fair value
        val rightSide = LinearLayout(ctx).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
            setPadding(dp(8), dp(12), dp(14), dp(8))
        }
        // Score bars
        listOf("Val" to h.scores.valuation, "Growth" to h.scores.growth,
               "Health" to h.scores.health, "Momt" to h.scores.momentum,
               "Div" to h.scores.dividend).forEach { (lbl, v) ->
            rightSide.addView(buildScoreBar(lbl, v))
        }
        // Fair value
        h.fairValue?.let { fv ->
            rightSide.addView(View(ctx).apply {
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(1))
                setBackgroundColor(color(R.color.border))
                (layoutParams as LinearLayout.LayoutParams).setMargins(0, dp(8), 0, dp(6))
            })
            rightSide.addView(TextView(ctx).apply { text = "Fair Value"; setTextColor(color(R.color.muted)); textSize = 9f; isAllCaps = true })
            rightSide.addView(buildFairValueRow("Current", fv.current, color(R.color.accent)))
            val fvColor = if (fv.pctDiff > 0) color(R.color.green) else color(R.color.red)
            rightSide.addView(buildFairValueRow("Est. Fair", fv.estimate, fvColor))
            val pctAbs = Math.abs(fv.pctDiff)
            val fvText = when (fv.status) {
                "undervalued" -> "+%.1f%% Undervalued".format(pctAbs)
                "overvalued" -> "-%.1f%% Overvalued".format(pctAbs)
                else -> "Fairly Valued"
            }
            rightSide.addView(TextView(ctx).apply {
                text = fvText
                setTextColor(when (fv.status) { "undervalued" -> color(R.color.green); "overvalued" -> color(R.color.red); else -> color(R.color.accent) })
                textSize = 10f
                setTypeface(null, android.graphics.Typeface.BOLD)
                setPadding(0, dp(3), 0, 0)
            })
        }
        body.addView(rightSide)
        root.addView(body)

        // Divider
        root.addView(View(ctx).apply { layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(1)); setBackgroundColor(color(R.color.border)) })

        // Risks & Rewards
        val rrSection = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), dp(10), dp(14), dp(6)) }
        rrSection.addView(TextView(ctx).apply { text = "RISKS & REWARDS"; setTextColor(color(R.color.muted)); textSize = 9f; isAllCaps = true; setPadding(0, 0, 0, dp(5)) })
        h.risks.take(3).forEach { r ->
            rrSection.addView(TextView(ctx).apply {
                text = "● ${r.text}"
                setTextColor(when (r.severity) { "critical" -> color(R.color.red); "high" -> color(R.color.orange); "medium" -> color(R.color.yellow); else -> color(R.color.accent) })
                textSize = 11f; setPadding(0, 2, 0, 2)
            })
        }
        h.rewards.take(2).forEach { r ->
            rrSection.addView(TextView(ctx).apply { text = "★ ${r.text}"; setTextColor(color(R.color.yellow)); textSize = 11f; setPadding(0, 2, 0, 2) })
        }
        root.addView(rrSection)

        // Footer: predictions
        root.addView(View(ctx).apply { layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(1)); setBackgroundColor(color(R.color.border)) })
        val footer = LinearLayout(ctx).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(14), dp(10), dp(14), dp(12)) }
        val predRow = LinearLayout(ctx).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        predRow.addView(TextView(ctx).apply { text = "3M"; setTextColor(color(R.color.muted)); textSize = 10f })
        predRow.addView(buildPredBadge(h.prediction.threeMonth).also { (it.layoutParams as LinearLayout.LayoutParams).setMargins(dp(6), 0, dp(12), 0) })
        predRow.addView(TextView(ctx).apply { text = "12M"; setTextColor(color(R.color.muted)); textSize = 10f })
        predRow.addView(buildPredBadge(h.prediction.twelveMonth).also { (it.layoutParams as LinearLayout.LayoutParams).setMargins(dp(6), 0, 0, 0) })
        footer.addView(predRow)
        if (h.prediction.rationale.isNotEmpty()) {
            footer.addView(TextView(ctx).apply { text = h.prediction.rationale; setTextColor(color(R.color.muted)); textSize = 11f; setPadding(0, dp(5), 0, 0) })
        }
        if (h.analystNote.isNotEmpty()) {
            val noteCard = LinearLayout(ctx).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(10), dp(7), dp(10), dp(7))
                setBackgroundColor(0x0A00D4FF)
                val lp = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                lp.topMargin = dp(8)
                layoutParams = lp
            }
            noteCard.addView(TextView(ctx).apply { text = h.analystNote; setTextColor(color(R.color.muted)); textSize = 11f; lineHeight = (textSize * 1.5).toInt() })
            footer.addView(noteCard)
        }
        root.addView(footer)

        card.addView(root)
        return card
    }

    private fun buildRadarChart(scores: Scores): RadarChart {
        val ctx = requireContext()
        val chart = RadarChart(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(dp(150), dp(150))
            description.isEnabled = false
            webColor = color(R.color.border)
            webColorInner = color(R.color.border)
            webAlpha = 100
            setTouchEnabled(false)
            legend.isEnabled = false
        }
        chart.xAxis.apply {
            valueFormatter = IndexAxisValueFormatter(arrayOf("Value", "Growth", "Health", "Momt", "Div"))
            textColor = color(R.color.muted)
            textSize = 8f
        }
        chart.yAxis.apply {
            axisMinimum = 0f; axisMaximum = 100f; setDrawLabels(false); setLabelCount(5, true)
        }
        val vals = listOf(scores.valuation, scores.growth, scores.health, scores.momentum, scores.dividend)
        val avg = vals.average()
        val lineColor = scoreColor(avg.toInt())
        val fillColor = when {
            avg >= 67 -> 0x1500E676.toInt()
            avg >= 34 -> 0x1500D4FF.toInt()
            else -> 0x15FF4444.toInt()
        }
        val entries = vals.map { RadarEntry(it.toFloat()) }
        val ds = RadarDataSet(entries, "").apply {
            color = lineColor; fillColor = fillColor
            setDrawFilled(true); lineWidth = 2f
            setDrawHighlightCircleEnabled(false)
            setDrawHighlightIndicators(false)
        }
        chart.data = RadarData(ds)
        return chart
    }

    private fun buildScoreBar(label: String, value: Int): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            (layoutParams as LinearLayout.LayoutParams).bottomMargin = dp(4)
        }
        row.addView(TextView(ctx).apply {
            text = label; setTextColor(color(R.color.muted)); textSize = 9f; isAllCaps = true
            layoutParams = LinearLayout.LayoutParams(dp(50), LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        val track = android.widget.ProgressBar(ctx, null, android.R.attr.progressBarStyleHorizontal).apply {
            layoutParams = LinearLayout.LayoutParams(0, dp(5), 1f)
            max = 100; progress = value
            progressTintList = android.content.res.ColorStateList.valueOf(scoreColor(value))
            progressBackgroundTintList = android.content.res.ColorStateList.valueOf(color(R.color.border))
        }
        row.addView(track)
        row.addView(TextView(ctx).apply {
            text = value.toString(); setTextColor(scoreColor(value)); textSize = 10f
            setTypeface(null, android.graphics.Typeface.BOLD)
            gravity = Gravity.END
            layoutParams = LinearLayout.LayoutParams(dp(24), LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        return row
    }

    private fun buildFairValueRow(label: String, price: Double, barColor: Int): View {
        val ctx = requireContext()
        val row = LinearLayout(ctx).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
            (layoutParams as LinearLayout.LayoutParams).bottomMargin = dp(3)
        }
        row.addView(TextView(ctx).apply {
            text = label; setTextColor(color(R.color.muted)); textSize = 10f
            layoutParams = LinearLayout.LayoutParams(dp(52), LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        row.addView(View(ctx).apply {
            layoutParams = LinearLayout.LayoutParams(0, dp(5), 1f)
            setBackgroundColor(barColor)
        })
        row.addView(TextView(ctx).apply {
            text = usd.format(price); setTextColor(color(R.color.text)); textSize = 10f
            setTypeface(null, android.graphics.Typeface.BOLD)
            gravity = Gravity.END
            setPadding(dp(4), 0, 0, 0)
            layoutParams = LinearLayout.LayoutParams(dp(56), LinearLayout.LayoutParams.WRAP_CONTENT)
        })
        return row
    }

    private fun buildVerdictBadge(verdict: String): TextView {
        return TextView(requireContext()).apply {
            text = verdict
            textSize = 10f
            setTypeface(null, android.graphics.Typeface.BOLD)
            setPadding(dp(8), dp(4), dp(8), dp(4))
            isAllCaps = true
            when (verdict) {
                "BUY" -> { setTextColor(color(R.color.green)); setBackgroundColor(0x1400E676.toInt()) }
                "HOLD" -> { setTextColor(color(R.color.yellow)); setBackgroundColor(0x14FFD700.toInt()) }
                "SELL" -> { setTextColor(color(R.color.orange)); setBackgroundColor(0x14FF8C42.toInt()) }
                "AVOID" -> { setTextColor(color(R.color.red)); setBackgroundColor(0x14FF4444.toInt()) }
                else -> { setTextColor(color(R.color.muted)); setBackgroundColor(Color.TRANSPARENT) }
            }
        }
    }

    private fun buildPredBadge(pred: String): TextView {
        return TextView(requireContext()).apply {
            text = pred; textSize = 10f
            setTypeface(null, android.graphics.Typeface.BOLD)
            setPadding(dp(8), dp(3), dp(8), dp(3))
            isAllCaps = true
            when (pred) {
                "BULLISH" -> { setTextColor(color(R.color.green)); setBackgroundColor(0x1400E676.toInt()) }
                "BEARISH" -> { setTextColor(color(R.color.red)); setBackgroundColor(0x14FF4444.toInt()) }
                else -> { setTextColor(color(R.color.muted)); setBackgroundColor(0x14FFFFFF.toInt()) }
            }
        }
    }

    private fun scoreColor(v: Int) = when {
        v >= 67 -> color(R.color.green)
        v >= 34 -> color(R.color.yellow)
        else -> color(R.color.red)
    }

    private fun color(id: Int) = ContextCompat.getColor(requireContext(), id)
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    override fun onDestroyView() { super.onDestroyView(); _b = null }
}
