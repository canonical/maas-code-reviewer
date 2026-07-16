# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ReviewMetrics:
    model_name: str = ""
    tokens_thinking: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    files_read: int = 0
    agents_md_read: bool = False
    diff_lines: int = 0
    diff_size_bytes: int = 0
    tool_call_limit: int = 0
    tool_call_limit_reached: bool = False
    resume_attempts: int = 0


def write_metrics(metrics: ReviewMetrics, path: Path) -> None:
    """Serialise *metrics* as JSON to *path*."""
    path.write_text(json.dumps(dataclasses.asdict(metrics), indent=2))
