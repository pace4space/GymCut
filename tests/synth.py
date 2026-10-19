"""Synthetic side-view gymnast: a kinematic stick figure with real ballistic flight.

Lengths are in body-lengths (BL = shoulder->ankle). Angles: phi = torso
orientation (0 upright, +clockwise in image coords), hip/knee/elbow = interior
joint angles (180 = straight), shoulder = arm elevation (0 down, 180 overhead).
"""
from __future__ import annotations

import numpy as np

TORSO, THIGH, SHANK = 0.372, 0.314, 0.314
UARM, FARM = 0.24, 0.22
ANKLE_H = 0.05                      # ankle joint height above the floor (BL)
G = 9.81


def _rot(v, a):
    c, s = np.cos(a), np.sin(a)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def pose(hip, phi, hip_ang=180, knee_ang=180, sh_ang=0, el_ang=180, facing=1, bl_px=200.0, plant=None):
    """Return [17, 3] COCO keypoints (pixels) for one frame.

    plant=(x, y): translate the body so the ankles stay at that point (feet on the floor).
    """
    p = np.radians(phi)
    u = np.array([np.sin(p), -np.cos(p)])           # torso direction (hip -> shoulder)
    fw = facing * np.array([-u[1], u[0]])           # body front
    hip = np.asarray(hip, float)
    L = lambda x: x * bl_px
    sh = hip + u * L(TORSO)
    a = np.radians(180 - hip_ang)
    thigh = np.cos(a) * (-u) + np.sin(a) * fw
    n_t = np.cos(a) * fw + np.sin(a) * u
    b = np.radians(180 - knee_ang)
    shank = np.cos(b) * thigh - np.sin(b) * n_t
    s = np.radians(sh_ang)
    arm = np.cos(s) * (-u) + np.sin(s) * fw
    n_a = np.cos(s) * fw + np.sin(s) * u
    c = np.radians(180 - el_ang)
    fore = np.cos(c) * arm + np.sin(c) * n_a
    k = np.zeros((17, 3))
    lat = 0.012 * bl_px * fw          # tiny offset so left/right are not identical points
    for side, sgn in (("l", 1), ("r", -1)):
        o = lat * sgn
        hp = hip + o
        kn = hp + thigh * L(THIGH)
        an = kn + shank * L(SHANK)
        shp = sh + o
        el = shp + arm * L(UARM)
        wr = el + fore * L(FARM)
        idx = {"l": (5, 7, 9, 11, 13, 15), "r": (6, 8, 10, 12, 14, 16)}[side]
        for j, pt in zip(idx, (shp, el, wr, hp, kn, an)):
            k[j, :2] = pt
    k[0, :2] = sh + u * L(0.17) + fw * L(0.07)                      # nose
    k[1, :2] = sh + u * L(0.2) + fw * L(0.05)
    k[2, :2] = k[1, :2]
    k[3, :2] = sh + u * L(0.17) - fw * L(0.03)                      # ears behind the nose
    k[4, :2] = k[3, :2]
    k[:, 2] = 0.9
    if plant is not None:
        k[:, :2] += np.asarray(plant, float) - (k[15, :2] + k[16, :2]) / 2
    return k


def standing_hip_y(ground_y, bl_px=200.0):
    return ground_y - (THIGH + SHANK + ANKLE_H) * bl_px


def back_tuck(fps=30.0, flight=0.6, bl_px=200.0, facing=1, bl_m=1.2, x0=400.0, ground=800.0,
              stand_before=1.0, stand_after=1.2, travel=-0.4):
    """Standing back tuck. Returns (kps [T,17,3], info dict with ground truth)."""
    g_px = G / bl_m * bl_px
    prep = 0.3
    land = 0.35
    T = int(round((stand_before + prep + flight + land + stand_after) * fps))
    kps = np.zeros((T, 17, 3))
    hy0 = standing_hip_y(ground, bl_px)
    t_takeoff = stand_before + prep
    t_land = t_takeoff + flight
    v0 = g_px * flight / 2
    for i in range(T):
        t = i / fps
        x = x0
        foot = (x0, ground - ANKLE_H * bl_px)
        if t < stand_before:
            k = pose((x, hy0), 0, facing=facing, bl_px=bl_px, plant=foot)
        elif t < t_takeoff:                     # countermovement + arm swing
            a = (t - stand_before) / prep
            bend = np.sin(np.pi * a)
            hip_y = hy0 + bend * 0.12 * bl_px
            k = pose((x, hip_y), 8 * bend * facing, hip_ang=180 - 50 * bend, knee_ang=180 - 60 * bend,
                     sh_ang=170 * a, facing=facing, bl_px=bl_px, plant=foot)
        elif t < t_land:                        # ballistic flight + backward rotation
            tau = t - t_takeoff
            r = tau / flight
            sm = r * r * (3 - 2 * r)
            phi = -360 * sm * facing            # backward salto rotates against facing
            tuck = np.sin(np.pi * min(1, r * 1.15))
            hip_y = hy0 - v0 * tau + 0.5 * g_px * tau * tau
            x = x0 + travel * facing * bl_px * r
            k = pose((x, hip_y), phi, hip_ang=180 - 110 * tuck, knee_ang=180 - 120 * tuck,
                     sh_ang=170 - 80 * tuck, facing=facing, bl_px=bl_px)
        else:                                   # landing absorption, then stand
            x = x0 + travel * facing * bl_px
            a = min(1.0, (t - t_land) / land)
            bend = np.sin(np.pi * a) * (1 if a < 1 else 0)
            hip_y = hy0 + bend * 0.1 * bl_px
            k = pose((x, hip_y), 0, hip_ang=180 - 45 * bend, knee_ang=180 - 55 * bend,
                     sh_ang=90 * (1 - a), facing=facing, bl_px=bl_px, plant=(x, ground - ANKLE_H * bl_px))
        kps[i] = k
    return kps, {"flight": flight, "takeoff_frame": t_takeoff * fps, "land_frame": t_land * fps,
                 "height_m": G * flight ** 2 / 8, "T": T}


def straight_jump(fps=30.0, flight=0.45, bl_px=200.0, x0=400.0, ground=800.0, split=False):
    g_px = G / 1.2 * bl_px
    T = int(round((1.0 + 0.3 + flight + 1.2) * fps))
    kps = np.zeros((T, 17, 3))
    hy0 = standing_hip_y(ground, bl_px)
    t0 = 1.3
    v0 = g_px * flight / 2
    for i in range(T):
        t = i / fps
        if t < 1.0:
            k = pose((x0, hy0), 0, bl_px=bl_px)
        elif t < t0:
            a = (t - 1.0) / 0.3
            bend = np.sin(np.pi * a)
            k = pose((x0, hy0 + 0.1 * bl_px * bend), 0, hip_ang=180 - 40 * bend, knee_ang=180 - 50 * bend, bl_px=bl_px,
                     plant=(x0, ground - ANKLE_H * bl_px))
        elif t < t0 + flight:
            tau = t - t0
            k = pose((x0, hy0 - v0 * tau + 0.5 * g_px * tau * tau), 0, bl_px=bl_px)
            if split:
                r = np.sin(np.pi * tau / flight)
                # front leg forward/up, back leg backward: build from two poses
                kf = pose((x0, k[11, 1]), 0, hip_ang=180 - 90 * r, bl_px=bl_px)
                kb = pose((x0, k[11, 1]), 0, hip_ang=180 + 80 * r, bl_px=bl_px)
                k[[11, 13, 15]] = kf[[11, 13, 15]]
                k[[12, 14, 16]] = kb[[12, 14, 16]]
        else:
            k = pose((x0, hy0), 0, bl_px=bl_px)
        kps[i] = k
    return kps, {"flight": flight, "T": T}


def idle(seconds, fps=30.0, bl_px=200.0, x0=400.0, ground=800.0):
    T = int(round(seconds * fps))
    hy0 = standing_hip_y(ground, bl_px)
    rng = np.random.default_rng(0)
    out = np.zeros((T, 17, 3))
    for i in range(T):
        out[i] = pose((x0 + rng.normal(0, 0.3), hy0 + rng.normal(0, 0.3)), rng.normal(0, 0.5), bl_px=bl_px)
    return out
