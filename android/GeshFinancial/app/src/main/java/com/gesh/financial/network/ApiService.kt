package com.gesh.financial.network

import com.gesh.financial.model.DeepAnalysisResponse
import retrofit2.Response
import retrofit2.http.POST

interface ApiService {
    @POST("api/deep-analysis")
    suspend fun getDeepAnalysis(): Response<DeepAnalysisResponse>
}
