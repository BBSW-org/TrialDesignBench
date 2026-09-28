"""Generate the TrialDesignBench logo and favicon.

Run through `docs/scripts/logo.sh`, which provides fontTools. Writes
`docs/assets/logo.svg` and `docs/assets/favicon.svg`.

The wordmark is set in Special Gothic Condensed One and converted to
outlines: SVGs shown through `<img>` (README, docs header) cannot load
web fonts.

The inner pattern draws two fans of Wang-Tsiatis group sequential
boundaries, z(t) = z_final * t^(delta - 1/2), from near Pocock (flat) to
steeper than O'Brien-Fleming, all meeting at the final analysis. The
lower fan is the upper one rotated 180 degrees.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen  # type: ignore[import-not-found]
from fontTools.pens.svgPathPen import SVGPathPen  # type: ignore[import-not-found]
from fontTools.pens.transformPen import TransformPen  # type: ignore[import-not-found]
from fontTools.ttLib import TTFont  # type: ignore[import-not-found]

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
FONT = HERE / "SpecialGothicCondensedOne-Regular.ttf"
WORDMARK = "TrialDesignBench"

# Palette: apricot -> coral -> plum.
RING = "#FBF7F4"
BORDER = ("#FFB25C", "#EF5B5B", "#6A1F4F")
INNER = ("#FFFDFB", "#F8E4DC")
CURVES = ("#F49A3F", "#E0505F", "#8C2A63")
TEXT = ("#5A1A45", "#C0364F")
SHADOW = "#3B1030"

Point = tuple[float, float]


@cache
def font() -> TTFont:
    return TTFont(FONT)


def fmt(value: float) -> str:
    """Format a coordinate with at most two decimals."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def pt(point: Point) -> str:
    return f"{fmt(point[0])} {fmt(point[1])}"


def mix(stops: Sequence[str], u: float) -> str:
    """Color at `u` in [0, 1] along evenly spaced hex color stops."""
    scaled = u * (len(stops) - 1)
    i = min(int(scaled), len(stops) - 2)
    w = scaled - i
    a, b = (bytes.fromhex(color[1:]) for color in stops[i : i + 2])
    return "#" + "".join(f"{round(x + (y - x) * w):02X}" for x, y in zip(a, b))


def gradient(name: str, stops: Sequence[str], start: Point, end: Point) -> str:
    offsets = "".join(
        f'<stop offset="{fmt(i / (len(stops) - 1))}" stop-color="{color}"/>'
        for i, color in enumerate(stops)
    )
    return (
        f'<linearGradient id="{name}" x1="{fmt(start[0])}" y1="{fmt(start[1])}" '
        f'x2="{fmt(end[0])}" y2="{fmt(end[1])}" gradientUnits="userSpaceOnUse">'
        f"{offsets}</linearGradient>"
    )


def hexagon(center: Point, apothem: float, radius: float) -> str:
    """Path of a pointy-top hexagon with rounded corners."""
    cx, cy = center
    circumradius = apothem * 2 / math.sqrt(3)
    vertices = [
        (cx + circumradius * math.cos(a), cy - circumradius * math.sin(a))
        for a in (math.radians(90 - 60 * i) for i in range(6))
    ]
    # Arcs meet the sides radius / sqrt(3) away from each 120 degree corner.
    inset = radius / math.sqrt(3) / circumradius
    d = ""
    for i, (vx, vy) in enumerate(vertices):
        px, py = vertices[i - 1]
        nx, ny = vertices[(i + 1) % 6]
        start = (vx + (px - vx) * inset, vy + (py - vy) * inset)
        end = (vx + (nx - vx) * inset, vy + (ny - vy) * inset)
        d += f"{'L' if d else 'M'}{pt(start)}A{fmt(radius)} {fmt(radius)} 0 0 1 {pt(end)}"
    return d + "Z"


def boundary(
    delta: float, left: float, right: float, z_final: float, reach: float
) -> list[Point]:
    """Cubic Bezier points of one boundary, relative to the hexagon center.

    Information time t runs from x = `left` (t = 0) to x = `right`
    (t = 1). The curve starts where it leaves the square of half-width
    `reach`, and ends at (right, -z_final).
    """
    power = delta - 0.5
    span = right - left
    t_min = max((reach / z_final) ** (1 / power), (-reach - left) / span)

    def at(t: float) -> tuple[Point, Point]:
        z = z_final * t**power
        return (left + span * t, -z), (span, -z * power / t)

    # Geometric spacing suits the power law; Hermite segments are exact
    # at the knots and within 0.01 units in between.
    segments = 8
    knots = [t_min ** (1 - i / segments) for i in range(segments + 1)]
    points = [at(knots[0])[0]]
    for t0, t1 in pairwise(knots):
        (p0, d0), (p1, d1) = at(t0), at(t1)
        h = (t1 - t0) / 3
        points += [
            (p0[0] + d0[0] * h, p0[1] + d0[1] * h),
            (p1[0] - d1[0] * h, p1[1] - d1[1] * h),
            p1,
        ]
    return points


def pattern(
    center: Point,
    inner: float,
    count: int,
    width: float,
    left: float = -120.0,
    right: float = 64.0,
    z_final: float = 24.0,
    deltas: tuple[float, float] = (0.3, -0.9),
    dot: float = 2.5,
) -> str:
    """Two fans of boundaries; the lower fan is rotated 180 degrees.

    `inner` is the apothem of the inner area; curves start beyond it.
    """
    cx, cy = center
    reach = inner * 2 / math.sqrt(3)
    lines = []
    for sign in (1, -1):
        for k in range(count):
            u = k / (count - 1)
            delta = deltas[0] + (deltas[1] - deltas[0]) * u
            points = [
                (cx + sign * x, cy + sign * y)
                for x, y in boundary(delta, left, right, z_final, reach)
            ]
            d = f"M{pt(points[0])}C" + " ".join(pt(p) for p in points[1:])
            color = mix(CURVES, 0.5 - 0.5 * sign * u)
            lines.append(f'<path d="{d}" stroke="{color}"/>')
        final = (cx + sign * right, cy - sign * z_final)
        lines.append(
            f'<circle cx="{fmt(final[0])}" cy="{fmt(final[1])}" r="{fmt(dot)}" '
            f'fill="{mix(CURVES, 0.5)}" stroke="#fff" stroke-width="{fmt(dot * 0.36)}"/>'
        )
    return (
        f'<g fill="none" stroke-width="{fmt(width)}" stroke-linecap="round">'
        + "".join(lines)
        + "</g>"
    )


def wordmark(size: float, tracking: float, origin: Point) -> tuple[str, Point]:
    """Outlined wordmark path and its horizontal ink extent."""
    glyphs = font().getGlyphSet()
    cmap = font().getBestCmap()
    scale = size / font()["head"].unitsPerEm
    path = SVGPathPen(glyphs, ntos=fmt)
    bounds = BoundsPen(glyphs)
    x = origin[0]
    for char in WORDMARK:
        glyph = glyphs[cmap[ord(char)]]
        transform = (scale, 0, 0, -scale, x, origin[1])
        glyph.draw(TransformPen(path, transform))
        glyph.draw(TransformPen(bounds, transform))
        x += (glyph.width + tracking) * scale
    return path.getCommands(), (bounds.bounds[0], bounds.bounds[2])


@dataclass(frozen=True)
class Badge:
    """Shared hexagon layers: light ring, color border, light inner area."""

    apothem: float
    radius: float
    ring: float
    border: float

    @property
    def width(self) -> float:
        return 2 * self.apothem

    @property
    def height(self) -> float:
        # The rounded top and bottom corners sit below the vertices.
        corner = self.radius * (2 / math.sqrt(3) - 1)
        return 2 * (self.apothem * 2 / math.sqrt(3) - corner)

    @property
    def center(self) -> Point:
        return self.width / 2, self.height / 2

    @property
    def inner(self) -> float:
        """Apothem of the light inner area."""
        return self.apothem - self.ring - self.border

    def defs(self) -> str:
        cx, cy = self.center
        circumradius = self.apothem * 2 / math.sqrt(3)
        top, bottom = cy - circumradius, cy + circumradius
        return gradient(
            "tdb-border", BORDER, (cx - 40, top), (cx + 40, bottom)
        ) + gradient("tdb-inner", INNER, (0, cy - 0.6 * circumradius), (0, bottom))

    def body(self) -> str:
        layers = (
            (0, RING),
            (self.ring, "url(#tdb-border)"),
            (self.ring + self.border, "url(#tdb-inner)"),
        )
        return "".join(
            f'<path d="{hexagon(self.center, self.apothem - d, self.radius - d)}" '
            f'fill="{fill}"/>'
            for d, fill in layers
        )

    def clip(self, inset: float) -> str:
        """Clip path for the pattern, `inset` inside the inner area."""
        return (
            '<clipPath id="tdb-clip"><path d="'
            f'{hexagon(self.center, self.inner - inset, 4)}"/></clipPath>'
        )


def logo() -> str:
    b = Badge(apothem=86, radius=18, ring=5, border=5)
    cx, cy = b.center
    curves = pattern(b.center, b.inner, count=14, width=0.75)

    banner_width, banner_height, padding = 132.0, 36.0, 7.0
    banner = (cx - banner_width / 2, cy - banner_height / 2)
    # Fit the ink width to the banner, then center the cap height.
    tracking = 18.0
    _, (x0, x1) = wordmark(1.0, tracking, (0, 0))
    size = (banner_width - 2 * padding) / (x1 - x0)
    cap_height = font()["OS/2"].sCapHeight / font()["head"].unitsPerEm
    origin = (banner[0] + padding - x0 * size, cy + cap_height * size / 2)
    text, (left, right) = wordmark(size, tracking, origin)

    shadow = "".join(
        f'<feDropShadow dx="{dx}" dy="{dy}" stdDeviation="{sd}" '
        f'flood-color="{SHADOW}" flood-opacity="{opacity}"/>'
        for dx, dy, sd, opacity in (
            (0, 0.8, 0.6, 0.16),
            (0.8, 2, 1.2, 0.1),
            (1.6, 4.5, 2.2, 0.06),
        )
    )
    return svg(
        b.width,
        b.height,
        '<title id="tdb-title">TrialDesignBench</title>',
        "<defs>",
        b.defs(),
        gradient("tdb-text", TEXT, (left, 0), (right, 0)),
        b.clip(3),
        '<filter id="tdb-shadow" x="-20%" y="-50%" width="140%" height="220%" '
        f'color-interpolation-filters="sRGB">{shadow}</filter>',
        "</defs>",
        b.body(),
        f'<g clip-path="url(#tdb-clip)">{curves}</g>',
        f'<rect x="{fmt(banner[0])}" y="{fmt(banner[1])}" '
        f'width="{fmt(banner_width)}" height="{fmt(banner_height)}" rx="3" '
        'fill="#fff" filter="url(#tdb-shadow)"/>',
        f'<path d="{text}" fill="url(#tdb-text)"/>',
        label="tdb-title",
    )


def favicon() -> str:
    """Bolder, text-free badge that stays legible at 16 to 32 pixels."""
    b = Badge(apothem=86, radius=24, ring=9, border=16)
    curves = pattern(
        b.center,
        b.inner,
        count=3,
        width=11,
        left=-100,
        right=50,
        z_final=14,
        deltas=(0.3, -1.5),
        dot=12,
    )
    side = b.height
    offset = (side - b.width) / 2
    return svg(
        side,
        side,
        f"<defs>{b.defs()}{b.clip(4)}</defs>",
        f'<g transform="translate({fmt(offset)} 0)">',
        b.body(),
        f'<g clip-path="url(#tdb-clip)">{curves}</g>',
        "</g>",
    )


def svg(width: float, height: float, *parts: str, label: str = "") -> str:
    size = f'width="{fmt(width)}" height="{fmt(height)}"'
    role = f' role="img" aria-labelledby="{label}"' if label else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" {size} '
        f'viewBox="0 0 {fmt(width)} {fmt(height)}"{role}>\n'
        + "\n".join(parts)
        + "\n</svg>\n"
    )


def main() -> None:
    (ASSETS / "logo.svg").write_text(logo())
    (ASSETS / "favicon.svg").write_text(favicon())


if __name__ == "__main__":
    main()
