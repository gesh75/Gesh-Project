package com.gesh.financial.ui.settings

import android.os.Bundle
import android.view.*
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.fragment.app.activityViewModels
import com.gesh.financial.databinding.FragmentSettingsBinding
import com.gesh.financial.network.RetrofitClient
import com.gesh.financial.viewmodel.PortfolioViewModel

class SettingsFragment : Fragment() {
    private var _b: FragmentSettingsBinding? = null
    private val b get() = _b!!
    private val vm: PortfolioViewModel by activityViewModels()

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?) =
        FragmentSettingsBinding.inflate(inflater, container, false).also { _b = it }.root

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val currentUrl = RetrofitClient.getBaseUrl(requireContext())
        b.etServerUrl.setText(currentUrl)
        b.tvCurrentUrl.text = "Current: $currentUrl"

        b.btnSave.setOnClickListener {
            val url = b.etServerUrl.text.toString().trim()
            if (url.isEmpty()) { Toast.makeText(requireContext(), "URL cannot be empty", Toast.LENGTH_SHORT).show(); return@setOnClickListener }
            val normalized = if (url.startsWith("http")) url else "http://$url"
            RetrofitClient.saveBaseUrl(requireContext(), normalized)
            b.tvCurrentUrl.text = "Current: $normalized"
            vm.invalidateAndReload()
            Toast.makeText(requireContext(), "✓ Saved — reconnecting...", Toast.LENGTH_SHORT).show()
        }

        b.btnTestConnection.setOnClickListener {
            b.tvTestResult.text = "Testing..."
            vm.loadPortfolio()
            vm.portfolio.observe(viewLifecycleOwner) { state ->
                when (state) {
                    is com.gesh.financial.viewmodel.UiState.Success -> b.tvTestResult.text = "✓ Connected successfully"
                    is com.gesh.financial.viewmodel.UiState.Error -> b.tvTestResult.text = "✗ ${state.message}"
                    else -> {}
                }
            }
        }
    }

    override fun onDestroyView() { super.onDestroyView(); _b = null }
}
