package com.gesh.financial.ui.portfolio

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import androidx.recyclerview.widget.LinearLayoutManager
import com.github.mikephil.charting.data.PieData
import com.github.mikephil.charting.data.PieDataSet
import com.github.mikephil.charting.data.PieEntry
import com.github.mikephil.charting.formatter.PercentFormatter
import com.gesh.financial.R
import com.gesh.financial.databinding.FragmentPortfolioBinding
import com.gesh.financial.model.Holding
import com.gesh.financial.viewmodel.PortfolioViewModel
import com.gesh.financial.viewmodel.UiState
import java.text.NumberFormat
import java.util.Locale

class PortfolioFragment : Fragment() {
    private var _b: FragmentPortfolioBinding? = null
    private val b get() = _b!!
    private val vm: PortfolioViewModel by activityViewModels()
    private val holdingAdapter = HoldingAdapter()
    private val watchlistAdapter = WatchlistAdapter()
    private val usd = NumberFormat.getCurrencyInstance(Locale.US)

    private val CHART_COLORS = intArrayOf(
        0xFF00D4FF.toInt(), 0xFF00E676.toInt(), 0xFFFFD700.toInt(),
        0xFFFF6B6B.toInt(), 0xFFA78BFA.toInt(), 0xFFFB923C.toInt(),
        0xFF34D399.toInt(), 0xFFF472B6.toInt(), 0xFF60A5FA.toInt(), 0xFFFACC15.toInt()
    )

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?) =
        FragmentPortfolioBinding.inflate(inflater, container, false).also { _b = it }.root

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        setupRecyclers()
        setupChart()
        setupSwipeRefresh()
        observeViewModel()
    }

    private fun setupRecyclers() {
        b.rvHoldings.layoutManager = LinearLayoutManager(requireContext())
        b.rvHoldings.adapter = holdingAdapter
        b.rvHoldings.isNestedScrollingEnabled = false

        b.rvWatchlist.layoutManager = LinearLayoutManager(requireContext())
        b.rvWatchlist.adapter = watchlistAdapter
        b.rvWatchlist.isNestedScrollingEnabled = false
    }

    private fun setupChart() {
        b.pieChart.apply {
            setUsePercentValues(true)
            description.isEnabled = false
            isDrawHoleEnabled = true
            holeRadius = 60f
            transparentCircleRadius = 63f
            setHoleColor(0xFF0D0D1A.toInt())
            setTransparentCircleColor(0xFF0D0D1A.toInt())
            legend.apply {
                textColor = 0xFFE2E8F0.toInt()
                textSize = 10f
                isEnabled = true
            }
            setEntryLabelColor(0x00FFFFFF)
        }
    }

    private fun setupSwipeRefresh() {
        b.swipeRefresh.setColorSchemeColors(requireContext().getColor(R.color.accent))
        b.swipeRefresh.setProgressBackgroundColorSchemeColor(requireContext().getColor(R.color.card))
        b.swipeRefresh.setOnRefreshListener { vm.loadPortfolio() }
    }

    private fun observeViewModel() {
        vm.portfolio.observe(viewLifecycleOwner) { state ->
            when (state) {
                is UiState.Loading -> {
                    b.swipeRefresh.isRefreshing = true
                    b.tvConnectionStatus.text = "Connecting..."
                    b.tvConnectionStatus.setTextColor(requireContext().getColor(R.color.muted))
                }
                is UiState.Success -> {
                    b.swipeRefresh.isRefreshing = false
                    b.tvConnectionStatus.text = "● LIVE"
                    b.tvConnectionStatus.setTextColor(requireContext().getColor(R.color.green))
                    val data = state.data
                    val eq = data.equitySummary
                    // Stats
                    b.tvEquity.text = usd.format(eq.portfolioEquity)
                    val dcp = eq.dayChangePct
                    b.tvEquitySub.text = "%+.2f%% today".format(dcp)
                    b.tvEquitySub.setTextColor(requireContext().getColor(if (dcp >= 0) R.color.green else R.color.red))
                    b.tvDayChange.text = usd.format(eq.dayChange)
                    b.tvDayChange.setTextColor(requireContext().getColor(if (eq.dayChange >= 0) R.color.green else R.color.red))
                    b.tvCash.text = usd.format(eq.cash)
                    b.tvBuyingPower.text = usd.format(eq.buyingPower)
                    b.tvPositionCount.text = "${data.holdings.size} positions"
                    // Lists
                    holdingAdapter.submitList(data.holdings)
                    watchlistAdapter.submitList(data.watchlist)
                    // Chart
                    updatePieChart(data.holdings)
                }
                is UiState.Error -> {
                    b.swipeRefresh.isRefreshing = false
                    b.tvConnectionStatus.text = "● OFFLINE"
                    b.tvConnectionStatus.setTextColor(requireContext().getColor(R.color.red))
                    Toast.makeText(requireContext(), state.message, Toast.LENGTH_LONG).show()
                }
                else -> {}
            }
        }
    }

    private fun updatePieChart(holdings: List<Holding>) {
        if (holdings.isEmpty()) return
        val entries = holdings.mapIndexed { i, h -> PieEntry(h.portfolioPct.toFloat(), h.symbol) }
        val colors = holdings.mapIndexed { i, _ -> CHART_COLORS[i % CHART_COLORS.size] }.toMutableList()
        val ds = PieDataSet(entries, "").apply {
            this.colors = colors
            sliceSpace = 2f
            valueFormatter = PercentFormatter(b.pieChart)
            valueTextColor = 0xFF000000.toInt()
            valueTextSize = 9f
        }
        b.pieChart.data = PieData(ds)
        b.pieChart.invalidate()
    }

    override fun onDestroyView() { super.onDestroyView(); _b = null }
}
