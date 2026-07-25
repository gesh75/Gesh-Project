package com.gesh.financial.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import androidx.lifecycle.viewModelScope
import com.gesh.financial.model.DeepAnalysis
import com.gesh.financial.model.PortfolioData
import com.gesh.financial.network.RetrofitClient
import kotlinx.coroutines.launch

sealed class UiState<out T> {
    object Idle : UiState<Nothing>()
    object Loading : UiState<Nothing>()
    data class Success<T>(val data: T) : UiState<T>()
    data class Error(val message: String) : UiState<Nothing>()
}

class PortfolioViewModel(app: Application) : AndroidViewModel(app) {

    private val _portfolio = MutableLiveData<UiState<PortfolioData>>(UiState.Idle)
    val portfolio: LiveData<UiState<PortfolioData>> = _portfolio

    private val _analysis = MutableLiveData<UiState<DeepAnalysis>>(UiState.Idle)
    val analysis: LiveData<UiState<DeepAnalysis>> = _analysis

    private val _analysisModel = MutableLiveData<String>("")
    val analysisModel: LiveData<String> = _analysisModel

    private val ownerPortfolioUnavailable =
        "Owner portfolio data is unavailable in the customer app."

    init {
        _portfolio.value = UiState.Error(ownerPortfolioUnavailable)
    }

    fun loadPortfolio() {
        _portfolio.value = UiState.Error(ownerPortfolioUnavailable)
    }

    fun runDeepAnalysis() {
        if (_analysis.value is UiState.Loading) return
        viewModelScope.launch {
            _analysis.value = UiState.Loading
            try {
                val resp = RetrofitClient.getApiService(getApplication()).getDeepAnalysis()
                if (resp.isSuccessful && resp.body() != null) {
                    _analysisModel.value = resp.body()!!.model
                    _analysis.value = UiState.Success(resp.body()!!.analysis)
                } else {
                    _analysis.value = UiState.Error("Analysis failed: ${resp.code()}")
                }
            } catch (e: Exception) {
                _analysis.value = UiState.Error(e.message ?: "Connection failed")
            }
        }
    }

    fun invalidateAndReload() {
        RetrofitClient.saveBaseUrl(getApplication(), RetrofitClient.getBaseUrl(getApplication()))
        loadPortfolio()
    }
}
