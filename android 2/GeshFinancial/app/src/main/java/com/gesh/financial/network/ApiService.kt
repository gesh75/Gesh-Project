package com.gesh.financial.network

import com.gesh.financial.model.DeepAnalysisResponse
import com.gesh.financial.model.PortfolioResponse
import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.POST

interface ApiService {
    @GET("api/portfolio")
    suspend fun getPortfolio(): Response<PortfolioResponse>

    @POST("api/deep-analysis")
    suspend fun getDeepAnalysis(): Response<DeepAnalysisResponse>
}
