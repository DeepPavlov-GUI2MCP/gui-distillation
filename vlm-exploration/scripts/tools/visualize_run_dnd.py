#!/usr/bin/env python3
"""
scripts/tools/visualize_run_dnd.py

Post-run visualization for automation_a11y_dnd.py runs.

Produces two PNG files inside the run directory:
  viz_hamming.png  — Hamming distance per step (bar chart, 4 phases)
  viz_heatmap.png  — Click coordinates overlaid on screenshot_start.png
                     with window-before and window-after rectangles

Requirements: Pillow (already a project dependency)

Usage:
    python scripts/tools/visualize_run_dnd.py \\
        --run-dir data/exploration/task_runs/run_a11y_dnd_001
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow required.  pip install Pillow", file=sys.stderr)
    sys.exit(1)

# ── Palette ───────────────────────────────────────────────────────────────────
BG      = (255, 255, 255)
GREY_L  = (230, 230, 230)
GREY_M  = (160, 160, 160)
DARK    = (30,  30,  30)

C_INITIAL   = (52,  152, 219)   # blue   — initial calculations
C_AFTER_MOVE= (26,  188, 156)   # teal   — calculations after window move
C_MODE      = (230, 126, 34)    # orange — mode-switch steps
C_AFTER_MODE= (39,  174, 96)    # green  — calculations after mode switch

PHASE_COLOR = {
    "initial":    C_INITIAL,
    "after_move": C_AFTER_MOVE,
    "mode_switch":C_MODE,
    "after_mode": C_AFTER_MODE,
}
PHASE_LABEL = {
    "initial":    "Initial calc",
    "after_move": "After move",
    "mode_switch":"Mode switch",
    "after_mode": "After mode",
}

# ── Font helpers ──────────────────────────────────────────────────────────────
_FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


def _font(size: int) -> ImageFont.ImageFont:
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


# ── Data helpers ──────────────────────────────────────────────────────────────

def _hamming(h1: Optional[str], h2: Optional[str]) -> Optional[int]:
    if not h1 or not h2:
        return None
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except ValueError:
        return None


class Step:
    __slots__ = ("step_id", "button", "short_label", "phase", "coords", "hamming")

    def __init__(self, step_id: int, button: str, phase: str,
                 coords: List[int], hamming: Optional[int]) -> None:
        self.step_id = step_id
        self.button = button
        self.short_label = button[:7]
        self.phase = phase
        self.coords = coords
        self.hamming = hamming


def load_run(run_dir: Path) -> Tuple[dict, List[Step]]:
    summary_path = run_dir / "run_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"run_summary.json not found in {run_dir}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    steps: List[Step] = []

    for s in summary.get("steps", []):
        step_id = s["step_id"]
        step_dir = Path(s["step_dir"])

        meta: dict = {}
        mp = step_dir / "metadata.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                pass

        coords = s.get("coords") or meta.get("action", {}).get("coordinates", [0, 0])

        dh: dict = meta.get("dhashes") or meta.get("phashes") or {}
        ham = dh.get("hamming_distance")
        if ham is None:
            ham = _hamming(dh.get("before"), dh.get("after"))

        steps.append(Step(
            step_id=step_id,
            button=s.get("button", "?"),
            phase=s.get("phase", "initial"),
            coords=coords,
            hamming=ham,
        ))

    return summary, steps


# ── Chart 1: Hamming bar chart ────────────────────────────────────────────────

def render_hamming(steps: List[Step], window_move: dict, out_path: Path) -> None:
    """Bar chart of Hamming distance per step with window-move separator."""

    W, H = 1000, 520
    ML, MR, MT, MB = 64, 30, 60, 110
    CW = W - ML - MR
    CH = H - MT - MB
    MAX_H = 64

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(16)
    f_axis  = _font(11)
    f_small = _font(10)
    f_anno  = _font(9)

    # Title
    title = "Hamming distance per step  (dHash before → after)"
    draw.text(((W - _tw(draw, title, f_title)) // 2, 14),
              title, font=f_title, fill=DARK)

    # Grid + Y labels
    for val in (0, 16, 32, 48, 64):
        gy = MT + CH - int(val / MAX_H * CH)
        draw.line([(ML, gy), (ML + CW, gy)], fill=GREY_L, width=1)
        lbl = str(val)
        draw.text((ML - _tw(draw, lbl, f_small) - 6,
                   gy - _th(draw, lbl, f_small) // 2),
                  lbl, font=f_small, fill=GREY_M)

    # Axes
    draw.line([(ML, MT), (ML, MT + CH)],             fill=DARK, width=2)
    draw.line([(ML, MT + CH), (ML + CW, MT + CH)],   fill=DARK, width=2)

    draw.text((4, MT + CH // 2 - 24), "Hamming", font=f_small, fill=GREY_M)
    draw.text((4, MT + CH // 2 - 6),  "distance", font=f_small, fill=GREY_M)

    n = len(steps)
    if n == 0:
        img.save(out_path)
        return

    slot_w = CW // n
    bar_w  = max(4, slot_w - 6)

    # Find boundary between "initial" and "after_move" phases for separator
    move_boundary: Optional[float] = None
    for i in range(len(steps) - 1):
        if steps[i].phase == "initial" and steps[i + 1].phase == "after_move":
            move_boundary = ML + (i + 1) * slot_w

    # Find boundary between "after_move" and "mode_switch"
    mode_boundary: Optional[float] = None
    for i in range(len(steps) - 1):
        if steps[i].phase == "after_move" and steps[i + 1].phase == "mode_switch":
            mode_boundary = ML + (i + 1) * slot_w

    # Draw bars
    for i, s in enumerate(steps):
        xc = ML + i * slot_w + slot_w // 2
        ham = s.hamming if s.hamming is not None else 0

        bar_h = max(2, int(ham / MAX_H * CH))
        x0 = xc - bar_w // 2
        y_top = MT + CH - bar_h

        color = PHASE_COLOR.get(s.phase, GREY_M)
        draw.rectangle([x0, y_top, x0 + bar_w, MT + CH], fill=color)

        if ham and ham > 0:
            vlbl = str(ham)
            draw.text((xc - _tw(draw, vlbl, f_small) // 2, y_top - 15),
                      vlbl, font=f_small, fill=DARK)

        sid = str(s.step_id)
        draw.text((xc - _tw(draw, sid, f_small) // 2, MT + CH + 5),
                  sid, font=f_small, fill=GREY_M)

        draw.text((xc - _tw(draw, s.short_label, f_small) // 2, MT + CH + 20),
                  s.short_label, font=f_small, fill=DARK)

    draw.text((ML, MT + CH + 5), "step:", font=f_small, fill=GREY_M)

    # Window-move vertical separator
    if move_boundary is not None:
        sx = int(move_boundary)
        for gy in range(MT, MT + CH, 8):
            draw.line([(sx, gy), (sx, min(gy + 5, MT + CH))],
                      fill=C_AFTER_MOVE, width=2)
        dx = window_move.get("coord_shift", {}).get("dx", 0)
        dy = window_move.get("coord_shift", {}).get("dy", 0)
        anno = f"Window move  dx={dx:+}  dy={dy:+}"
        aw = _tw(draw, anno, f_anno)
        ax = sx - aw - 6
        if ax < ML:
            ax = sx + 6
        draw.text((ax, MT + 2), anno, font=f_anno, fill=C_AFTER_MOVE)

    # Mode-switch separator
    if mode_boundary is not None:
        sx = int(mode_boundary)
        for gy in range(MT, MT + CH, 8):
            draw.line([(sx, gy), (sx, min(gy + 5, MT + CH))],
                      fill=C_MODE, width=2)

    # Legend
    lx, ly = ML, H - 22
    for phase, color in PHASE_COLOR.items():
        lbl = PHASE_LABEL[phase]
        draw.rectangle([lx, ly, lx + 13, ly + 13], fill=color)
        draw.text((lx + 17, ly + 1), lbl, font=f_small, fill=DARK)
        lx += _tw(draw, lbl, f_small) + 46

    img.save(out_path)
    print(f"  Saved: {out_path}")


# ── Chart 2: Heatmap ──────────────────────────────────────────────────────────

def render_heatmap(steps: List[Step], summary: dict, out_path: Path) -> None:
    """
    Click points overlaid on screenshot_start.png.

    Draws:
    - Dashed rectangle = window position before move (blue)
    - Dashed rectangle = window position after move (teal)
    - Arrow from center-before to center-after indicating the move
    - Numbered circles for each click, colored by phase
    """
    LEGEND_H = 80
    MAX_IMG_W = 1400

    run_dir = out_path.parent
    screenshot_path = run_dir / "screenshot_start.png"

    if screenshot_path.exists():
        bg = Image.open(screenshot_path).convert("RGB")
    else:
        bg = Image.new("RGB", (1280, 1024), (210, 210, 210))

    scale = min(1.0, MAX_IMG_W / bg.width)
    bw = int(bg.width  * scale)
    bh = int(bg.height * scale)
    bg = bg.resize((bw, bh), Image.LANCZOS)

    img = Image.new("RGB", (bw, bh + LEGEND_H), BG)
    img.paste(bg, (0, 0))
    draw = ImageDraw.Draw(img)

    f_title = _font(15)
    f_num   = _font(11)
    f_small = _font(10)

    # Connecting lines between consecutive clicks
    valid: List[Tuple[Step, int, int]] = []
    for s in steps:
        cx, cy = s.coords[0], s.coords[1]
        if cx == 0 and cy == 0:
            continue
        valid.append((s, int(cx * scale), int(cy * scale)))

    for i in range(len(valid) - 1):
        _, x1, y1 = valid[i]
        _, x2, y2 = valid[i + 1]
        draw.line([(x1, y1), (x2, y2)], fill=(200, 200, 200), width=1)

    # Circles with step numbers
    R = max(10, int(14 * scale))
    for s, sx, sy in valid:
        color = PHASE_COLOR.get(s.phase, GREY_M)
        draw.ellipse([sx - R, sy - R, sx + R, sy + R],
                     fill=color, outline=BG, width=2)
        lbl = str(s.step_id)
        draw.text((sx - _tw(draw, lbl, f_num) // 2,
                   sy - _th(draw, lbl, f_num) // 2),
                  lbl, font=f_num, fill=BG)

    # Legend strip
    title = "Click heatmap — A11Y coordinates per step  (window before/after move shown as dashed outlines)"
    draw.text(((bw - _tw(draw, title, _font(13))) // 2, bh + 6),
              title, font=_font(13), fill=DARK)

    lx, ly = 20, bh + 48
    for phase, color in PHASE_COLOR.items():
        lbl = PHASE_LABEL[phase]
        draw.ellipse([lx, ly, lx + 14, ly + 14], fill=color)
        draw.text((lx + 18, ly + 1), lbl, font=f_small, fill=DARK)
        lx += _tw(draw, lbl, f_small) + 52

    img.save(out_path)
    print(f"  Saved: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visualize an automation_a11y_dnd.py pipeline run"
    )
    parser.add_argument(
        "--run-dir", required=True,
        help="Path to the run directory containing run_summary.json",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"ERROR: not a directory: {run_dir}", file=sys.stderr)
        return 1

    print(f"Loading run: {run_dir}")
    summary, steps = load_run(run_dir)

    if not steps:
        print("No steps found in run_summary.json", file=sys.stderr)
        return 1

    print(f"  Steps: {len(steps)}")
    wm = summary.get("window_move", {})
    print(f"  Window move: wmctrl_success={wm.get('wmctrl_success')}  "
          f"coord_shift={wm.get('coord_shift')}")

    print("Rendering viz_hamming.png …")
    render_hamming(steps, wm, run_dir / "viz_hamming.png")

    print("Rendering viz_heatmap.png …")
    render_heatmap(steps, summary, run_dir / "viz_heatmap.png")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
