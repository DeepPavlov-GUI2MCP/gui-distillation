#!/usr/bin/env python3
"""
scripts/tools/visualize_run.py

Post-run visualization for automation_a11y.py (and automation_dhash.py) runs.

Produces three PNG files inside the run directory:
  viz_hamming.png  — Hamming distance per step (bar chart)
  viz_heatmap.png  — Click coordinates overlaid on screenshot_start.png
  viz_states.png   — State-transition graph (unique dHash states → edges = actions)

Requirements: Pillow (already a project dependency)

Backwards-compatible: works with both old "phashes" format (run_dhash_001)
and new "dhashes" + hamming_distance format (run_a11y_001).

Usage:
    python scripts/tools/visualize_run.py \\
        --run-dir data/exploration/task_runs/run_a11y_001
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("ERROR: Pillow required.  pip install Pillow", file=sys.stderr)
    sys.exit(1)

# ── Palette ──────────────────────────────────────────────────────────────────
BG      = (255, 255, 255)
GREY_L  = (230, 230, 230)
GREY_M  = (160, 160, 160)
DARK    = (30,  30,  30)
C_CALC1 = (52,  152, 219)   # blue   — calculations before mode switch
C_MODE  = (230, 126, 34)    # orange — mode-switch steps
C_CALC2 = (39,  174, 96)    # green  — calculations after mode switch

PHASE_COLOR = {"calc_before": C_CALC1, "mode": C_MODE, "calc_after": C_CALC2}
PHASE_LABEL = {"calc_before": "Calc 1", "mode": "Mode switch", "calc_after": "Calc 2"}

# ── Font helpers ─────────────────────────────────────────────────────────────
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


# ── Drawing primitives ───────────────────────────────────────────────────────

def _arrowhead(draw: ImageDraw.ImageDraw,
               tip_x: float, tip_y: float, angle: float,
               size: int = 10, color: tuple = DARK) -> None:
    """Filled triangle arrowhead at `tip` pointing in direction `angle` (radians)."""
    a1 = angle + math.pi * 5 / 6
    a2 = angle - math.pi * 5 / 6
    pts = [
        (int(tip_x), int(tip_y)),
        (int(tip_x + size * math.cos(a1)), int(tip_y + size * math.sin(a1))),
        (int(tip_x + size * math.cos(a2)), int(tip_y + size * math.sin(a2))),
    ]
    draw.polygon(pts, fill=color)


def _straight_arrow(draw: ImageDraw.ImageDraw,
                    x1: float, y1: float, x2: float, y2: float,
                    color: tuple = DARK, width: int = 2, head: int = 10) -> None:
    draw.line([(int(x1), int(y1)), (int(x2), int(y2))], fill=color, width=width)
    angle = math.atan2(y2 - y1, x2 - x1)
    _arrowhead(draw, x2, y2, angle, size=head, color=color)


def _qbezier_pts(p0: Tuple, ctrl: Tuple, p2: Tuple,
                 n: int = 40) -> List[Tuple[int, int]]:
    """Quadratic Bézier approximated as n+1 line-segment points."""
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t ** 2 * p2[1]
        pts.append((int(x), int(y)))
    return pts


# ── Data model ───────────────────────────────────────────────────────────────

def _hamming(h1: Optional[str], h2: Optional[str]) -> Optional[int]:
    if not h1 or not h2:
        return None
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except ValueError:
        return None


class Step:
    """All visualisation-relevant data for one pipeline step."""

    __slots__ = (
        "step_id", "button", "short_label", "phase",
        "coords", "hamming", "dhash_before", "dhash_after",
    )

    def __init__(self, step_id: int, button: str, coords: List[int],
                 hamming: Optional[int],
                 dhash_before: str, dhash_after: str) -> None:
        self.step_id = step_id
        self.button = button
        self.short_label = button[:7]
        self.phase = ""
        self.coords = coords
        self.hamming = hamming
        self.dhash_before = dhash_before
        self.dhash_after = dhash_after


def load_run(run_dir: Path) -> Tuple[dict, List[Step]]:
    """Load run_summary.json + per-step metadata.json."""
    summary_path = run_dir / "run_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"run_summary.json not found in {run_dir}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    steps: List[Step] = []

    for s in summary.get("steps", []):
        step_id = s["step_id"]
        step_dir = Path(s["step_dir"])

        # Load metadata.json
        meta: dict = {}
        mp = step_dir / "metadata.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Coordinates: summary.coords > metadata.action.coordinates
        coords = s.get("coords") or meta.get("action", {}).get("coordinates", [0, 0])

        # dHash: new format "dhashes", fallback to old "phashes"
        dh: dict = meta.get("dhashes") or meta.get("phashes") or {}
        dh_before = dh.get("before") or ""
        dh_after  = dh.get("after")  or ""

        # Hamming: stored in new format; compute from hex strings otherwise
        ham = dh.get("hamming_distance")
        if ham is None:
            ham = _hamming(dh_before, dh_after)

        steps.append(Step(
            step_id=step_id,
            button=s.get("button", "?"),
            coords=coords,
            hamming=ham,
            dhash_before=dh_before,
            dhash_after=dh_after,
        ))

    # Assign phases relative to first mode-switch step
    mode_start: Optional[int] = None
    for s in steps:
        if "mode" in s.button.lower():
            if mode_start is None:
                mode_start = s.step_id

    for s in steps:
        if "mode" in s.button.lower():
            s.phase = "mode"
        elif mode_start is not None and s.step_id > mode_start:
            s.phase = "calc_after"
        else:
            s.phase = "calc_before"

    return summary, steps


# ── Chart 1: Hamming distance bar chart ──────────────────────────────────────

def render_hamming(steps: List[Step], out_path: Path) -> None:
    """Bar chart of Hamming distance (before→after dHash) per step."""

    W, H = 960, 500
    ML, MR, MT, MB = 64, 30, 55, 90   # margins left/right/top/bottom
    CW = W - ML - MR                   # chart width
    CH = H - MT - MB                   # chart height
    MAX_H = 64                         # maximum possible Hamming distance

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(16)
    f_axis  = _font(11)
    f_small = _font(10)

    # Title
    title = "Hamming distance per step  (dHash before → after)"
    draw.text(((W - _tw(draw, title, f_title)) // 2, 14),
              title, font=f_title, fill=DARK)

    # Horizontal grid lines + Y axis labels
    for val in (0, 16, 32, 48, 64):
        gy = MT + CH - int(val / MAX_H * CH)
        draw.line([(ML, gy), (ML + CW, gy)], fill=GREY_L, width=1)
        lbl = str(val)
        draw.text((ML - _tw(draw, lbl, f_small) - 6,
                   gy - _th(draw, lbl, f_small) // 2),
                  lbl, font=f_small, fill=GREY_M)

    # Axes
    draw.line([(ML, MT), (ML, MT + CH)],       fill=DARK, width=2)
    draw.line([(ML, MT + CH), (ML + CW, MT + CH)], fill=DARK, width=2)

    # Y axis title (vertical text approximation — two lines)
    draw.text((4, MT + CH // 2 - 24), "Hamming", font=f_small, fill=GREY_M)
    draw.text((4, MT + CH // 2 - 6),  "distance", font=f_small, fill=GREY_M)

    n = len(steps)
    if n == 0:
        img.save(out_path)
        return

    slot_w = CW // n
    bar_w  = max(4, slot_w - 6)

    for i, s in enumerate(steps):
        xc = ML + i * slot_w + slot_w // 2
        ham = s.hamming if s.hamming is not None else 0

        bar_h = max(2, int(ham / MAX_H * CH))
        x0 = xc - bar_w // 2
        y_top = MT + CH - bar_h
        y_bot = MT + CH

        color = PHASE_COLOR.get(s.phase, GREY_M)
        draw.rectangle([x0, y_top, x0 + bar_w, y_bot], fill=color)

        # Value above bar (only if non-zero and bar tall enough)
        if ham and ham > 0:
            vlbl = str(ham)
            draw.text((xc - _tw(draw, vlbl, f_small) // 2, y_top - 15),
                      vlbl, font=f_small, fill=DARK)

        # Step id below X axis
        sid = str(s.step_id)
        draw.text((xc - _tw(draw, sid, f_small) // 2, MT + CH + 5),
                  sid, font=f_small, fill=GREY_M)

        # Button label further below (truncated)
        draw.text((xc - _tw(draw, s.short_label, f_small) // 2, MT + CH + 20),
                  s.short_label, font=f_small, fill=DARK)

    # X axis labels header
    draw.text((ML, MT + CH + 5),  "step:", font=f_small, fill=GREY_M)

    # Legend
    lx, ly = ML, H - 22
    for phase, color in PHASE_COLOR.items():
        lbl = PHASE_LABEL[phase]
        draw.rectangle([lx, ly, lx + 13, ly + 13], fill=color)
        draw.text((lx + 17, ly + 1), lbl, font=f_small, fill=DARK)
        lx += _tw(draw, lbl, f_small) + 46

    img.save(out_path)
    print(f"  Saved: {out_path}")


# ── Chart 2: Click heatmap ───────────────────────────────────────────────────

def render_heatmap(steps: List[Step], screenshot_path: Path,
                   screen_w: int, screen_h: int, out_path: Path) -> None:
    """Click positions (numbered circles) overlaid on screenshot_start.png."""

    LEGEND_H = 72
    MAX_IMG_W = 1200

    # Background: screenshot or grey placeholder
    if screenshot_path.exists():
        bg = Image.open(screenshot_path).convert("RGB")
    else:
        bg = Image.new("RGB", (screen_w, screen_h), (210, 210, 210))

    scale = min(1.0, MAX_IMG_W / bg.width)
    bw = int(bg.width * scale)
    bh = int(bg.height * scale)
    bg = bg.resize((bw, bh), Image.LANCZOS)

    img = Image.new("RGB", (bw, bh + LEGEND_H), BG)
    img.paste(bg, (0, 0))
    draw = ImageDraw.Draw(img)

    f_title = _font(15)
    f_num   = _font(11)
    f_small = _font(10)

    # Filter out steps with invalid/zero coords
    valid: List[Tuple[Step, int, int]] = []
    for s in steps:
        cx, cy = s.coords[0], s.coords[1]
        if cx == 0 and cy == 0:
            continue
        valid.append((s, int(cx * scale), int(cy * scale)))

    # Connecting lines (drawn first so circles sit on top)
    for i in range(len(valid) - 1):
        _, x1, y1 = valid[i]
        _, x2, y2 = valid[i + 1]
        draw.line([(x1, y1), (x2, y2)], fill=(180, 180, 180), width=1)

    # Circles with step numbers
    R = max(10, int(16 * scale))
    for s, sx, sy in valid:
        color = PHASE_COLOR.get(s.phase, GREY_M)
        draw.ellipse([sx - R, sy - R, sx + R, sy + R],
                     fill=color, outline=BG, width=2)
        lbl = str(s.step_id)
        draw.text((sx - _tw(draw, lbl, f_num) // 2,
                   sy - _th(draw, lbl, f_num) // 2),
                  lbl, font=f_num, fill=BG)

    # Legend strip at bottom
    title = "Click heatmap — A11Y coordinates per step"
    draw.text(((bw - _tw(draw, title, f_title)) // 2, bh + 6),
              title, font=f_title, fill=DARK)

    lx, ly = 20, bh + 46
    for phase, color in PHASE_COLOR.items():
        lbl = PHASE_LABEL[phase]
        draw.ellipse([lx, ly, lx + 14, ly + 14], fill=color)
        draw.text((lx + 18, ly + 1), lbl, font=f_small, fill=DARK)
        lx += _tw(draw, lbl, f_small) + 52

    img.save(out_path)
    print(f"  Saved: {out_path}")


# ── Chart 3: State-transition graph ──────────────────────────────────────────

def render_states(steps: List[Step], out_path: Path) -> None:
    """
    State-transition graph where each node is a unique dHash value and
    each directed edge is a pipeline action (button click).

    Parallel edges (multiple actions between the same two states) are
    aggregated into a single labelled edge to keep the graph readable.
    Self-loops (action that leaves the visual state unchanged) are drawn
    as arcs above the node.
    """

    # ── Build graph ──────────────────────────────────────────────────
    state_order: List[str] = []          # ordered unique state keys
    state_index: Dict[str, int] = {}     # key → index

    def get_state(h: str) -> int:
        key = (h[:16] if h else "?")
        if key not in state_index:
            state_index[key] = len(state_order)
            state_order.append(key)
        return state_index[key]

    # (from_idx, to_idx) → [(button, step_id, phase), ...]
    edge_map: Dict[Tuple[int, int], list] = {}

    for s in steps:
        fi = get_state(s.dhash_before)
        ti = get_state(s.dhash_after)
        key = (fi, ti)
        edge_map.setdefault(key, []).append((s.short_label, s.step_id, s.phase))

    n = len(state_order)
    if n == 0:
        print("  No state data — skipping viz_states.png")
        return

    # ── Layout ───────────────────────────────────────────────────────
    MAX_ROW = 6
    NODE_R  = 36
    H_GAP   = 158
    V_GAP   = 150
    MARGIN  = 88

    n_rows   = math.ceil(n / MAX_ROW)
    canvas_w = min(n, MAX_ROW) * H_GAP + 2 * MARGIN
    canvas_h = n_rows * V_GAP + 2 * MARGIN

    pos: Dict[int, Tuple[int, int]] = {}
    for i in range(n):
        row = i // MAX_ROW
        col = i % MAX_ROW
        n_in_row  = min(MAX_ROW, n - row * MAX_ROW)
        row_span  = n_in_row * H_GAP
        x_origin  = (canvas_w - row_span) // 2 + H_GAP // 2
        pos[i] = (x_origin + col * H_GAP, MARGIN + row * V_GAP)

    FOOTER_H = 52
    img  = Image.new("RGB", (canvas_w, canvas_h + FOOTER_H), BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(16)
    f_node  = _font(11)
    f_prefix = _font(9)
    f_edge  = _font(10)
    f_small = _font(9)

    # Title
    title = "State-transition graph  (unique dHash states)"
    draw.text(((canvas_w - _tw(draw, title, f_title)) // 2, 10),
              title, font=f_title, fill=DARK)

    # ── Edges ────────────────────────────────────────────────────────
    for (fi, ti), actions in edge_map.items():
        x1, y1 = pos[fi]
        x2, y2 = pos[ti]

        # Dominant phase → edge colour
        phases   = [a[2] for a in actions]
        dom      = max(set(phases), key=phases.count)
        color    = PHASE_COLOR.get(dom, GREY_M)

        # Aggregated label (up to 5 actions, then "…")
        labels   = [a[0] for a in sorted(actions, key=lambda a: a[1])[:5]]
        if len(actions) > 5:
            labels.append("…")
        lbl = " · ".join(labels)

        if fi == ti:
            # ── Self-loop: small arc above the node ──
            arc_r = NODE_R + 14
            ax, ay = x1, y1 - NODE_R - arc_r + 10
            draw.arc([ax - arc_r, ay - arc_r, ax + arc_r, ay + arc_r],
                     start=30, end=150, fill=color, width=2)
            # Arrowhead at arc end (≈150 °)
            end_rad = math.radians(150)
            tx = ax + arc_r * math.cos(end_rad)
            ty = ay + arc_r * math.sin(end_rad)
            _arrowhead(draw, tx, ty, math.radians(240), size=8, color=color)
            # Label above arc
            lw = _tw(draw, lbl, f_edge)
            draw.text((ax - lw // 2, ay - arc_r - 16), lbl, font=f_edge, fill=color)

        else:
            # ── Regular edge: straight or curved ──
            dx, dy = x2 - x1, y2 - y1
            dist   = math.hypot(dx, dy) or 1
            ux, uy = dx / dist, dy / dist

            # Start/end on circle perimeters
            sx, sy = x1 + ux * NODE_R, y1 + uy * NODE_R
            ex, ey = x2 - ux * (NODE_R + 10), y2 - uy * (NODE_R + 10)

            row_diff = abs(fi // MAX_ROW - ti // MAX_ROW)
            if row_diff > 0 or abs(fi - ti) > 1:
                # Curved edge — arc bows upward
                lift = -60 * (1 + row_diff * 0.5)
                mid  = ((sx + ex) / 2, (sy + ey) / 2 + lift)
                pts  = _qbezier_pts((sx, sy), mid, (ex, ey))
                draw.line(pts, fill=color, width=2)
                # Arrowhead tangent at the last segment
                if len(pts) >= 2:
                    p_prev, p_last = pts[-2], pts[-1]
                    ang = math.atan2(p_last[1] - p_prev[1], p_last[0] - p_prev[0])
                    _arrowhead(draw, ex, ey, ang, size=9, color=color)
                # Label at curve midpoint
                mid_pt = pts[len(pts) // 2]
                lw = _tw(draw, lbl, f_edge)
                lh = _th(draw, lbl, f_edge)
                draw.rectangle([mid_pt[0] - lw // 2 - 2, mid_pt[1] - lh - 4,
                                 mid_pt[0] + lw // 2 + 2, mid_pt[1] + 2], fill=BG)
                draw.text((mid_pt[0] - lw // 2, mid_pt[1] - lh - 2),
                          lbl, font=f_edge, fill=color)
            else:
                # Straight edge
                _straight_arrow(draw, sx, sy, ex, ey, color=color, width=2, head=9)
                # Label at midpoint (with white background)
                mx = int((sx + ex) / 2)
                my = int((sy + ey) / 2) - 14
                lw = _tw(draw, lbl, f_edge)
                lh = _th(draw, lbl, f_edge)
                draw.rectangle([mx - lw // 2 - 2, my - 2,
                                 mx + lw // 2 + 2, my + lh + 2], fill=BG)
                draw.text((mx - lw // 2, my), lbl, font=f_edge, fill=color)

    # ── Nodes ────────────────────────────────────────────────────────
    for i, key in enumerate(state_order):
        x, y = pos[i]
        draw.ellipse([x - NODE_R, y - NODE_R, x + NODE_R, y + NODE_R],
                     fill=(248, 248, 248), outline=DARK, width=2)

        # State label "S0", "S1", …
        si_lbl = f"S{i}"
        draw.text((x - _tw(draw, si_lbl, f_node) // 2,
                   y - _th(draw, si_lbl, f_node) // 2 - 6),
                  si_lbl, font=f_node, fill=DARK)

        # dHash prefix below
        prefix = key[:8] if key != "?" else "unknown"
        draw.text((x - _tw(draw, prefix, f_prefix) // 2,
                   y + 8),
                  prefix, font=f_prefix, fill=GREY_M)

    # ── Legend ───────────────────────────────────────────────────────
    lx, ly = 16, canvas_h + 16
    for phase, color in PHASE_COLOR.items():
        lbl = PHASE_LABEL[phase]
        draw.rectangle([lx, ly, lx + 13, ly + 13], fill=color)
        draw.text((lx + 17, ly + 1), lbl, font=f_small, fill=DARK)
        lx += _tw(draw, lbl, f_small) + 46

    img.save(out_path)
    print(f"  Saved: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visualize an automation_a11y / automation_dhash pipeline run"
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

    print(f"  Steps loaded : {len(steps)}")
    has_hamming = sum(1 for s in steps if s.hamming is not None)
    print(f"  Hamming data : {has_hamming}/{len(steps)} steps")

    # Screen size from summary (fallback to 1280×1024)
    scr = (summary.get("screenshot_start") or {}).get("path")
    screen_w, screen_h = 1280, 1024

    screenshot_path = run_dir / "screenshot_start.png"

    print("Rendering viz_hamming.png …")
    render_hamming(steps, run_dir / "viz_hamming.png")

    print("Rendering viz_heatmap.png …")
    render_heatmap(steps, screenshot_path, screen_w, screen_h,
                   run_dir / "viz_heatmap.png")

    print("Rendering viz_states.png …")
    render_states(steps, run_dir / "viz_states.png")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
