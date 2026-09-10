"""GENESIS Real-Time Live Model Pricing Engine.

Fetches, normalizes, and caches real-time pricing for 400+ frontier and open-weight models
(OpenAI, Anthropic, Google Gemini, DeepSeek, Meta LLaMA, Mistral, Qwen, etc.).
Provides exact token-cost accounting with prompt caching discount factors per Rule 21 and Rule 31.
"""

import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request

logger = logging.getLogger("genesis.pricing")

CACHE_DIR = Path.home() / ".genesis"
CACHE_FILE = CACHE_DIR / "pricing_cache.json"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
CACHE_TTL_SECONDS = 3600 * 6  # 6 hours TTL
# Offline-first: a normalized snapshot ships inside the wheel so the engine is
# deterministic on first launch / air-gapped hosts (no network, no ~/.genesis state).
BUNDLED_CATALOG_FILE = Path(__file__).resolve().parent / "pricing_catalog.json"

# Fallback baseline pricing per 1M tokens (USD) if completely offline on first launch
STATIC_DEFAULTS: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "cached": 1.25, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60},
    "claude-3-5-sonnet": {"input": 3.00, "cached": 0.30, "output": 15.00},
    "claude-3-7-sonnet": {"input": 3.00, "cached": 0.30, "output": 15.00},
    "claude-3-haiku": {"input": 0.25, "cached": 0.025, "output": 1.25},
    "gemini-3.8-flash": {"input": 0.10, "cached": 0.025, "output": 0.40},
    "gemini-2.5-flash": {"input": 0.15, "cached": 0.0375, "output": 0.60},
    "gemini-2.5-pro": {"input": 1.25, "cached": 0.3125, "output": 5.00},
    "deepseek-chat": {"input": 0.27, "cached": 0.07, "output": 1.10},
    "deepseek-coder": {"input": 0.27, "cached": 0.07, "output": 1.10},
}


def normalize_catalog(models: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Converts an OpenRouter-style model list into the per-1M-token GENESIS catalog shape."""
    catalog: Dict[str, Dict[str, Any]] = {}
    for m in models:
        mid = str(m.get("id", "")).strip()
        if not mid:
            continue
        pricing = m.get("pricing", {}) or {}
        # Source pricing is per raw token (e.g. 0.0000025) -> USD per 1M tokens
        try:
            prompt_per_m = float(pricing.get("prompt", 0) or 0) * 1_000_000
            comp_per_m = float(pricing.get("completion", 0) or 0) * 1_000_000
        except (TypeError, ValueError):
            continue
        low = mid.lower()
        # Prompt-cache discount factors: Anthropic 90%, Gemini/DeepSeek 75%, default 50%
        if "anthropic" in low:
            cached_per_m = prompt_per_m * 0.10
        elif "gemini" in low or "google" in low or "deepseek" in low:
            cached_per_m = prompt_per_m * 0.25
        else:
            cached_per_m = prompt_per_m * 0.50
        catalog[mid] = {
            "name": m.get("name", mid),
            "input_per_m": round(prompt_per_m, 4),
            "cached_per_m": round(cached_per_m, 4),
            "output_per_m": round(comp_per_m, 4),
            "context_length": m.get("context_length", 0) or 0,
        }
    return catalog


class ModelPricingEngine:
    """Manages real-time LLM token rates and exact multi-model cost savings accounting."""

    def __init__(self, auto_fetch: bool = True):
        self._catalog: Dict[str, Dict[str, Any]] = {}
        self._last_fetched: float = 0.0
        self.source: str = "none"
        if not self._load_from_disk_cache():
            self._load_bundled_catalog()
        if auto_fetch and (time.time() - self._last_fetched > CACHE_TTL_SECONDS or not self._catalog):
            self.refresh_catalog_sync()

    def _load_from_disk_cache(self) -> bool:
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                catalog = data.get("catalog", {})
                if catalog:
                    self._catalog = catalog
                    self._last_fetched = float(data.get("timestamp", 0.0) or 0.0)
                    self.source = "disk_cache"
                    return True
            except Exception as exc:
                logger.warning("Failed to read disk pricing cache: %s", exc)
        return False

    def _load_bundled_catalog(self) -> bool:
        if not BUNDLED_CATALOG_FILE.exists():
            return False
        try:
            with open(BUNDLED_CATALOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._catalog = data.get("catalog", {})
            self._last_fetched = float(data.get("timestamp", 0.0) or 0.0)
            self.source = "bundled"
            return bool(self._catalog)
        except Exception as exc:
            logger.warning("Failed to read bundled pricing catalog: %s", exc)
            return False

    def _save_to_disk_cache(self) -> None:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": self._last_fetched,
                    "count": len(self._catalog),
                    "catalog": self._catalog,
                }, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to save disk pricing cache: %s", exc)

    def refresh_catalog_sync(self, timeout: float = 6.0) -> int:
        """Fetches live pricing catalog for 400+ models synchronously."""
        req = urllib.request.Request(
            CATALOG_URL,
            headers={"User-Agent": "GENESIS-Pricing-Engine/1.0", "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    new_catalog = normalize_catalog(payload.get("data", []))
                    if new_catalog:
                        self._catalog = new_catalog
                        self._last_fetched = time.time()
                        self.source = "live"
                        self._save_to_disk_cache()
                        logger.info("Real-time pricing catalog refreshed: %d models loaded.", len(self._catalog))
                        return len(self._catalog)
        except Exception as exc:
            logger.warning("Live catalog refresh failed (relying on cache/fallback): %s", exc)
        return len(self._catalog)

    def resolve_model_pricing(self, model_id: Optional[str]) -> Dict[str, Any]:
        """Resolves exact or fuzzy-matched pricing for any model string."""
        if not model_id:
            model_id = "gemini-3.8-flash"
        norm = model_id.lower().strip()

        # 1. Exact match in live catalog
        if norm in self._catalog:
            res = dict(self._catalog[norm])
            res["matched_id"] = norm
            return res

        # 2. Canonical fuzzy match in catalog (e.g. "gemini-3.8-flash" inside "google/gemini-3.8-flash")
        best_match = None
        for cat_id, info in self._catalog.items():
            cat_norm = cat_id.lower()
            if norm in cat_norm or cat_norm.endswith(f"/{norm}"):
                best_match = dict(info)
                best_match["matched_id"] = cat_id
                break

        if best_match:
            return best_match

        # 3. Fallback to STATIC_DEFAULTS if offline or unlisted preview model
        for key, val in STATIC_DEFAULTS.items():
            if key in norm or norm in key:
                return {
                    "name": key,
                    "input_per_m": val["input"],
                    "cached_per_m": val["cached"],
                    "output_per_m": val["output"],
                    "matched_id": key,
                    "is_static_fallback": True,
                }

        # 4. Ultimate conservative default
        return {
            "name": model_id,
            "input_per_m": 0.50,
            "cached_per_m": 0.125,
            "output_per_m": 2.00,
            "matched_id": "generic_default",
            "is_static_fallback": True,
        }

    def calculate_cost_savings(
        self,
        tokens_stripped: int,
        tokens_retained: int,
        tokens_cached: int = 0,
        completion_tokens: int = 0,
        model_id: Optional[str] = "gemini-3.8-flash",
    ) -> Dict[str, Any]:
        """Calculates exact dollars saved for the active model AND compares across benchmark models."""
        active_rate = self.resolve_model_pricing(model_id)
        input_rate = active_rate["input_per_m"] / 1_000_000
        cached_rate = active_rate["cached_per_m"] / 1_000_000
        output_rate = active_rate["output_per_m"] / 1_000_000

        # Effective actual cost of the compacted request
        uncached_retained = max(0, tokens_retained - tokens_cached)
        actual_cost = (
            (tokens_cached * cached_rate)
            + (uncached_retained * input_rate)
            + (completion_tokens * output_rate)
        )

        # Baseline cost if GENESIS had NOT stripped the prior history turns
        uncompressed_prompt = tokens_stripped + tokens_retained
        # If uncompressed, cached portion stays same, rest is full uncached input
        uncompressed_uncached = max(0, uncompressed_prompt - tokens_cached)
        baseline_cost = (
            (tokens_cached * cached_rate)
            + (uncompressed_uncached * input_rate)
            + (completion_tokens * output_rate)
        )

        dollars_saved = max(0.0, baseline_cost - actual_cost)

        # Cross-model comparisons: "If this same developer was using GPT-4o or Claude 3.5 Sonnet..."
        gpt4o_rate = self.resolve_model_pricing("openai/gpt-4o")
        claude_rate = self.resolve_model_pricing("anthropic/claude-3.5-sonnet")
        deepseek_rate = self.resolve_model_pricing("deepseek/deepseek-chat")

        equiv_gpt4o_saved = (tokens_stripped / 1_000_000) * gpt4o_rate["input_per_m"]
        equiv_claude_saved = (tokens_stripped / 1_000_000) * claude_rate["input_per_m"]
        equiv_deepseek_saved = (tokens_stripped / 1_000_000) * deepseek_rate["input_per_m"]

        return {
            "model_id": model_id,
            "resolved_name": active_rate.get("name", model_id),
            "matched_id": active_rate.get("matched_id", model_id),
            "rate_per_m_input": active_rate["input_per_m"],
            "rate_per_m_cached": active_rate["cached_per_m"],
            "rate_per_m_output": active_rate["output_per_m"],
            "tokens_stripped": tokens_stripped,
            "tokens_retained": tokens_retained,
            "dollars_saved": round(dollars_saved, 6),
            "actual_cost": round(actual_cost, 6),
            "baseline_uncompressed_cost": round(baseline_cost, 6),
            "benchmarks_equivalent_saved": {
                "claude_3_5_sonnet": round(equiv_claude_saved, 4),
                "gpt_4o": round(equiv_gpt4o_saved, 4),
                "deepseek_chat": round(equiv_deepseek_saved, 4),
            },
            "catalog_total_models": len(self._catalog),
            "last_synced_epoch": self._last_fetched,
        }

    def get_all_models_summary(self, paid_only: bool = False) -> List[Dict[str, Any]]:
        """Returns simplified list of all available models and their current rates."""
        models: List[Dict[str, Any]] = []
        for mid, info in self._catalog.items():
            input_rate = float(info.get("input_per_m", 0.0) or 0.0)
            output_rate = float(info.get("output_per_m", 0.0) or 0.0)
            is_paid = (input_rate > 0.0 or output_rate > 0.0)
            if paid_only and not is_paid:
                continue

            raw_prov = mid.split("/")[0].lower() if "/" in mid else "other"
            if "openai" in raw_prov:
                prov = "OpenAI"
            elif "anthropic" in raw_prov:
                prov = "Anthropic"
            elif "google" in raw_prov or "gemini" in raw_prov:
                prov = "Google"
            elif "deepseek" in raw_prov:
                prov = "DeepSeek"
            elif "meta" in raw_prov or "llama" in raw_prov:
                prov = "Meta"
            elif "mistral" in raw_prov:
                prov = "Mistral"
            elif "qwen" in raw_prov or "alibaba" in raw_prov:
                prov = "Qwen"
            elif "cohere" in raw_prov:
                prov = "Cohere"
            elif "x-ai" in raw_prov or "grok" in raw_prov:
                prov = "xAI"
            elif "amazon" in raw_prov:
                prov = "Amazon"
            elif "microsoft" in raw_prov:
                prov = "Microsoft"
            else:
                prov = raw_prov.capitalize()

            models.append({
                "id": mid,
                "name": info.get("name", mid),
                "provider": prov,
                "input_per_m": round(input_rate, 4),
                "cached_per_m": round(float(info.get("cached_per_m", 0.0) or 0.0), 4),
                "output_per_m": round(output_rate, 4),
                "context_length": int(info.get("context_length", 0) or 0),
                "is_paid": is_paid,
            })
        return sorted(models, key=lambda x: x["input_per_m"], reverse=True)
