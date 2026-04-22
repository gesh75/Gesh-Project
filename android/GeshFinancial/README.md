# Gesh Financial — Android App

Native Kotlin Android app that connects to your local Python server.

## Requirements
- Android Studio Hedgehog (2023.1.1) or newer
- Android phone running API 26+ (Android 8.0+)
- Python server running on same WiFi network

## Setup
1. Open this folder in Android Studio
2. Let Gradle sync complete
3. Go to **Settings tab** in the app
4. Enter your PC's local IP: `http://192.168.x.x:9000`
5. Tap **Save & Reconnect**

## Start your Python server
```bash
cd gesh-project
source venv/bin/activate
python server.py
```
Find your PC IP: `ipconfig` (Windows) or `ifconfig` (Mac/Linux)

## Features
- **Portfolio tab** — live positions, allocation donut chart, watchlist (auto-refreshes every 30s)
- **Analysis tab** — AI deep analysis with per-stock snowflake radar charts, fair value bars, risk/reward, predictions
- **Settings tab** — configure server URL
