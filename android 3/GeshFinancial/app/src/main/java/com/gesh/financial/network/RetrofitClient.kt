package com.gesh.financial.network

import android.content.Context
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object RetrofitClient {
    private const val PREF_NAME = "gesh_prefs"
    private const val KEY_SERVER_URL = "server_url"
    const val DEFAULT_URL = "http://10.0.0.113:9000"

    private var retrofit: Retrofit? = null
    private var currentBaseUrl: String = ""

    fun getBaseUrl(ctx: Context): String =
        ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .getString(KEY_SERVER_URL, DEFAULT_URL) ?: DEFAULT_URL

    fun saveBaseUrl(ctx: Context, url: String) {
        ctx.getSharedPreferences(PREF_NAME, Context.MODE_PRIVATE)
            .edit().putString(KEY_SERVER_URL, url).apply()
        retrofit = null // invalidate cache
    }

    fun getApiService(ctx: Context): ApiService {
        val url = getBaseUrl(ctx).trimEnd('/') + "/"
        if (retrofit == null || currentBaseUrl != url) {
            currentBaseUrl = url
            val logging = HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
            val client = OkHttpClient.Builder()
                .addInterceptor(logging)
                .connectTimeout(15, TimeUnit.SECONDS)
                .readTimeout(180, TimeUnit.SECONDS)
                .writeTimeout(30, TimeUnit.SECONDS)
                .build()
            retrofit = Retrofit.Builder()
                .baseUrl(url)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
        }
        return retrofit!!.create(ApiService::class.java)
    }
}
