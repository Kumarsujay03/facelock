"""Offline tests (no camera, no root): python -I tests/test_offline.py"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="facelock-test-")
os.environ["FACELOCK_STATE"] = TMP
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from facelock import config, liveness, store  # noqa: E402


def unit(n, seed):
    v = np.random.default_rng(seed).normal(size=(n, 128)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class FakeFace:
    def __init__(self, x=0.0):
        self.landmarks = np.array([[100 + x, 100], [160 + x, 100], [130 + x, 130], [110, 160], [150, 160]], np.float32)
        self.box = np.array([70 + x, 50, 120, 150], np.float32)


def eye_frame(open_eyes, x=0.0):
    g = np.full((240, 320), 150, np.uint8)
    if open_eyes:
        for cx in (100 + x, 160 + x):
            g[96:105, int(cx) - 4:int(cx) + 5] = 30  # pupils
    return g


def unit_dim(n, seed, dim):
    v = np.random.default_rng(seed).normal(size=(n, dim)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class StoreTests(unittest.TestCase):
    def test_enrol_learn_cap(self):
        store.save_enrolment("alice", unit(10, 1), {"kept": 10}, strong=unit_dim(10, 1, 512))
        g = store.Gallery("alice")
        self.assertEqual(len(g.enrolled), 10)
        self.assertTrue(g.strong_enrolled)
        weak, strong = unit(12, 2), unit_dim(12, 2, 512)
        for i in range(12):
            g.learn(weak[i], strong[i], cap=5)
        g2 = store.Gallery("alice")
        self.assertEqual(len(g2.learned), 5)
        self.assertEqual(len(g2.learned_strong), 5)  # kept in pairs
        self.assertEqual(len(g2.enrolled), 10)        # enrolment never changes
        self.assertTrue(np.allclose(g2.learned_strong[-1], strong[-1]))  # newest kept
        self.assertEqual(oct(os.stat(os.path.join(TMP, "alice", "learned_strong.npy")).st_mode & 0o777), "0o600")

    def test_reenrol_drops_learned(self):
        store.save_enrolment("bob", unit(5, 3), {}, strong=unit_dim(5, 3, 512))
        store.Gallery("bob").learn(unit(1, 4)[0], unit_dim(1, 4, 512)[0], cap=5)
        store.save_enrolment("bob", unit(5, 5), {}, strong=unit_dim(5, 5, 512))
        self.assertEqual(len(store.Gallery("bob").learned), 0)

    def test_old_enrolment_needs_redoing(self):
        store.save_enrolment("erin", unit(5, 6), {})  # 1.1.x: SFace only
        self.assertFalse(store.Gallery("erin").strong_enrolled)

    def test_bad_user(self):
        for bad in ("../etc", "", "a/b", "-x"):
            self.assertFalse(store.valid_user(bad))
            with self.assertRaises(ValueError):
                store.Gallery(bad)


class NoNearMissLearning(unittest.TestCase):
    def test_service_has_no_near_miss_learning(self):
        from facelock import daemon
        self.assertFalse(hasattr(daemon.Lockouts, "remember"))


def toward(base, other, sim):
    """A unit vector with cosine `sim` to `base`, mixed from `other`."""
    o = other - (other @ base) * base
    o /= np.linalg.norm(o)
    v = sim * base + np.sqrt(1 - sim ** 2) * o
    return (v / np.linalg.norm(v)).astype(np.float32)


class LookAlikeTests(unittest.TestCase):
    """The 1.0.x hole: a brother scoring ~0.50 unlocked, and was learned.
    1.2 needs two models to agree over 5 frames."""

    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()
        self.me, self.me_s = unit(1, 11)[0], unit_dim(1, 11, 512)[0]
        rng = 20
        weak = np.array([toward(self.me, unit(1, rng + i)[0], 0.95) for i in range(6)])
        strong = np.array([toward(self.me_s, unit_dim(1, rng + i, 512)[0], 0.92) for i in range(6)])
        store.save_enrolment("dana", weak, {}, strong=strong)
        self.g = store.Gallery("dana")
        self.bar = self.scan.strong_bar(self.g, self.cfg)

    def frames(self, weak_sim, strong_sim, n=5, seed=40, base=None, base_s=None):
        base = self.me if base is None else base
        base_s = self.me_s if base_s is None else base_s
        out = []
        for i in range(n):
            w = toward(base, unit(1, seed + i)[0], weak_sim)
            s = toward(base_s, unit_dim(1, seed + i, 512)[0], strong_sim)
            ws, _, _ = self.scan.frame_score(w, self.g, self.cfg)
            ss, _, _ = self.scan.strong_score(s, self.g, self.cfg)
            out.append((ws, ss, s))
        return out

    def test_owner_unlocks(self):
        self.assertTrue(self.scan.decide(self.frames(0.9, 0.85), self.cfg, self.bar))

    def test_needs_five_frames(self):
        self.assertFalse(self.scan.decide(self.frames(0.9, 0.85, n=4), self.cfg, self.bar))

    def test_look_alike_never_unlocks(self):
        # the brother's real SFace scores (0.48-0.54), even if AuraFace were fooled
        self.assertFalse(self.scan.decide(self.frames(0.52, 0.85), self.cfg, self.bar))
        # and an AuraFace score at a stranger's worst (0.39) with a perfect SFace
        self.assertFalse(self.scan.decide(self.frames(0.9, 0.40), self.cfg, self.bar))

    def test_one_lucky_frame_is_not_enough(self):
        window = self.frames(0.9, 0.40, n=4) + self.frames(0.95, 0.95, n=1, seed=60)
        self.assertFalse(self.scan.decide(window, self.cfg, self.bar))

    def test_frames_must_be_one_person(self):
        # 5 frames each matching the owner but from two different faces
        other_s = unit_dim(1, 99, 512)[0]
        window = self.frames(0.9, 0.85, n=3) + self.frames(0.9, 0.85, n=2, seed=70, base_s=other_s)
        self.assertFalse(self.scan.decide(window, self.cfg, self.bar))

    def test_bar_never_below_the_minimum(self):
        self.g.meta["strong_accept"] = 0.2
        self.assertGreaterEqual(self.scan.strong_bar(self.g, self.cfg), self.cfg.strong_accept)

    def test_look_alike_is_never_learned(self):
        self.assertLess(0.54, self.cfg.learn_from)
        self.assertGreaterEqual(self.cfg.learn_strong, self.cfg.strong_accept + 0.15)

    def test_other_person_is_refused(self):
        bro_w = toward(self.me, unit(1, 80)[0], 0.52)
        bro_s = toward(self.me_s, unit_dim(1, 80, 512)[0], 0.5)
        self.g.add_others([bro_w], [bro_s])
        s, _, other = self.scan.strong_score(toward(bro_s, unit_dim(1, 81, 512)[0], 0.95), self.g, self.cfg)
        self.assertTrue(other)
        self.assertLessEqual(s, 0.0)
        s, _, other = self.scan.strong_score(toward(self.me_s, unit_dim(1, 82, 512)[0], 0.9), self.g, self.cfg)
        self.assertFalse(other)

    def test_old_learned_data_is_discarded(self):
        old = os.path.join(TMP, "dana", "learned.npy")
        np.save(old, unit(3, 18))
        store.Gallery("dana")
        self.assertFalse(os.path.exists(old))


class BlinkTests(unittest.TestCase):
    def run_seq(self, seq, drop=0.8, shift=None):
        b = liveness.BlinkDetector(drop)
        for i, o in enumerate(seq):
            x = shift(i) if shift else 0.0
            if b.update(eye_frame(o, x), FakeFace(x)):
                return True
        return False

    def test_blink_seen(self):
        self.assertTrue(self.run_seq([1] * 12 + [0, 0] + [1] * 3))

    def test_photo_never_blinks(self):
        self.assertFalse(self.run_seq([1] * 60))

    def test_long_close_is_not_blink(self):
        self.assertFalse(self.run_seq([1] * 10 + [0] * 30 + [1] * 3))

    def test_head_move_is_not_blink(self):
        # the patch changes because the head jumped sideways, not the eyes
        self.assertFalse(self.run_seq([1] * 12 + [0, 0] + [1] * 3,
                                      shift=lambda i: 0.0 if i < 12 else 20.0))

    def test_exposure_change_is_not_blink(self):
        b = liveness.BlinkDetector(0.8)
        frames = [eye_frame(1)] * 10 + [eye_frame(1) // 3] * 3 + [eye_frame(1)] * 5  # dark, then bright
        self.assertFalse(any(b.update(f, FakeFace()) for f in frames))

    def test_calibrate(self):
        steady = [(r, 0.5) for r in np.linspace(0.93, 1.07, 40)]
        self.assertIsNone(liveness.calibrate(steady, [(0.98, 0.5)] * 30))
        blinks = [(1.0, 0.0)] * 10 + [(0.7, 9.0)] * 3 + [(1.0, 0.0)] * 10 + [(0.72, 8.0)] * 3
        drop, rise = liveness.calibrate(steady, blinks)
        self.assertTrue(0.85 <= drop <= 0.9 and rise >= 4.0)


class ConfigTests(unittest.TestCase):
    def test_parse_and_bad_values(self):
        path = os.path.join(TMP, "c.conf")
        with open(path, "w") as f:
            f.write("[match]\nthreshold = 0.3\naccept = 0.6\nstrong_frames = x\n[liveness]\nliveness = maybe\n")
        cfg = config.load(path)
        self.assertEqual(cfg.accept, 0.6)
        self.assertEqual(cfg.strong_frames, 5)  # bad value keeps the default
        self.assertFalse(hasattr(cfg, "threshold"))  # 1.0.x key is ignored
        self.assertEqual(cfg.liveness, "observe")


if __name__ == "__main__":
    unittest.main(verbosity=1)
