package com.gesh.financial.ui.portfolio

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.gesh.financial.R
import com.gesh.financial.databinding.ItemWatchlistBinding
import com.gesh.financial.model.WatchlistItem
import java.text.NumberFormat
import java.util.Locale

class WatchlistAdapter : ListAdapter<WatchlistItem, WatchlistAdapter.VH>(DIFF) {
    private val usd = NumberFormat.getCurrencyInstance(Locale.US)

    inner class VH(val b: ItemWatchlistBinding) : RecyclerView.ViewHolder(b.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int) =
        VH(ItemWatchlistBinding.inflate(LayoutInflater.from(parent.context), parent, false))

    override fun onBindViewHolder(h: VH, pos: Int) {
        val item = getItem(pos)
        h.b.apply {
            tvSymbol.text = item.symbol
            tvLast.text = usd.format(item.lastPrice)
            tvBid.text = usd.format(item.bidPrice)
            tvAsk.text = usd.format(item.askPrice)
            val pct = item.changePct
            tvChange.text = "%+.2f%%".format(pct)
            tvChange.setTextColor(root.context.getColor(if (pct >= 0) R.color.green else R.color.red))
        }
    }

    companion object {
        val DIFF = object : DiffUtil.ItemCallback<WatchlistItem>() {
            override fun areItemsTheSame(a: WatchlistItem, b: WatchlistItem) = a.symbol == b.symbol
            override fun areContentsTheSame(a: WatchlistItem, b: WatchlistItem) = a == b
        }
    }
}
