## Методология дистилляции GUI-навыков из мультимодальных языковых моделей в набор специализированных, компактных моделей, предоставляемых в виде MCP-tools (GUI Distillation)

![Methodology Pipeline](https://github.com/RT41yo/gui_distillation/blob/phase_1/assets/methodology_pipeline.png)

![Workflow](https://github.com/RT41yo/gui_distillation/blob/phase_1/assets/workflow.png)


## Phase 0: инфраструктура GUI и формализация навыков

Фаза 0 создает детерминированную, воспроизводимую инфраструктуру для автоматизации GUI exploration.

Что реализовано:

- Детерминированная X11-среда (Xvfb)
- Ядро GUI-автоматизации
- Калиброванные координаты центров кнопок
- Формализованные Pydantic-схемы
- Полностью воспроизводимый тестовый пайплайн


### Структура скриптов

**setup_vm.sh**

Подготовка виртуальной машины:

```
sudo bash scripts/setup/setup_vm.sh
```

Устанавливает: python3, pip, venv, git, build-essential, базовые системные зависимости, GUI-инструменты (wmctrl, xdotool, x11-utils), а также `python3-pyatspi` для захвата A11Y-дерева. Автоматически создаёт `system_dist_packages.pth` в venv, чтобы `pyatspi` был доступен внутри виртуального окружения.

**install_apps.sh**

Установка GUI-приложения (GNOME Calculator):

```
sudo bash scripts/setup/install_apps.sh
```

**setup_xvfb.sh**

Установка Xvfb и X11-инструментов:

```
sudo bash scripts/setup/setup_xvfb.sh
```

**reset_display.sh**

Очистка и перезапуск виртуального дисплея. Рекомендуется запускать перед тестами.

```
bash scripts/setup/reset_display.sh
```

Скрипт:
- убивает старый Xvfb
- снимает lock-файл
- очищает DISPLAY
- запускает новый Xvfb :99
- проверяет доступность

Это официальный способ подготовки среды.


### Инструменты (scripts/tools)

**find_coordinates.py**

Используется для калибровки координат центров кнопок.

```
config/apps/calculator.yaml
```

В большинстве случаев повторная калибровка не требуется. Калибровка нужна только если:
- изменилось разрешение
- изменился размер/позиция окна
- используется другая версия калькулятора
- тест "digit_5" не проходит

Запуск:
```
export DISPLAY=:0
gnome-calculator &
python scripts/tools/find_coordinates.py --output config/apps/calculator.yaml
```

**test_automation.py**

Полный тест инфраструктуры GUI.

Проверяет:
- доступность дисплея
- запуск приложения
- создание скриншотов
- выполнение кликов
- before/after артефакты
- клик по digit_5 (если есть координаты)

Запуск:
```
python scripts/tools/test_automation.py --display :99 --app gnome-calculator -v
```

Если все корректно:
```
🎉 ALL TESTS PASSED! Infrastructure is ready.
```


### Unit-тесты схем

Проверка формальных контрактов:

```
pytest -q tests/unit/test_schemas.py
```

Минимальный сценарий запуска "с нуля"

```
git clone <repo>
cd <repo>

sudo bash scripts/setup/setup_vm.sh
sudo bash scripts/setup/install_apps.sh
sudo bash scripts/setup/setup_xvfb.sh

bash scripts/setup/reset_display.sh

pytest -q tests/unit/test_schemas.py
python scripts/tools/test_automation.py --display :99 --app gnome-calculator -v
```

**Если тесты проходят — Фаза 0 завершена.**


## Phase 1: текущий рабочий протокол запуска (пока пайплайн раздельный - human in the loop)

На текущем этапе Phase 1 пайплайн состоит из **двух отдельных частей**:

1. **Execution / data collection** — `/src/core/automation.py` запускает приложение в виртуальном дисплее, выполняет действия в приложении (рандомные) и сохраняет шаги траектории: скриншот интерфейса до действия, скриншот интерфейса после действия, действие (рандомное), метаинформацию (`before.png`, `after.png`, `action.json`, `metadata.json`).
2. **Semantic annotation** — отдельный запуск annotator/LLM-модуля, который по уже собранным шагам траектории формирует `observation.json` (описание элементов интерфейса) и `delta.json` (описание изменений состояния интерфейса).


### Важно
- `gnome-calculator` **не запускать вручную** перед `/src/core/automation.py`.
- `Xvfb` нужно поднимать **только если он еще не запущен**.
- `DISPLAY` должен быть выставлен в `:99`.

---


### Часть 1. Сбор шагов траекторий через `/src/core/automation.py`

Если Xvfb еще не запущен:
```bash
Xvfb :99 -screen 0 1280x1024x24 -ac &
export DISPLAY=:99
xdpyinfo | grep dimensions
```

Перед новым прогоном (рекомендуется):
```bash
pkill -f gnome-calculator
```

Пример запуска `/src/core/automation.py` на 5 шагах:
```bash
python -m src.core.automation --random-buttons --steps 5 \
  --settings config/settings.yaml \
  --app-config config/apps/calculator.yaml \
  --output data/exploration/phase_1_debug
```


### Часть 2. Аннотирование шагов траекторий

После того как шаги траектории собраны в `data/exploration/phase_1_debug`, запускается annotator:

```bash
python -m src.exploration.teacher_debug_runner \
  --steps-root data/exploration/phase_1_debug \
  --settings config/settings.yaml \
  --teacher-config config/teachers/openai_gpt.yaml \
  --max-steps 5
```

Для каждого шага набор аннотаций:
- `before.png`
- `after.png`
- `action.json`
- `metadata.json`
- `observation.json`
- `delta.json`

В корне run-директории создается сводный отчет:
- `teacher_debug_report.json`


### Часть 3. Эксперимент с `observation_grounded` (bbox через MLLM): сравнение совпадения центров элементов UI

Добавлен второй промпт для MLLM - вернуть не только семантическое описание элементов, но и их bbox в абсолютных координатах: `config/prompts/observation_grounded_v1.md`.

В промпте зафиксированы:
- полный размер входного скриншота (1280x1024);
- требование, чтобы все bbox лежали внутри границ экрана;
- `element_id`;
- контролируемый список элементов UI.

При включенном флаге:
```
features:
  phase_1:
    use_grounded_observation: true
```

annotator формирует дополнительный файл: `observation_grounded.json` командой:

```bash
python -m src.exploration.teacher_debug_runner \
  --steps-root data/exploration/phase_1_debug \
  --settings config/settings.yaml \
  --teacher-config config/teachers/openai_gpt.yaml \
  --max-steps 2
```

Для первичной оценки качества bbox реализован скрипт `src/exploration/evaluate_bbox.py`.
Он сравнивает центры bbox, полученных от MLLM с откалиброванными `click points` из `config/apps/calculator.yaml`.

```bash
python -m src.exploration.evaluate_bbox \
  --calculator-yaml config/apps/calculator.yaml \
  --observation-grounded data/exploration/phase_1_debug/step_0001/observation_grounded.json
```


#### Результаты (для 0000 и 0001 шагов траектории):
MLLM стабильно возвращает:
- корректный `screen = 1280x1024`;
- контролируемый список элементов UI;
- bbox.

Однако количественная проверка показала, что точность bbox пока недостаточна для прямой замены откалиброванных координат в `executor`, поэтому на текущем этапе `calculator.yaml` остается основным источником координат, а `observation_grounded` используется как дополнительная расширенная аннотация.

1. `mean_center_error` = 40–44 px - средняя ошибка положения центра bbox, который дала MLLM, относительно откалиброванной точки кнопки из `calculator.yaml`, то есть в среднем центр кнопки, оцененный MLLM через bbox, смещен от калиброванной точки примерно на 40–44 пикселя.
2. `point_in_bbox_hit_rate` = 0.18–0.41 - это доля кнопок, для которых откалиброванная точка из `calculator.yaml` попала внутрь bbox, предсказанного MLLM.


### Часть 4. Эксперимент с `observation_grounded` (bbox через MLLM): расчет IoU-метрики

С помощью скрипта `scripts/tools/find_coordinates_bboxes.py` выполнена калибровка bboxes для элементов UI.

Команда запуска:

```bash
export DISPLAY=:0
gnome-calculator &
python scripts/tools/find_coordinates_bboxes.py --output config/apps/calculator_bboxes.yaml
```

Таким образом создается `config/apps/calculator_bboxes.yaml` - gold standard.

`src/exploration/evaluate_iou.py` выполняет расчет IoU-метрики.

Команда запуска:

```bash
python -m src.exploration.evaluate_iou \
  --gold-bboxes config/apps/calculator_bboxes.yaml \
  --predicted data/exploration/phase_1_debug/step_0000/observation_grounded.json
```

#### Результаты (для 0000 шага траектории):

- `mean_iou` = 0.1791 - это низкое среднее совпадение bbox от MLLM с вручную размеченными bbox.
- `iou_at_0_5` = 0.0556 - только примерно 5.6% элементов имеют IoU не меньше 0.5 (то есть из 18 совпавших элементов фактически только 1 элемент локализован на приемлемом уровне).
- `iou_at_0_75` = 0.0 - очень точных bbox нет вообще.
- `mean_center_error` = 41.75 - то согласуется с предыдущими измерениями: центры bbox от MLLM в среднем смещены примерно на 42 пикселя относительно gold-разметки.

MLLM-grounded observation корректно восстанавливает топологию интерфейса и основные элементы, однако качество локализации bbox относительно вручную размеченного gold standard остается низким (`mean_iou` - 0.18, `IoU@0.5` - 0.056), поэтому на текущем этапе такие bbox следует рассматривать как weak grounding, а не как точную геометрическую разметку.


### Часть 5. Task Runner

`src/exploration/task_runner.py` — LLM-управляемый агент, который выполняет задачи непосредственно во время работы приложения - на каждом шаге получает скриншот текущего состояния, решает какую кнопку нажать, выполняет действие и переходит к следующему шагу.

LLM получает только символьные идентификаторы кнопок (`digit_3`, `plus`, `equals` и т.д.) и выбирает следующую. Координаты подставляются детерминированно из `calculator.yaml`.


#### Режим 1: выполнение конкретной задачи

LLM выполняет одну задачу, заданную на естественном языке. Останавливается когда задача выполнена (`task_complete: true`) или исчерпан лимит шагов.

```bash
Xvfb :99 -screen 0 1280x1024x24 -ac &
export DISPLAY=:99

python -m src.exploration.task_runner \
  --task "add 3 and 5, then subtract 2" \
  --max-steps 20 \
  --settings config/settings.yaml \
  --app-config config/apps/calculator.yaml \
  --teacher-config config/teachers/openai_gpt.yaml \
  --output data/exploration/task_runs/run_001
```

Промпт-шаблон: `config/prompts/action_task_v1.md`

JSON-ответ LLM на каждом шаге:
```json
{
  "button_id": "digit_3",
  "rationale": "enter first number",
  "task_complete": false
}
```


#### Режим 2: автономный exploration (`--exploration`)

LLM самостоятельно планирует и выполняет серию подзадач, покрывая разные операции. Каждая завершенная подзадача архивируется, история сбрасывается, LLM выбирает новую цель. Работает до исчерпания лимита шагов.

```bash
python -m src.exploration.task_runner \
  --exploration \
  --task "Explore GNOME Calculator by solving diverse calculations. Cover addition, subtraction, multiplication, division, multi-step expressions, and edge cases." \
  --max-steps 50 \
  --settings config/settings.yaml \
  --app-config config/apps/calculator.yaml \
  --teacher-config config/teachers/openai_gpt.yaml \
  --output data/exploration/task_runs/explore_001
```

Промпт-шаблон: `config/prompts/action_task_exploration_v1.md`

JSON-ответ LLM на каждом шаге:
```json
{
  "current_goal": "compute 7 × 6 to test multiplication",
  "button_id": "digit_7",
  "rationale": "enter first operand",
  "goal_complete": false,
  "task_complete": false
}
```

При `goal_complete: true` или `button_id: "__done__"` текущая подзадача фиксируется в `completed_goals`, история обнуляется и LLM начинает новую цель.

Пример результата за 20 шагов — 4 завершенные подзадачи:
- `7 + 5 = 12`
- `9 - 4 = 5`
- `5 × 6 = 30`
- `30 ÷ 6 = 5`
- начат `(8 + 2) × 3` (лимит шагов исчерпан)


#### Артефакты запуска

В `--output` директории:
```
step_0000/before.png, after.png, action.json, metadata.json
step_0001/...
...
task_run_report.json # сводный отчет по всему запуску
```

`task_run_report.json` содержит для каждого шага: `button_id`, `rationale`, `latency_s`, `usage` (токены), `current_goal` / `goal_complete` (в exploration-режиме). В exploration-режиме в корне отчета — `completed_goals` и `goals_completed`.

После запуска task runner можно прогнать офлайн-аннотирование поверх собранных шагов стандартной командой `teacher_debug_runner`.


### Часть 6. Эксперимент: dHash + A11Y tree как самообновляемая система координат

#### Идея

Статичный `calculator.yaml` с откалиброванными координатами не работает при смене режима интерфейса (Basic/Advanced/Programming) — кнопки перемещаются или исчезают. Эксперимент доказывает, что можно автоматически отслеживать смену интерфейса через dHash и пересчитывать координаты через повторный захват A11Y-дерева, без ручной перекалибровки.

#### Компоненты

**`src/core/a11y_capture.py`** — захват и парсинг A11Y-дерева:
- `capture_to_xml()` — обходит AT-SPI2 дерево через `pyatspi`, сохраняет в XML с координатами и состояниями элементов
- `parse_xml_to_txt()` — фильтрует по ролям (push-button, label и т.д.), сохраняет в tab-separated TXT
- `find_unchecked_mode_button()` — ищет radio-кнопку режима, которая сейчас не активна (по AT-SPI states)

**`src/core/automation_dhash.py`** — оркестратор пайплайна:
- Координаты кнопок берутся исключительно из A11Y-дерева — зависимость от `calculator.yaml` полностью устранена
- Текущий режим калькулятора читается через `gsettings get org.gnome.calculator button-mode` — надёжнее, чем AT-SPI states (GNOME Calculator не выставляет `checked` на radio-кнопках режима)
- Смена режима — двухшаговая: клик по `"Mode selection"` (открывает попап) → повторный захват A11Y → клик по целевому режиму
- После смены режима выполняется ещё 2 вычисления с обновлёнными координатами (fallback на исходный A11Y, если dHash не изменился)

#### Запуск

```bash
# Виртуальный дисплей должен быть запущен
export DISPLAY=:99

python -m src.core.automation_dhash \
  --output data/exploration/task_runs/run_dhash_001 \
  --display :99 \
  --verbose
```

#### Структура артефактов

```
run_dhash_001/
  screenshot_start.png           # Скриншот в начале пайплайна (после запуска)
  screenshot_final.png           # Скриншот в конце пайплайна (после всех действий)
  a11y_tree_initial.xml          # A11Y дерево до смены режима
  a11y_buttons_initial.txt       # Отфильтрованные кнопки с координатами
  step_0000/ … step_0007/        # 2 вычисления (3+5, 7×4), по кнопке на шаг
  step_0008/                     # Клик "Mode selection" (открытие попапа)
  a11y_tree_popup.xml            # A11Y дерево с открытым попапом режимов
  step_0009/                     # Клик целевого режима (before/after + хеши)
  a11y_tree_after_mode_change.xml   # A11Y дерево после смены режима (если dHash изменился)
  a11y_buttons_after_mode_change.txt
  step_0010/ … step_0017/        # 2 вычисления после смены режима (9−2, 6÷3)
  dhash_comparison.json          # dHash до/после шага смены режима
  run_summary.json               # Полный отчёт по прогону

# Каждый step_XXXX/ содержит: before.png, after.png, action.json, metadata.json
```

#### Результаты эксперимента (Basic → Programming)

dHash сигнал корректно сработал при смене режима Basic → Programming:

```json
{
  "dhash_before": "7070707070000000",
  "dhash_after":  "7070707070700000",
  "dhash_changed": true
}
```

Масштаб изменений интерфейса:

| Метрика | Basic | Programming |
|---|---|---|
| Кнопок (валидных) | 27 | 96 |
| Изменили координаты | — | 22 из 25 общих |
| Появились новые | — | A, B, C, D, E, F (HEX) |

Ключевой вывод: при смене режима координаты большинства кнопок меняются (например, цифра `7` переехала с `(82, 314)` на `(235, 562)`). dHash детектировал изменение, повторный захват A11Y-дерева предоставил актуальные координаты для всех 96 кнопок — без ручной перекалибровки.

### Часть 7. Эксперимент: A11Y tree на каждом шаге + расстояние Хэмминга

#### Идея

Развитие предыдущего эксперимента. Вместо того чтобы захватывать A11Y-дерево один раз в начале и повторно только при детектированной смене режима, A11Y-дерево снимается перед **каждым** действием. Координаты для клика берутся из свежего дерева того же шага. Это устраняет любое расхождение между деревом и реальным состоянием интерфейса — даже при постепенных изменениях, которые dHash не детектирует.

Дополнительно вместо бинарного флага `dhash_changed: true/false` вычисляется **расстояние Хэмминга** между dHash до и после каждого клика. Расстояние Хэмминга — число позиций, в которых биты двух хешей различаются (0 = изображения идентичны, 64 = максимально различны для 8×8 dHash). Это дает количественную меру величины изменения интерфейса на каждом шаге, а не только факт его наличия.

#### Компоненты

**`src/core/automation_a11y.py`** — оркестратор пайплайна:
- На каждом шаге: создаётся `step_NNNN/`, в него захватывается A11Y-дерево, из него берутся координаты, выполняется клик
- Смена режима — двухшаговая (как в `automation_dhash.py`): Step A — клик по `"Mode selection"` (открывает попап), Step B — повторный захват A11Y с открытым попапом → клик по целевому режиму
- Корневых A11Y-файлов (`a11y_tree_initial.xml` и т.п.) нет — все деревья живут внутри `step_NNNN/`
- `dhash_comparison.json` не сохраняется — Хэмминг-расстояние на каждом шаге достаточно

**`src/core/automation.py`** — вычисление расстояния Хэмминга:
- Статический метод `GUIAutomation.hamming_distance(hash1, hash2)` — XOR двух hex-строк, подсчёт единичных бит
- Вызывается в `run_step()` после расчёта dHash до и после действия
- Результат сохраняется в `metadata.json` под ключом `dhashes.hamming_distance`

#### Запуск

```bash
# Виртуальный дисплей должен быть запущен
export DISPLAY=:99

python -m src.core.automation_a11y \
  --output data/exploration/task_runs/run_a11y_001 \
  --display :99 \
  --verbose
```

#### Структура артефактов

```
run_a11y_001/
  screenshot_start.png           # Скриншот в начале пайплайна
  screenshot_final.png           # Скриншот в конце пайплайна
  run_summary.json               # Список шагов с ключевой информацией
  step_0000/
    a11y_tree.xml                # A11Y дерево, снятое перед этим действием
    a11y_buttons.txt             # Только видимые элементы (x≥0, y≥0, w>0, h>0) — ~28 строк вместо ~390
    before.png
    after.png
    action.json
    metadata.json                # dhashes.hamming_distance — изменение интерфейса на шаге
  step_0001/ … step_0007/        # 2 вычисления (3+5, 7×4), по кнопке на шаг
  step_0008/                     # Открытие попапа смены режима
  step_0009/                     # Клик целевого режима
  step_0010/ … step_0017/        # 2 вычисления после смены режима (9−2, 6÷3)
```

Структура `metadata.json` каждого шага (ключевые поля):
```json
{
  "step_id": 9,
  "action": { "button_name": "mode_programming", ... },
  "hashes":  { "before": "a1b2...", "after": "c3d4..." },
  "dhashes": {
    "before": "7070707070000000",
    "after":  "7070707070700000",
    "hamming_distance": 3
  },
  "changed": true
}
```

`run_summary.json` содержит только список шагов с ключевыми полями (`step_id`, `button`, `coords`, `step_dir`) плюс пути и хэши `screenshot_start` / `screenshot_final`. Детальные данные каждого шага живут в `step_NNNN/metadata.json`.

#### Визуализация результатов

После прогона пайплайна можно сгенерировать три диаграммы одной командой:

```bash
python scripts/tools/visualize_run.py \
    --run-dir data/exploration/task_runs/run_a11y_001
```

Скрипт читает `run_summary.json` и `step_NNNN/metadata.json` и сохраняет три PNG-файла в директорию прогона:

| Файл | Что показывает |
|---|---|
| `viz_hamming.png` | Расстояние Хэмминга на каждом шаге (столбчатая диаграмма). Наглядно показывает, какие действия изменяют интерфейс и насколько. Пик на шаге смены режима — количественное подтверждение детектирования изменения. |
| `viz_heatmap.png` | Точки кликов поверх `screenshot_start.png`, пронумерованные и окрашенные по фазе. Показывает пространственное распределение действий и смещение координат после смены режима. |
| `viz_states.png` | Граф переходов между состояниями (узел = уникальное значение dHash, стрелка = действие). Самозацикленные узлы — шаги, не изменившие интерфейс визуально. |

Цветовая схема единая во всех трёх диаграммах: синий — вычисления до смены режима, оранжевый — смена режима, зелёный — вычисления после.

Скрипт обратно совместим: работает на прогонах как нового (`run_a11y_001`, формат `dhashes` + `hamming_distance`), так и старого (`run_dhash_001`, формат `phashes`) форматов. Зависимостей за пределами уже установленного Pillow не требует.

### Часть 8. Эксперимент: перемещение окна + автоматическое обновление координат

#### Идея

Если A11Y-дерево захватывается перед каждым шагом и координаты берутся из него (а не из статичного `calculator.yaml`), то перемещение окна на экране не должно требовать никакой перекалибровки — следующий же шаг сам получит актуальные абсолютные координаты.

Эксперимент это доказывает: пайплайн решает пример, перемещает окно через `wmctrl`, затем решает ещё один пример. Клики второго примера используют координаты из A11Y-дерева, снятого **уже на новом месте**, и попадают точно в кнопки.

#### Компоненты

**`src/core/automation_a11y_dnd.py`** — оркестратор:
- `_get_window_geometry()` — читает позицию, размер и **window ID** калькулятора через `wmctrl -l -G` (парсит строку по ключевому слову `calculator`)
- `_move_window(x, y, wid)` — перемещает окно через `wmctrl -i -r <wid> -e 0,x,y,-1,-1`; использует числовой ID окна вместо совпадения по заголовку — не зависит от локали (заголовок может быть «Calculator», «Калькулятор» и т.д.)
- `_sample_button_coord()` — берёт координату кнопки `=` из A11Y-дерева шага до и после перемещения — для верификации сдвига
- В `run_summary.json` записывается секция `window_move` с позицией до/после (включая `wid`), флагом `wmctrl_success` и вычисленным смещением (`coord_shift`)

**`wmctrl`** — системная CLI-утилита для управления X11-окнами. Уже установлена в VM (`setup_vm.sh`, строка 95). Вызывается через `subprocess.run`, никаких pip-зависимостей.

#### Запуск

```bash
export DISPLAY=:99

python -m src.core.automation_a11y_dnd \
  --output data/exploration/task_runs/run_a11y_dnd_001 \
  --display :99 \
  --move-x 600 --move-y 300 \
  --verbose
```

`--move-x` / `--move-y` — целевые координаты левого верхнего угла окна после перемещения (default: 600, 300).

#### Структура артефактов

```
run_a11y_dnd_001/
  screenshot_start.png          # калькулятор в исходном положении
  screenshot_after_move.png     # калькулятор на новом месте, до следующего клика
  screenshot_final.png
  run_summary.json              # включает секцию window_move с coord_shift
  step_0000/ … step_0003/       # вычисление 3+5=8 в исходном положении
  step_0004/ … step_0007/       # вычисление 2+3=5 на НОВОМ месте (сдвинутые A11Y-координаты)
  step_0008/ step_0009/         # смена режима
  step_0010/ … step_0013/       # вычисление 9−2=7 после смены режима
```

Секция `window_move` в `run_summary.json`:
```json
{
  "window_move": {
    "target": {"x": 600, "y": 300},
    "position_before": {"wid": "0x03200008", "x": 88, "y": 8, "w": 412, "h": 541},
    "position_after":  {"wid": "0x03200008", "x": 1200, "y": 600, "w": 412, "h": 541},
    "wmctrl_success": true,
    "coord_shift": {"dx": 1112, "dy": 592}
  }
}
```

`coord_shift` вычисляется из разницы `position_after - position_before`. Координаты кликов в шагах 4–7 (`after_move`) отличаются от шагов 0–3 (`initial`) ровно на эту величину — AT-SPI2 корректно возвращает абсолютные координаты после перемещения окна без какой-либо перекалибровки.

#### Визуализация

```bash
python scripts/tools/visualize_run_dnd.py \
  --run-dir data/exploration/task_runs/run_a11y_dnd_001
```

Генерирует два файла в директории прогона:

| Файл | Содержание |
|---|---|
| `viz_hamming.png` | Столбчатая диаграмма расстояния Хэмминга по шагам; 4 цвета по фазам; пунктирные вертикальные линии разделяют `initial → after_move` и `after_move → mode_switch` с аннотацией `dx/dy` |
| `viz_heatmap.png` | Пронумерованные кружки кликов поверх `screenshot_start.png`; цвет кружка = фаза; виден сдвиг координат между группами шагов |

#### Зависимость pyatspi

`python3-pyatspi` — системный пакет, недоступный через pip. `setup_vm.sh` устанавливает его через apt и автоматически прописывает путь в venv:

```bash
# Выполняется автоматически в setup_vm.sh:
echo "/usr/lib/python3/dist-packages" > \
  /home/$USER/gui-distill-venv/lib/python3.10/site-packages/system_dist_packages.pth
```
