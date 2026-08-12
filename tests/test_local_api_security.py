import os
import tempfile
import unittest

from web_app import app


class LocalApiSecurityTests(unittest.TestCase):
    def test_non_loopback_host_is_rejected(self):
        with app.test_client() as client:
            response = client.get("/", headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 403)

    def test_cross_origin_write_is_rejected(self):
        with tempfile.TemporaryDirectory() as output_dir:
            path = os.path.join(output_dir, "template.csv")
            with app.test_client() as client:
                response = client.post(
                    "/api/save-text-file",
                    json={"path": path, "content": "a,b"},
                    headers={"Origin": "https://attacker.example"},
                )
            self.assertEqual(response.status_code, 403)
            self.assertFalse(os.path.exists(path))

    def test_text_file_endpoint_only_writes_csv(self):
        with tempfile.TemporaryDirectory() as output_dir:
            path = os.path.join(output_dir, "not-a-template.txt")
            with app.test_client() as client:
                data = client.post(
                    "/api/save-text-file",
                    json={"path": path, "content": "a,b"},
                ).get_json()
            self.assertFalse(data["success"])
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
