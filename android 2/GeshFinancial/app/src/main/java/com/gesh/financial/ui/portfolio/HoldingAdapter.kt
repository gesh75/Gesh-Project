package com.gesh.financial.ui.portfolio

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.gesh.financial.R
import com.gesh.financial.databinding.ItemHoldingBinding
import com.gesh.financial.model.Holding
import java.text.NumberFormat
import java.util.Locale

class HoldingAdapter : ListAdapter<Holding, HoldingAdapter.VH>(DIFF) {
    private val usd = NumberFormat.getCurrencyInstance(Locale.US)

    inner class VH(val b: ItemHoldingBinding) : RecyclerView.ViewHolder(b.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        VH(ItemHoldingBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun onBindViewHolder(h: VH, pos: Int) {
        val item = getItem(pos)
        h.b.apply {
            tvSymbol.text = item.symbol
            tvQty.text = "%.4f".format(item.quantity)
            tvAvgCost.text = usd.format(item.avgBuyPrice)
            tvLastPrice.text = if (item.currentPrice > 0) usd.format(item.currentPrice) else "—"
            tvEquity.text = usd.format(item.equity)
            tvAlloc.text = "%.1f%%".format(item.portfolioPct)
            val pct = item.percentChange
            tvChange.text = "%+.2f%%".format(pct)
            tvChange.setTextColor(root.context.getColor(if (pct >= 0) R.color.green else R.color.red))
        }
    }

    companion object {
        val DIFF = object : DiffUtil.ItemCallback<Holding>() {
            override fun areItemsTheSame(a: Holding, b: Holding) = a.symbol == b.symbol
            override fun areContentsTheSame(a: Holding, b: Holding) = a == b
        }
    }
}
