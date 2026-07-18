import unittest

from utils import validate_app_id


class AppIdRegistryTest(unittest.TestCase):
    def test_resolves_authoritative_wmi_app_id(self):
        self.assertTrue(validate_app_id("46615E8C-69DD-4B8F-94D1-290EFA867143", "WMI"))

    def test_resolves_new_authoritative_app_ids(self):
        expected_mappings = {
            "0e4f8ac8-af3d-4fbd-802f-fd06607fe0b9": "Discord",
            "48ce45b2-0de5-474f-be52-8266350325cd": "Cisco Secure Access",
            "88e88f59-5c78-457b-9d81-1f41f9fd2096": "Doppel",
        }

        for app_id, app_name in expected_mappings.items():
            with self.subTest(app_id=app_id):
                self.assertTrue(validate_app_id(app_id, app_name))


if __name__ == "__main__":
    unittest.main()
