"""Synthetic regression checks for local filtering; no survey data or GUI needed."""
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('filter_engine', ROOT / 'modules/engine_source.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def point(depth=10, lat=24, lon=67, height=5):
    return SimpleNamespace(time='', raw_depth=depth, latitude=lat, longitude=lon,
                           easting=None, northing=None, altitude_m=height,
                           rtk_reduced_depth=None, rtk_reduced_height=None, ktd_cell_k=None)

class InputFilterTests(unittest.TestCase):
    def test_isolated_spikes_preserve_original_and_sample_count(self):
        filt = engine.IntelligentInputFilter()
        start = datetime(2026, 10, 24, tzinfo=timezone.utc)
        outputs = []
        for i in range(12):
            original = point(depth=100 if i == 10 else 10,
                             lat=25 if i == 10 else 24,
                             height=200 if i == 10 else 5)
            before = vars(original).copy()
            filtered, audit = filt.apply('shared', start + timedelta(seconds=i), original)
            self.assertEqual(vars(original), before)
            self.assertEqual(audit['original'], before)
            outputs.append(filtered)
        self.assertEqual(len(outputs), 12)
        self.assertEqual(outputs[10].raw_depth, 10)
        self.assertAlmostEqual(outputs[10].latitude, 24)
        self.assertEqual(outputs[10].altitude_m, 5)
        self.assertEqual(outputs[11].raw_depth, 10)

    def test_steps_gaps_and_independent_sensors(self):
        filt = engine.IntelligentInputFilter()
        start = datetime(2026, 10, 24, tzinfo=timezone.utc)
        filt.apply('one', start, point())
        for i in range(1, 4):
            filtered, audit = filt.apply('one', start + timedelta(seconds=i), point(depth=15))
        self.assertEqual(filtered.raw_depth, 15)
        self.assertEqual(audit['decisions']['depth'], 'confirmed_step')
        filtered, _ = filt.apply('two', start, point(depth=50))
        self.assertEqual(filtered.raw_depth, 50)
        filtered, _ = filt.apply('one', start + timedelta(seconds=30), point(depth=30))
        self.assertEqual(filtered.raw_depth, 30)

    def test_disabled_filter_keeps_observation(self):
        config = dict(engine.FILTER_DEFAULTS, enabled=False)
        filtered, audit = engine.IntelligentInputFilter(config).apply(
            'one', datetime.now(timezone.utc), point(depth=999))
        self.assertEqual(filtered.raw_depth, 999)
        self.assertEqual(audit['decisions']['filter'], 'disabled')
