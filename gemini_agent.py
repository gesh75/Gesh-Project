"""
gemini_agent.py — Google Gemini AI integration for GESH Financial

Provides multi-LLM capability using Georgi's Gemini subscription.
Acts as an intelligent complement to Claude:

  1. gemini_news_sentiment()     — batch news scoring via gemini-2.0-flash
                                   (fast + cheap, replaces claude-haiku-4-5 for bulk sentiment)
  2. gemini_second_opinion()     — full contrarian analysis via gemini-1.5-pro
                                   (independent reasoning, different training data than Claude)
  3. gemini_market_commentary()  — broad market outlook + macro narrative
  4. gemini_quick_analysis()     — fast portfolio summary (same role as ollama, but cloud quality)
  5. get_gemini_status()         — check API key and model availability

Model routing:
  • gemini-2.0-flash   → high-volume / latency-sensitive tasks (sentiment batch)
  • gemini-1.5-pro     → deep analysis, second opinions (matches Claude Sonnet quality)

Setup:
  1. Get your API key from https://aistudio.google.com/app/apikey
  2. Add to .env:  GEMINI_API_KEY=your_key_here
  3. Install SDK:  pip install google-generativeai --break-system-packages

Fallback chain for news sentiment:
  Claude Haiku → Gemini Flash (if GEMINI_API_KEY set) → Ollama (if running)
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

import llm_contracts

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────

GEMINI_FLASH_MODEL = "gemini-2.5-flash"   # Fast, cheap — news batch sentiment
GEMINI_PRO_MODEL   = "gemini-2.5-pro"     # Deep analysis — second opinions
GEMINI_FALLBACK    = "gemini-2.0-flash-lite"  # Lite fallback if quota issues

_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")


# ─── SDK lazy import ──────────────────────────────────────────────────────────

def _get_client(*, timeout_s: float | None = None):
    """
    Lazy-load google-genai SDK (new official SDK, replaces deprecated google-generativeai).
    Returns a configured genai.Client or raises on failure.
    Install: pip install google-genai --break-system-packages
    """
    if not _GEMINI_KEY:
        raise ValueError(
            "GEMINI_API_KEY not set. Add it to .env: GEMINI_API_KEY=your_key_here\n"
            "Get your key at: https://aistudio.google.com/app/apikey"
        )
    try:
        from google import genai
        from google.genai import types as genai_types

        kwargs: dict[str, Any] = {"api_key": _GEMINI_KEY}
        if timeout_s is not None and timeout_s > 0:
            ms = int(timeout_s * 1000)
            try:
                kwargs["http_options"] = genai_types.HttpOptions(timeout=ms)
            except AttributeError:
                kwargs["http_options"] = {"timeout": ms}
        return genai.Client(**kwargs)
    except ImportError:
        raise ImportError(
            "google-genai not installed. Run:\n"
            "  pip install google-genai --break-system-packages"
        )


# ─── Availability check ───────────────────────────────────────────────────────

def get_gemini_status() -> dict[str, Any]:
    """
    Check Gemini API availability and return status dict.

    Returns
    -------
    {
      available: bool,
      flash_model: str,
      pro_model: str,
      key_configured: bool,
      sdk_installed: bool,
      error: str (if unavailable),
      as_of: str,
    }
    """
    status: dict[str, Any] = {
        "available":      False,
        "flash_model":    GEMINI_FLASH_MODEL,
        "pro_model":      GEMINI_PRO_MODEL,
        "key_configured": bool(_GEMINI_KEY),
        "sdk_installed":  False,
        "as_of":          datetime.now(timezone.utc).isoformat(),
    }

    if not _GEMINI_KEY:
        status["error"] = "GEMINI_API_KEY not set in .env"
        status["setup"] = {
            "step1": "Get API key at https://aistudio.google.com/app/apikey",
            "step2": "Add to .env: GEMINI_API_KEY=your_key_here",
            "step3": "pip install google-genai --break-system-packages",
        }
        return status

    try:
        from google import genai  # noqa: F401
        status["sdk_installed"] = True
    except ImportError:
        status["error"] = "SDK not installed: pip install google-genai --break-system-packages"
        return status

    # Quick API test — list models
    try:
        client = _get_client()
        models = [m.name for m in client.models.list()]
        flash_ok = any(GEMINI_FLASH_MODEL in m for m in models)
        status.update({
            "available":    True,
            "flash_ready":  flash_ok,
            "models_found": len(models),
        })
    except Exception as exc:
        status["error"] = f"API test failed: {exc}"

    return status


# ─── Core generate ────────────────────────────────────────────────────────────

def _generate(
    prompt: str,
    model: str = GEMINI_FLASH_MODEL,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 1024,
    retry_on_quota: bool = True,
    response_mime_type: str | None = None,
    timeout_s: float | None = None,
) -> str:
    """
    Send a prompt to Gemini using google-genai SDK and return the response text.
    Auto-retries with fallback model on 429 rate-limit errors.
    Raises exceptions on failure — callers should handle gracefully.
    """
    from google.genai import types as genai_types

    try:
        client = _get_client(timeout_s=timeout_s)
    except TypeError:
        # Test stubs are `lambda: client` and reject timeout_s.
        client = _get_client()
    full_prompt = f"{system}\n\n{prompt}" if system else prompt

    models_to_try = [model]
    if retry_on_quota and model != GEMINI_FALLBACK:
        models_to_try.append(GEMINI_FALLBACK)

    last_exc: Exception | None = None
    for attempt_model in models_to_try:
        try:
            cfg_kwargs: dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if response_mime_type:
                cfg_kwargs["response_mime_type"] = response_mime_type
            response = client.models.generate_content(
                model=attempt_model,
                contents=full_prompt,
                config=genai_types.GenerateContentConfig(**cfg_kwargs),
            )
            text = response.text
            if text:
                return text.strip()
            raise ValueError("Empty response from Gemini")
        except Exception as exc:
            last_exc = exc
            err_str = str(exc)
            is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            is_missing_model = (
                "404" in err_str
                or "NOT_FOUND" in err_str
                or "no longer available" in err_str.lower()
            )
            if is_quota or is_missing_model:
                # Quota: brief pause. Retired model id: skip straight to fallback
                # (gemini-2.5-pro 404s for new API users — sleeping does not help).
                if is_quota:
                    time.sleep(2)
                continue
            raise  # other errors propagate immediately

    raise last_exc or RuntimeError("All Gemini models failed")


def _extract_json(text: str) -> dict:
    """Extract JSON object from text, handling markdown code blocks."""
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError(f"No JSON found in response: {text[:200]}")


# ─── News Sentiment (Flash — replaces Haiku for bulk) ────────────────────────

def gemini_news_sentiment(symbol: str, articles: list[dict]) -> dict[str, Any]:
    """
    Score news sentiment for a symbol using Gemini Flash.
    Designed as a drop-in replacement for claude-haiku-4-5 in news_agent.py.

    Parameters
    ----------
    symbol   : ticker symbol
    articles : list of article dicts with title, description, source, published

    Returns same schema as news_agent.analyze_news_sentiment()
    """
    if not articles:
        # Honesty rule: never fabricate NEUTRAL/0 when there is no evidence.
        # Mirror news_agent.analyze_news_sentiment, which this is a drop-in for,
        # so _save_sentiment / consensus correctly treat it as UNAVAILABLE.
        return llm_contracts.unavailable_news_result(
            symbol=symbol, reason="no_articles", articles=[]
        )

    articles_text = "\n\n".join(
        f"[{i+1}] {a.get('title','')}\nSource: {a.get('source','')} | {a.get('published','')}\n{a.get('description','')[:400]}"
        for i, a in enumerate(articles)
    )

    prompt = f"""Analyze these {len(articles)} news articles about {symbol} and return ONLY valid JSON.
No markdown. No text outside the JSON object. Use double-quotes.

{articles_text}

Return this exact JSON structure:
{{
  "symbol": "{symbol}",
  "overall_sentiment": "<VERY_POSITIVE|POSITIVE|NEUTRAL|NEGATIVE|VERY_NEGATIVE>",
  "sentiment_score": <integer -100 to +100>,
  "impact_level": "<HIGH|MEDIUM|LOW>",
  "article_count": {len(articles)},
  "key_themes": ["<theme1>", "<theme2>"],
  "positive_signals": ["<specific positive fact>"],
  "negative_signals": ["<specific negative fact>"],
  "notable_events": ["<earnings/products/lawsuits/partnerships>"],
  "momentum_narrative": "<2-3 sentences on current news narrative>",
  "investor_action": "<WATCH_CLOSELY|RESEARCH_MORE|MONITOR|NO_ACTION>",
  "confidence": "<HIGH|MEDIUM|LOW>",
  "scored_by": "gemini-flash"
}}

Scoring: +100=extremely bullish, 0=neutral, -100=extremely bearish."""

    try:
        raw = _generate(prompt, model=GEMINI_FLASH_MODEL, temperature=0.2, max_tokens=800)
        result = _extract_json(raw)
        result["articles"] = articles[:5]
        result["scored_by"] = "gemini-flash"
        return result
    except Exception as exc:
        # A provider failure is not neutral news — return the honest UNAVAILABLE
        # envelope (null sentiment/score) rather than a fabricated flat reading.
        result = llm_contracts.unavailable_news_result(
            symbol=symbol, reason=f"gemini_error: {exc}", articles=list(articles[:5])
        )
        result["scored_by"] = "gemini-flash"
        return result


# ─── Second Opinion (Pro — matches Claude Sonnet depth) ──────────────────────

def gemini_second_opinion(
    symbol: str,
    claude_analysis: str,
    verdict: str = "",
) -> dict[str, Any]:
    """
    Generate a contrarian second opinion using Gemini 1.5 Pro.
    Independent from Claude — different training data, different reasoning patterns.
    Best used to stress-test STRONG BUY signals before execution.

    Returns
    -------
    {
      available: bool,
      model: str,
      symbol: str,
      second_opinion: str,
      risk_flags: list[str],
      agreement_level: str,  # AGREE / PARTIALLY_AGREE / DISAGREE
      type: "gemini_contrarian_review",
      as_of: str,
    }
    """
    system = (
        "You are a rigorous, skeptical financial analyst. Your role is to find what "
        "bullish analysts miss. Be precise, cite specific numbers when available, "
        "and give your honest independent assessment. No disclaimers."
    )

    verdict_line = f"Claude's verdict: {verdict}\n\n" if verdict else ""
    prompt = (
        f"Here is an AI analysis of {symbol}:\n\n"
        f"{verdict_line}"
        f"{claude_analysis[:1200]}\n\n"
        "Provide:\n"
        "1. Two specific risks or counterarguments this analysis may have missed\n"
        "2. Your independent assessment: do you AGREE, PARTIALLY_AGREE, or DISAGREE?\n"
        "3. Rate your confidence in this stock: BULLISH / NEUTRAL / BEARISH\n\n"
        "Return ONLY valid JSON:\n"
        '{"second_opinion": "<full analysis 3-5 sentences>", '
        '"risk_flags": ["<risk1>", "<risk2>"], '
        '"agreement_level": "<AGREE|PARTIALLY_AGREE|DISAGREE>", '
        '"gemini_stance": "<BULLISH|NEUTRAL|BEARISH>"}'
    )

    try:
        raw = _generate(prompt, model=GEMINI_PRO_MODEL, system=system, temperature=0.4, max_tokens=600)
        parsed = _extract_json(raw)
        return {
            "available":       True,
            "model":           GEMINI_PRO_MODEL,
            "symbol":          symbol,
            "second_opinion":  parsed.get("second_opinion", raw),
            "risk_flags":      parsed.get("risk_flags", []),
            "agreement_level": parsed.get("agreement_level", "UNKNOWN"),
            "gemini_stance":   parsed.get("gemini_stance", "NEUTRAL"),
            "type":            "gemini_contrarian_review",
            "as_of":           datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "available": False,
            "model":     GEMINI_PRO_MODEL,
            "symbol":    symbol,
            "error":     str(exc),
            "type":      "gemini_contrarian_review",
        }


# ─── Market Commentary (Flash — macro narrative) ─────────────────────────────

def gemini_market_commentary(
    regime_summary: str = "",
    macro_context: str = "",
    portfolio_summary: str = "",
) -> dict[str, Any]:
    """
    Generate a broad market outlook combining regime, macro (FRED), and portfolio context.
    Uses Gemini Flash for speed. Ideal for daily briefings.

    Returns
    -------
    {
      available: bool,
      model: str,
      commentary: str,
      market_stance: str,   # RISK_ON / NEUTRAL / RISK_OFF
      top_themes: list[str],
      action_bias: str,     # BUY_DIPS / HOLD / REDUCE / AVOID
      as_of: str,
    }
    """
    context_parts = []
    if regime_summary:
        context_parts.append(f"TECHNICAL REGIME:\n{regime_summary}")
    if macro_context:
        context_parts.append(f"MACRO DATA:\n{macro_context}")
    if portfolio_summary:
        context_parts.append(f"PORTFOLIO:\n{portfolio_summary}")

    if not context_parts:
        context_parts.append("No specific context provided — give general US equity market outlook.")

    prompt = (
        "You are a senior portfolio strategist. Based on the following market context, "
        "provide a concise market commentary.\n\n"
        + "\n\n".join(context_parts)
        + "\n\nReturn ONLY valid JSON:\n"
        '{"commentary": "<3-4 sentence market narrative>", '
        '"market_stance": "<RISK_ON|NEUTRAL|RISK_OFF>", '
        '"top_themes": ["<theme1>", "<theme2>", "<theme3>"], '
        '"action_bias": "<BUY_DIPS|HOLD|REDUCE|AVOID>"}'
    )

    try:
        raw = _generate(prompt, model=GEMINI_FLASH_MODEL, temperature=0.35, max_tokens=500)
        parsed = _extract_json(raw)
        return {
            "available":     True,
            "model":         GEMINI_FLASH_MODEL,
            "commentary":    parsed.get("commentary", ""),
            "market_stance": parsed.get("market_stance", "NEUTRAL"),
            "top_themes":    parsed.get("top_themes", []),
            "action_bias":   parsed.get("action_bias", "HOLD"),
            "as_of":         datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "available": False,
            "model":     GEMINI_FLASH_MODEL,
            "error":     str(exc),
        }


# ─── Quick Portfolio Analysis (Flash — fast daily check) ─────────────────────

def gemini_quick_analysis(portfolio_summary: str) -> dict[str, Any]:
    """
    Fast Gemini Flash portfolio commentary — cloud quality, low cost.
    Same interface as ollama_quick_analysis() but uses Gemini API.
    Falls back gracefully if API key not set.

    Returns
    -------
    {
      available: bool,
      model: str,
      analysis: str,    # 3 actionable lines
      type: "gemini_quick_commentary",
      as_of: str,
    }
    """
    if not _GEMINI_KEY:
        return {
            "available": False,
            "error": "GEMINI_API_KEY not configured",
            "type":  "gemini_quick_commentary",
        }

    system = (
        "You are a concise portfolio analyst. Give direct, actionable insights. "
        "No hedging language or disclaimers. Cite specific positions and percentages."
    )
    prompt = (
        f"Analyze this portfolio:\n\n{portfolio_summary}\n\n"
        "Respond with exactly 3 lines:\n"
        "1. Overall positioning (1 sentence with specific numbers)\n"
        "2. Biggest current risk (1 sentence — name the specific position or factor)\n"
        "3. Best action to take today (1 sentence — specific and actionable)"
    )

    try:
        response = _generate(prompt, model=GEMINI_FLASH_MODEL, system=system, temperature=0.3, max_tokens=300)
        return {
            "available": True,
            "model":     GEMINI_FLASH_MODEL,
            "analysis":  response,
            "type":      "gemini_quick_commentary",
            "as_of":     datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {
            "available": False,
            "error":     str(exc),
            "type":      "gemini_quick_commentary",
        }
