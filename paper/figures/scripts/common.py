"""Shared, deterministic rendering and validation helpers for paper figures."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[3]
FIGURES = ROOT / "paper" / "figures"
GENERATED = FIGURES / "generated"

# Okabe-Ito colors, paired with markers/linestyles/hatches rather than used alone.
BLUE = "#0072B2"
ORANGE = "#E69F00"
GREEN = "#009E73"
RED = "#D55E00"
PURPLE = "#CC79A7"
SKY = "#56B4E9"
YELLOW = "#F0E442"
INK = "#202124"
GRAY = "#777777"
LIGHT = "#E8E8E8"


def configure() -> None:
    """Apply the paper-wide visual and deterministic-output contract."""
    os.environ.setdefault("SOURCE_DATE_EPOCH", "0")
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.0,
            "axes.labelsize": 8.0,
            "axes.titlesize": 8.0,
            "xtick.labelsize": 7.0,
            "ytick.labelsize": 7.0,
            "legend.fontsize": 7.0,
            "figure.dpi": 160,
            "savefig.dpi": 240,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": INK,
            "axes.labelcolor": INK,
            "text.color": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "svg.fonttype": "none",
            "svg.hashsalt": "k3-paper-issue124",
            "pdf.fonttype": 42,
            "pdf.compression": 9,
        }
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(relative: str, expected_sha256: str | None = None) -> Path:
    path = ROOT / relative
    if not path.is_file():
        raise FileNotFoundError(f"required input missing: {path}")
    if expected_sha256 is not None:
        actual = sha256(path)
        if actual != expected_sha256:
            raise ValueError(
                f"input checksum mismatch for {relative}: expected {expected_sha256}, got {actual}"
            )
    return path


def read_json(relative: str, expected_sha256: str | None = None) -> Any:
    with require_file(relative, expected_sha256).open(encoding="utf-8") as handle:
        return json.load(handle)


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_set(actual: Iterable[Any], expected: Iterable[Any], label: str) -> None:
    actual_set, expected_set = set(actual), set(expected)
    if actual_set != expected_set:
        raise AssertionError(
            f"{label}: missing={sorted(expected_set - actual_set)!r}, "
            f"unexpected={sorted(actual_set - expected_set)!r}"
        )


def panel_label(ax: mpl.axes.Axes, label: str) -> None:
    ax.text(
        -0.08,
        1.03,
        label,
        transform=ax.transAxes,
        fontweight="bold",
        fontsize=9,
        va="bottom",
    )


def evidence_badge(ax: mpl.axes.Axes, text: str, *, x: float = 0.99, y: float = 0.99) -> None:
    ax.text(
        x,
        y,
        text,
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=6.4,
        color=INK,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": GRAY, "lw": 0.7},
        zorder=20,
    )


def save_figure(fig: mpl.figure.Figure, stem: str) -> list[Path]:
    GENERATED.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for suffix in ("svg", "pdf", "png"):
        output = GENERATED / f"{stem}.{suffix}"
        metadata: dict[str, Any]
        if suffix == "pdf":
            metadata = {
                "Creator": "paper/figures issue #124",
                "Producer": "Matplotlib",
                "CreationDate": None,
                "ModDate": None,
            }
        elif suffix == "svg":
            metadata = {"Creator": "paper/figures issue #124", "Date": None}
        else:
            metadata = {"Software": "Matplotlib; paper/figures issue #124"}
        fig.savefig(output, bbox_inches="tight", pad_inches=0.04, metadata=metadata)
        if suffix == "svg":
            # Matplotlib writes harmless spaces after multiline path commands;
            # canonicalize them so repository whitespace validation also passes.
            lines = output.read_text(encoding="utf-8").splitlines()
            output.write_text("\n".join(line.rstrip() for line in lines) + "\n", encoding="utf-8")
        outputs.append(output)
    plt.close(fig)
    return outputs


def draw_box(
    ax: mpl.axes.Axes,
    xy: tuple[float, float],
    width: float,
    height: float,
    text: str,
    *,
    facecolor: str = "white",
    edgecolor: str = INK,
    linestyle: str = "-",
    fontsize: float = 8,
    linewidth: float = 1.0,
) -> mpl.patches.FancyBboxPatch:
    patch = mpl.patches.FancyBboxPatch(
        xy,
        width,
        height,
        boxstyle="round,pad=0.015,rounding_size=0.018",
        facecolor=facecolor,
        edgecolor=edgecolor,
        linestyle=linestyle,
        linewidth=linewidth,
    )
    ax.add_patch(patch)
    ax.text(xy[0] + width / 2, xy[1] + height / 2, text, ha="center", va="center", fontsize=fontsize)
    return patch


def arrow(
    ax: mpl.axes.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str = INK,
    linestyle: str = "-",
    linewidth: float = 1.0,
    text: str | None = None,
    text_offset: tuple[float, float] = (0.0, 0.02),
) -> None:
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={
            "arrowstyle": "-|>",
            "color": color,
            "lw": linewidth,
            "linestyle": linestyle,
            "shrinkA": 2,
            "shrinkB": 2,
        },
    )
    if text:
        ax.text(
            (start[0] + end[0]) / 2 + text_offset[0],
            (start[1] + end[1]) / 2 + text_offset[1],
            text,
            ha="center",
            va="bottom",
            fontsize=6.8,
            color=color,
        )
