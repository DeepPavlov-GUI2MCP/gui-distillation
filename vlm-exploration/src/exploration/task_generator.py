"""
LLM-based child task generator for DFS exploration.

Given a screenshot of the current calculator state (after completing a task),
generates a list of new tasks that can be started from this state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from pydantic import BaseModel

from src.teachers.openai_client import OpenAIAnnotatorClient

logger = logging.getLogger(__name__)


class _ChildTasksResponse(BaseModel):
    tasks: List[str]


class TaskGenerator:
    """Generate child tasks from the current GUI state via LLM."""

    def __init__(
        self,
        llm_client: OpenAIAnnotatorClient,
        prompt_template: str,
    ) -> None:
        self.llm_client = llm_client
        self.prompt_template = prompt_template

    def generate(
        self,
        screenshot_path: Path,
        completed_task: str,
        branching_factor: int,
        button_list: str,
        max_steps: int,
        ancestor_tasks: str = "(none)",
    ) -> List[str]:
        """
        Generate child tasks from the current screen state.

        Returns a list of task strings (may be shorter than branching_factor
        if LLM returns fewer or the call fails).
        """
        prompt = self.prompt_template.format(
            completed_task=completed_task,
            button_list=button_list,
            branching_factor=branching_factor,
            max_steps=max_steps,
            ancestor_tasks=ancestor_tasks,
        )

        try:
            result = self.llm_client.infer_structured(
                prompt,
                response_model=_ChildTasksResponse,
                image_paths=[screenshot_path],
            )
        except Exception:
            logger.exception("LLM call failed during task generation")
            return []

        tasks = [t.strip() for t in result.tasks if t.strip()][:branching_factor]

        logger.info(
            "Generated %d child tasks from '%s': %s",
            len(tasks),
            completed_task[:60],
            [t[:50] for t in tasks],
        )
        return tasks
