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


class StoreTests(unittest.TestCase):
    def test_enrol_learn_cap(self):
        store.save_enrolment("alice", unit(10, 1), {"kept": 10})
        g = store.Gallery("alice")
        self.assertEqual(len(g.enrolled), 10)
        for i, v in enumerate(unit(12, 2)):
            g.learn(v, cap=5)
        g2 = store.Gallery("alice")
        self.assertEqual(len(g2.learned), 5)
        self.assertEqual(len(g2.enrolled), 10)  # enrolment never changes
        self.assertTrue(np.allclose(g2.learned[-1], unit(12, 2)[-1]))  # newest kept
        self.assertEqual(oct(os.stat(os.path.join(TMP, "alice", "learned2.npy")).st_mode & 0o777), "0o600")

    def test_reenrol_drops_learned(self):
        store.save_enrolment("bob", unit(5, 3), {})
        store.Gallery("bob").learn(unit(1, 4)[0], cap=5)
        store.save_enrolment("bob", unit(5, 5), {})
        self.assertEqual(len(store.Gallery("bob").learned), 0)

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
    """The 1.0.x hole: a brother scoring ~0.50 unlocked, and was learned."""

    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()
        self.me = unit(1, 11)[0]
        store.save_enrolment("dana", toward(self.me, unit(1, 12)[0], 0.95)[None, :], {})
        self.g = store.Gallery("dana")
        self.brother = toward(self.me, unit(1, 13)[0], 0.52)

    def score(self, vec):
        return self.scan.frame_score(vec, self.g, self.cfg)

    def test_look_alike_never_unlocks(self):
        s, e, other = self.score(self.brother)
        self.assertLess(s, self.cfg.accept)
        # even many frames at that score don't add up
        self.assertFalse(self.scan.accepted(0, s, e, self.cfg))

    def test_owner_unlocks(self):
        s, e, _ = self.score(toward(self.me, unit(1, 14)[0], 0.9))
        self.assertTrue(self.scan.accepted(1, s, e, self.cfg))

    def test_look_alike_is_never_learned(self):
        # learning needs the enrolment itself at learn_from; a look-alike
        # (0.52 here, 0.54 at most in real use) never gets there, so it
        # can't end up among the learned samples (1.0.x learned it)
        _, enrolled, _ = self.score(self.brother)
        self.assertLess(enrolled, self.cfg.learn_from)
        self.assertGreaterEqual(self.cfg.learn_from - 0.54, 0.10)

    def test_learned_samples_only_help_faces_near_the_enrolment(self):
        far = toward(self.me, unit(1, 15)[0], 0.40)
        self.g.learn(far, cap=40)
        s, _, _ = self.score(far)
        self.assertLess(s, self.cfg.accept)

    def test_other_person_is_refused(self):
        self.g.add_others([self.brother])
        s, _, other = self.score(toward(self.brother, unit(1, 16)[0], 0.95))
        self.assertTrue(other)
        self.assertLessEqual(s, 0.0)
        # the owner is unaffected
        s, _, other = self.score(toward(self.me, unit(1, 17)[0], 0.9))
        self.assertFalse(other)
        self.assertGreater(s, self.cfg.accept_instant)

    def test_old_learned_data_is_discarded(self):
        import os
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
            f.write("[match]\nthreshold = 0.3\naccept = 0.6\naccept_frames = x\n[liveness]\nliveness = maybe\n")
        cfg = config.load(path)
        self.assertEqual(cfg.accept, 0.6)
        self.assertEqual(cfg.accept_frames, 3)  # bad value keeps the default
        self.assertFalse(hasattr(cfg, "threshold"))  # 1.0.x key is ignored
        self.assertEqual(cfg.liveness, "observe")


if __name__ == "__main__":
    unittest.main(verbosity=1)
