You are generating calculator task instructions for training a GUI agent on GNOME Calculator in ADVANCED (scientific) mode.

## Available buttons
{button_list}

## Already used buttons in previous tasks (prioritize UNDERUSED ones in new tasks)
{used_buttons}

## Button-to-task mapping (use this to generate tasks for ALL buttons)
- sin, cos, tan: "Calculate sin(60)", "Compute cos(45) + sin(45)", "Find tan(30)"
- sinh, cosh, tanh: "Calculate sinh(1)", "Compute cosh(0)", "Find tanh(0.5)"
- ln, log: "Calculate ln(e)", "Find log(1000)", "Compute ln(2) + ln(3)"
- euler: "Compute e^3", "Calculate e × 2", "Find e^(−1)"
- factorial: "Calculate 7!", "Compute 5! ÷ 4!", "Find 0!"
- power: "Compute 2^10", "Calculate 3^4", "Find 5^3 − 4^3"
- inverse: "Calculate 1/7", "Compute inverse of 0.25", "Find 1/3 + 1/6"
- abs_value: "Compute |−42|", "Find |3 − 8|"
- sqrt: "Calculate sqrt(144)", "Compute sqrt(2) × sqrt(8)"
- sci_exponent: "Calculate 6.02 × 10^23", "Compute 3 × 10^8"
- factorize: "Factorize 120", "Find prime factors of 360"
- imaginary: "Compute i²", "Calculate 3 + 4i", "Find i × i"
- re, im: "Find Re(3+4i)", "Compute Im(5+2i)"
- conj: "Find conjugate of 3+4i", "Compute conj(2+7i)"
- arg: "Calculate Arg(1+i)", "Find Arg(−1)"
- mod: "Compute 17 mod 5", "Calculate 100 mod 13"
- percent: "Find 15% of 200", "Calculate 33% of 999"
- pi: "Compute π × 4", "Calculate sin(π ÷ 6)"
- decimal: "Calculate 0.5 + 0.3", "Compute 1.25 × 4"
- exponent: "Calculate 2^8", "Compute 10^(−2)"

## Instructions
Generate exactly {n_easy} easy, {n_medium} medium, and {n_hard} hard tasks.

**EASY** — single scientific operation or basic arithmetic:
- "Calculate sin(30)"
- "Compute sinh(1)"
- "Calculate ln(e)"
- "Find log(100)"
- "Calculate 5!"
- "Compute |−7|"
- "Find cosh(0)"
- "Calculate i²"
- "Compute 1/4 (use inverse button)"
- "Factorize 24"

**MEDIUM** — 2 operations, mixing basic + scientific:
- "Calculate sin(45) + cos(45)"
- "Compute sinh(1) + cosh(1)"
- "Compute e² − 1"
- "Find ln(10) × 2"
- "Calculate 3! + 4!"
- "Find Re(3 + 4i)"
- "Compute conj(2 + 5i)"
- "Calculate Arg(1 + i)"
- "Find 1/3 + 1/6 (use inverse button)"
- "Compute tanh(1) × 2"
- "Calculate 3 × 10^5 (use sci_exponent)"

**HARD** — chains (3+ operations), complex scientific expressions:
- "Calculate sin(30)² + cos(30)²"
- "Compute sinh(1)² − cosh(1)² + 1"
- "Compute e^(ln(5)) − 5"
- "Find log(2) + log(5)"
- "Calculate (3!)² ÷ 9"
- "Find |Im(3+4i)| × |Re(3+4i)|"
- "Compute conj(1+2i) × (1+2i)"
- "Calculate Arg(−1+i) + Arg(1+i)"
- "Find 1/2 + 1/3 + 1/6 (use inverse)"
- "Compute tanh(ln(2))"
- "Calculate 6.022 × 10^23 ÷ (3 × 10^11)"

## Buttons that MUST be covered
The following buttons are critical and MUST appear in tasks. If any of these have been used 0 times, you MUST generate a task using them:
**sinh, cosh, tanh, inverse, sci_exponent, factorize, imaginary, re, im, conj, arg, mod, percent, decimal, pi**

## Diversity rules — STRICTLY FOLLOW
1. Each task must be completable using ONLY the listed buttons.
2. Use specific numbers, not vague descriptions.
3. Do NOT repeat tasks from the already used list: {previous_tasks}
4. **MANDATORY coverage**: check "Already used buttons". For EVERY button with 0 uses, you MUST include a task that uses it. This is the most important rule.
5. Vary the digits used: include tasks with digits 6, 7, 8, 9 and two-digit numbers.
6. Each batch must use at least 6 distinct scientific operations.
7. Hard tasks must genuinely require 5+ button presses (not counting equals).
8. Include at least one edge-case per batch: ln(0), tan(90), 0!, sinh(0), cosh(0), i², conj(i).
