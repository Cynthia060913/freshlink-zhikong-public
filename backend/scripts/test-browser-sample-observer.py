"""Pure harness regressions; no application/database/config imports."""

import importlib.util
import json
import unittest
from pathlib import Path
from uuid import UUID

spec = importlib.util.spec_from_file_location(
    "browser_harness", Path(__file__).with_name("check-live-control-browser.py")
)
assert spec and spec.loader
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def sample(number: int, temperature: float) -> bytes:
    return json.dumps(
        {"message_id": str(UUID(int=number)), "metrics": [{"value": temperature}]}
    ).encode()


class SampleObserverTests(unittest.TestCase):
    def test_redelivery_burst_does_not_exhaust_unique_sample_capacity(self):
        observer = harness.SampleObserver()
        raw = sample(1, 10)
        for _ in range(501):
            observer.observe(raw)
        self.assertEqual(observer.values, [10])
        self.assertEqual(
            observer.counts(),
            {
                "received": 501,
                "unique": 1,
                "duplicate_deliveries": 500,
            },
        )

    def test_old_redelivery_does_not_rewind_observed_cooling(self):
        observer = harness.SampleObserver()
        old = sample(1, 10)
        observer.observe(old)
        observer.observe(sample(2, 8))
        observer.observe(old)
        self.assertEqual(observer.values, [10, 8])

    def test_same_id_different_body_is_an_error(self):
        observer = harness.SampleObserver()
        observer.observe(sample(1, 10))
        with self.assertRaisesRegex(RuntimeError, "sample_identity_conflict"):
            observer.observe(sample(1, 8))
        self.assertEqual(observer.values, [10])

    def test_unique_capacity_still_fails_closed(self):
        observer = harness.SampleObserver(capacity=2)
        observer.observe(sample(1, 10))
        observer.observe(sample(2, 8))
        with self.assertRaisesRegex(RuntimeError, "sample_capacity"):
            observer.observe(sample(3, 7))
        self.assertEqual(observer.values, [10, 8])


if __name__ == "__main__":
    unittest.main()
