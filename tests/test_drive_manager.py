import unittest
from unittest.mock import MagicMock, patch

from src import drive_manager


class FetchLatestEpisodeStateTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_returns_latest_row(self, get_client):
        client = MagicMock()
        execute_result = MagicMock()
        execute_result.data = [{"episode_no": 105, "status": "published"}]
        client.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value = (
            execute_result
        )
        get_client.return_value = client

        state = drive_manager.fetch_latest_episode_state()

        self.assertEqual(state["episode"], 105)

    @patch("src.drive_manager.get_client")
    def test_empty_table_returns_default(self, get_client):
        client = MagicMock()
        execute_result = MagicMock()
        execute_result.data = []
        client.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value = (
            execute_result
        )
        get_client.return_value = client

        state = drive_manager.fetch_latest_episode_state()

        self.assertEqual(state, drive_manager.DEFAULT_STATE)

    @patch("src.drive_manager.get_client")
    def test_db_failure_returns_default(self, get_client):
        get_client.side_effect = RuntimeError("no credentials")

        state = drive_manager.fetch_latest_episode_state()

        self.assertEqual(state, drive_manager.DEFAULT_STATE)


class StartEpisodeTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_inserts_script_ready_row_and_returns_id(self, get_client):
        client = MagicMock()
        get_client.return_value = client

        episode_id = drive_manager.start_episode({"episode": 103, "villain": "Debt Titan"})

        self.assertTrue(episode_id.startswith("ep-0103-"))
        insert_call = client.table.return_value.insert.call_args[0][0]
        self.assertEqual(insert_call["episode_no"], 103)
        self.assertEqual(insert_call["status"], "script_ready")
        self.assertEqual(insert_call["revision"], 1)
        client.table.return_value.insert.return_value.execute.assert_called_once()


class UpdateEpisodeTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_update_sends_fields_with_updated_at(self, get_client):
        client = MagicMock()
        get_client.return_value = client

        drive_manager.update_episode("ep-0103-abcd1234", status="published", youtube_video_id="abc123")

        update_call = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_call["status"], "published")
        self.assertEqual(update_call["youtube_video_id"], "abc123")
        self.assertIn("updated_at", update_call)
        client.table.return_value.update.return_value.eq.assert_called_once_with("id", "ep-0103-abcd1234")

    @patch("src.drive_manager.get_client")
    def test_no_fields_is_noop(self, get_client):
        drive_manager.update_episode("ep-0103-abcd1234")

        get_client.assert_not_called()


class StepRunTest(unittest.TestCase):
    @patch("src.drive_manager.get_client")
    def test_start_and_finish_success_path(self, get_client):
        client = MagicMock()
        get_client.return_value = client

        step_run_id = drive_manager.record_step_start("ep-0103-abcd1234", "render")
        self.assertIsNotNone(step_run_id)
        insert_call = client.table.return_value.insert.call_args[0][0]
        self.assertEqual(insert_call["episode_id"], "ep-0103-abcd1234")
        self.assertEqual(insert_call["step"], "render")
        self.assertEqual(insert_call["status"], "running")

        drive_manager.record_step_finish(step_run_id, "success")
        update_call = client.table.return_value.update.call_args[0][0]
        self.assertEqual(update_call["status"], "success")

    @patch("src.drive_manager.get_client")
    def test_start_failure_returns_none_without_raising(self, get_client):
        get_client.side_effect = RuntimeError("db down")

        step_run_id = drive_manager.record_step_start("ep-0103-abcd1234", "render")

        self.assertIsNone(step_run_id)

    def test_finish_with_none_id_is_noop(self):
        # 예외가 발생하지 않으면 성공
        drive_manager.record_step_finish(None, "success")


if __name__ == "__main__":
    unittest.main()


class EpisodeNumberingTest(unittest.TestCase):
    def test_default_state_starts_numbering_at_one(self):
        # 빈 테이블이면 next_ep = 0 + 1 = 1 이어야 한다
        self.assertEqual(drive_manager.DEFAULT_STATE["episode"], 0)

    @patch("src.drive_manager.get_client")
    def test_empty_table_yields_episode_one(self, get_client):
        client = MagicMock()
        result = MagicMock()
        result.data = []
        client.table.return_value.select.return_value.in_.return_value.order.return_value.limit.return_value.execute.return_value = (
            result
        )
        get_client.return_value = client

        state = drive_manager.fetch_latest_episode_state()

        self.assertEqual(state["episode"] + 1, 1)

    @patch("src.drive_manager.get_client")
    def test_db_failure_also_yields_episode_one(self, get_client):
        get_client.side_effect = RuntimeError("db down")

        state = drive_manager.fetch_latest_episode_state()

        self.assertEqual(state["episode"] + 1, 1)

    @patch("src.drive_manager.get_client")
    def test_episode_id_is_zero_padded_for_single_digit(self, get_client):
        get_client.return_value = MagicMock()

        episode_id = drive_manager.start_episode({"episode": 1, "villain": "Debt Titan"})

        self.assertTrue(episode_id.startswith("ep-0001-"))
