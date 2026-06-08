from __future__ import annotations

import argparse
import logging
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional, Tuple

from src.exploration._io import JsonDict, load_json, load_yaml, write_json

logger = logging.getLogger(__name__)

BBox = Tuple[float, float, float, float]


def to_bbox(values: List[float] | Tuple[float, float, float, float]) -> BBox:
    if len(values) != 4:
        raise ValueError(f"Expected bbox of length 4, got: {values}")
    x1, y1, x2, y2 = map(float, values)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid bbox: {values}")
    return x1, y1, x2, y2


def bbox_area(b: BBox) -> float:
    x1, y1, x2, y2 = b
    return (x2 - x1) * (y2 - y1)


def intersection_area(a: BBox, b: BBox) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    return (ix2 - ix1) * (iy2 - iy1)


def iou(a: BBox, b: BBox) -> float:
    inter = intersection_area(a, b)
    union = bbox_area(a) + bbox_area(b) - inter
    if union <= 0:
        return 0.0
    return inter / union


def bbox_center(b: BBox) -> Tuple[float, float]:
    x1, y1, x2, y2 = b
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0


def center_error(a: BBox, b: BBox) -> float:
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def load_gold_bboxes(path: Path) -> Dict[str, BBox]:
    data = load_yaml(path)
    out: Dict[str, BBox] = {}

    for section_name in ("regions", "buttons"):
        section = data.get(section_name, {})
        if isinstance(section, dict):
            for element_id, bbox_vals in section.items():
                try:
                    out[element_id] = to_bbox(bbox_vals)
                except Exception as exc:
                    logger.warning("Skipping gold bbox %r in section %r: %s", element_id, section_name, exc)
                    continue
    return out


def load_predicted_bboxes(path: Path) -> Dict[str, BBox]:
    data = load_json(path)
    elements = data.get("elements", [])
    out: Dict[str, BBox] = {}

    if not isinstance(elements, list):
        return out

    for item in elements:
        if not isinstance(item, dict):
            continue
        element_id = item.get("id")
        bbox_vals = item.get("bbox")
        if not element_id or bbox_vals is None:
            continue
        try:
            out[str(element_id)] = to_bbox(bbox_vals)
        except Exception as exc:
            logger.warning("Skipping predicted bbox %r: %s", element_id, exc)
            continue
    return out


def evaluate_iou(
    gold_bboxes_path: Path,
    predicted_path: Path,
    include_display: bool = True,
) -> JsonDict:
    gold = load_gold_bboxes(gold_bboxes_path)
    pred = load_predicted_bboxes(predicted_path)

    gold_ids = set(gold.keys())
    pred_ids = set(pred.keys())

    if not include_display:
        gold_ids.discard("display")
        pred_ids.discard("display")

    matched_ids = sorted(gold_ids & pred_ids)
    missing_in_prediction = sorted(gold_ids - pred_ids)
    extra_in_prediction = sorted(pred_ids - gold_ids)

    per_element: List[JsonDict] = []
    ious: List[float] = []
    center_errors: List[float] = []

    for element_id in matched_ids:
        g = gold[element_id]
        p = pred[element_id]
        element_iou = iou(g, p)
        err = center_error(g, p)

        ious.append(element_iou)
        center_errors.append(err)

        per_element.append(
            {
                "id": element_id,
                "gold_bbox": [round(v, 2) for v in g],
                "pred_bbox": [round(v, 2) for v in p],
                "iou": round(element_iou, 4),
                "center_error": round(err, 2),
            }
        )

    summary: JsonDict = {
        "matched_elements": len(matched_ids),
        "missing_in_prediction": len(missing_in_prediction),
        "extra_in_prediction": len(extra_in_prediction),
        "mean_iou": round(mean(ious), 4) if ious else None,
        "median_iou": round(median(ious), 4) if ious else None,
        "min_iou": round(min(ious), 4) if ious else None,
        "max_iou": round(max(ious), 4) if ious else None,
        "iou_at_0_5": round(sum(v >= 0.5 for v in ious) / len(ious), 4) if ious else None,
        "iou_at_0_75": round(sum(v >= 0.75 for v in ious) / len(ious), 4) if ious else None,
        "mean_center_error": round(mean(center_errors), 2) if center_errors else None,
    }

    return {
        "gold_bboxes": str(gold_bboxes_path),
        "predicted_bboxes": str(predicted_path),
        "include_display": include_display,
        "summary": summary,
        "per_element": per_element,
        "missing_in_prediction_ids": missing_in_prediction,
        "extra_in_prediction_ids": extra_in_prediction,
    }


def main() -> int:
    parser = argparse.ArgumentParser("evaluate_iou")
    parser.add_argument(
        "--gold-bboxes",
        default="config/apps/calculator_bboxes.yaml",
        help="Path to gold bbox yaml.",
    )
    parser.add_argument(
        "--predicted",
        required=True,
        help="Path to observation_grounded.json or observation_grounded_omni.json.",
    )
    parser.add_argument(
        "--exclude-display",
        action="store_true",
        help="Exclude display from comparison.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to <predicted_parent>/iou_eval.json",
    )
    args = parser.parse_args()

    gold_path = Path(args.gold_bboxes)
    pred_path = Path(args.predicted)

    report = evaluate_iou(
        gold_bboxes_path=gold_path,
        predicted_path=pred_path,
        include_display=not args.exclude_display,
    )

    if args.output:
        output_path = Path(args.output)
    else:
        stem = pred_path.stem  # e.g. "observation_grounded_gpt-4.1"
        suffix = stem.replace("observation_grounded", "").lstrip("_")
        out_name = f"iou_eval_{suffix}.json" if suffix else "iou_eval.json"
        output_path = pred_path.parent / out_name
    write_json(output_path, report)
    print(f"Saved IoU evaluation to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
