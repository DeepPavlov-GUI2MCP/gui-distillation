from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import ValidationError

try:
    from src.skills.teacher_schemas import DeltaResponse, ObservationResponse, GroundedObservationResponse
except ImportError:  # pragma: no cover
    from src.skills.teacher_schemas import DeltaResponse, InventoryResponse as ObservationResponse

from src.teachers.json_parser import RobustJSONParser
from src.teachers.openai_client import OpenAIAnnotatorClient, SettingsLoader
from src.teachers.prompt_loader import PromptLoader

JsonDict = Dict[str, Any]


def _sum_tokens(usages: List[Optional[JsonDict]]) -> JsonDict:
    """Aggregate token counts from a list of usage dicts (handles both Responses and Chat API key names)."""
    input_total = 0
    output_total = 0
    for u in usages:
        if not isinstance(u, dict):
            continue
        input_total += int(u.get("input_tokens") or u.get("prompt_tokens") or 0)
        output_total += int(u.get("output_tokens") or u.get("completion_tokens") or 0)
    return {
        "input_tokens": input_total,
        "output_tokens": output_total,
        "total_tokens": input_total + output_total,
    }


def _model_name_from_teacher_config(teacher_config_path: str) -> str:
    """Read model name from teacher YAML config (e.g. 'gpt-4.1', 'gpt-5.4-mini')."""
    with open(teacher_config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return str(cfg.get("model", "unknown"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class StepRun:
    step: str
    observation_ok: bool
    observation_grounded_ok: bool
    delta_ok: bool
    observation_parse_mode: str
    observation_grounded_parse_mode: str
    delta_parse_mode: str
    latency_observation_s: float
    latency_observation_grounded_s: float
    latency_delta_s: float
    errors: List[str]
    usage_observation: Optional[JsonDict] = None
    usage_observation_grounded: Optional[JsonDict] = None
    usage_delta: Optional[JsonDict] = None


class AnnotatorRunner:
    """
    Offline pass over existing steps:
      - observation(before.png) -> observation.json
      - observation_grounded(before.png) -> observation_grounded_{model}.json
      - delta(before.png, after.png, action.json) -> delta.json

    The grounded observation filename is derived from the model name in the teacher
    config (e.g. gpt-4.1 -> observation_grounded_gpt-4.1.json).
    """

    def __init__(
        self,
        steps_root: Path,
        settings_path: str = "config/settings.yaml",
        teacher_config_path: str = "config/teachers/openai_gpt.yaml",
    ) -> None:
        self.steps_root = steps_root
        self.settings = SettingsLoader.load(settings_path)

        # Step file names (from settings)
        self.before_name = str(SettingsLoader.get(self.settings, "data.step_files.before", "before.png"))
        self.after_name = str(SettingsLoader.get(self.settings, "data.step_files.after", "after.png"))
        self.metadata_name = str(SettingsLoader.get(self.settings, "data.step_files.metadata", "metadata.json"))
        self.action_name = str(SettingsLoader.get(self.settings, "data.step_files.action", "action.json"))

        self.observation_out_name = str(
            SettingsLoader.get(
                self.settings,
                "data.step_files.observation",
                SettingsLoader.get(self.settings, "data.step_files.inventory", "observation.json"),
            )
        )

        # Derive grounded observation filename from model name in teacher config
        model_name = _model_name_from_teacher_config(teacher_config_path)
        self.observation_grounded_out_name = f"observation_grounded_{model_name}.json"

        self.delta_out_name = str(SettingsLoader.get(self.settings, "data.step_files.delta", "delta.json"))

        # Raw/debug filenames
        self.observation_raw_name = self.observation_out_name.replace(".json", "_raw.json")
        self.observation_grounded_raw_name = self.observation_grounded_out_name.replace(".json", "_raw.json")
        self.delta_raw_name = self.delta_out_name.replace(".json", "_raw.json")

        self.keep_raw_on_success = bool(
            SettingsLoader.get(
                self.settings,
                "development.keep_annotator_raw_on_success",
                SettingsLoader.get(self.settings, "development.keep_teacher_raw_on_success", False),
            )
        )

        self.enable_grounded_observation = bool(
            SettingsLoader.get(self.settings, "features.phase_1.use_grounded_observation", False)
        )

        prompts_dir = Path(str(SettingsLoader.get(self.settings, "paths.prompts_dir", "config/prompts")))
        prompt_loader = PromptLoader(prompts_dir)
        self.prompt_observation = prompt_loader.load("observation_v1.md", fallback="inventory_v1.md")
        self.prompt_observation_grounded = prompt_loader.load_optional("observation_grounded_v1.md")
        self.prompt_delta = prompt_loader.load("delta_v1.md")

        self.client = OpenAIAnnotatorClient(
            settings_path=settings_path,
            teacher_config_path=teacher_config_path,
            dotenv_path=".env",
            api_key_env="OPENAI_API_KEY",
        )
        self.parser = RobustJSONParser()

    def _iter_step_dirs(self) -> List[Path]:
        return sorted([p for p in self.steps_root.iterdir() if p.is_dir() and p.name.startswith("step_")])

    def _save_raw_payload(
        self,
        step_dir: Path,
        filename: str,
        *,
        error: Optional[str],
        raw_text: Optional[str],
        parse_mode: Optional[str],
        parse_error: Optional[str],
        parsed_data_preview: Optional[Any],
        prompt_name: str,
        model: Optional[str],
        latency_s: Optional[float],
        usage: Optional[JsonDict] = None,
    ) -> None:
        payload: JsonDict = {
            "error": error,
            "prompt": prompt_name,
            "model": model,
            "latency_s": latency_s,
            "parse_mode": parse_mode,
            "parse_error": parse_error,
            "raw_text": raw_text,
        }

        if usage is not None:
            payload["usage"] = usage

        if parsed_data_preview is not None:
            payload["parsed_data_preview"] = parsed_data_preview

        write_json(step_dir / filename, payload)

    def run(self, max_steps: Optional[int] = None) -> JsonDict:
        step_dirs = self._iter_step_dirs()
        if max_steps is not None:
            step_dirs = step_dirs[:max_steps]

        per_step: List[StepRun] = []

        for sd in step_dirs:
            errs: List[str] = []

            before = sd / self.before_name
            after = sd / self.after_name
            action = sd / self.action_name

            if not before.exists():
                per_step.append(
                    StepRun(
                        step=sd.name,
                        observation_ok=False,
                        observation_grounded_ok=False,
                        delta_ok=False,
                        observation_parse_mode="none",
                        observation_grounded_parse_mode="none",
                        delta_parse_mode="none",
                        latency_observation_s=0.0,
                        latency_observation_grounded_s=0.0,
                        latency_delta_s=0.0,
                        errors=[f"missing {before.name}"],
                    )
                )
                continue

            # -------------------------------------------------
            # OBSERVATION
            # -------------------------------------------------
            observation_ok = False
            observation_mode = "none"
            observation_latency = 0.0
            observation_raw_text: Optional[str] = None
            observation_model: Optional[str] = None
            observation_parse_error: Optional[str] = None
            observation_parsed_preview: Optional[Any] = None
            observation_usage: Optional[JsonDict] = None

            try:
                r = self.client.infer(self.prompt_observation, image_paths=[before], prefer_json=True)
                observation_latency = r.latency_s
                observation_raw_text = r.text
                observation_model = r.model
                observation_usage = r.usage

                parsed = self.parser.parse(r.text)
                observation_mode = parsed.mode
                observation_parse_error = parsed.error

                if parsed.ok and isinstance(parsed.data, dict):
                    observation_parsed_preview = list(parsed.data.keys())
                else:
                    observation_parsed_preview = None

                if not parsed.ok or not isinstance(parsed.data, dict):
                    raise RuntimeError(f"observation parse failed: {parsed.error}")

                observation_obj = ObservationResponse.model_validate(parsed.data)
                write_json(sd / self.observation_out_name, observation_obj.model_dump())
                observation_ok = True

                if self.keep_raw_on_success:
                    self._save_raw_payload(
                        sd,
                        self.observation_raw_name,
                        error=None,
                        raw_text=observation_raw_text,
                        parse_mode=observation_mode,
                        parse_error=observation_parse_error,
                        parsed_data_preview=observation_parsed_preview,
                        prompt_name="observation_v1",
                        model=observation_model,
                        latency_s=observation_latency,
                        usage=observation_usage,
                    )

            except Exception as e:
                err_str = str(e)
                errs.append(f"observation: {err_str}")

                self._save_raw_payload(
                    sd,
                    self.observation_raw_name,
                    error=err_str,
                    raw_text=observation_raw_text,
                    parse_mode=observation_mode,
                    parse_error=observation_parse_error,
                    parsed_data_preview=observation_parsed_preview,
                    prompt_name="observation_v1",
                    model=observation_model,
                    latency_s=observation_latency if observation_latency > 0 else None,
                    usage=observation_usage,
                )

            # -------------------------------------------------
            # GROUNDED OBSERVATION
            # -------------------------------------------------
            observation_grounded_ok = False
            observation_grounded_mode = "none"
            observation_grounded_latency = 0.0
            observation_grounded_raw_text: Optional[str] = None
            observation_grounded_model: Optional[str] = None
            observation_grounded_parse_error: Optional[str] = None
            observation_grounded_parsed_preview: Optional[Any] = None
            observation_grounded_usage: Optional[JsonDict] = None

            if self.enable_grounded_observation and self.prompt_observation_grounded is not None:
                try:
                    rg = self.client.infer(self.prompt_observation_grounded, image_paths=[before], prefer_json=True)
                    observation_grounded_latency = rg.latency_s
                    observation_grounded_raw_text = rg.text
                    observation_grounded_model = rg.model
                    observation_grounded_usage = rg.usage

                    parsed_g = self.parser.parse(rg.text)
                    observation_grounded_mode = parsed_g.mode
                    observation_grounded_parse_error = parsed_g.error

                    if parsed_g.ok and isinstance(parsed_g.data, dict):
                        observation_grounded_parsed_preview = list(parsed_g.data.keys())
                    else:
                        observation_grounded_parsed_preview = None

                    if not parsed_g.ok or not isinstance(parsed_g.data, dict):
                        raise RuntimeError(f"observation_grounded parse failed: {parsed_g.error}")

                    observation_grounded_obj = GroundedObservationResponse.model_validate(parsed_g.data)
                    write_json(sd / self.observation_grounded_out_name, observation_grounded_obj.model_dump())
                    observation_grounded_ok = True

                    if self.keep_raw_on_success:
                        self._save_raw_payload(
                            sd,
                            self.observation_grounded_raw_name,
                            error=None,
                            raw_text=observation_grounded_raw_text,
                            parse_mode=observation_grounded_mode,
                            parse_error=observation_grounded_parse_error,
                            parsed_data_preview=observation_grounded_parsed_preview,
                            prompt_name="observation_grounded_v1",
                            model=observation_grounded_model,
                            latency_s=observation_grounded_latency,
                            usage=observation_grounded_usage,
                        )

                except Exception as e:
                    err_str = str(e)
                    errs.append(f"observation_grounded: {err_str}")

                    self._save_raw_payload(
                        sd,
                        self.observation_grounded_raw_name,
                        error=err_str,
                        raw_text=observation_grounded_raw_text,
                        parse_mode=observation_grounded_mode,
                        parse_error=observation_grounded_parse_error,
                        parsed_data_preview=observation_grounded_parsed_preview,
                        prompt_name="observation_grounded_v1",
                        model=observation_grounded_model,
                        latency_s=observation_grounded_latency if observation_grounded_latency > 0 else None,
                        usage=observation_grounded_usage,
                    )

            # -------------------------------------------------
            # DELTA
            # -------------------------------------------------
            delta_ok = False
            delta_mode = "none"
            delta_latency = 0.0
            delta_raw_text: Optional[str] = None
            delta_model: Optional[str] = None
            delta_parse_error: Optional[str] = None
            delta_parsed_preview: Optional[Any] = None
            delta_usage: Optional[JsonDict] = None

            if not after.exists():
                errs.append(f"delta skipped: missing {after.name}")
                self._save_raw_payload(
                    sd,
                    self.delta_raw_name,
                    error=f"delta skipped: missing {after.name}",
                    raw_text=None,
                    parse_mode=None,
                    parse_error=None,
                    parsed_data_preview=None,
                    prompt_name="delta_v1",
                    model=None,
                    latency_s=None,
                )
            elif not action.exists():
                errs.append(f"delta skipped: missing {action.name}")
                self._save_raw_payload(
                    sd,
                    self.delta_raw_name,
                    error=f"delta skipped: missing {action.name}",
                    raw_text=None,
                    parse_mode=None,
                    parse_error=None,
                    parsed_data_preview=None,
                    prompt_name="delta_v1",
                    model=None,
                    latency_s=None,
                )
            else:
                try:
                    action_data = json.loads(action.read_text(encoding="utf-8"))
                    action_json_str = json.dumps(action_data, ensure_ascii=False)
                    if "{{action_json}}" in self.prompt_delta:
                        delta_prompt = self.prompt_delta.replace("{{action_json}}", action_json_str)
                    else:
                        delta_prompt = self.prompt_delta + "\n\nACTION_JSON:\n" + action_json_str

                    r2 = self.client.infer(delta_prompt, image_paths=[before, after], prefer_json=True)
                    delta_latency = r2.latency_s
                    delta_raw_text = r2.text
                    delta_model = r2.model
                    delta_usage = r2.usage

                    parsed2 = self.parser.parse(r2.text)
                    delta_mode = parsed2.mode
                    delta_parse_error = parsed2.error

                    if parsed2.ok and isinstance(parsed2.data, dict):
                        delta_parsed_preview = list(parsed2.data.keys())
                    else:
                        delta_parsed_preview = None

                    if not parsed2.ok or not isinstance(parsed2.data, dict):
                        raise RuntimeError(f"delta parse failed: {parsed2.error}")

                    delta_obj = DeltaResponse.model_validate(parsed2.data)
                    write_json(sd / self.delta_out_name, delta_obj.model_dump())
                    delta_ok = True

                    if self.keep_raw_on_success:
                        self._save_raw_payload(
                            sd,
                            self.delta_raw_name,
                            error=None,
                            raw_text=delta_raw_text,
                            parse_mode=delta_mode,
                            parse_error=delta_parse_error,
                            parsed_data_preview=delta_parsed_preview,
                            prompt_name="delta_v1",
                            model=delta_model,
                            latency_s=delta_latency,
                            usage=delta_usage,
                        )

                except Exception as e:
                    err_str = str(e)
                    errs.append(f"delta: {err_str}")
                    self._save_raw_payload(
                        sd,
                        self.delta_raw_name,
                        error=err_str,
                        raw_text=delta_raw_text,
                        parse_mode=delta_mode,
                        parse_error=delta_parse_error,
                        parsed_data_preview=delta_parsed_preview,
                        prompt_name="delta_v1",
                        model=delta_model,
                        latency_s=delta_latency if delta_latency > 0 else None,
                        usage=delta_usage,
                    )

            per_step.append(
                StepRun(
                    step=sd.name,
                    observation_ok=observation_ok,
                    observation_grounded_ok=observation_grounded_ok,
                    delta_ok=delta_ok,
                    observation_parse_mode=observation_mode,
                    observation_grounded_parse_mode=observation_grounded_mode,
                    delta_parse_mode=delta_mode,
                    latency_observation_s=observation_latency,
                    latency_observation_grounded_s=observation_grounded_latency,
                    latency_delta_s=delta_latency,
                    errors=errs,
                    usage_observation=observation_usage,
                    usage_observation_grounded=observation_grounded_usage,
                    usage_delta=delta_usage,
                )
            )

        observation_ok_count = sum(1 for r in per_step if r.observation_ok)
        observation_grounded_ok_count = sum(1 for r in per_step if r.observation_grounded_ok)
        delta_ok_count = sum(1 for r in per_step if r.delta_ok)

        observation_latency_values = [r.latency_observation_s for r in per_step if r.latency_observation_s > 0]
        observation_grounded_latency_values = [
            r.latency_observation_grounded_s for r in per_step if r.latency_observation_grounded_s > 0
        ]
        delta_latency_values = [r.latency_delta_s for r in per_step if r.latency_delta_s > 0]

        total_tokens = _sum_tokens(
            [r.usage_observation for r in per_step]
            + [r.usage_observation_grounded for r in per_step]
            + [r.usage_delta for r in per_step]
        )

        report: JsonDict = {
            "steps_root": str(self.steps_root),
            "total_steps": len(per_step),
            "observation_ok": observation_ok_count,
            "observation_grounded_ok": observation_grounded_ok_count,
            "delta_ok": delta_ok_count,
            "observation_ok_rate": observation_ok_count / len(per_step) if per_step else 0.0,
            "observation_grounded_ok_rate": observation_grounded_ok_count / len(per_step) if per_step else 0.0,
            "delta_ok_rate": delta_ok_count / len(per_step) if per_step else 0.0,
            "avg_latency_observation_s": (
                sum(observation_latency_values) / len(observation_latency_values)
                if observation_latency_values else 0.0
            ),
            "avg_latency_observation_grounded_s": (
                sum(observation_grounded_latency_values) / len(observation_grounded_latency_values)
                if observation_grounded_latency_values else 0.0
            ),
            "avg_latency_delta_s": (
                sum(delta_latency_values) / len(delta_latency_values)
                if delta_latency_values else 0.0
            ),
            "total_tokens": total_tokens,
            "per_step": [
                {
                    "step": r.step,
                    "observation_ok": r.observation_ok,
                    "observation_grounded_ok": r.observation_grounded_ok,
                    "delta_ok": r.delta_ok,
                    "observation_parse_mode": r.observation_parse_mode,
                    "observation_grounded_parse_mode": r.observation_grounded_parse_mode,
                    "delta_parse_mode": r.delta_parse_mode,
                    "latency_observation_s": r.latency_observation_s,
                    "latency_observation_grounded_s": r.latency_observation_grounded_s,
                    "latency_delta_s": r.latency_delta_s,
                    "errors": r.errors,
                    "usage": {
                        "observation": r.usage_observation,
                        "observation_grounded": r.usage_observation_grounded,
                        "delta": r.usage_delta,
                    },
                }
                for r in per_step
            ],
        }

        write_json(self.steps_root / "annotator_debug_report.json", report)
        return report


def main() -> int:
    ap = argparse.ArgumentParser("annotator_runner")
    ap.add_argument("--steps-root", required=True)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--teacher-config", default="config/teachers/openai_gpt.yaml")
    ap.add_argument("--max-steps", type=int, default=None)
    args = ap.parse_args()

    runner = AnnotatorRunner(
        steps_root=Path(args.steps_root),
        settings_path=args.settings,
        teacher_config_path=args.teacher_config,
    )
    runner.run(max_steps=args.max_steps)
    print(f"Done: {Path(args.steps_root) / 'annotator_debug_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
