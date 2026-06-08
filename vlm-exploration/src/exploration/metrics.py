"""
Exploration quality metrics and deduplication.

Deduplication:
    deduplicate_graph(graph, screenshots_dir, threshold=5)
    — merges screens whose dHash hamming distance < threshold

Metrics:
    compute_metrics(graph)
    — discovery_rate, elements_per_step, elements_per_1k_tokens, cost

Usage:
    python -m src.exploration.metrics --graph path/to/state_graph.json [--dedup --threshold 5]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _compute_dhashes(
    screenshots_dir: Path,
    screen_labels: Dict[str, str],
    hash_size: int = 16,
) -> Dict[str, Any]:
    """Compute dHash for each screen's screenshot. Returns {label: hash}."""
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        logger.warning("imagehash/Pillow not installed — skipping dedup")
        return {}

    hashes = {}
    for label, info in screen_labels.items():
        ss_path = screenshots_dir / Path(info).name if "/" in info else screenshots_dir / info
        if not ss_path.exists():
            # Try relative to screenshots_dir parent
            ss_path = screenshots_dir.parent / info
        if ss_path.exists():
            hashes[label] = imagehash.dhash(Image.open(ss_path), hash_size=hash_size)
        else:
            logger.debug("Screenshot not found for %s: %s", label, ss_path)
    return hashes


def deduplicate_graph(
    graph: dict,
    screenshots_dir: Path,
    threshold: int = 5,
) -> dict:
    """
    Merge screens whose dHash hamming distance < threshold.

    Keeps the first-discovered screen as canonical, merges elements from duplicates.
    Updates transitions to point to canonical labels.
    Returns a new graph dict (does not mutate input).
    """
    screens = graph.get("screens", {})
    if len(screens) <= 1:
        return graph

    # Compute hashes
    screen_ss = {label: info.get("screenshot", "") for label, info in screens.items()}
    hashes = _compute_dhashes(screenshots_dir, screen_ss)

    if not hashes:
        return graph

    # Build merge map: duplicate_label -> canonical_label
    labels = list(screens.keys())  # insertion order = discovery order
    merge_map: Dict[str, str] = {}  # dup -> canonical

    for i, label_a in enumerate(labels):
        if label_a in merge_map:
            continue
        ha = hashes.get(label_a)
        if ha is None:
            continue
        for label_b in labels[i + 1:]:
            if label_b in merge_map:
                continue
            hb = hashes.get(label_b)
            if hb is None:
                continue
            if ha - hb < threshold:
                merge_map[label_b] = label_a
                logger.info(
                    "Dedup: '%s' -> '%s' (hamming=%d)",
                    label_b, label_a, ha - hb,
                )

    if not merge_map:
        return graph

    # Merge elements into canonical screens
    merged_screens = {}
    for label, info in screens.items():
        canonical = merge_map.get(label, label)
        if canonical not in merged_screens:
            merged_screens[canonical] = dict(info)
            merged_screens[canonical]["merged_from"] = [canonical]
        else:
            # Merge elements by (name, type)
            existing = {
                (e["name"], e["type"]): e
                for e in merged_screens[canonical].get("elements", [])
            }
            for el in info.get("elements", []):
                key = (el["name"], el["type"])
                if key not in existing:
                    existing[key] = el
            merged_screens[canonical]["elements"] = sorted(
                existing.values(), key=lambda e: (e["type"], e["name"])
            )
            merged_screens[canonical]["merged_from"].append(label)

    # Rebuild buttons for backward compat
    interactive = {"button", "input", "dropdown", "toggle", "radio", "menu_item", "link"}
    for info in merged_screens.values():
        info["buttons"] = sorted({
            e["name"] for e in info.get("elements", [])
            if e.get("type") in interactive
        })

    # Remap transitions
    new_transitions = []
    seen = set()
    for t in graph.get("transitions", []):
        fr = merge_map.get(t.get("from", ""), t.get("from", ""))
        to = merge_map.get(t.get("to", ""), t.get("to", ""))
        action = t.get("action", "")
        key = (fr, action, to)
        if key not in seen:
            seen.add(key)
            new_transitions.append({**t, "from": fr, "to": to})

    # Build new graph
    new_graph = dict(graph)
    new_graph["screens"] = merged_screens
    new_graph["transitions"] = new_transitions
    new_graph["total_screens"] = len(merged_screens)
    new_graph["total_transitions"] = len(new_transitions)
    new_graph["dedup"] = {
        "threshold": threshold,
        "screens_before": len(screens),
        "screens_after": len(merged_screens),
        "merged": {v: k for k, v in merge_map.items()},
    }

    return new_graph


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

# Pricing per 1M tokens: (input, output)
MODEL_PRICING: Dict[str, tuple] = {
    "gpt-5.4-mini": (1.10, 4.40),
    "gpt-5.4": (10.0, 40.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


def _count_unique_elements(graph: dict) -> int:
    """Count unique (name, type) pairs across all screens."""
    seen = set()
    for screen in graph.get("screens", {}).values():
        elements = screen.get("elements", [])
        if elements:
            for el in elements:
                seen.add((el.get("name", ""), el.get("type", "")))
        else:
            # Backward compat: old graphs with only "buttons"
            for btn in screen.get("buttons", []):
                seen.add((btn, "button"))
    return len(seen)


def compute_metrics(graph: dict) -> Dict[str, Any]:
    """Compute exploration quality metrics from a state_graph dict."""
    total_steps = graph.get("total_steps", 0) or 0
    total_screens = graph.get("total_screens", 0) or 0
    unique_elements = _count_unique_elements(graph)

    usage = graph.get("usage", {}) or {}
    tokens_input = usage.get("tokens_input", 0) or 0
    tokens_output = usage.get("tokens_output", 0) or 0
    tokens_total = usage.get("tokens_total", 0) or (tokens_input + tokens_output)

    model = graph.get("model")

    # Core metrics
    discovery_rate = total_screens / total_steps if total_steps > 0 else 0.0
    elements_per_step = unique_elements / total_steps if total_steps > 0 else 0.0

    elements_per_1k_tokens: Optional[float] = None
    if tokens_total > 0:
        elements_per_1k_tokens = unique_elements / (tokens_total / 1000)

    # Cost estimation
    cost_usd: Optional[float] = None
    elements_per_dollar: Optional[float] = None
    if model and model in MODEL_PRICING and tokens_total > 0:
        price_in, price_out = MODEL_PRICING[model]
        cost_usd = (tokens_input * price_in + tokens_output * price_out) / 1_000_000
        if cost_usd > 0:
            elements_per_dollar = unique_elements / cost_usd

    return {
        "total_screens": total_screens,
        "total_steps": total_steps,
        "unique_elements": unique_elements,
        "tokens_total": tokens_total,
        "discovery_rate": round(discovery_rate, 3),
        "elements_per_step": round(elements_per_step, 2),
        "elements_per_1k_tokens": round(elements_per_1k_tokens, 2) if elements_per_1k_tokens is not None else None,
        "cost_usd": round(cost_usd, 4) if cost_usd is not None else None,
        "elements_per_dollar": round(elements_per_dollar, 1) if elements_per_dollar is not None else None,
        "model": model,
    }


def main() -> int:
    parser = argparse.ArgumentParser("exploration_metrics")
    parser.add_argument("--graph", required=True, help="Path to state_graph.json")
    parser.add_argument("--dedup", action="store_true", help="Run dHash deduplication before metrics")
    parser.add_argument("--threshold", type=int, default=5, help="dHash hamming distance threshold (default 5)")
    args = parser.parse_args()

    path = Path(args.graph)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    graph = json.loads(path.read_text(encoding="utf-8"))

    if args.dedup:
        screenshots_dir = path.parent / "screenshots"
        graph = deduplicate_graph(graph, screenshots_dir, threshold=args.threshold)
        print(f"Dedup: {graph['dedup']['screens_before']} -> {graph['dedup']['screens_after']} screens", file=sys.stderr)

    metrics = compute_metrics(graph)
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
