import unittest
from unittest.mock import patch

from src import db_client


class GetClientTest(unittest.TestCase):
    def tearDown(self):
        db_client.reset_client_for_tests()

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_env_raises(self):
        with self.assertRaises(RuntimeError):
            db_client.get_client()

    @patch.dict(
        "os.environ",
        {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "svc-key"},
        clear=True,
    )
    @patch("src.db_client.create_client")
    def test_client_is_singleton(self, create_client):
        sentinel = object()
        create_client.return_value = sentinel

        first = db_client.get_client()
        second = db_client.get_client()

        self.assertIs(first, sentinel)
        self.assertIs(second, sentinel)
        create_client.assert_called_once_with("https://example.supabase.co", "svc-key")


if __name__ == "__main__":
    unittest.main()
