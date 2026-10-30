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
            ss, *_ = self.scan.strong_score(s, self.g, self.cfg)
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
        self.assertGreaterEqual(self.cfg.learn_strong, 0.55 + 0.15)  # the brother reached 0.55

    def test_other_person_is_refused(self):
        bro_w = toward(self.me, unit(1, 80)[0], 0.52)
        bro_s = toward(self.me_s, unit_dim(1, 80, 512)[0], 0.5)
        self.g.add_others([bro_w], [bro_s])
        s, _, other, *_ = self.scan.strong_score(toward(bro_s, unit_dim(1, 81, 512)[0], 0.95), self.g, self.cfg)
        self.assertTrue(other)
        self.assertLessEqual(s, 0.0)
        s, _, other, *_ = self.scan.strong_score(toward(self.me_s, unit_dim(1, 82, 512)[0], 0.9), self.g, self.cfg)
        self.assertFalse(other)

    def test_old_learned_data_is_discarded(self):
        old = os.path.join(TMP, "dana", "learned.npy")
        np.save(old, unit(3, 18))
        store.Gallery("dana")
        self.assertFalse(os.path.exists(old))


class RealScoresTests(unittest.TestCase):
    """Real frames from one laptop (1.2.0): the owner right after enrolment,
    then his look-alike brother. The brother's AuraFace frames reached 0.55,
    the 1.2.0 bar; 1.2.1 must refuse him with a clear margin."""

    OWNER = [(0.858, 0.797), (0.863, 0.781), (0.848, 0.827), (0.831, 0.776), (0.875, 0.822)]
    # every frame the brother's scan checked with both models, in order
    BROTHER = [(0.451, 0.494), (0.467, 0.429), (0.451, 0.479), (0.462, 0.520), (0.478, 0.462),
               (0.484, 0.515), (0.452, 0.477), (0.458, 0.534), (0.455, 0.509), (0.452, 0.511),
               (0.500, 0.528), (0.468, 0.541), (0.450, 0.520), (0.470, 0.538), (0.455, 0.471),
               (0.477, 0.446), (0.458, 0.528), (0.472, 0.489), (0.488, 0.516), (0.489, 0.485),
               (0.495, 0.480), (0.452, 0.523), (0.481, 0.491), (0.466, 0.464), (0.486, 0.460),
               (0.482, 0.458), (0.476, 0.528), (0.474, 0.491), (0.456, 0.506), (0.464, 0.523),
               (0.462, 0.452), (0.476, 0.492), (0.454, 0.512), (0.497, 0.500), (0.450, 0.519),
               (0.487, 0.535), (0.493, 0.539), (0.506, 0.546), (0.480, 0.536), (0.464, 0.507),
               (0.492, 0.480), (0.510, 0.541), (0.485, 0.527), (0.466, 0.503), (0.464, 0.521),
               (0.482, 0.503), (0.534, 0.551), (0.504, 0.507), (0.477, 0.517), (0.496, 0.523),
               (0.569, 0.503), (0.520, 0.506), (0.458, 0.497), (0.469, 0.462), (0.458, 0.488),
               (0.473, 0.545), (0.454, 0.514), (0.454, 0.530), (0.477, 0.501)]
    # the same brother after the owner re-enrolled (p10 0.755) and added
    # frames with glasses (32 samples): without, then with glasses
    BROTHER_2 = [(0.480, 0.512), (0.479, 0.468), (0.478, 0.510), (0.458, 0.496), (0.476, 0.564),
                 (0.487, 0.509), (0.485, 0.487), (0.454, 0.478), (0.502, 0.465), (0.452, 0.448),
                 (0.455, 0.434), (0.457, 0.487), (0.477, 0.535), (0.498, 0.539), (0.545, 0.523),
                 (0.514, 0.546), (0.520, 0.560), (0.531, 0.576), (0.534, 0.576), (0.537, 0.573),
                 (0.533, 0.547), (0.578, 0.579), (0.514, 0.526), (0.542, 0.568), (0.544, 0.573),
                 (0.553, 0.548), (0.550, 0.611), (0.519, 0.552), (0.542, 0.594), (0.543, 0.597),
                 (0.534, 0.609), (0.547, 0.545), (0.555, 0.590), (0.550, 0.569), (0.534, 0.575),
                 (0.553, 0.573), (0.555, 0.567), (0.525, 0.554), (0.535, 0.543), (0.527, 0.532),
                 (0.550, 0.576), (0.547, 0.571), (0.571, 0.589), (0.552, 0.589), (0.570, 0.579),
                 (0.536, 0.533), (0.531, 0.571), (0.544, 0.563), (0.547, 0.568), (0.555, 0.587),
                 (0.564, 0.598), (0.568, 0.588), (0.562, 0.589), (0.571, 0.585), (0.566, 0.558)]
    BROTHER_GLASSES = [(0.604, 0.578), (0.579, 0.546), (0.586, 0.517), (0.590, 0.513), (0.607, 0.507),
                       (0.598, 0.529), (0.626, 0.528), (0.614, 0.530), (0.593, 0.525), (0.608, 0.514),
                       (0.626, 0.546), (0.592, 0.518), (0.600, 0.506), (0.596, 0.577), (0.620, 0.565),
                       (0.649, 0.603), (0.587, 0.523), (0.603, 0.518), (0.592, 0.558), (0.599, 0.542),
                       (0.616, 0.550), (0.585, 0.556), (0.618, 0.549), (0.622, 0.575), (0.600, 0.546),
                       (0.592, 0.548), (0.601, 0.555), (0.614, 0.564), (0.604, 0.546), (0.619, 0.567),
                       (0.635, 0.551), (0.649, 0.561), (0.612, 0.564), (0.602, 0.560), (0.627, 0.570),
                       (0.646, 0.564), (0.660, 0.559), (0.630, 0.549), (0.624, 0.543), (0.602, 0.548)]
    OWNER_2 = [(0.850, 0.756), (0.849, 0.741), (0.835, 0.782), (0.851, 0.778), (0.864, 0.760)]
    OWNER_SELF_P10 = 0.758  # measured at that enrolment

    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()
        self.vec = unit_dim(1, 5, 512)[0]  # one person throughout: the strictest case
        g = type("G", (), {})()
        g.meta = {"strong_self_p10": self.OWNER_SELF_P10, "strong_accept": 0.55}
        self.bar = scan.strong_bar(g, self.cfg)

    def windows(self, frames):
        return [[(w, s, self.vec) for w, s in frames[i - 5:i]] for i in range(5, len(frames) + 1)]

    def test_owner_unlocks(self):
        self.assertTrue(self.scan.decide([(w, s, self.vec) for w, s in self.OWNER], self.cfg, self.bar))

    def test_brother_never_unlocks_even_on_aurafaces_own(self):
        for window in self.windows(self.BROTHER):
            self.assertFalse(self.scan.decide(window, self.cfg, self.bar))
            # and with SFace taken out of the decision entirely
            fooled_sface = [(0.99, s, v) for _, s, v in window]
            self.assertFalse(self.scan.decide(fooled_sface, self.cfg, self.bar))

    def test_brother_after_glasses_enrolment(self):
        g = type("G", (), {"meta": {"strong_self_p10": 0.755}})()
        bar = self.scan.strong_bar(g, self.cfg)
        self.assertGreaterEqual(bar, 0.67)
        for frames in (self.BROTHER_2, self.BROTHER_GLASSES):
            for window in self.windows(frames):
                fooled_sface = [(0.99, s, v) for _, s, v in window]
                self.assertFalse(self.scan.decide(fooled_sface, self.cfg, bar))
                med = sorted(s for _, s, _ in window)[2]
                self.assertGreaterEqual(bar - med, 0.07)
        self.assertTrue(self.scan.decide([(w, s, self.vec) for w, s in self.OWNER_2], self.cfg, bar))

    def test_margins(self):
        brother_best = max(s for _, s in self.BROTHER)
        owner_worst = min(s for _, s in self.OWNER)
        self.assertGreaterEqual(self.bar - brother_best, 0.08)
        self.assertGreaterEqual(owner_worst - self.bar, 0.07)

    def test_settings_cant_lower_the_floor(self):
        self.cfg.strong_accept = 0.30
        self.cfg.accept = 0.30
        g = type("G", (), {"meta": {}})()
        self.assertGreaterEqual(self.scan.strong_bar(g, self.cfg), self.scan.STRONG_FLOOR)
        for window in self.windows(self.BROTHER):
            self.assertFalse(self.scan.decide(window, self.cfg, self.scan.strong_bar(g, self.cfg)))


class AdaptiveBarTests(unittest.TestCase):
    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()

    def run_unlocks(self, medians, start=0.638):
        bar, seen = start, []
        for m in medians:
            seen.append(m)
            bar = self.scan.next_bar(bar, seen)
        return bar

    def test_only_collects_for_the_first_20(self):
        self.assertEqual(self.run_unlocks([0.85] * 19), 0.638)

    def test_rises_slowly_after_that(self):
        bar = self.run_unlocks([0.85] * 21)
        self.assertGreater(bar, 0.638)
        self.assertLessEqual(bar, 0.638 + 2 * self.scan.ADAPT_STEP + 1e-9)

    def test_rises_with_better_scores_up_to_the_cap(self):
        self.assertEqual(self.run_unlocks([0.95] * 400), self.scan.ADAPT_CAP)
        # the owner's own worst stays above it
        self.assertLess(self.run_unlocks([0.80] * 400), 0.80 - 0.05)

    def test_never_moves_on_weak_scores_and_never_drops(self):
        self.assertEqual(self.run_unlocks([0.66] * 50), 0.638)
        high = self.run_unlocks([0.85] * 60)
        self.assertEqual(self.run_unlocks([0.85] * 60 + [0.60] * 30), high)

    def test_record_and_reset_on_enrolment(self):
        store.save_enrolment("finn", unit(5, 30), {}, strong=unit_dim(5, 30, 512))
        bar = 0.638
        for _ in range(35):
            bar = store.record_unlock("finn", 0.85, 0, bar)
        info = store.load_meta("finn")["groups"]["0"]
        self.assertEqual(len(info["unlock_medians"]), 30)
        self.assertEqual(info["unlock_count"], 35)
        self.assertGreater(info["adapted"], 0.638)
        self.assertEqual(self.scan.strong_bar(store.Gallery("finn"), self.cfg), info["adapted"])
        store.save_enrolment("finn", unit(5, 31), {}, strong=unit_dim(5, 31, 512))
        self.assertEqual(store.load_meta("finn")["groups"], {"0": {}})


class ImproveTests(unittest.TestCase):
    def test_adds_samples_and_keeps_learning(self):
        store.save_enrolment("gus", unit(16, 40), {}, strong=unit_dim(16, 40, 512))
        g = store.Gallery("gus")
        g.learn(unit(1, 41)[0], unit_dim(1, 41, 512)[0], cap=40)
        store.record_unlock("gus", 0.8, 0, 0.638)
        n, group = store.add_enrolment("gus", unit(16, 42), unit_dim(16, 42, 512), p10=0.7)
        g = store.Gallery("gus")
        self.assertEqual((n, group), (32, 1))
        self.assertEqual(len(g.enrolled), len(g.enrolled_strong))
        self.assertEqual(list(g.enrolled_group), [0] * 16 + [1] * 16)
        self.assertEqual(len(g.learned), 1)                                 # learning kept
        self.assertEqual(store.load_meta("gus")["groups"]["0"]["unlock_count"], 1)  # bar history kept
        self.assertEqual(store.load_meta("gus")["groups"]["1"], {"p10": 0.7})

    def test_capped_and_keeps_new_samples(self):
        store.save_enrolment("hal", unit(16, 50), {}, strong=unit_dim(16, 50, 512))
        for i in range(4):
            store.add_enrolment("hal", unit(16, 51 + i), unit_dim(16, 51 + i, 512))
        g = store.Gallery("hal")
        self.assertEqual(len(g.enrolled_strong), store.ENROLLED_CAP)
        self.assertEqual(len(g.enrolled_group), store.ENROLLED_CAP)
        newest = unit_dim(16, 54, 512)
        self.assertTrue(any(np.allclose(newest[0], row) for row in g.enrolled_strong))


class GroupBarTests(unittest.TestCase):
    """A friend enrolled in good light (his face matched itself at 0.83 in
    one capture, bar 0.75), then added dim bedroom light. In 1.2.7 the single
    0.75 bar refused him in dim light, where he scores 0.62-0.70. Each capture
    now has its own bar."""

    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()
        self.bright, self.dim = unit_dim(1, 60, 512)[0], unit_dim(1, 61, 512)[0]
        self.dim = toward(self.bright, self.dim, 0.55)  # same person, very different light
        bright = np.array([toward(self.bright, unit_dim(1, 62 + i, 512)[0], 0.93) for i in range(16)])
        dim = np.array([toward(self.dim, unit_dim(1, 80 + i, 512)[0], 0.88) for i in range(16)])
        store.save_enrolment("ivy", unit(16, 63), {"strong_self_p10": 0.83}, strong=bright)
        store.add_enrolment("ivy", unit(16, 64), dim, p10=0.72)
        self.g = store.Gallery("ivy")

    def window(self, base, sim, seed):
        out = []
        for i in range(5):
            v = toward(base, unit_dim(1, seed + i, 512)[0], sim)
            s, _, _, group, bar = self.scan.strong_score(v, self.g, self.cfg)
            out.append((0.8, s, v, bar, group))
        return out

    def test_each_capture_has_its_own_bar(self):
        bars = self.scan.strong_bars(self.g, self.cfg)
        self.assertEqual(bars[0], 0.75)
        self.assertEqual(bars[1], 0.64)

    def test_dim_light_unlocks_against_the_dim_group(self):
        w = self.window(self.dim, 0.80, 100)
        self.assertTrue(self.scan.decide(w, self.cfg))
        self.assertTrue(all(f[4] == 1 for f in w))

    def test_bright_light_still_needs_the_strict_bar(self):
        # a face matching only the bright group at 0.70 is below its 0.75 bar
        w = [(0.8, 0.70, self.bright, 0.75, 0)] * 5
        self.assertFalse(self.scan.decide(w, self.cfg))

    def test_no_group_bar_below_the_floor(self):
        store.add_enrolment("ivy", unit(16, 65), unit_dim(16, 65, 512), p10=0.3)
        self.assertGreaterEqual(min(self.scan.strong_bars(store.Gallery("ivy"), self.cfg).values()),
                                self.scan.STRONG_FLOOR)

    def test_legacy_single_bar_is_capped(self):
        g = type("G", (), {"meta": {"strong_self_p10": 0.83}})()
        self.assertEqual(self.scan.strong_bar(g, self.cfg), 0.70)

    def test_unlocks_tighten_only_their_own_group(self):
        bar = 0.75
        for _ in range(40):
            bar = store.record_unlock("ivy", 0.95, 0, bar)
        bars = self.scan.strong_bars(store.Gallery("ivy"), self.cfg)
        self.assertEqual(bars[1], 0.64)


def condition(center, n, sim, seed):
    """n samples of one face in one condition: each `sim` from its centre."""
    return np.array([toward(center, unit_dim(1, seed + i, 512)[0], sim) for i in range(n)])


class MigrationTests(unittest.TestCase):
    """Enrolments from 1.2.x have no groups: they are split automatically on
    the first load after the update, with no new enrolment or improve."""

    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()
        self.face = unit_dim(1, 200, 512)[0]
        self.bright_c = toward(self.face, unit_dim(1, 201, 512)[0], 0.9)
        self.dim_c = toward(self.face, unit_dim(1, 202, 512)[0], 0.6)  # dim light: far from bright

    def legacy(self, user, rows, meta):
        """Write face data the way 1.2.7 did: no group file, no groups."""
        store.save_enrolment(user, unit(len(rows), 203), meta, strong=rows)
        d = os.path.join(TMP, user)
        os.unlink(os.path.join(d, "enrolled_group.npy"))
        m = store.load_meta(user)
        m.pop("groups", None)
        with open(os.path.join(d, "meta.json"), "w") as f:
            import json
            json.dump(m, f)

    def probe_window(self, g, center, sim, seed):
        out = []
        for i in range(5):
            v = toward(center, unit_dim(1, seed + i, 512)[0], sim)
            s, _, _, grp, bar = self.scan.strong_score(v, g, self.cfg)
            out.append((0.8, s, v, bar, grp))
        return out

    def test_friends_case_is_fixed_without_new_captures(self):
        rows = np.vstack([condition(self.bright_c, 16, 0.85, 300), condition(self.dim_c, 32, 0.8, 400)])
        self.legacy("jon", rows, {"strong_self_p10": 0.83})   # 1.2.7 raised it: bar 0.75
        g = store.Gallery("jon")
        groups = self.scan.groups_of(g)
        self.assertEqual(len(groups), 2)
        bars = self.scan.strong_bars(g, self.cfg)
        self.assertTrue(all(0.62 <= b <= 0.75 for b in bars.values()))
        # he unlocks in dim light (scores like his real 0.62-0.70) and in good light
        self.assertTrue(self.scan.decide(self.probe_window(g, self.dim_c, 0.82, 500), self.cfg))
        self.assertTrue(self.scan.decide(self.probe_window(g, self.bright_c, 0.85, 600), self.cfg))
        # saved, so the split is stable from now on
        self.assertTrue(os.path.exists(os.path.join(TMP, "jon", "enrolled_group.npy")))
        self.assertIn("groups", store.load_meta("jon"))

    def test_single_condition_keeps_a_strict_bar(self):
        rows = condition(self.bright_c, 32, 0.88, 700)
        self.legacy("kim", rows, {"strong_self_p10": 0.755})  # the owner's real enrolment: 0.675
        g = store.Gallery("kim")
        self.assertEqual(len(self.scan.groups_of(g)), 1)
        self.assertGreaterEqual(self.scan.strong_bar(g, self.cfg), 0.67)

    def test_small_groups_fold_into_neighbours(self):
        # regression: folding a small group into a later one once crashed
        rows = np.vstack([condition(self.bright_c, 10, 0.9, 800), condition(self.dim_c, 2, 0.9, 820),
                          condition(unit_dim(1, 830, 512)[0], 3, 0.9, 840), condition(self.dim_c, 10, 0.9, 850)])
        ids, groups = store.auto_groups(rows)
        sizes = np.bincount(ids)
        self.assertTrue((sizes >= store.GROUP_MIN).all())
        self.assertEqual(len(groups), len(sizes))


class InvariantTests(unittest.TestCase):
    """Rules that must hold whatever the enrolment, so a user is never
    locked out of a condition they enrolled, and nobody gets a weak bar."""

    def setUp(self):
        from facelock import scan
        self.scan = scan
        self.cfg = config.Config()

    def gallery_with(self, user, captures):
        """captures: [(centre, spread)] enrolled one after another."""
        first = True
        for i, (c, sim) in enumerate(captures):
            rows = condition(c, 16, sim, 1000 + 50 * i)
            p10 = float(np.percentile([np.sort(rows @ r)[::-1][1:4].mean() for r in rows], 10))
            if first:
                store.save_enrolment(user, unit(16, 1001), {"strong_self_p10": p10}, strong=rows)
                first = False
            else:
                store.add_enrolment(user, unit(16, 1002 + i), rows, p10=p10)
        return store.Gallery(user)

    def test_every_enrolled_condition_unlocks_and_bars_stay_in_range(self):
        rng = np.random.default_rng(5)
        for trial in range(6):
            face = unit_dim(1, 2000 + trial, 512)[0]
            captures = [(toward(face, unit_dim(1, 2100 + 10 * trial + j, 512)[0], float(rng.uniform(0.55, 0.95))),
                         float(rng.uniform(0.80, 0.92))) for j in range(int(rng.integers(1, 4)))]
            g = self.gallery_with(f"inv{trial}", captures)
            bars = self.scan.strong_bars(g, self.cfg)
            self.assertTrue(all(0.62 <= b <= 0.75 for b in bars.values()), bars)
            for j, (c, sim) in enumerate(captures):
                # a live face in that condition matches its samples about as
                # well as they match each other
                window = []
                for i in range(5):
                    v = toward(c, unit_dim(1, 3000 + 100 * trial + 10 * j + i, 512)[0], sim)
                    s, _, _, grp, bar = self.scan.strong_score(v, g, self.cfg)
                    window.append((0.8, s, v, bar, grp))
                self.assertTrue(self.scan.decide(window, self.cfg), (trial, j, [round(w[1], 3) for w in window], bars))

    def test_improve_never_changes_existing_bars(self):
        face = unit_dim(1, 4000, 512)[0]
        g = self.gallery_with("lee", [(face, 0.9)])
        before = self.scan.strong_bars(g, self.cfg)
        store.add_enrolment("lee", unit(16, 4001), condition(face, 16, 0.85, 4100), p10=0.9)
        after = self.scan.strong_bars(store.Gallery("lee"), self.cfg)
        self.assertEqual({k: after[k] for k in before}, before)

    def test_strangers_never_pass(self):
        face = unit_dim(1, 5000, 512)[0]
        g = self.gallery_with("max", [(face, 0.9), (toward(face, unit_dim(1, 5001, 512)[0], 0.6), 0.85)])
        for i in range(50):
            v = unit_dim(1, 6000 + i, 512)[0]  # unrelated faces: near-zero similarity
            s, _, _, _, bar = self.scan.strong_score(v, g, self.cfg)
            self.assertLess(s, bar)


class CommandTests(unittest.TestCase):
    """Commands that need no camera run without crashing (1.3.0's
    `facelock status` used a setting removed in 1.2.0)."""

    def test_status_and_stats_run(self):
        import contextlib
        import io
        import types
        from facelock import cli
        store.save_enrolment("noa", unit(16, 70), {"strong_self_p10": 0.8}, strong=unit_dim(16, 70, 512))
        for fn, args in ((cli.cmd_status, {}), (cli.cmd_stats, {"days": 14, "last": 5})):
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    fn(types.SimpleNamespace(**args))
                except SystemExit:
                    pass

    def test_every_config_field_used_exists(self):
        import re
        fields = set(config.Config.__dataclass_fields__)
        for name in os.listdir(os.path.join(ROOT, "facelock")):
            if name.endswith(".py"):
                src = open(os.path.join(ROOT, "facelock", name)).read()
                for attr in set(re.findall(r"\bcfg\.([a-z_]+)", src)):
                    self.assertIn(attr, fields, f"{name} uses cfg.{attr}, which isn't a setting")


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
