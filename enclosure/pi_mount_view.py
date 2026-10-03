#!/usr/bin/env python3
"""Rough Raspberry Pi 5 on the box's right wall, to check how it mounts.

    python pi_mount_view.py     # writes pi_mount.png and pi_mount.glb here

The Pi is blocks at the positions of Raspberry Pi Ltd drawing RP-008347-DS-1
(board 85 x 56, holes 3.5 in, 58 x 49 apart): PCB, GPIO header, two USB
stacks, Ethernet, USB-C, two micro HDMI. Drawing coordinates: u along the 85
from the holes end, v up the 56 from the USB-C edge, component side up.
pi_mount.glb opens in Windows 3D Viewer with colours; nothing here is printed.
"""
import os

import numpy as np
import trimesh
from trimesh.creation import box, cylinder

import box as bx
from parts import at

HERE = os.path.dirname(os.path.realpath(__file__))

STANDOFF_L, STANDOFF_D = 7.0, 4.5      # hex standoff body (rough); stud through the wall
PCB_T = 1.6

#        name           u0     u1     v0     v1    height above the PCB
PARTS = [('GPIO header',  7.1,  57.9,  50.0,  55.0,  8.5),
         ('USB 2',       70.0,  88.0,  40.5,  53.5, 16.0),
         ('USB 3',       70.0,  88.0,  22.5,  35.7, 16.0),
         ('Ethernet',    66.0,  88.0,   2.2,  18.2, 13.5),
         ('USB-C',        6.7,  15.7,  -1.0,   6.5,  3.2),
         ('HDMI 0',      22.1,  29.5,  -0.5,   6.5,  3.4),
         ('HDMI 1',      35.5,  42.9,  -0.5,   6.5,  3.4),
         ('SoC',         20.0,  35.0,  20.0,  35.0,  2.5)]


def block(u0, u1, v0, v1, w0, w1):
    return at(box((u1 - u0, v1 - v0, w1 - w0)), (u0 + u1) / 2, (v0 + v1) / 2, (w0 + w1) / 2)


def pi_parts():
    """[(name, mesh, colour)] in drawing coordinates (w = 0 the PCB underside)."""
    out = [('PCB', block(0, 85, 0, 56, 0, PCB_T), (0.15, 0.5, 0.25))]
    for name, u0, u1, v0, v1, h in PARTS:
        col = (0.1, 0.1, 0.1) if name in ('GPIO header', 'SoC') else (0.75, 0.75, 0.78)
        out.append((name, block(u0, u1, v0, v1, PCB_T, PCB_T + h), col))
    for u in (3.5, 61.5):
        for v in (3.5, 52.5):
            out.append(('standoff', at(cylinder(radius=STANDOFF_D / 2, height=STANDOFF_L, sections=6),
                                       u, v, -STANDOFF_L / 2), (0.8, 0.65, 0.2)))
    return out


def onto_right_wall(mesh):
    """Drawing (u, v, w) -> box: u along +y from PI_Y0, v up from the board's
    bottom edge, w outwards from the standoff tops (PCB underside)."""
    m = mesh.copy()
    u, v, w = m.vertices.T
    x_out = bx.IN_L + bx.WALL                       # outer face of the right wall
    m.vertices = np.c_[x_out + STANDOFF_L + w, bx.PI_Y0 + u, bx.PI_TOP - bx.PI_W + v]
    if m.volume < 0:
        m.invert()
    return m


def render(items, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    views = [(18, -35, 'from the front-right corner'),
             (4, 0.01, 'right side, square on'),
             (90, -90, 'from above (front at the bottom)')]
    fig = plt.figure(figsize=(18, 6.5))
    allv = np.vstack([m.vertices for _, m, _ in items])
    lo, hi = allv.min(0), allv.max(0)
    span, c = (hi - lo).max(), (hi + lo) / 2
    light = np.array([0.5, 0.3, 0.8])
    for k, (el, az, title) in enumerate(views):
        ax = fig.add_subplot(1, 3, k + 1, projection='3d')
        # one collection, so matplotlib depth-sorts faces across parts
        tris, cols = [], []
        for _, m, col in items:
            shade = 0.4 + 0.6 * np.clip(m.face_normals @ (light / np.linalg.norm(light)), 0, 1)
            tris.append(m.vertices[m.faces])
            cols += [tuple(ch * s for ch in col) for s in shade]
        ax.add_collection3d(Poly3DCollection(np.vstack(tris), facecolors=cols, edgecolor='none'))
        for f, s in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), c):
            f(s - span / 2, s + span / 2)
        ax.set_proj_type('ortho')
        ax.view_init(elev=el, azim=az)
        ax.set_title(title, fontsize=10)
        ax.set_axis_off()
    fig.savefig(path, dpi=100, bbox_inches='tight')


if __name__ == '__main__':
    base = bx.build_base()
    items = [('box', base, (0.85, 0.6, 0.35))]
    items += [(n, onto_right_wall(m), col) for n, m, col in pi_parts()]
    render(items, os.path.join(HERE, 'pi_mount.png'))
    scene = trimesh.Scene()
    for i, (n, m, col) in enumerate(items):
        m = m.copy()
        m.visual.face_colors = [int(255 * ch) for ch in col] + [255]
        scene.add_geometry(m, node_name='%s_%d' % (n, i))
    scene.export(os.path.join(HERE, 'pi_mount.glb'))
    print("PCB underside %.1f mm off the wall; board bottom edge %.1f mm above the table; "
          "port fronts %.1f mm short of the back face"
          % (STANDOFF_L, bx.PI_TOP - bx.PI_W + bx.FLOOR, (bx.IN_W + bx.WALL) - (bx.PI_Y0 + 88)))
