# Данные для обучения GUI Skills: что есть, чего не хватает, зачем

## 1. Конечная цель

Обучить 4 маленькие специализированные модели (skills), которые вместе заменяют большую MLLM при управлении GUI:

| Skill | Вход | Выход | Зачем |
|-------|------|-------|-------|
| **TaskSimplifier** | Высокоуровневая цель на естественном языке | Упорядоченный список атомарных подзадач | Разложить "посчитай 2+3 и умножь на 7" в последовательность конкретных кликов |
| **Skill 0 (Grounding)** | Скриншот + текстовое описание элемента | Bounding box элемента на скриншоте | Найти "кнопку плюс" на картинке |
| **Skill A (Action)** | Скриншот + подзадача + bbox целевого элемента | Тип действия + параметры | Решить что делать: кликнуть, ввести текст, скроллить |
| **Skill B (State)** | Скриншот до + действие + скриншот после | Текстовое описание изменения | Понять что произошло: "на дисплее появилось число 8" |

Ground truth для обучения — **знания Teacher MLLM** (GPT-5.4 и т.п.), которая смотрит на скриншоты и описывает что видит. Мы дистиллируем её знания в маленькие модели.

---

## 2. Что собирается сейчас

Пайплайн двухэтапный: сначала онлайн-сбор (клики по калькулятору), потом офлайн-аннотация (Teacher смотрит на сохранённые скриншоты).

### 2.1 Онлайн: артефакты шага (`automation.py` / `task_runner.py`)

На каждое действие (клик по кнопке) создаётся `step_NNNN/`:

```
step_0000/
  before.png      — скриншот интерфейса ДО клика (1280×1024)
  after.png       — скриншот интерфейса ПОСЛЕ клика
  action.json     — что сделали: тип, координаты, параметры
  metadata.json   — хеши состояния, изменился ли экран, тайминги
```

**`action.json`** — выполненное действие:
```json
{
  "action_type": "click",
  "coordinates": [286, 330],
  "parameters": {"button": "left", "clicks": 1}
}
```

**`metadata.json`** — метаданные шага:
```json
{
  "step_id": 0,
  "app": "gnome-calculator",
  "screen": {"width": 1280, "height": 1024},
  "action": {...},
  "hashes": {"before": "99b26ea3...", "after": "aa7d4578..."},
  "dhashes": {"before": "7070707070000000", "after": "7070307070000000", "hamming_distance": 2},
  "changed": true
}
```

- `hashes` — MD5 для точного сравнения "изменился ли экран"
- `dhashes` — перцептивный хеш (dHash) для визуального сходства
- `hamming_distance` — насколько сильно изменился экран (0 = одинаково, больше = сильнее)

### 2.2 Офлайн: аннотации от Teacher MLLM (`annotator_runner.py`)

Teacher MLLM (GPT-5.4) смотрит на сохранённые скриншоты и создаёт аннотации. Это и есть **ground truth для обучения**.

**`observation_grounded_{model}.json`** — Teacher описывает все UI-элементы + их bbox:
```json
{
  "screen": {"width": 1280, "height": 1024},
  "elements": [
    {"id": "digit_5", "type": "button", "text": "5", "bbox": [162, 368, 230, 428], "confidence": 0.99},
    {"id": "plus", "type": "button", "text": "+", "bbox": [300, 465, 330, 495], "confidence": 0.98},
    {"id": "display", "type": "text_field", "text": "12", "bbox": [74, 120, 466, 210], "confidence": 0.98}
  ]
}
```
→ **Ground truth для Skill 0 (Grounding)**: пара (скриншот + "digit_5") → bbox [162, 368, 230, 428]

**`delta.json`** — Teacher описывает что изменилось между before и after:
```json
{
  "success": true,
  "change_type": "text_updated",
  "description": "The calculator display changed from empty to showing 8",
  "before_text": "",
  "after_text": "8",
  "confidence": 0.98,
  "self_check": {
    "action_visually_plausible": true,
    "text_change_consistent_with_action": true,
    "layout_changed": false
  }
}
```
→ **Ground truth для Skill B (State)**: тройка (before.png + click + after.png) → "на дисплее появилось 8"

**`observation.json`** — инвентаризация UI без координат (id, type, text, confidence). Вспомогательный файл.

### 2.3 Отчёт запуска

**`task_run_report.json`** — сводка по всему exploration-запуску:
```json
{
  "completed_goals": [
    "Perform a simple addition: 7 + 5",
    "Perform a simple subtraction: 9 - 4"
  ],
  "steps": [
    {"step_id": 0, "button_id": "digit_7", "rationale": "enter first number", "current_goal": "7 + 5"},
    {"step_id": 1, "button_id": "plus", "rationale": "add", "current_goal": "7 + 5"}
  ]
}
```

---

## 3. Чего не хватает

### 3.1 Связь шага с задачей (для TaskSimplifier и Skill A)

**Проблема**: информация "к какой задаче относится шаг" и "зачем этот клик" живёт **только** в `task_run_report.json`. В самих `step_NNNN/` директориях этого нет. Когда офлайн-аннотатор обрабатывает шаги — он не знает контекста задачи.

**Почему это важно**: для TaskSimplifier нужны пары (goal → [subgoal_1, subgoal_2, ...]). Для Skill A нужен контекст: "нажми = **потому что** заканчиваем сложение". Без связи шаг↔задача эти данные не собрать.

**Решение**: поле `dfs_context` в `metadata.json` каждого шага:
```json
{
  "dfs_context": {
    "node_id": "0.1",
    "depth": 1,
    "task": "Multiply the result by 7",
    "step_within_task": 2,
    "parent_node_id": "0",
    "rationale": "press multiply to start multiplication"
  }
}
```

### 3.2 Иерархия задач (для TaskSimplifier)

**Проблема**: сейчас exploration плоский — LLM придумывает задачи одну за другой, без связи между ними. Нет понятия "эта задача выросла из результата предыдущей".

**Почему это важно**: TaskSimplifier должен уметь разлагать **составные** задачи. "Посчитай 2+3, потом умножь на 7" — это цепочка, а не два изолированных примера. Плоский exploration не создаёт таких цепочек.

**Решение**: DFS-дерево задач + файл `dfs_tree.json`:
```json
{
  "nodes": {
    "0":   {"task": "Calculate 2 + 3",         "children_ids": ["0.0", "0.1", "0.2"], "steps": [0,1,2,3,4]},
    "0.0": {"task": "Multiply the result by 7", "parent_id": "0",  "steps": [5,6,7]},
    "0.1": {"task": "Take square root",         "parent_id": "0",  "steps": [8,9]},
    "0.2": {"task": "Add 0.5 to the result",    "parent_id": "0",  "steps": [10,11,12]}
  }
}
```

Цепочка узлов root→child→grandchild = составная задача, разложенная на этапы.

### 3.3 Разнообразие данных (для всех skills)

**Проблема**: LLM в плоском exploration-режиме генерирует однотипные задачи. Реальный пример из запуска на 20 шагов:
1. "7 + 5" — сложение
2. "9 − 4" — вычитание
3. "5 × 6" — умножение
4. "30 ÷ 6" — деление

Всё — простейшие однооперационные примеры. Нет десятичных дробей, скобок, цепочек, edge cases.

**Почему это важно**: skill-модели должны работать в **разных контекстах**. Кнопка "=" после "2+3" выглядит так же, но контекст другой чем после "sin(90)" или после "√16×π". Для робастного обучения нужны разнообразные примеры.

**Решение**: DFS-ветвление. Из результата каждой задачи рождаются дочерние задачи в другом контексте:
```
"2 + 3" = 5
├── "Multiply by 7"  → 35     (контекст: "5" на дисплее)
│   ├── "Take square root" → √35
│   └── "Divide by 5" → 7
├── "Add 0.5" → 5.5           (контекст: "5" на дисплее, другая ветка)
└── "Square the result" → 25
```

Одна и та же операция (например, "=") встречается в десятках разных визуальных контекстов.

---

## 4. Сводка: какие данные → для какого skill

### 4.1 TaskSimplifier: goal → [subgoals]

| Источник данных | Что даёт |
|----------------|----------|
| `dfs_tree.json` → цепочка node_id (root → child → grandchild) | Составная задача как последовательность этапов |
| `dfs_context.task` в каждом шаге | Текст подзадачи |
| `dfs_context.rationale` | Объяснение каждого атомарного действия |
| `action.json` → button_id | Конкретное действие на каждом шаге |

**Пример обучающей записи**:
- Input: "Calculate 2 + 3 and then multiply the result by 7"
- Output: ["press digit_2", "press plus", "press digit_3", "press equals", "press multiply", "press digit_7", "press equals"]

### 4.2 Skill 0 (Grounding): screenshot + описание → bbox

| Источник данных | Что даёт |
|----------------|----------|
| `before.png` | Скриншот состояния |
| `observation_grounded_{model}.json` → elements[].id, elements[].bbox | Teacher MLLM показывает где элемент на экране |

**Пример обучающей записи**:
- Input: (скриншот калькулятора, "кнопка digit_5")
- Output: bbox [162, 368, 230, 428]

DFS даёт разнообразие: один и тот же элемент на скриншотах с разным содержимым дисплея.

### 4.3 Skill A (Action): screenshot + подзадача + bbox → действие

| Источник данных | Что даёт |
|----------------|----------|
| `before.png` | Скриншот текущего состояния |
| `dfs_context.task` + `dfs_context.rationale` | Что пытаемся сделать и почему |
| `observation_grounded_{model}.json` → bbox целевого элемента | Где находится элемент |
| `action.json` → action_type, parameters | Какое действие было выполнено |

**Пример обучающей записи**:
- Input: (скриншот, "press equals to compute result", bbox кнопки =)
- Output: {"action_type": "click", "parameters": {"button": "left"}}

### 4.4 Skill B (State): before + action + after → описание

| Источник данных | Что даёт |
|----------------|----------|
| `before.png` + `after.png` | Визуальное изменение |
| `action.json` | Что было сделано |
| `delta.json` от Teacher MLLM | Текстовое описание изменения |

**Пример обучающей записи**:
- Input: (before.png, "click at [286, 330]", after.png)
- Output: "The calculator display changed from showing '2 +' to showing '2 + 3'"

---

## 5. Пайплайн сбора данных

```
                          ОНЛАЙН (на VM с GUI)
                          ═══════════════════
                                  │
                    ┌─────────────┴─────────────┐
                    │    DFS Exploration Runner  │
                    │                            │
                    │  1. Запустить калькулятор   │
                    │  2. Выполнить задачу (LLM) │
                    │  3. Сгенерировать дочерние │
                    │  4. Rollback + рекурсия    │
                    └─────────────┬─────────────┘
                                  │
                    Сохраняет на каждом шаге:
                    • before.png, after.png
                    • action.json
                    • metadata.json + dfs_context
                    На весь запуск:
                    • dfs_tree.json
                                  │
                                  ▼
                         ОФЛАЙН (без GUI)
                         ════════════════
                                  │
                    ┌─────────────┴─────────────┐
                    │   Annotator Runner         │
                    │   (Teacher MLLM)           │
                    │                            │
                    │  Для каждого step_NNNN/:   │
                    │  • observation_grounded    │
                    │  • delta                   │
                    └─────────────┬─────────────┘
                                  │
                                  ▼
                      ДАТАСЕТ ДЛЯ ОБУЧЕНИЯ
                      ════════════════════
                                  │
                    ┌─────────────┴─────────────┐
                    │  Конвертация в train/val   │
                    │  для каждого skill         │
                    │  (ещё не реализовано)       │
                    └───────────────────────────┘
```

---

## 6. Формат артефактов: было → стало

| Компонент | Было (Phase 1) | Стало (DFS Exploration) |
|-----------|---------------|------------------------|
| `metadata.json` | step_id, hashes, dhashes, timing | + `dfs_context` (node_id, depth, task, rationale, step_within_task) |
| Структура задач | Плоский список в `task_run_report.json` | Дерево в `dfs_tree.json` + ссылки из каждого шага |
| Артефакты шага | before/after/action/metadata | Без изменений, формат обратно совместим |
| Офлайн-аннотации | observation, observation_grounded, delta | Без изменений, аннотатор работает как прежде |
| Покрытие данных | ~4 однотипные задачи за 20 шагов | Десятки задач в разных контекстах (DFS-ветвление) |

Обратная совместимость: шаги без `dfs_context` остаются валидными. Офлайн-аннотатор (`annotator_runner.py`) не требует модификации.
