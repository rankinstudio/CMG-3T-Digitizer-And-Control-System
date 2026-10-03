#!/usr/bin/env python3
"""Fit-test plate for the CMG-3T board box: the wall connectors, as they'll be built.

    python fit_test.py          # writes fit_test.stl and fit_test.png here

A 2.5 mm panel (the box wall) with one of each wall opening, at the box's sizes:
  - a keystone socket (parts.py: 14.9 x 19.44 opening in a 1.35 mm lip
    behind a chamfered lead-in), cut straight into the panel
  - a 12 V panel jack hole (JACK_HOLE)
  - a LOCK / UNLOCK / CENTRE button hole (box.BUTTON_D)
Print with the panel's outer face on the bed, no supports. Snap a jack in
from the back: it should click and not rattle.
"""
import os

import numpy as np
import trimesh
from trimesh.creation import box, cylinder

from box import BUTTON_D
from parts import (at, keystone_void, jack_hole, KS_W, KS_H, KS_D, JACK_HOLE)

HERE = os.path.dirname(os.path.realpath(__file__))
WALL = KS_D                                  # 2.5, same as the box wall
W, H = 60.0, 33.0


def build():
    panel = at(box((W, H, WALL)), W / 2, H / 2, WALL / 2)
    cuts = [at(jack_hole(WALL + 1, JACK_HOLE), 10, H / 2, 0),
            at(jack_hole(WALL + 1, BUTTON_D), 25, H / 2, 0),
            at(keystone_void(), 36, (H - KS_H) / 2, 0)]
    return trimesh.boolean.difference([panel] + cuts)


def preview(mesh, path, views=((60, -60), (-55, 120))):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig = plt.figure(figsize=(5 * len(views), 4.5))
    lo, hi = mesh.bounds
    span, c = (hi - lo).max(), (hi + lo) / 2
    for k, (el, az) in enumerate(views):
        ax = fig.add_subplot(1, len(views), k + 1, projection='3d')
        tri = mesh.vertices[mesh.faces]
        shade = 0.3 + 0.7 * np.clip(mesh.face_normals @ np.array([0.3, -0.5, 0.8]), 0, 1)
        ax.add_collection3d(Poly3DCollection(tri, facecolors=plt.cm.Greys(0.25 + 0.6 * shade), edgecolor='none'))
        for f, s in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), c):
            f(s - span / 2, s + span / 2)
        ax.view_init(elev=el, azim=az)
        ax.set_axis_off()
    fig.savefig(path, dpi=100, bbox_inches='tight')


if __name__ == '__main__':
    m = build()
    assert m.is_watertight, "mesh not watertight"
    out = os.path.join(HERE, 'fit_test.stl')
    m.export(out)
    preview(m, os.path.join(HERE, 'fit_test.png'))
    print("wrote %s: %.0f x %.0f x %.1f mm, watertight %s" % (out, *(m.bounds[1] - m.bounds[0]), m.is_watertight))
