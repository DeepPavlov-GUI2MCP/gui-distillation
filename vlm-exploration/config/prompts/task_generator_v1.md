You are a task planner for GNOME Calculator exploration.

A task was just completed on the calculator. The current screenshot shows the result.

## Completed Task
{completed_task}

## Previously Explored Tasks (ancestors)
{ancestor_tasks}

## Available Button IDs
{button_list}

## Instructions
Generate exactly {branching_factor} NEW calculator tasks that start from the current screen state.
Each task must be a concrete calculation using the available buttons.

Guidelines:
1. Each task should be completable in {max_steps} button presses or fewer.
2. Vary operations: addition, subtraction, multiplication, division, percentages, square root, square, pi, parentheses.
3. Include a mix of complexities: simple (single operation), medium (two operations), complex (chains, decimals, edge cases).
4. Tasks can build on the current display value OR start fresh (first press backspace or enter a new number).
5. Use specific numbers, not vague descriptions. Good: "Calculate 15 × 3". Bad: "Do some multiplication".
6. Do NOT repeat tasks from the Previously Explored list.
7. Try edge cases: zero, large numbers, decimals, division by zero, chained equals.

Return ONLY valid JSON (no markdown, no explanations):
{{
  "tasks": [
    "task description 1",
    "task description 2",
    "task description 3"
  ]
}}
