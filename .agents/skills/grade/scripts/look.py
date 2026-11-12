import colorsys
import math

A, B, C = 0.17883277, 0.28466892, 0.55991073
BT2020_TO_709 = [[1.6605, -0.5876, -0.0728], [-0.1246, 1.1329, -0.0083], [-0.0182, -0.1006, 1.1187]]
NEUTRAL = dict(curve=[[0, 0], [1, 1]], sat=1.0, skin_hue=0, skin_width=28, skin_shift=0, skin_lift=0,
               wall_center=0, wall_width=28, wall_min_sat=0.42, wall_min_light=0.35, wall_deepen=0, wall_hue=0,
               spill_hue=225, spill_width=40, spill_desat=0, lift_tint=[1, 1, 1])
WHITE = 4.93


def hlg_inv(x):
    return x * x / 3 if x <= 0.5 else (math.exp((x - C) / A) + B) / 12


def luma709(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def hable(x):
    a, b, c, d, e, f = 0.15, 0.50, 0.10, 0.20, 0.02, 0.30
    return (x * (a * x + c * b) + d * e) / (x * (a * x + b) + d * f) - e / f


def monotone(pts):
    """Fritsch-Carlson spline: a tone curve through the points that never overshoots."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    n = len(xs)
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0 if d[i - 1] * d[i] <= 0 else 2 / (1 / d[i - 1] + 1 / d[i]) for i in range(1, n - 1)] + [d[-1]]

    def f(x):
        x = min(max(x, 0), 1)
        i = n - 2 if x >= 1 else max(j for j in range(n - 1) if xs[j] <= x)
        h = xs[i + 1] - xs[i]
        t = (x - xs[i]) / h
        return ((2 * t**3 - 3 * t**2 + 1) * ys[i] + (t**3 - 2 * t**2 + t) * h * m[i]
                + (-2 * t**3 + 3 * t**2) * ys[i + 1] + (t**3 - t**2) * h * m[i + 1])
    return f


def band(v, lo, hi, soft):
    return min(1, max(0, (v - lo) / soft)) * min(1, max(0, (hi - v) / soft))


def hue_window(hd, center, width):
    d = (hd - center + 180) % 360 - 180
    return 0.5 * (1 + math.cos(math.pi * d / width)) if abs(d) < width else 0


def make_transform(look, gains=(1, 1, 1)):
    P = dict(NEUTRAL, **look)
    curve = monotone(P['curve'])

    def transform(r, g, b):
        rgb = [hlg_inv(v) * k for v, k in zip((r, g, b), gains)]
        Y = 0.2627 * rgb[0] + 0.6780 * rgb[1] + 0.0593 * rgb[2]
        s = Y ** 0.2 if Y > 0 else 0  # HLG system gamma 1.2 (1000-nit reference)
        rgb = [sum(BT2020_TO_709[i][j] * rgb[j] * s for j in range(3)) for i in range(3)]
        # Lamp colours sit outside Rec.709; pulling them toward grey keeps their hue, where clipping
        # a channel would shift it (the red wall turned orange).
        Y, mn = luma709(rgb), min(rgb)
        if mn < 0 and Y > 0:
            t = Y / (Y - mn)
            rgb = [Y + (c - Y) * t for c in rgb]
        rgb = [hable(max(c, 0) / 0.203) / hable(WHITE) for c in rgb]
        rgb = [min(max(c, 0), 1) for c in rgb]
        rgb = [4.5 * c if c < 0.018 else 1.099 * c ** 0.45 - 0.099 for c in rgb]

        Y = luma709(rgb)
        if Y > 0:
            y2 = curve(min(Y, 1))
            if y2 >= Y:
                # Additive lift: scaling near-black pixels would amplify their noise and blue cast.
                rgb = [c + (y2 - Y) * t for c, t in zip(rgb, P['lift_tint'])]
            else:
                rgb = [c * y2 / Y for c in rgb]

        rgb = [min(max(c, 0), 1) for c in rgb]
        h, l, sat = colorsys.rgb_to_hls(*rgb)
        # Lamp light reflected off the performer (white shirt, black clothes) is weakly saturated;
        # the wall itself is near full saturation, so a saturation ceiling separates them.
        spill = hue_window(h * 360, P['spill_hue'], P['spill_width']) * min(1, max(0, (0.5 - sat) / 0.15))
        if spill:
            # Near-white pixels have noisy hue (compression), so this must only touch colour:
            # pulling toward luma keeps brightness fixed and the noise never becomes blotches.
            Y = luma709(rgb)
            k = 1 - P['spill_desat'] * spill
            rgb = [Y + (c - Y) * k for c in rgb]
            h, l, sat = colorsys.rgb_to_hls(*rgb)
        hd = h * 360
        # Under a red lamp, skin and wall share a hue (~0-10 deg); they differ in saturation and lightness.
        skin = hue_window(hd, P['skin_hue'], P['skin_width']) * band(sat, 0.08, 0.48, 0.08) * band(l, 0.08, 0.46, 0.08)
        hd0 = hd
        hd += P['skin_shift'] * skin
        l = min(1, l * (1 + P['skin_lift'] * skin))
        wall = (hue_window(hd0, P['wall_center'], P['wall_width'])
                * min(1, max(0, (sat - P['wall_min_sat']) / 0.1)) * min(1, max(0, (l - P['wall_min_light']) / 0.1)))
        hd += P['wall_hue'] * wall
        sat = min(1, sat * (1 + (P['sat'] - 1) * (1 - sat)))
        rgb = colorsys.hls_to_rgb((hd % 360) / 360, l, sat)
        return [min(max(c * (1 - P['wall_deepen'] * wall), 0), 1) for c in rgb]

    return transform


def write_cube(path, look, gains=(1, 1, 1), n=65):
    f = make_transform(look, gains)
    lines = [f'LUT_3D_SIZE {n}']
    for bi in range(n):
        for gi in range(n):
            for ri in range(n):
                lines.append('%.6f %.6f %.6f' % tuple(f(ri / (n - 1), gi / (n - 1), bi / (n - 1))))
    with open(path, 'w') as fh:
        fh.write('\n'.join(lines) + '\n')
