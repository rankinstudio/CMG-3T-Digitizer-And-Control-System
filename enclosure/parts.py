"""Shared geometry for the CMG-3T board box: keystone socket, 12 V jack hole.

Coordinates for a wall feature: the wall's OUTER face is z = 0, z grows into
the box; x across the opening, y up.

Keystone socket, cut straight into the 2.5 mm wall. A keystone snaps in from
BEHIND: its face pushes through the lip's opening, the bottom hook catches the
lower edge and the top clip springs up behind the upper edge, on the wall's
back face.
  lead-in       z 0 .. 1.15       45 deg chamfer, opening 1.15 larger top and bottom at the face
  lip           z 1.15 .. 2.5     opening 14.9 x 19.44   (the "panel" the jack clips to)
  footprint     18 x 25, opening centred in x, 2.6 from the bottom
"""
import numpy as np
import trimesh
from trimesh.creation import box, cylinder

KS_W, KS_H = 18.0, 25.0          # block footprint on the wall
KS_D = 2.5                       # = the wall thickness; the socket is cut straight into the wall
KS_OPEN_W, KS_OPEN_H = 14.9, 19.44
KS_OPEN_Y0 = 2.6                 # opening bottom above block bottom
KS_LIP = 1.35
KS_BEVEL = KS_D - KS_LIP         # lead-in depth = chamfer size

JACK_HOLE = 12.0                 # 12 V panel jack, 11.5 mm thread


def at(mesh, x, y, z):
    mesh.apply_translation((x, y, z))
    return mesh


def prism_yz(profile_yz, x0, x1):
    """Extrude a (y, z) polygon along x from x0 to x1."""
    from shapely.geometry import Polygon
    m = trimesh.creation.extrude_polygon(Polygon(profile_yz), x1 - x0)
    # extrude_polygon works in the polygon's (u, v) plane along w: map u->y, v->z, w->x
    m.vertices = m.vertices[:, [2, 0, 1]] + [x0, 0, 0]
    if m.volume < 0:
        m.invert()
    return m


def keystone_void():
    """The space a keystone socket removes, in block coordinates
    (x 0..KS_W, y 0..KS_H, z 0..KS_D with z = 0 the wall's outer face)."""
    x0 = (KS_W - KS_OPEN_W) / 2
    x1 = x0 + KS_OPEN_W
    y0, y1 = KS_OPEN_Y0, KS_OPEN_Y0 + KS_OPEN_H
    b = KS_BEVEL
    e = 0.01
    # one (y, z) profile for the whole passage, extruded across the opening width
    prof = [(y0 - b, -e), (y1 + b, -e),                # lead-in, widest at the face
            (y1, b), (y1, KS_D + e),                   # lip, top edge, through to the back
            (y0, KS_D + e), (y0, b)]                   # lip, bottom edge
    return prism_yz(prof, x0, x1)


def jack_hole(depth=10.0, d=JACK_HOLE):
    """Cylinder along z, centred on the origin in x/y, from z = -1 to depth."""
    c = cylinder(radius=d / 2, height=depth + 1, sections=96)
    return at(c, 0, 0, (depth + 1) / 2 - 1)
