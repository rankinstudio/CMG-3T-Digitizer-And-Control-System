#!/usr/bin/env python3
"""CMG-3T board box: base + snap-on lid for the ADC/AUX ElectroCookie stack.

    python box.py               # writes box_base.stl, box_lid.stl, box.png, box_floor.png here

Board: ElectroCookie half-size, 88.9 x 52.1 mm; the stack stands on its M2
corner holes, 78.7 x 35.6 mm centre to centre. The bottom standoffs have a
2 mm threaded stud, 4 mm long: each stands on a short floor pad, the stud goes
down through it and a nut (6 mm) goes on from under the box, sunk into a 10 mm
pocket. Pad + floor is 4 mm, so the stud ends flush with the bottom.

Front wall (y = 0, a long side), left to right from outside: VIN jack (12 V in),
AUX keystone (CTL), ADC keystone (SIG), VOUT jack (sensor power lead), and
above them the LOCK / UNLOCK / CENTRE buttons (7.4 mm holes). Keystone
sockets (parts.py) take Cat6 keystone jacks latch side up, snapped in from
inside; ~49 mm from the front wall to the boards clears the jacks'
punch-down ends and cables.

Right side wall (x = IN_L, seen from the front): the Raspberry Pi 5 mounts on
the outside, on the same stud standoffs as the board (stud in from outside, nut
inside in a 10 mm pocket that thins the wall to 1.5 mm). Pi long side
horizontal, component side out: GPIO header along the top edge, USB/Ethernet
end towards the back, USB-C and HDMI along the bottom edge (12 mm above
the table, so a right-angle USB-C plug). The wires go over the Pi's top edge
into an open-topped slot in the wall, closed off by the lid.

Floor and lid carry a honeycomb of hex holes to save plastic. The lid's inner
lip has six bumps that click into dimples in the base walls; the lid fits one
way round, with its lip gap over the Pi slot.

fit_test.py prints one of each wall opening on a small plate: check the
keystone, DC jack and button fit before printing the box.

Print the base upright (open side up), no supports: the keystone openings bridge
14.9 mm. Print the lid top-down (flat face on the bed).
"""
import os

import numpy as np
import trimesh
from trimesh.creation import box, cylinder

from parts import at, keystone_void, jack_hole, KS_W, KS_H, KS_D, JACK_HOLE

HERE = os.path.dirname(os.path.realpath(__file__))

# ------------------------------------------------------------------ settings --
WALL = KS_D                               # 2.5: keystones cut straight into the wall
FLOOR = 2.0
IN_L, IN_W, IN_H = 96.0, 107.0, 72.0     # inside: length (x), width (y, front to back), height (z)
CORNER_R = 4.0                            # outside corner radius (vertical edges)

BOARD_L, BOARD_W = 88.9, 52.1
HOLE_DX, HOLE_DY = 78.7, 35.6             # M2 corner holes, centre to centre
BOARD_BACK_GAP = 6.0                      # board edge to the back wall
POST_H, POST_D = 2.0, 14.0                # pad under each standoff (covers the nut pocket); FLOOR + POST_H = 4 = stud length
SCREW_D = 2.4                             # clearance for the 2.2 mm stud
NUT_D, NUT_H = 10.0, 2.5                  # nut pocket under the floor; leaves 1.5 mm clamped. Room to
                                          # turn a 6 mm nut (6.9 across corners) with a driver

CONN_Z = 22.0                             # connector centre height above the floor (inside)
#   x positions (inside, from the left inner wall) of the front-wall connectors
CONNECTORS = [('jack', 12.0), ('keystone', 34.0), ('keystone', 58.0), ('jack', 82.0)]

# LOCK / UNLOCK / CENTRE panel buttons, a row above the connectors on the front
# wall; x from the left as seen from OUTSIDE (same as the connectors)
BUTTON_D, BUTTON_Z = 7.4, 50.0               # 7 mm panel buttons
BUTTONS = [('LOCK', 28.0), ('UNLOCK', 48.0), ('CENTRE', 68.0)]

# Raspberry Pi 5 on the outside of the right wall. Pi board 85 x 56, M2.5 holes
# 58 x 49 apart, 3.5 mm in from the edges at the end away from USB/Ethernet.
PI_L, PI_W = 85.0, 56.0
PI_HOLE_DX, PI_HOLE_DY, PI_HOLE_IN = 58.0, 49.0, 3.5
PI_Y0 = IN_W - 88.0                       # board's front (holes) end, inside y; ports (88 incl. overhang) end at the back wall
PI_TOP = 66.0                             # board's top edge, inside z coordinate (base rim at IN_H = 72)
PI_STUD_D = SCREW_D                       # same 2 mm stud standoffs as the floor
PI_NUT_D, PI_CLAMP = NUT_D, 1.5           # nut pocket from inside; wall left under the standoff
PI_SLOT_Y = (PI_Y0 + 10.0, PI_Y0 + 54.0)  # wire slot behind the GPIO header (header runs ~7..58 from the holes end)
PI_SLOT_DEPTH = 15.0                      # from the top edge of the wall

# honeycomb cut-outs in the floor and lid to save plastic: hexes this far across the
# flats, ribs this wide, kept clear of the walls and the standoff pads
HEX_AF, HEX_RIB = 10.0, 2.4
HEX_WALL_MARGIN = 4.0                     # solid floor along the inside of the walls
HEX_PAD_CLEAR = 9.5                       # solid floor this radius around each standoff (pad is 7)

LID_T = 2.0
LIP_H, LIP_T, LIP_GAP = 6.0, 1.6, 0.05    # lid's inner lip: height, thickness, clearance per side (snug)

# snap detents: round bumps on the outside of the lid's lip click into dimples in
# the base's inner walls. Bump h proud of the lip, dimple d deep, both on spheres
# of radius R, DET_DOWN below the base rim (2 mm above the lip's free edge, where it flexes)
DET_R, DET_H, DET_D, DET_DOWN = 2.0, 0.4, 0.5, 4.0
#   (wall, position along it): front / back walls by x, left / right by y. The right
#   one sits in front of the Pi wire slot, clear of the Pi's nut pockets.
DETENTS = [('front', 25.0), ('front', 71.0), ('back', 25.0), ('back', 71.0),
           ('left', IN_W / 2), ('right', 12.0)]


# ---------------------------------------------------------------- helpers --
def rounded_box(l, w, h, r):
    """Box with rounded vertical edges, corner at the origin, z from 0 to h."""
    from shapely.geometry import box as sbox
    poly = sbox(r, r, l - r, w - r).buffer(r, resolution=16)
    return trimesh.creation.extrude_polygon(poly, h)


def along_x(d, x0, x1, y, z):
    """Round hole of diameter d along x, from x0 to x1, centred on (y, z)."""
    c = cylinder(radius=d / 2, height=x1 - x0, sections=48)
    c.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (0, 1, 0)))
    return at(c, (x0 + x1) / 2, y, z)


def onto_front_wall(mesh_in_wall_coords, x_centre, z_centre):
    """parts.py wall features use x across, y up, z into the box from the outer
    face. Map onto the front wall: x -> x, y -> box z, z -> box y (outer face at
    y = -WALL). That swap is a mirror; the features are symmetric in x so it
    only needs the normals fixed."""
    m = mesh_in_wall_coords.copy()
    v = m.vertices.copy()
    m.vertices = np.c_[v[:, 0], v[:, 2], v[:, 1]]
    m.invert()
    if m.volume < 0:
        m.invert()
    return at(m, x_centre, -WALL, z_centre)


def detent_spheres(r, out_from_face):
    """Spheres of radius r, one per detent, in base coordinates, centred on the
    line DET_DOWN below the rim, `out_from_face` mm outward (into the wall) from
    the inner wall face (negative = towards the box centre)."""
    z = IN_H - DET_DOWN
    faces = {'front': ((0, 0), (0, -1)), 'back': ((0, IN_W), (0, 1)),
             'left': ((0, 0), (-1, 0)), 'right': ((IN_L, 0), (1, 0))}
    out = []
    for wall, pos in DETENTS:
        (fx, fy), (nx, ny) = faces[wall]
        x = pos if wall in ('front', 'back') else fx
        y = pos if wall in ('left', 'right') else fy
        out.append((at(trimesh.creation.icosphere(subdivisions=3, radius=r),
                       x + nx * out_from_face, y + ny * out_from_face, z), (nx, ny), (x, y)))
    return out


def lid_bumps():
    """The lid's bumps, in base coordinates (lid in place): sphere caps DET_H proud
    of the lip's outer face, rooted 0.8 mm into the lip."""
    g, caps = LIP_GAP, []
    for sph, (nx, ny), (x, y) in detent_spheres(DET_R, -g - DET_R + DET_H):
        keep = at(box((6 if nx else 2 * DET_R + 1, 6 if ny else 2 * DET_R + 1, 2 * DET_R + 1)),
                  x + nx * (3 - g - 0.8), y + ny * (3 - g - 0.8), IN_H - DET_DOWN)
        caps.append(trimesh.boolean.intersection([sph, keep]))
    return caps


def pad_xy():
    bx = IN_L / 2
    by = IN_W - BOARD_BACK_GAP - BOARD_W / 2
    return [(bx + sx * HOLE_DX / 2, by + sy * HOLE_DY / 2) for sx in (-1, 1) for sy in (-1, 1)]


def honeycomb(z0, t, pads=True):
    """Hex holes from z0 through thickness t: every hex of the grid that fits whole
    inside the box's inner footprint less a wall margin (and, for the floor, a
    ring round each standoff pad). The grid is centred, so it lines up the same
    on the floor and on the flipped-over lid."""
    from shapely.geometry import Point, Polygon, box as sbox
    from shapely.ops import unary_union
    m = HEX_WALL_MARGIN
    keep = sbox(m, m, IN_L - m, IN_W - m)
    if pads:
        keep = keep.difference(unary_union([Point(x, y).buffer(HEX_PAD_CLEAR) for x, y in pad_xy()]))
    r = HEX_AF / np.sqrt(3)                           # circumradius, flats top and bottom
    pitch_x, pitch_y = 1.5 * r + HEX_RIB * np.sqrt(3) / 2, HEX_AF + HEX_RIB
    hexes = []
    for i in range(-1, int(IN_L / pitch_x) + 2):
        for j in range(-1, int(IN_W / pitch_y) + 2):
            cx = IN_L / 2 + (i - int(IN_L / pitch_x) // 2) * pitch_x
            cy = IN_W / 2 + (j - int(IN_W / pitch_y) // 2 + (0.5 if i % 2 else 0)) * pitch_y
            h = Polygon([(cx + r * np.cos(a), cy + r * np.sin(a)) for a in np.arange(6) * np.pi / 3])
            if keep.contains(h):
                hexes.append(h)
    m3 = trimesh.util.concatenate([trimesh.creation.extrude_polygon(h, t + 1) for h in hexes])
    return at(m3, 0, 0, z0 - 0.5)


def build_base():
    L, W = IN_L + 2 * WALL, IN_W + 2 * WALL
    H = FLOOR + IN_H
    outer = at(rounded_box(L, W, H, CORNER_R), -WALL, -WALL, -FLOOR)
    inner = at(rounded_box(IN_L, IN_W, IN_H + 1, max(0.5, CORNER_R - WALL)), 0, 0, 0)
    adds, cuts = [], []

    # standoff pads: stud through, nut pocket underneath
    for x, y in pad_xy():
            adds.append(at(cylinder(radius=POST_D / 2, height=POST_H + 0.5, sections=48), x, y, (POST_H + 0.5) / 2 - 0.5))
            cuts.append(at(cylinder(radius=SCREW_D / 2, height=FLOOR + POST_H + 2, sections=32), x, y, (POST_H - FLOOR) / 2))
            cuts.append(at(cylinder(radius=NUT_D / 2, height=NUT_H + 0.5, sections=48), x, y, -FLOOR + (NUT_H - 0.5) / 2))

    cuts.append(honeycomb(-FLOOR, FLOOR))

    # front-wall connectors
    for kind, x in CONNECTORS:
        if kind == 'keystone':
            off = (-KS_W / 2, -KS_H / 2, 0)                # centre the block on (x, CONN_Z)
            void = at(keystone_void(), *off)
            cuts.append(onto_front_wall(void, x, CONN_Z))
        else:
            cuts.append(onto_front_wall(jack_hole(WALL + 1), x, CONN_Z))

    # panel buttons
    for _, x in BUTTONS:
        cuts.append(onto_front_wall(jack_hole(WALL + 1, BUTTON_D), x, BUTTON_Z))

    # Raspberry Pi on the outside of the right wall: stud holes, nut pockets, wire slot
    xw = IN_L                                         # inside face of the right wall
    for hy in (PI_Y0 + PI_HOLE_IN, PI_Y0 + PI_HOLE_IN + PI_HOLE_DX):
        for hz in (PI_TOP - PI_HOLE_IN, PI_TOP - PI_HOLE_IN - PI_HOLE_DY):
            cuts.append(along_x(PI_STUD_D, xw - 1, xw + WALL + 1, hy, hz))
            cuts.append(along_x(PI_NUT_D, xw - 1, xw + WALL - PI_CLAMP, hy, hz))
    sy0, sy1 = PI_SLOT_Y
    cuts.append(at(box((WALL * 3, sy1 - sy0, PI_SLOT_DEPTH + 1)), xw + WALL / 2, (sy0 + sy1) / 2,
                   IN_H - PI_SLOT_DEPTH / 2 + 0.5))

    # order matters: hollow the shell first, then add what stands inside it
    # (posts), then cut the holes through everything
    shell = trimesh.boolean.difference([outer, inner])
    body = trimesh.boolean.union([shell] + adds)
    # detent dimples in the inner walls, DET_D deep
    cuts += [sph for sph, _, _ in detent_spheres(DET_R, -DET_R + DET_D)]
    return trimesh.boolean.difference([body] + cuts)


def build_lid():
    L, W = IN_L + 2 * WALL, IN_W + 2 * WALL
    top = at(rounded_box(L, W, LID_T, CORNER_R), -WALL, -WALL, 0)
    g = LIP_GAP
    lip_o = at(rounded_box(IN_L - 2 * g, IN_W - 2 * g, LIP_H, max(0.5, CORNER_R - WALL - g)), g, g, LID_T)
    lip_i = at(rounded_box(IN_L - 2 * g - 2 * LIP_T, IN_W - 2 * g - 2 * LIP_T, LIP_H + 1,
                           max(0.5, CORNER_R - WALL - g - LIP_T)), g + LIP_T, g + LIP_T, LID_T)
    lip = trimesh.boolean.difference([lip_o, lip_i])
    # leave the lip open over the Pi wire slot so the wires pass under the lid. The
    # lid is built lip-up and flipped over x to fit, so the slot's y is mirrored here.
    sy0, sy1 = PI_SLOT_Y
    notch = at(box((LIP_T * 4, sy1 - sy0 + 4, LIP_H + 1)), IN_L - g - LIP_T / 2, IN_W - (sy0 + sy1) / 2,
               LID_T + LIP_H / 2 + 0.5)
    lip = trimesh.boolean.difference([lip, notch])
    top = trimesh.boolean.difference([top, honeycomb(0, LID_T, pads=False)])   # same hexes as the floor
    # detent bumps: built in place on the base, then flipped into lid coordinates
    # (the same turn over x that puts the lid on: y -> IN_W - y, z -> IN_H + LID_T - z)
    flip = trimesh.transformations.rotation_matrix(np.pi, (1, 0, 0), (0, IN_W / 2, (IN_H + LID_T) / 2))
    bumps = [b.copy() for b in lid_bumps()]
    for b in bumps:
        b.apply_transform(flip)
    return trimesh.boolean.union([top, lip] + bumps)


def preview(parts, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    views = [(25, -60, 'front (connector wall)'), (25, 30, 'right side (Pi mount)'), (-90, -90, 'underside (front at the bottom)')]
    fig = plt.figure(figsize=(15, 5))
    allv = np.vstack([p.vertices for p, _ in parts])
    lo, hi = allv.min(0), allv.max(0)
    span, c = (hi - lo).max(), (hi + lo) / 2
    for k, (el, az, title) in enumerate(views):
        ax = fig.add_subplot(1, 3, k + 1, projection='3d')
        for m, col in parts:
            tri = m.vertices[m.faces]
            shade = 0.35 + 0.65 * np.clip(m.face_normals @ np.array([0.4, -0.6, 0.7]), 0, 1)
            ax.add_collection3d(Poly3DCollection(tri, facecolors=[(col[0] * s, col[1] * s, col[2] * s) for s in shade],
                                                 edgecolor='none'))
        for f, s in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), c):
            f(s - span / 2, s + span / 2)
        ax.view_init(elev=el, azim=az)
        ax.set_title(title, fontsize=9)
        ax.set_axis_off()
    fig.savefig(path, dpi=95, bbox_inches='tight')


def floor_plan(base, path):
    """Sections through the floor: from below (mirrored, as you'd see it) and
    from above at pad height."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from shapely.geometry import Polygon
    fig, axs = plt.subplots(1, 2, figsize=(13, 6.5))
    for ax, (z, title) in zip(axs, ((-FLOOR / 2, 'floor, seen from below (front at the bottom)'),
                                    (POST_H / 2, 'floor + pads, seen from above (front at the bottom)'))):
        g = Polygon()
        for e in base.section(plane_origin=[0, 0, z], plane_normal=[0, 0, 1]).discrete:
            g = g.symmetric_difference(Polygon(np.asarray(e)[:, :2]).buffer(0))
        flip = (lambda xs: IN_L - np.asarray(xs)) if z < 0 else np.asarray
        for poly in getattr(g, 'geoms', [g]):
            xs, ys = poly.exterior.xy
            ax.fill(flip(xs), ys, color='#c8955a', ec='#7a5530', lw=0.6)
            for ring in poly.interiors:
                xs, ys = ring.xy
                ax.fill(flip(xs), ys, color='white', ec='#7a5530', lw=0.6)
        ax.set_aspect('equal')
        ax.set_title(title, fontsize=10)
        ax.axis('off')
    fig.savefig(path, dpi=90, bbox_inches='tight')


if __name__ == '__main__':
    base = build_base()
    lid = build_lid()
    for m, name in ((base, 'box_base.stl'), (lid, 'box_lid.stl')):
        assert m.is_watertight, name + " not watertight"
        m.export(os.path.join(HERE, name))
        print("wrote %s: %.1f x %.1f x %.1f mm, %.0f cm3 of plastic"
              % (name, *(m.bounds[1] - m.bounds[0]), m.volume / 1000))
    # preview: lid shown lifted above the base, upside down as it would sit
    shown = lid.copy()
    shown.apply_transform(trimesh.transformations.rotation_matrix(np.pi, (1, 0, 0), (IN_L / 2, IN_W / 2, 0)))
    shown.apply_translation((0, 0, IN_H + 45))
    preview([(base, (0.85, 0.6, 0.35)), (shown, (0.55, 0.7, 0.85))], os.path.join(HERE, 'box.png'))
    floor_plan(base, os.path.join(HERE, 'box_floor.png'))
