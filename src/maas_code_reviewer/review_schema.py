from __future__ import annotations

REVIEW_JSON_SCHEMA: dict = {
    "type": "object",
    "required": ["general_comment", "inline_comments"],
    "properties": {
        "general_comment": {"type": "string"},
        "inline_comments": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        },
    },
}


def parse_diff_files_and_lines(diff_text: str) -> dict[str, set[int]]:
    """Extract the set of valid file paths and changed line numbers from a unified diff.

    Returns a dict mapping file path → set of new-side line numbers that
    appear in the diff hunk headers and added/context lines.  Only lines
    that are actually present in the new version of the file (i.e. not
    deletion-only lines starting with ``-``) are included.
    """
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    current_line: int = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            # Extract the file path, stripping the "b/" prefix if present.
            path = raw_line[4:]
            if path.startswith("b/"):
                path = path[2:]
            if path != "/dev/null":
                current_file = path
                if current_file not in result:
                    result[current_file] = set()
            else:
                current_file = None
            continue

        if raw_line.startswith("--- "):
            # Handled via +++
            continue

        if raw_line.startswith("@@ "):
            # Parse the new-file start line from the hunk header.
            # Format: @@ -old_start[,old_count] +new_start[,new_count] @@
            parts = raw_line.split(" ")
            for part in parts:
                if part.startswith("+"):
                    line_info = part[1:]
                    if "," in line_info:
                        line_info = line_info.split(",")[0]
                    try:
                        current_line = int(line_info)
                    except ValueError:
                        current_line = 0
                    break
            continue

        if current_file is None:
            continue

        if raw_line.startswith("-"):
            # Deletion line — does not appear in new file; skip without
            # advancing the new-file line counter.
            continue

        if raw_line.startswith("+") or raw_line.startswith(" ") or raw_line == "":
            # Added line (+), context line ( ), or a bare empty line (some diff
            # generators strip the trailing space from empty context lines) —
            # all present in the new file.
            result[current_file].add(current_line)
            current_line += 1

    return result


def validate_review_json(data: dict, diff_text: str) -> list[str]:
    """Validate *data* against the review JSON schema and against the diff.

    Returns a list of error strings.  An empty list means the data is valid.

    Checks performed:

    1. ``data`` must be a ``dict``.
    2. ``general_comment`` must be present and be a ``str``.
    3. ``inline_comments`` must be present and be a ``dict``.
    4. Each value in ``inline_comments`` must be a ``dict``.
    5. Each key in each per-file dict must be a string that represents an
       integer (the line number).
    6. Each value in each per-file dict must be a ``str``.
    7. Each file path in ``inline_comments`` must appear in the diff.
    8. Each line number in ``inline_comments`` must appear in the diff for
       that file.
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        got = type(data).__name__
        errors.append(f"Review must be a JSON object (dict), got {got}.")
        return errors

    if "general_comment" not in data:
        errors.append("Missing required key: 'general_comment'.")
    elif not isinstance(data["general_comment"], str):
        got = type(data["general_comment"]).__name__
        errors.append(f"'general_comment' must be a string, got {got}.")

    if "inline_comments" not in data:
        errors.append("Missing required key: 'inline_comments'.")
        return errors

    inline = data["inline_comments"]
    if not isinstance(inline, dict):
        got = type(inline).__name__
        errors.append(f"'inline_comments' must be a JSON object (dict), got {got}.")
        return errors

    diff_files = parse_diff_files_and_lines(diff_text)

    for file_path, line_comments in inline.items():
        if not isinstance(line_comments, dict):
            errors.append(
                f"inline_comments[{file_path!r}] must be a JSON object (dict), "
                f"got {type(line_comments).__name__}."
            )
            continue

        if file_path not in diff_files:
            errors.append(
                f"File path {file_path!r} in 'inline_comments' does not"
                " appear in the diff."
            )
            # Still validate line/comment types even for unknown paths.

        valid_lines = diff_files.get(file_path, set())

        for line_key, comment in line_comments.items():
            # Validate that the key looks like an integer.
            try:
                line_number = int(line_key)
            except ValueError:
                errors.append(
                    f"inline_comments[{file_path!r}]: line key {line_key!r} is not "
                    f"a valid integer string."
                )
                continue

            if not isinstance(comment, str):
                errors.append(
                    f"inline_comments[{file_path!r}][{line_key!r}] must be a string, "
                    f"got {type(comment).__name__}."
                )

            if file_path in diff_files and line_number not in valid_lines:
                errors.append(
                    f"Line {line_number} of {file_path!r} does not appear in the diff."
                )

    return errors
