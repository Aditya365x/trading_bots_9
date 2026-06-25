"""Strategy registry — maps config `strategy` names to their classes."""
from __future__ import annotations

import importlib

# name -> (module, class)
_REGISTRY = {
    "precision_sniper":     ("precision_sniper", "PrecisionSniper"),
    "breakout_pattern":     ("breakout_pattern", "BreakoutPattern"),
    "pulse_trend_radar":    ("pulse_trend_radar", "PulseTrendRadar"),
    "synapse_trail_pro":    ("synapse_trail_pro", "SynapseTrailPro"),
    "adaptive_fib_trailing":("adaptive_fib_trailing", "AdaptiveFibTrailing"),
    "meridian_flow":        ("meridian_flow", "MeridianFlow"),
    "liquidity_pools":      ("liquidity_pools", "LiquidityPools"),
    "fib_structure_engine": ("fib_structure_engine", "FibStructureEngine"),
    "ict_session_zones":    ("ict_session_zones", "IctSessionZones"),
    "mlrsi":                ("mlrsi", "MLRSI"),
    "power_order_blocks":   ("power_order_blocks", "PowerOrderBlocks"),
    "anchored_vwap":        ("anchored_vwap", "AnchoredVWAP"),
    "mirage_liquidity_sweep": ("mirage_liquidity_sweep", "MirageLiquiditySweep"),
    "bollinger_breakout":   ("bollinger_breakout", "BollingerBreakout"),
    "order_flow_sniper":    ("order_flow_sniper", "OrderFlowSniper"),
    "trap_master":          ("trap_master", "TrapMaster"),
    "mlsmc":                ("mlsmc", "MLSMC"),
    "liquidity_shift":      ("liquidity_shift", "LiquidityShift"),
}


def get_strategy(name: str):
    if name not in _REGISTRY:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(_REGISTRY)}")
    mod_name, cls_name = _REGISTRY[name]
    mod = importlib.import_module(f"bots.strategies.{mod_name}")
    return getattr(mod, cls_name)


def available() -> list[str]:
    return list(_REGISTRY)
