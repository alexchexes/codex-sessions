import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path

from codex_sessions.errors import CliError
from codex_sessions.sessions.paths import (
    default_output_path,
    infer_output_format,
    output_filename,
    resolve_conversion_input,
    resolve_output_path,
)


class ConversionPathsTests(unittest.TestCase):
    def test_infer_output_format_prefers_explicit_flags_and_output_suffix(self) -> None:
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=True, yaml=False, format=None, output=Path("out.yaml"))
            ),
            "md",
        )
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=False, yaml=True, format="md", output=Path("out.md"))
            ),
            "yaml",
        )
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=False, yaml=False, format="markdown", output=Path("out.yaml"))
            ),
            "md",
        )
        self.assertEqual(
            infer_output_format(
                argparse.Namespace(md=False, yaml=False, format=None, output=Path("out.md"))
            ),
            "md",
        )

    def test_output_filename_uses_stem_and_jsonl_suffix(self) -> None:
        self.assertEqual(output_filename(Path("rollout.jsonl")), "rollout.yaml")
        self.assertEqual(output_filename(Path("rollout.jsonl"), "md"), "rollout.md")
        self.assertEqual(output_filename(Path("rollout.txt"), "yaml"), "rollout.txt.yaml")
        self.assertEqual(
            output_filename(Path("rollout.jsonl"), "yaml", "session-id"), "session-id.yaml"
        )

    def test_default_and_directory_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / ".codex"
            input_path = codex_home / "sessions" / "2026" / "04" / "30" / "rollout.jsonl"
            output_dir = root / "out"
            output_dir.mkdir()

            self.assertEqual(
                default_output_path(input_path, codex_home, "yaml"),
                codex_home / "tmp" / "sessions" / "2026" / "04" / "30" / "rollout.yaml",
            )
            self.assertEqual(
                resolve_output_path(output_dir, input_path, codex_home, "md", "abc"),
                output_dir / "abc.md",
            )

    def test_resolve_conversion_input_reports_missing_file_without_stack_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.jsonl"

            with self.assertRaises(CliError) as raised:
                resolve_conversion_input(missing, Path(tmpdir) / ".codex")

        self.assertEqual(str(raised.exception), f"Input file not found: {missing}")

    def test_resolve_conversion_input_accepts_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-2026-05-28T10-00-00-older.jsonl"
            newer = sessions_day / "rollout-2026-05-28T11-00-00-newer.jsonl"
            write_jsonl(
                older,
                [
                    {
                        "timestamp": "2026-05-28T12:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "older"},
                    }
                ],
            )
            write_jsonl(
                newer,
                [
                    {
                        "timestamp": "2026-05-28T13:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "newer"},
                    }
                ],
            )
            os.utime(older, (1_800_000_100, 1_800_000_100))
            os.utime(newer, (1_800_000_000, 1_800_000_000))

            resolved = resolve_conversion_input(Path("latest"), codex_home)

        self.assertEqual(resolved.path, newer.resolve())
        self.assertIsNone(resolved.output_stem)

    def test_resolve_conversion_input_accepts_codex_home_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            relative_path = Path("sessions/2026/05/28/rollout.jsonl")
            rollout = codex_home / relative_path
            write_jsonl(rollout, [{"type": "session_meta", "payload": {"id": "session-id"}}])

            resolved = resolve_conversion_input(relative_path, codex_home)

        self.assertEqual(resolved.path, rollout.resolve())
        self.assertIsNone(resolved.output_stem)

    def test_resolve_conversion_input_latest_falls_back_to_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            sessions_day.mkdir(parents=True)
            older = sessions_day / "rollout-older.jsonl"
            newer = sessions_day / "rollout-newer.jsonl"
            write_jsonl(older, [{"type": "session_meta", "payload": {"id": "older"}}])
            write_jsonl(newer, [{"type": "session_meta", "payload": {"id": "newer"}}])
            os.utime(older, (1_800_000_000, 1_800_000_000))
            os.utime(newer, (1_800_000_100, 1_800_000_100))

            resolved = resolve_conversion_input(Path("latest"), codex_home)

        self.assertEqual(resolved.path, newer.resolve())

    def test_resolve_conversion_input_accepts_exact_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir) / ".codex"
            session_id = "019f0000-0000-7000-8000-000000000001"
            sessions_day = codex_home / "sessions" / "2026" / "05" / "28"
            rollout = sessions_day / f"rollout-2026-05-28T10-00-00-{session_id}.jsonl"
            sessions_day.mkdir(parents=True)
            write_jsonl(rollout, [{"type": "session_meta", "payload": {"id": session_id}}])
            write_jsonl(
                codex_home / "session_index.jsonl",
                [{"id": session_id, "thread_name": "Fix README.md docs"}],
            )

            resolved = resolve_conversion_input(Path("Fix README.md docs"), codex_home)

        self.assertEqual(resolved.path, rollout.resolve())
        self.assertEqual(resolved.output_stem, session_id)


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
