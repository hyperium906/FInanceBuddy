"""The visual layer: meters, composition bars, and the palette behind them.

Every colour here comes from a palette that has been run through a
colour-vision-deficiency validator rather than chosen by eye, in both modes.
The categorical order is fixed and never cycled — a category keeps its colour
when a filter changes how many are on screen, because colour follows the
entity and not its rank.

Two deliberate form choices, because the obvious ones are wrong:

*"How much of this budget is left" is a meter, not a pie.* It is one ratio
against one limit. A meter shows the limit as a track and the spend as a fill,
so "most of the way through" is legible at a glance and overspending can run
past the end of the track — which a pie, having no outside, cannot show at all.

*"Where did it go" is a stacked bar, not a pie.* Human eyes compare lengths
along a shared baseline far better than angles, and a pie of nine categories
is unreadable at the small end exactly where the interesting tail lives.
"""

from __future__ import annotations

from dataclasses import dataclass

import altair as alt
import pandas as pd
import streamlit as st

#: Categorical hues in fixed assignment order, validated for CVD separation in
#: both modes on the adjacent pairlist. Never cycle past the end: an extra
#: series folds into "Other" instead of repeating a colour.
CATEGORICAL_LIGHT = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                     "#e87ba4", "#008300", "#4a3aa7", "#e34948")
CATEGORICAL_DARK = ("#3987e5", "#d95926", "#199e70", "#c98500",
                    "#d55181", "#008300", "#9085e9", "#e66767")

#: Beyond this many categories the tail is unreadable; the rest become "Other".
MAX_SERIES = 7

#: Status palette. Fixed, never themed, and never reused as a series colour.
#: Each is shipped with a label or glyph, so colour never carries meaning alone.
STATUS = {
    "green": "#0ca30c",
    "amber": "#fab219",
    "red": "#d03b3b",
    "none": "#8a8a85",
}

#: Glyph paired with each status, so the state survives greyscale and CVD.
STATUS_GLYPH = {"green": "●", "amber": "▲", "red": "■", "none": "○"}

#: Colour for the "Other" fold and for an unfilled meter track.
NEUTRAL_LIGHT, NEUTRAL_DARK = "#8a8a85", "#9a9a94"


@dataclass(frozen=True, slots=True)
class Theme:
    """The resolved colours for whichever mode the viewer is in."""

    dark: bool

    @property
    def categorical(self) -> tuple[str, ...]:
        return CATEGORICAL_DARK if self.dark else CATEGORICAL_LIGHT

    @property
    def neutral(self) -> str:
        return NEUTRAL_DARK if self.dark else NEUTRAL_LIGHT

    @property
    def track(self) -> str:
        """The unfilled part of a meter.

        A neutral grey at low alpha rather than a fixed hex: it darkens against
        a light surface and lightens against a dark one, so one value is
        correct in both and no third colour enters the palette.
        """
        return "rgba(128,128,128,.22)"

    @property
    def surface(self) -> str:
        """The page background, which is what a gap between fills shows through.

        Taken from ``.streamlit/config.toml`` rather than the palette's generic
        surface, so a 2px separator actually matches the page behind it instead
        of drawing a faint grey line.
        """
        return "#11151a" if self.dark else "#ffffff"

    @property
    def ink(self) -> str:
        return "#e3e6ea" if self.dark else "#16191d"

    @property
    def muted(self) -> str:
        return "#9a9a94" if self.dark else "#52514e"


def theme() -> Theme:
    """The viewer's current mode, defaulting to dark to match config.toml."""
    try:
        return Theme(dark=str(st.context.theme.type).lower() != "light")
    except Exception:  # noqa: BLE001 - no runtime, or an older Streamlit
        return Theme(dark=True)


# --------------------------------------------------------------------------
# Meters — one ratio against one limit
# --------------------------------------------------------------------------


def meter(
    share: float,
    color: str,
    *,
    over: float = 0.0,
    height: int = 14,
) -> str:
    """A meter as inline HTML: a track, a fill, and an overflow segment.

    ``share`` is the fraction of the limit consumed and ``over`` the fraction
    beyond it. Overspending is drawn as a distinct segment past the fill rather
    than by letting the bar run off, so "110% of budget" reads as a tenth again
    rather than as a full bar identical to exactly-on-budget.

    The 2px gap between the two segments is the surface showing through, which
    is what keeps adjacent fills legible without a border.
    """
    palette = theme()
    filled = max(0.0, min(float(share), 1.0)) * 100
    spill = max(0.0, min(float(over), 1.0)) * 100
    radius = "4px"
    parts = [
        f'<div style="background:{color};width:{filled:.1f}%;height:{height}px;'
        f'border-radius:{radius} 0 0 {radius}"></div>'
    ]
    if spill > 0:
        parts.append(
            f'<div style="width:2px;height:{height}px"></div>'
            f'<div style="background:{STATUS["red"]};width:{spill:.1f}%;'
            f'height:{height}px;border-radius:0 {radius} {radius} 0;'
            'opacity:.75"></div>'
        )
    return (
        f'<div style="background:{palette.track};border-radius:{radius};'
        f'height:{height}px;width:100%;display:flex;overflow:hidden">'
        + "".join(parts)
        + "</div>"
    )


def status_meter(spent: float, allowance: float, status: str, height: int = 14) -> str:
    """A budget meter: how much of an allowance is gone, and by how much over."""
    if allowance <= 0:
        return meter(0.0, theme().neutral, height=height)
    share = spent / allowance
    return meter(
        min(share, 1.0),
        STATUS.get(status, STATUS["none"]),
        over=max(share - 1.0, 0.0),
        height=height,
    )


def status_chip(status: str, label: str) -> str:
    """A status glyph and its label, so the state never rests on colour alone."""
    colour = STATUS.get(status, STATUS["none"])
    return (
        f'<span style="color:{colour};font-weight:600">'
        f'{STATUS_GLYPH.get(status, "○")} {label}</span>'
    )


# --------------------------------------------------------------------------
# Composition — part-to-whole
# --------------------------------------------------------------------------


def fold_tail(
    table: pd.DataFrame, label: str, value: str, limit: int = MAX_SERIES
) -> pd.DataFrame:
    """Keep the largest ``limit`` rows and sum the rest into "Other".

    The palette has eight slots and no ninth; folding is what keeps colour
    assignment fixed rather than cycling a hue back onto a second category.
    """
    if table is None or table.empty or len(table) <= limit:
        return table if table is not None else pd.DataFrame(columns=[label, value])

    ordered = table.sort_values(value, ascending=False, kind="stable")
    head, tail = ordered.iloc[:limit], ordered.iloc[limit:]
    folded = pd.DataFrame([{label: "Other", value: float(tail[value].sum())}])
    return pd.concat([head, folded], ignore_index=True)


def composition(
    table: pd.DataFrame,
    label: str = "Category",
    value: str = "Spent",
    height: int = 56,
) -> alt.Chart:
    """A single horizontal stacked bar: what made up the total.

    One bar, segments ordered largest first, so the reader compares lengths on
    a shared baseline instead of angles. A 2px surface gap separates segments —
    the secondary encoding the palette's CVD floor asks for — and every segment
    carries its value in the tooltip.
    """
    palette = theme()
    folded = fold_tail(table, label, value)
    names = list(folded[label])
    colours = list(palette.categorical[: len(names)])
    if "Other" in names:
        colours[names.index("Other")] = palette.neutral

    total = float(folded[value].sum()) or 1.0
    data = folded.assign(
        Share=folded[value] / total,
        Order=range(len(folded)),
    )

    return (
        alt.Chart(data)
        # The 2px stroke is the surface showing through between segments. It is
        # the secondary encoding the palette's CVD floor asks for: adjacent
        # hues that measure close still read as separate blocks.
        .mark_bar(height=height, stroke=palette.surface, strokeWidth=2,
                  cornerRadius=4)
        .encode(
            x=alt.X(f"{value}:Q", stack="normalize", title=None,
                    axis=None, scale=alt.Scale(nice=False)),
            color=alt.Color(
                f"{label}:N",
                scale=alt.Scale(domain=names, range=colours),
                sort=names,
                legend=alt.Legend(title=None, orient="bottom", columns=4,
                                  labelColor=palette.ink, symbolType="square"),
            ),
            order=alt.Order("Order:Q"),
            tooltip=[
                alt.Tooltip(f"{label}:N", title="Category"),
                alt.Tooltip(f"{value}:Q", title="Spent", format="$,.2f"),
                alt.Tooltip("Share:Q", title="Share", format=".1%"),
            ],
        )
        .properties(height=height + 6)
        .configure_view(strokeWidth=0)
        .configure_axis(grid=False, domain=False)
    )


def ranked_bars(
    table: pd.DataFrame,
    label: str = "Category",
    value: str = "Spent",
    height: int = 26,
) -> alt.Chart:
    """Horizontal bars, largest first — for comparing categories against each other.

    Sequential single hue, because the job is magnitude and not identity: a
    reader comparing "which is biggest" needs length, and a second hue would
    imply a distinction the data does not carry.
    """
    palette = theme()
    ordered = table.sort_values(value, ascending=False, kind="stable")
    bar_colour = palette.categorical[0]

    return (
        alt.Chart(ordered)
        .mark_bar(cornerRadiusEnd=4, color=bar_colour, height=height - 8)
        .encode(
            y=alt.Y(f"{label}:N", sort="-x", title=None,
                    axis=alt.Axis(labelColor=palette.ink, domain=False,
                                  ticks=False, labelLimit=200)),
            x=alt.X(f"{value}:Q", title=None,
                    axis=alt.Axis(labelColor=palette.muted, format="$,.0f",
                                  grid=True, gridOpacity=.15, domain=False, ticks=False)),
            tooltip=[
                alt.Tooltip(f"{label}:N", title="Category"),
                alt.Tooltip(f"{value}:Q", title="Spent", format="$,.2f"),
            ],
        )
        .properties(height=max(len(ordered) * height, height))
        .configure_view(strokeWidth=0)
    )


def trend(
    table: pd.DataFrame,
    x: str = "Period",
    series: str = "Measure",
    value: str = "Amount",
) -> alt.Chart:
    """Money in against money out, one line each, across pay periods.

    Deliberately one axis. Two measures of the same unit belong on one scale;
    a second y-axis would let any relationship be manufactured by rescaling.
    """
    palette = theme()
    names = sorted(set(table[series]))
    return (
        alt.Chart(table)
        .mark_line(point=alt.OverlayMarkDef(size=60, filled=True), strokeWidth=2)
        .encode(
            x=alt.X(f"{x}:N", title=None, sort=None,
                    axis=alt.Axis(labelColor=palette.muted, labelAngle=0,
                                  domain=False, ticks=False)),
            y=alt.Y(f"{value}:Q", title=None,
                    axis=alt.Axis(labelColor=palette.muted, format="$,.0f",
                                  grid=True, gridOpacity=.15, domain=False, ticks=False)),
            color=alt.Color(
                f"{series}:N",
                scale=alt.Scale(domain=names,
                                range=list(palette.categorical[: len(names)])),
                legend=alt.Legend(title=None, orient="top",
                                  labelColor=palette.ink, symbolType="stroke"),
            ),
            tooltip=[
                alt.Tooltip(f"{x}:N", title="Period"),
                alt.Tooltip(f"{series}:N", title=""),
                alt.Tooltip(f"{value}:Q", title="Amount", format="$,.2f"),
            ],
        )
        .properties(height=220)
        .configure_view(strokeWidth=0)
    )
