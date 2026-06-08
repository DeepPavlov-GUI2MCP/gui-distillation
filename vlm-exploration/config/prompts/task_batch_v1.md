You are generating calculator task instructions for training a GUI agent on GNOME Calculator.

## Available buttons
{button_list}

## Already used buttons in previous tasks (prioritize UNDERUSED ones in new tasks)
{used_buttons}

## Instructions
Generate exactly {n_easy} easy, {n_medium} medium, and {n_hard} hard tasks.

**EASY** — single operation, small integers ≤ 20:
- "Calculate 7 + 4"
- "Find the square root of 9"
- "Calculate 15 mod 4"
- "What is 8 squared?"
- "Find 50% of 80"
- "Compute π × 1"

**MEDIUM** — 2 operations, or decimals, or parentheses, or %, π:
- "Calculate (3 + 5) × 6"
- "Calculate 1.5 + 2.7, then multiply by 4"
- "Find 20% of 350"
- "Calculate 7² + 3"
- "Compute π × 2"
- "Calculate 13 mod 4, then add 9"
- "Find 75% of 120"
- "Compute 6.25 + 1.75, then divide by 2"

**HARD** — chains (3+ operations), edge cases, large/unusual numbers:
- "Calculate (7 + 3) × (8 - 5) ÷ 6"
- "Compute sqrt(2) × sqrt(2)"
- "Calculate 1 ÷ 0"
- "Compute 0.1 + 0.2, then subtract 0.3"
- "Calculate sqrt(π × 4)"
- "Compute (12 mod 5)² + sqrt(16)"
- "Find 33% of 999"
- "Calculate 2³ + 3² - sqrt(25)"
- "Compute π × π, then take the square root"
- "Calculate (100 mod 7) × 3 + sqrt(81)"

## Diversity rules — STRICTLY FOLLOW
1. Each task must be completable using ONLY the listed buttons.
2. Use specific numbers, not vague descriptions like "some number".
3. Do NOT repeat tasks from the already used list: {previous_tasks}
4. **Force button diversity**: if a button appears in "Already used buttons" fewer than 3 times, include at least one task that uses it.
5. Vary the digits used: do not generate all tasks using only small digits (1-5). Include tasks with digits 6, 7, 8, 9 and two-digit numbers like 17, 23, 48, 96.
6. Vary operations across the batch: each batch must include tasks using at least 4 distinct operations (e.g. +, -, ×, ÷, mod, %, sqrt, x², π).
7. Hard tasks must genuinely require 5+ button presses (not counting equals).
8. Include at least one edge-case task per batch: division by zero, very large numbers (>1000), or floating-point quirks (0.1+0.2-0.3).
