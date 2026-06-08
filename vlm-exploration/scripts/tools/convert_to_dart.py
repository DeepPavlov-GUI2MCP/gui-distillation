"""
Convert flat exploration run data to DART/UI-TARS training format.

Takes step artifacts (before.png, action.json) + flat_run_report.json
and produces a JSONL dataset where each line is a training sample:

  {
    "id": "run_006__task_000__step_002",
    "image": "path/to/before.png",
    "task": "Calculate 9 + 6",
    "task_complete": false,
    "history": [
      {"action": "click", "button_id": "digit_9", "coordinates": [249, 333]},
      {"action": "click", "button_id": "plus",    "coordinates": [315, 480]}
    ],
    "target": {
      "action": "click",
      "button_id": "digit_6",
      "coordinates": [247, 382]
    },
    "screen": {"width": 1280, "height": 1024}
  }

Usage:
    python scripts/tools/convert_to_dart.py \
        --run-dir data/exploration/flat_runs/run_006 \
        --output data/training/run_006_dart.jsonl \
        --only-complete          # optional: only tasks where task_complete=true
        --copy-images output/images/  # optional: copy before.png into flat dir
"""

from __future__ import annotations

import argparse
import json
import shutil
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_step(step_dir: Path) -> dict | None:
    """Load action.json and metadata.json from a step directory."""
    action_file = step_dir / "action.json"
    if not action_file.exists():
        return None
    action = json.loads(action_file.read_text())
    metadata_file = step_dir / "metadata.json"
    metadata = json.loads(metadata_file.read_text()) if metadata_file.exists() else {}
    before_img = step_dir / "before.png"
    return {
        "action": action,
        "metadata": metadata,
        "before_img": str(before_img) if before_img.exists() else None,
        "step_dir": str(step_dir),
    }


def convert_run(
    run_dir: Path,
    output_path: Path,
    only_complete: bool = False,
    copy_images_dir: Path | None = None,
) -> dict:
    """Convert a flat exploration run to DART JSONL format."""
    report_path = run_dir / "flat_run_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"No flat_run_report.json in {run_dir}")

    report = json.loads(report_path.read_text())
    tasks = report["tasks"]
    run_name = run_dir.name

    if copy_images_dir:
        copy_images_dir.mkdir(parents=True, exist_ok=True)

    samples = []
    skipped_no_step = 0
    skipped_incomplete = 0

    for task_idx, task_info in enumerate(tasks):
        task_text = task_info["task"]
        step_ids = task_info["step_ids"]
        task_complete = task_info["task_complete"]

        if only_complete and not task_complete:
            skipped_incomplete += len(step_ids)
            continue

        # Load all steps for this task
        steps = []
        for sid in step_ids:
            step_dir = run_dir / f"step_{sid:04d}"
            step_data = load_step(step_dir)
            if step_data is None:
                skipped_no_step += 1
                continue
            steps.append((sid, step_data))

        # Build training samples: for each step, history = all previous steps in task
        for i, (sid, step_data) in enumerate(steps):
            action = step_data["action"]
            screen = step_data["metadata"].get("screen", {"width": 1280, "height": 1024})

            # History: all previous actions in this task
            history = []
            for prev_sid, prev_step in steps[:i]:
                prev_action = prev_step["action"]
                history.append({
                    "action": prev_action.get("action_type", "click"),
                    "button_id": prev_action.get("button_id", ""),
                    "coordinates": prev_action.get("coordinates", []),
                })

            # Image path
            image_path = step_data["before_img"]
            if copy_images_dir and image_path and Path(image_path).exists():
                img_name = f"{run_name}__task_{task_idx:03d}__step_{i:03d}.png"
                dest = copy_images_dir / img_name
                shutil.copy2(image_path, dest)
                image_path = str(dest)

            # Is this the last step and task was completed?
            is_final = (i == len(steps) - 1) and task_complete

            sample = {
                "id": f"{run_name}__task_{task_idx:03d}__step_{i:03d}",
                "image": image_path,
                "task": task_text,
                "task_complete": is_final,
                "history": history,
                "target": {
                    "action": action.get("action_type", "click"),
                    "button_id": action.get("button_id", ""),
                    "coordinates": action.get("coordinates", []),
                },
                "screen": screen,
            }
            samples.append(sample)

    # Write JSONL
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    stats = {
        "total_samples": len(samples),
        "total_tasks": len(tasks),
        "tasks_complete": sum(1 for t in tasks if t["task_complete"]),
        "tasks_incomplete": sum(1 for t in tasks if not t["task_complete"]),
        "skipped_no_step": skipped_no_step,
        "skipped_incomplete": skipped_incomplete,
        "output": str(output_path),
    }
    logger.info("Conversion done: %s", json.dumps(stats, indent=2))
    return stats


def group_by_task(
    run_dir: Path,
    output_dir: Path,
    only_complete: bool = False,
) -> dict:
    """
    Organize run data into per-task folders with screenshots and a task log.

    Output structure:
        output_dir/
          task_000__Calculate_9_plus_6/
            task_info.json          # task metadata + step log
            step_00_before.png
            step_00_after.png
            step_01_before.png
            step_01_after.png
            ...
          task_001__Find_the_square_root_of_16/
            ...
    """
    report_path = run_dir / "flat_run_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"No flat_run_report.json in {run_dir}")

    report = json.loads(report_path.read_text())
    tasks = report["tasks"]
    output_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0

    for task_idx, task_info in enumerate(tasks):
        task_text = task_info["task"]
        step_ids = task_info["step_ids"]
        task_complete = task_info["task_complete"]

        if only_complete and not task_complete:
            skipped += 1
            continue

        # Sanitize task name for folder
        safe_name = task_text[:60].replace(" ", "_").replace("/", "÷")
        for ch in '<>:"|?*\\':
            safe_name = safe_name.replace(ch, "")
        task_dir_name = f"task_{task_idx:03d}__{safe_name}"
        task_dir = output_dir / task_dir_name
        task_dir.mkdir(parents=True, exist_ok=True)

        # Collect step details and copy images
        step_log = []
        for i, sid in enumerate(step_ids):
            step_dir = run_dir / f"step_{sid:04d}"
            step_data = load_step(step_dir)
            if step_data is None:
                continue

            action = step_data["action"]
            entry = {
                "step_within_task": i,
                "global_step_id": sid,
                "action_type": action.get("action_type", "click"),
                "button_id": action.get("button_id", ""),
                "coordinates": action.get("coordinates", []),
            }
            step_log.append(entry)

            # Copy before/after screenshots
            for img_name in ("before.png", "after.png"):
                src = step_dir / img_name
                if src.exists():
                    dst = task_dir / f"step_{i:02d}_{img_name}"
                    shutil.copy2(src, dst)

        # Write task info
        task_info_out = {
            "task": task_text,
            "task_idx": task_idx,
            "task_complete": task_complete,
            "num_steps": len(step_log),
            "steps": step_log,
        }
        (task_dir / "task_info.json").write_text(
            json.dumps(task_info_out, indent=2, ensure_ascii=False)
        )
        created += 1

    stats = {
        "tasks_created": created,
        "tasks_skipped": skipped,
        "output_dir": str(output_dir),
    }
    logger.info("Group-by-task done: %s", json.dumps(stats, indent=2))
    return stats


def main():
    parser = argparse.ArgumentParser(description="Convert flat run to DART training format")
    parser.add_argument("--run-dir", required=True, help="Path to flat run directory")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--only-complete", action="store_true",
                        help="Only include steps from tasks where task_complete=true")
    parser.add_argument("--copy-images", default=None,
                        help="Copy before.png images to this directory with flat naming")
    parser.add_argument("--group-by-task", default=None,
                        help="Create per-task folders with screenshots and logs in this directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    stats = convert_run(
        run_dir=Path(args.run_dir),
        output_path=Path(args.output),
        only_complete=args.only_complete,
        copy_images_dir=Path(args.copy_images) if args.copy_images else None,
    )
    print(json.dumps(stats, indent=2))

    if args.group_by_task:
        group_stats = group_by_task(
            run_dir=Path(args.run_dir),
            output_dir=Path(args.group_by_task),
            only_complete=args.only_complete,
        )
        print(json.dumps(group_stats, indent=2))


if __name__ == "__main__":
    main()
