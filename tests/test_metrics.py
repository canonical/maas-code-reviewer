# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import json
from pathlib import Path

from maas_code_reviewer.metrics import ReviewMetrics, write_metrics


class TestReviewMetrics:
    def test_default_field_values(self) -> None:
        metrics = ReviewMetrics()
        assert metrics.model_name == ""
        assert metrics.tokens_thinking == 0
        assert metrics.tokens_input == 0
        assert metrics.tokens_output == 0
        assert metrics.files_read == 0
        assert metrics.agents_md_read is False
        assert metrics.diff_lines == 0
        assert metrics.diff_size_bytes == 0


class TestWriteMetrics:
    def test_writes_valid_json_that_round_trips(self, tmp_path: Path) -> None:
        metrics = ReviewMetrics(
            model_name="gemini-3-flash-preview",
            tokens_thinking=100,
            tokens_input=200,
            tokens_output=300,
            files_read=5,
            agents_md_read=True,
            diff_lines=42,
            diff_size_bytes=2048,
        )
        out = tmp_path / "metrics.json"
        write_metrics(metrics, out)

        data = json.loads(out.read_text())
        assert data["model_name"] == "gemini-3-flash-preview"
        assert data["tokens_thinking"] == 100
        assert data["tokens_input"] == 200
        assert data["tokens_output"] == 300
        assert data["files_read"] == 5
        assert data["agents_md_read"] is True
        assert data["diff_lines"] == 42
        assert data["diff_size_bytes"] == 2048

    def test_agents_md_read_serialises_as_json_boolean(self, tmp_path: Path) -> None:
        metrics = ReviewMetrics(agents_md_read=True)
        out = tmp_path / "metrics.json"
        write_metrics(metrics, out)

        raw = out.read_text()
        data = json.loads(raw)
        # Verify it's a real JSON boolean, not an int
        assert data["agents_md_read"] is True
        assert isinstance(data["agents_md_read"], bool)

    def test_agents_md_read_false_serialises_as_json_boolean(
        self, tmp_path: Path
    ) -> None:
        metrics = ReviewMetrics(agents_md_read=False)
        out = tmp_path / "metrics.json"
        write_metrics(metrics, out)

        data = json.loads(out.read_text())
        assert data["agents_md_read"] is False
        assert isinstance(data["agents_md_read"], bool)

    def test_default_values_write_correctly(self, tmp_path: Path) -> None:
        metrics = ReviewMetrics()
        out = tmp_path / "metrics.json"
        write_metrics(metrics, out)

        data = json.loads(out.read_text())
        assert data["model_name"] == ""
        assert data["tokens_thinking"] == 0
        assert data["tokens_input"] == 0
        assert data["tokens_output"] == 0
        assert data["files_read"] == 0
        assert data["agents_md_read"] is False
        assert data["diff_lines"] == 0
        assert data["diff_size_bytes"] == 0
