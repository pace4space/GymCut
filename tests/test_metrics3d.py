import numpy as np

from gymcut.metrics3d import somersault_deg, twist_deg


def _rot_y(v, a):
    c, s = np.cos(a), np.sin(a)
    return np.array([c * v[0] + s * v[2], v[1], -s * v[0] + c * v[2]])


def test_somersault_full_turn():
    trunk = np.array([_rot_y(np.array([0, 0, 1.0]), a) for a in np.linspace(0, 2 * np.pi, 40)])
    assert abs(somersault_deg(trunk) - 360) < 2


def test_twist_ignores_somersault():
    # pure salto: shoulder line stays along y while the trunk rotates about y -> no twist
    a = np.linspace(0, 2 * np.pi, 40)
    trunk = np.array([_rot_y(np.array([0, 0, 1.0]), x) for x in a])
    across = np.tile([0, 1.0, 0], (40, 1))
    assert abs(twist_deg(trunk, across)) < 3


def test_full_twist_upright():
    a = np.linspace(0, 2 * np.pi, 60)
    trunk = np.tile([0, 0, 1.0], (60, 1))
    across = np.stack([np.cos(a), np.sin(a), np.zeros_like(a)], 1)
    assert abs(abs(twist_deg(trunk, across)) - 360) < 2
