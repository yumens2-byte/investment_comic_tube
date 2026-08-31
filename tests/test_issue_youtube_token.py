import sys
import unittest
from unittest.mock import MagicMock, patch

from scripts.issue_youtube_token import build_client_config, main


class BuildClientConfigTest(unittest.TestCase):
    def test_installed_app_shape(self):
        config = build_client_config("cid", "csecret")
        installed = config["installed"]
        self.assertEqual(installed["client_id"], "cid")
        self.assertEqual(installed["client_secret"], "csecret")
        self.assertEqual(installed["redirect_uris"], ["http://localhost"])


class MainMissingArgsTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_client_id_and_secret_returns_1(self):
        with patch.object(sys, "argv", ["issue_youtube_token.py"]):
            self.assertEqual(main(), 1)


class MainSuccessPathTest(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "csecret"},
        clear=True,
    )
    @patch("googleapiclient.discovery.build")
    @patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config")
    def test_success_path_prints_refresh_token(self, from_client_config, build_youtube):
        fake_creds = MagicMock()
        fake_creds.refresh_token = "NEW_REFRESH_TOKEN_VALUE"

        fake_flow = MagicMock()
        fake_flow.run_local_server.return_value = fake_creds
        from_client_config.return_value = fake_flow

        fake_youtube = MagicMock()
        fake_youtube.channels.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "snippet": {"title": "EDT Universe"},
                    "status": {"privacyStatus": "public"},
                }
            ]
        }
        build_youtube.return_value = fake_youtube

        with patch.object(sys, "argv", ["issue_youtube_token.py"]):
            result = main()

        self.assertEqual(result, 0)
        fake_flow.run_local_server.assert_called_once_with(
            port=8765, access_type="offline", prompt="consent"
        )

    @patch.dict(
        "os.environ",
        {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "csecret"},
        clear=True,
    )
    @patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config")
    def test_empty_refresh_token_returns_1(self, from_client_config):
        fake_creds = MagicMock()
        fake_creds.refresh_token = None

        fake_flow = MagicMock()
        fake_flow.run_local_server.return_value = fake_creds
        from_client_config.return_value = fake_flow

        with patch.object(sys, "argv", ["issue_youtube_token.py"]):
            result = main()

        self.assertEqual(result, 1)

    @patch.dict(
        "os.environ",
        {"YOUTUBE_CLIENT_ID": "cid", "YOUTUBE_CLIENT_SECRET": "csecret"},
        clear=True,
    )
    @patch("google_auth_oauthlib.flow.InstalledAppFlow.from_client_config")
    def test_browser_flow_exception_returns_1(self, from_client_config):
        fake_flow = MagicMock()
        fake_flow.run_local_server.side_effect = RuntimeError("port in use")
        from_client_config.return_value = fake_flow

        with patch.object(sys, "argv", ["issue_youtube_token.py"]):
            result = main()

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
