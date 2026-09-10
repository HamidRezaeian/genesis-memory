import pytest
from genesis_memory.proxy.pricing_engine import ModelPricingEngine


def test_pricing_engine_disk_catalog_loading():
    engine = ModelPricingEngine(auto_fetch=False)
    assert len(engine._catalog) >= 400
    assert engine._last_fetched > 0


def test_pricing_engine_model_resolution():
    engine = ModelPricingEngine(auto_fetch=False)

    # Test exact / fuzzy match for Gemini 3.8 Flash
    gemini_info = engine.resolve_model_pricing("gemini-3.8-flash")
    assert gemini_info["input_per_m"] > 0
    assert gemini_info["output_per_m"] > 0

    # Test GPT-4o
    gpt4o_info = engine.resolve_model_pricing("openai/gpt-4o")
    assert gpt4o_info["input_per_m"] == 2.50
    assert gpt4o_info["output_per_m"] == 10.00

    # Test Claude 3.5 Sonnet
    claude_info = engine.resolve_model_pricing("claude-3-5-sonnet")
    assert claude_info["input_per_m"] > 0


def test_pricing_engine_cost_savings_calculation():
    engine = ModelPricingEngine(auto_fetch=False)
    res = engine.calculate_cost_savings(
        tokens_stripped=100000,
        tokens_retained=1000,
        tokens_cached=500,
        completion_tokens=200,
        model_id="gemini-3.8-flash",
    )
    assert res["dollars_saved"] > 0
    assert res["actual_cost"] < res["baseline_uncompressed_cost"]
    assert "benchmarks_equivalent_saved" in res
    assert res["benchmarks_equivalent_saved"]["gpt_4o"] > 0
    assert res["benchmarks_equivalent_saved"]["claude_3_5_sonnet"] > 0
