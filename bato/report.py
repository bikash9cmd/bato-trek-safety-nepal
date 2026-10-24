"""Charts for the README (static PNGs: GitHub renders images, not scripts)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import config  # noqa: E402

IMG = Path(__file__).resolve().parents[1] / "docs/img"
BLUE, ORANGE, INK, MUTED, GRID, SURFACE = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def hudhud(h: dict, path: Path = IMG / "hudhud_2014.png") -> Path:
    runs, obs = h["runs"], h["era5_daily_snow_mm"]
    days = sorted(set(runs) | set(obs))
    fig, ax = plt.subplots(figsize=(8.5, 4.2), facecolor=SURFACE)
    _style(ax)
    x = range(len(days))
    ax.bar([i + 0.18 for i in x], [obs.get(d, 0) for d in days], width=0.34, color=BLUE,
           label="What fell that UTC day (ERA5 snowfall, mm water)")
    for i, d in enumerate(days):
        ms = runs.get(d, {}).get("members", {})
        if ms:
            ax.scatter([i - 0.18] * len(ms), list(ms.values()), s=36, color=ORANGE, edgecolor=SURFACE, linewidth=1.5,
                       zorder=3, label="Forecast that morning: wettest 24 h ahead (5 ensemble members)" if i == 0 else None)
    ax.axhline(config.HOLD_MM_24H, color=INK, linewidth=1, linestyle="--")
    ax.text(len(days) - 0.5, config.HOLD_MM_24H + 2, f"HOLD {config.HOLD_MM_24H:.0f} mm", ha="right", color=INK, fontsize=9)
    ax.axhline(config.WATCH_MM_24H, color=MUTED, linewidth=1, linestyle=":")
    ax.text(len(days) - 0.5, config.WATCH_MM_24H + 2, f"WATCH {config.WATCH_MM_24H:.0f} mm", ha="right", color=MUTED,
            fontsize=9)
    ax.set_xticks(list(x), [d[5:].replace("-", " Oct ").split(" Oct ")[1] + " Oct" for d in days])
    ax.set_ylabel("mm (water equivalent)", color=MUTED, fontsize=9)
    ax.set_title("Thorong La, October 2014: the forecast saw the blizzard a day ahead", loc="left", color=INK,
                 fontsize=11)
    ax.legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=MUTED)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def thresholds(sweep: list[dict], path: Path = IMG / "hold_threshold.png") -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), facecolor=SURFACE)
    t = [r["threshold_mm"] for r in sweep]
    for ax, key, title, color in (
        (axes[0], "pod", "Dangerous snow days warned (share)", BLUE),
        (axes[1], "warning_days_per_pass_per_season", "Hold days per pass per season", ORANGE),
    ):
        _style(ax)
        ax.plot(t, [r[key] for r in sweep], color=color, linewidth=2, marker="o", markersize=5,
                markeredgecolor=SURFACE, markeredgewidth=1.5)
        ax.axvline(config.HOLD_MM_24H, color=INK, linewidth=1, linestyle="--")
        ax.set_title(title, loc="left", color=INK, fontsize=10)
        ax.set_xlabel("HOLD threshold (forecast mm in 24 h)", color=MUTED, fontsize=9)
    axes[0].set_ylim(0, 1.05)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def grace(rows: list[dict], path: Path = IMG / "grace_policy.png") -> Path:
    rows = [r for r in rows if r["policy"] != "family"]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8), facecolor=SURFACE, sharey=True)
    names = [r["policy"].replace("_", " ") for r in rows]
    y = range(len(rows))
    for ax, key, title, color in (
        (axes[0], "false_rescue_cases_per_1000_trek_days", "False rescue cases per 1,000 trek-days", ORANGE),
        (axes[1], "median_h_to_rescue_case", "Median hours from accident to rescue case", BLUE),
    ):
        _style(ax)
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        vals = [r[key] or 0 for r in rows]
        ax.barh(list(y), vals, color=color, height=0.55)
        for i, v in enumerate(vals):
            ax.text(v, i, f" {v:g}", va="center", color=INK, fontsize=9)
        ax.set_title(title, loc="left", color=INK, fontsize=10)
    axes[0].set_yticks(list(y), names)
    axes[0].invert_yaxis()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
