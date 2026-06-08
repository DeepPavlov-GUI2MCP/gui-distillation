"""
Quick smoke-test for infer_structured() with the configured vLLM endpoint.

Usage:
    cd gui_distillation
    .venv/bin/python scripts/tools/test_structured_output.py \
        --teacher-config config/teachers/vllm.yaml
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.teachers.openai_client import OpenAIAnnotatorClient


class ChildTasksResponse(BaseModel):
    tasks: List[str]


class StepResponse(BaseModel):
    button_id: str
    rationale: str
    task_complete: bool


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--teacher-config", default="config/teachers/vllm.yaml")
    parser.add_argument("--settings", default="config/settings.yaml")
    args = parser.parse_args()

    client = OpenAIAnnotatorClient(
        settings_path=args.settings,
        teacher_config_path=args.teacher_config,
    )
    print(f"Model: {client.model}  api_mode: {client.api_mode}\n")

    # --- Test 1: ChildTasksResponse (task generator schema) ---
    print("=== Test 1: ChildTasksResponse ===")
    prompt = (
        "You just finished 'Calculate 2 + 3' on a calculator. "
        "Generate exactly 3 new calculator tasks starting from the current state (result = 5). "
        "Use specific numbers."
    )
    result = client.infer_structured(prompt, response_model=ChildTasksResponse)
    print(f"tasks ({len(result.tasks)}):")
    for t in result.tasks:
        print(f"  - {t}")

    # --- Test 2: StepResponse (action schema) ---
    print("\n=== Test 2: StepResponse ===")
    prompt2 = (
        "You are a GUI agent for GNOME Calculator. Task: Calculate 2 + 3. "
        "No actions taken yet. Available buttons: digit_2, plus, digit_3, equals. "
        "What is the next button to click?"
    )
    step = client.infer_structured(prompt2, response_model=StepResponse)
    print(f"button_id:     {step.button_id}")
    print(f"rationale:     {step.rationale}")
    print(f"task_complete: {step.task_complete}")

    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
