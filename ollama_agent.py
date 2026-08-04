"""
ollama_agent.py — Local LLM integration via Ollama

Provides fast, free, offline AI analysis using locally-run models.
Inspired by Aureon's llama3.2:1b integration pattern.

Use cases inside Gesh:
  1. ollama_quick_analysis()   — fast portfolio commentary (no API cost)
  2. ollama_news_sentiment()   — batch news scoring (backup to Claude Haiku)
  3. ollama_second_opinion()   — contrarian review of Claude's analysis
  4. get_ollama_status()       — check availability + installed models

Requirements (one-time setup):
  1. Install Ollama:  https://ollama.ai
  2. Pull a model:    ollama pull llama3.2:3b   (fast, 2GB)
                      ollama pull llama3.2:1b   (ultra-fast, 1.3GB)
  3. Server starts automatically — no manual `ollama serve` needed

All functions fail gracefully if Ollama is not running.
Uses only stdlib (urllib) — zero extra dependencies.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any

# ─── Config ──────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL  = "http://localhost:11434"
DEFAULT_MODEL    = "llama3.2:3b"   # good balance of speed vs quality
FAST_MODEL       = "llama3.2:1b"   # use for high-volume sentiment scoring
TIMEOUT_GENERATE = 45              # seconds — generation timeout
TIMEOUT_CHECK    = 3               # seconds — availability check timeout
MAX_TOKENS       = 512             # response length cap


# ─── Availability check ───────────────────────────────────────────────────────

def is_ollama_available(model: str | None = None) -> bool:
    """
    Return True if Ollama server is reachable and (optionally) model is loaded.
    Fast check — always called before any generate request.
    """
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_CHECK) as resp:
            if resp.status != 200:
                return False
            if model is None:
                return True
            data   = json.loads(resp.read())
            loaded = {m["name"].split(":")[0] for m in data.get("models", [])}
            return model.split(":")[0] in loaded
    except Exception:
        return False


def get_ollama_status() -> dict[str, Any]:
    """
    Return full Ollama status including installed models and setup guidance.

    Returns
    -------
    {
      running: bool,
      models:  [{name, size_gb, modified}],
      default_model, fast_model,
      ready_for_gesh: bool,    # True if at least one llama3.2 variant present
      setup_guide (if not running)
    }
    """
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/tags",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_CHECK) as resp:
            data   = json.loads(resp.read())
            models = [
                {
                    "name":     m["name"],
                    "size_gb":  round(m.get("size", 0) / 1_000_000_000, 1),
                    "modified": m.get("modified_at", "")[:10],
                }
                for m in data.get("models", [])
            ]
            names = {m["name"].split(":")[0] for m in models}
            ready = bool(names & {"llama3.2", "llama3", "mistral", "phi3", "qwen2.5"})
            return {
                "running":       True,
                "models":        models,
                "default_model": DEFAULT_MODEL,
                "fast_model":    FAST_MODEL,
                "ready_for_gesh": ready,
                "as_of":         datetime.now(timezone.utc).isoformat() + "Z",
            }
    except Exception:
        return {
            "running":      False,
            "models":       [],
            "ready_for_gesh": False,
            "setup_guide": {
                "step1": "Install Ollama from https://ollama.ai",
                "step2": "Run: ollama pull llama3.2:3b",
                "step3": "Ollama starts automatically — then restart Gesh server",
            },
        }


# ─── Core generate ────────────────────────────────────────────────────────────

def _generate(
    prompt: str,
    model: str = DEFAULT_MODEL,
    system: str = "",
    temperature: float = 0.3,
) -> str:
    """
    Send a prompt to Ollama and return the generated text.
    Raises ConnectionError if Ollama is unavailable.
    """
    payload: dict[str, Any] = {
        "model":  model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature":  temperature,
            "num_predict":  MAX_TOKENS,
            "stop":         ["```", "Note:", "Disclaimer:"],
        },
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_GENERATE) as resp:
            result = json.loads(resp.read())
            return result.get("response", "").strip()
    except urllib.error.URLError as exc:
        raise ConnectionError(f"Ollama unreachable: {exc}") from exc


# ─── Portfolio quick analysis ─────────────────────────────────────────────────

def ollama_quick_analysis(portfolio_summary: str) -> dict[str, Any]:
    """
    Fast local AI commentary on portfolio.
    Returns 3 concise bullet points — no API cost.

    Parameters
    ----------
    portfolio_summary : str
        Plain-text summary of holdings, equity, top movers, etc.
    """
    if not is_ollama_available(DEFAULT_MODEL):
        return {"available": False, "error": "Ollama not running or model not loaded"}

    system = (
        "You are a concise portfolio analyst. Give direct, actionable insights. "
        "No hedging language or disclaimers. Use plain text — no markdown."
    )
    prompt = (
        f"Analyze this portfolio briefly:\n\n{portfolio_summary}\n\n"
        "Respond with exactly 3 lines:\n"
        "1. Overall positioning (1 sentence)\n"
        "2. Biggest risk (1 sentence)\n"
        "3. Best action to take today (1 sentence)"
    )

    try:
        response = _generate(prompt, DEFAULT_MODEL, system)
        return {
            "available": True,
            "model":     DEFAULT_MODEL,
            "analysis":  response,
            "type":      "quick_commentary",
            "as_of":     datetime.now(timezone.utc).isoformat() + "Z",
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ─── News sentiment scoring ───────────────────────────────────────────────────

def ollama_news_sentiment(
    symbol: str,
    headlines: list[str],
) -> dict[str, Any]:
    """
    Fast batch sentiment scoring for a list of news headlines.
    Uses the fast model (1b) for speed.

    Returns score (-100 to +100), signal, and brief reason.
    """
    if not is_ollama_available(FAST_MODEL):
        # Try fallback to default model
        if not is_ollama_available(DEFAULT_MODEL):
            return {"available": False, "error": "Ollama not running"}
        model = DEFAULT_MODEL
    else:
        model = FAST_MODEL

    if not headlines:
        # No headlines is not neutral sentiment — do not fabricate score 0 /
        # NEUTRAL. Report unavailable so callers can ABSTAIN rather than vote.
        return {"available": False, "symbol": symbol, "reason": "no headlines"}

    headlines_text = "\n".join(f"- {h}" for h in headlines[:10])  # cap at 10

    prompt = (
        f"Rate the overall investor sentiment for {symbol} based on these headlines:\n"
        f"{headlines_text}\n\n"
        'Reply ONLY with this JSON (no other text): '
        '{"score": <integer -100 to 100>, "signal": "<BULLISH|NEUTRAL|BEARISH>", '
        '"reason": "<max 8 words>"}'
    )

    try:
        response = _generate(prompt, model, temperature=0.1)
        # Robustly extract JSON
        start = response.find("{")
        end   = response.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(response[start:end])
            return {
                "available": True,
                "model":     model,
                "symbol":    symbol,
                "score":     int(parsed.get("score", 0)),
                "signal":    str(parsed.get("signal", "NEUTRAL")).upper(),
                "reason":    str(parsed.get("reason", "")),
                "headlines_scored": len(headlines),
                "as_of":     datetime.now(timezone.utc).isoformat() + "Z",
            }
        # JSON parse failure is not a NEUTRAL reading — surface it as unavailable
        # (with the raw response for debugging) instead of a fabricated score 0.
        return {
            "available": False,
            "model":     model,
            "symbol":    symbol,
            "reason":    "parse_error",
            "raw":       response[:200],
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ─── Second opinion (contrarian review) ──────────────────────────────────────

def ollama_second_opinion(
    symbol: str,
    claude_analysis: str,
    verdict: str = "",
) -> dict[str, Any]:
    """
    Get a contrarian second opinion from local LLM on Claude's verdict.
    Surfaces missed risks or counter-arguments.

    Best used after Claude gives a STRONG BUY to stress-test the thesis.
    """
    if not is_ollama_available(DEFAULT_MODEL):
        return {"available": False, "error": "Ollama not running"}

    system = (
        "You are a skeptical risk analyst. Your job is to find what a bullish "
        "analyst might miss. Be concise, specific, and honest. No disclaimers."
    )
    verdict_line = f"Claude's verdict: {verdict}\n\n" if verdict else ""
    prompt = (
        f"This is an AI analysis of {symbol}:\n\n"
        f"{verdict_line}"
        f"{claude_analysis[:800]}\n\n"
        "What are 2 specific risks or counterarguments this analysis may have missed? "
        "Be direct and brief."
    )

    try:
        response = _generate(prompt, DEFAULT_MODEL, system)
        return {
            "available":      True,
            "model":          DEFAULT_MODEL,
            "symbol":         symbol,
            "second_opinion": response,
            "type":           "contrarian_review",
            "as_of":          datetime.now(timezone.utc).isoformat() + "Z",
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


# ─── Portfolio-level Ollama analysis ─────────────────────────────────────────

def ollama_portfolio_summary(holdings: list[dict], equity_summary: dict) -> dict[str, Any]:
    """
    Generate a full portfolio summary using the local LLM.
    Input: raw holdings list + equity summary dict from data_fetcher.
    """
    if not is_ollama_available(DEFAULT_MODEL):
        return {"available": False, "error": "Ollama not running"}

    total = equity_summary.get("portfolio_equity", 0)
    top_holdings = sorted(holdings, key=lambda h: h.get("equity", 0), reverse=True)[:6]
    holdings_text = "\n".join(
        f"  {h['symbol']}: {h.get('portfolio_percentage', 0):.1f}% alloc, "
        f"{h.get('percent_change', 0):+.1f}% today"
        for h in top_holdings
    )

    portfolio_summary = (
        f"Total equity: ${total:,.0f}\n"
        f"Top holdings:\n{holdings_text}"
    )

    return ollama_quick_analysis(portfolio_summary)
