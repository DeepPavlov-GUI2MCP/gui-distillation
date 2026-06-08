from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.exploration._io import JsonDict, load_json, load_yaml, write_json

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]


def bbox_center(bbox: BBox) -> Point:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def point_in_bbox(point: Point, bbox: BBox) -> bool:
    x, y = point
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def euclidean_distance(p1: Point, p2: Point) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


def evaluate_bbox(
    calculator_yaml: Path,
    observation_grounded_json: Path,
) -> JsonDict:
    calc = load_yaml(calculator_yaml)
    obs = load_json(observation_grounded_json)

    buttons: Dict[str, List[float]] = calc.get("buttons", {})
    elements: List[JsonDict] = obs.get("elements", [])

    results: List[JsonDict] = []
    skipped: List[JsonDict] = []

    distances: List[float] = []
    hits: List[bool] = []

    for element in elements:
        element_id = element.get("id")
        bbox = element.get("bbox")

        if element_id == "display":
            skipped.append(
                {
                    "id": element_id,
                    "reason": "display has no calibration point in calculator.yaml",
                }
            )
            continue

        if element_id not in buttons:
            skipped.append(
                {
                    "id": element_id,
                    "reason": "element id not found in calculator.yaml buttons",
                }
            )
            continue

        if bbox is None:
            skipped.append({"id": element_id, "reason": "bbox is null"})
            continue

        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            skipped.append(
                {
                    "id": element_id,
                    "reason": f"bbox has wrong structure (expected list of 4): {bbox!r}",
                }
            )
            continue
        try:
            x1_b, y1_b, x2_b, y2_b = map(float, bbox)
        except (TypeError, ValueError) as exc:
            skipped.append({"id": element_id, "reason": f"bbox contains non-numeric values: {exc}"})
            continue
        if x2_b <= x1_b or y2_b <= y1_b:
            skipped.append(
                {
                    "id": element_id,
                    "reason": f"invalid bbox dimensions (x2<=x1 or y2<=y1): {bbox}",
                }
            )
            continue

        raw_point = buttons[element_id]
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) != 2:
            skipped.append(
                {
                    "id": element_id,
                    "reason": f"calculator.yaml point has wrong structure (expected [x, y]): {raw_point!r}",
                }
            )
            continue
        try:
            yaml_point: Point = (float(raw_point[0]), float(raw_point[1]))
        except (TypeError, ValueError) as exc:
            skipped.append({"id": element_id, "reason": f"calculator.yaml point contains non-numeric values: {exc}"})
            continue

        validated_bbox: BBox = (x1_b, y1_b, x2_b, y2_b)
        center = bbox_center(validated_bbox)

        dx = center[0] - yaml_point[0]
        dy = center[1] - yaml_point[1]
        dist = euclidean_distance(center, yaml_point)
        hit = point_in_bbox(yaml_point, validated_bbox)

        distances.append(dist)
        hits.append(hit)

        results.append(
            {
                "id": element_id,
                "yaml_point": [yaml_point[0], yaml_point[1]],
                "bbox": list(validated_bbox),
                "bbox_center": [round(center[0], 2), round(center[1], 2)],
                "dx": round(dx, 2),
                "dy": round(dy, 2),
                "distance": round(dist, 2),
                "point_in_bbox": hit,
            }
        )

    summary = {
        "matched_elements": len(results),
        "skipped_elements": len(skipped),
        "mean_center_error": round(sum(distances) / len(distances), 2) if distances else None,
        "max_center_error": round(max(distances), 2) if distances else None,
        "min_center_error": round(min(distances), 2) if distances else None,
        "point_in_bbox_hits": sum(1 for h in hits if h),
        "point_in_bbox_hit_rate": round(sum(1 for h in hits if h) / len(hits), 4) if hits else None,
    }

    return {
        "calculator_yaml": str(calculator_yaml),
        "observation_grounded_json": str(observation_grounded_json),
        "summary": summary,
        "results": results,
        "skipped": skipped,
    }


def main() -> int:
    parser = argparse.ArgumentParser("evaluate_bbox")
    parser.add_argument(
        "--calculator-yaml",
        default="config/apps/calculator.yaml",
        help="Path to calibrated calculator.yaml",
    )
    parser.add_argument(
        "--observation-grounded",
        required=True,
        help="Path to observation_grounded.json",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path for bbox_eval.json",
    )
    args = parser.parse_args()

    calculator_yaml = Path(args.calculator_yaml)
    observation_grounded_json = Path(args.observation_grounded)

    report = evaluate_bbox(
        calculator_yaml=calculator_yaml,
        observation_grounded_json=observation_grounded_json,
    )

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = observation_grounded_json.parent / "bbox_eval.json"

    write_json(output_path, report)
    print(f"Saved bbox evaluation to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
