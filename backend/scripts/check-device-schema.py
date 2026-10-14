"""Run in the application container: python scripts/check-device-schema.py."""

import unittest
import uuid

from pydantic import ValidationError

from app.modules.iot.models import Device, DeviceCreate, DevicePublic


class DeviceSchemaChecks(unittest.TestCase):
    def test_normalization(self):
        value = DeviceCreate(
            code=" TEMP_01 ", name=" Temperature ", kind="temperature_sensor"
        )
        self.assertEqual(value.code, "temp_01")
        self.assertEqual(value.name, "Temperature")

    def test_invalid_inputs(self):
        for change in [
            {"code": ""},
            {"code": "x" * 65},
            {"name": "   "},
            {"kind": "unknown"},
            {"warehouse_id": uuid.uuid4()},
        ]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                DeviceCreate(
                    **(
                        {
                            "code": "temp-1",
                            "name": "Temperature",
                            "kind": "temperature_sensor",
                        }
                        | change
                    )
                )

    def test_no_false_online(self):
        device = Device(code="cool-1", name="Cooling", kind="cooling_actuator")
        public = DevicePublic.model_validate(device)
        self.assertEqual(public.connectivity, "unknown")
        self.assertIsNone(public.last_seen_at)
        self.assertTrue(public.is_simulated)
        self.assertIsNotNone(public.created_at.tzinfo)


if __name__ == "__main__":
    unittest.main()
