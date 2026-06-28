import json
import tempfile
import unittest
from pathlib import Path

from codex_sessions.core.json_streams import iter_jsonl_objects


class JsonStreamsTests(unittest.TestCase):
    def test_iter_jsonl_objects_rejects_non_object_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            input_path.write_text("[]\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Expected JSON object on line 1"):
                list(iter_jsonl_objects(input_path))

    def test_iter_jsonl_objects_can_ignore_invalid_final_line_without_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            input_path.write_text(
                json.dumps({"type": "session_meta"}) + '\n{"type": "response_item"',
                encoding="utf-8",
            )

            records = list(iter_jsonl_objects(input_path, ignore_invalid_final_line=True))

        self.assertEqual(records, [(1, {"type": "session_meta"})])

    def test_iter_jsonl_objects_keeps_complete_line_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "rollout.jsonl"
            input_path.write_text(
                json.dumps({"type": "session_meta"}) + '\n{"type": "response_item"\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Invalid JSON on line 2"):
                list(iter_jsonl_objects(input_path, ignore_invalid_final_line=True))


if __name__ == "__main__":
    unittest.main()
