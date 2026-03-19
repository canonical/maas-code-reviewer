from __future__ import annotations

from lp_ci_tools.review_schema import parse_diff_files_and_lines, validate_review_json

# ---------------------------------------------------------------------------
# Sample diffs used across multiple tests
# ---------------------------------------------------------------------------

SIMPLE_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,4 +1,5 @@
 import os
+import sys

 def main():
-    pass
+    print("hello")
"""

# A diff touching two files
TWO_FILE_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 import os
+import sys

 def main():
--- a/src/bar.py
+++ b/src/bar.py
@@ -10,4 +10,5 @@
 def helper():
+    # new comment
     return 42

 def other():
"""

# A diff that deletes a file (target is /dev/null)
DELETE_FILE_DIFF = """\
--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-import os
-
-def gone(): pass
"""

# A diff that creates a new file
NEW_FILE_DIFF = """\
--- /dev/null
+++ b/new_module.py
@@ -0,0 +1,3 @@
+import os
+
+def fresh(): pass
"""


# ---------------------------------------------------------------------------
# TestParseDiffFilesAndLines
# ---------------------------------------------------------------------------


class TestParseDiffFilesAndLines:
    def test_returns_dict(self) -> None:
        result = parse_diff_files_and_lines(SIMPLE_DIFF)
        assert isinstance(result, dict)

    def test_identifies_correct_file(self) -> None:
        result = parse_diff_files_and_lines(SIMPLE_DIFF)
        assert "src/foo.py" in result

    def test_strips_b_prefix_from_path(self) -> None:
        result = parse_diff_files_and_lines(SIMPLE_DIFF)
        # Should NOT have the raw "b/src/foo.py" key
        assert "b/src/foo.py" not in result

    def test_line_numbers_are_integers(self) -> None:
        result = parse_diff_files_and_lines(SIMPLE_DIFF)
        for lines in result.values():
            for ln in lines:
                assert isinstance(ln, int)

    def test_simple_diff_line_numbers(self) -> None:
        # Hunk: @@ -1,4 +1,5 @@
        # new-file lines: 1 (context "import os"), 2 (added "import sys"),
        #                 3 (context blank), 4 (context "def main():"),
        #                 5 (added "    print(...)")
        # deletion ("-    pass") does NOT advance new-file counter
        result = parse_diff_files_and_lines(SIMPLE_DIFF)
        lines = result["src/foo.py"]
        assert 1 in lines  # context: import os
        assert 2 in lines  # added: import sys
        assert 3 in lines  # context: blank line
        assert 4 in lines  # context: def main():
        assert 5 in lines  # added: print(...)

    def test_deletion_lines_not_included(self) -> None:
        # The "-    pass" line is a deletion; it must not appear in the set
        result = parse_diff_files_and_lines(SIMPLE_DIFF)
        lines = result["src/foo.py"]
        # Line 5 in the OLD file is the deletion but new-file line 5 is the addition.
        # The important thing is that we never include a new-file line number
        # purely from a deletion.  The deletion in this hunk is at old line 4;
        # its new-file equivalent would be between new lines 4 and 5.
        # We verify by checking that we have exactly the expected count.
        # (5 new-side lines total: 3 context + 2 added)
        assert len(lines) == 5

    def test_two_file_diff(self) -> None:
        result = parse_diff_files_and_lines(TWO_FILE_DIFF)
        assert "src/foo.py" in result
        assert "src/bar.py" in result

    def test_two_file_diff_bar_lines(self) -> None:
        # Hunk for bar.py: @@ -10,4 +10,5 @@
        # new-file lines starting at 10:
        #   10: context "def helper():"
        #   11: added "    # new comment"
        #   12: context "    return 42"
        #   13: context blank
        #   14: context "def other():"
        result = parse_diff_files_and_lines(TWO_FILE_DIFF)
        lines = result["src/bar.py"]
        assert 10 in lines
        assert 11 in lines
        assert 12 in lines
        assert 13 in lines
        assert 14 in lines

    def test_deleted_file_not_in_result(self) -> None:
        # When +++ is /dev/null the file should not appear
        result = parse_diff_files_and_lines(DELETE_FILE_DIFF)
        assert "old.py" not in result
        assert "/dev/null" not in result

    def test_new_file_included(self) -> None:
        result = parse_diff_files_and_lines(NEW_FILE_DIFF)
        assert "new_module.py" in result

    def test_new_file_line_numbers(self) -> None:
        # @@ -0,0 +1,3 @@ — new file, 3 added lines starting at 1
        result = parse_diff_files_and_lines(NEW_FILE_DIFF)
        lines = result["new_module.py"]
        assert 1 in lines
        assert 2 in lines
        assert 3 in lines

    def test_empty_diff_returns_empty_dict(self) -> None:
        result = parse_diff_files_and_lines("")
        assert result == {}

    def test_multiple_hunks_same_file(self) -> None:
        diff = (
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1,2 +1,3 @@\n"
            " line1\n"
            "+added_a\n"
            " line2\n"
            "@@ -10,2 +11,3 @@\n"
            " line10\n"
            "+added_b\n"
            " line11\n"
        )
        result = parse_diff_files_and_lines(diff)
        lines = result["x.py"]
        # First hunk: 1, 2, 3
        assert 1 in lines
        assert 2 in lines
        assert 3 in lines
        # Second hunk: 11, 12, 13
        assert 11 in lines
        assert 12 in lines
        assert 13 in lines

    def test_path_without_b_prefix_kept_as_is(self) -> None:
        diff = "--- a/plain.py\n+++ plain.py\n@@ -1 +1 @@\n+x\n"
        result = parse_diff_files_and_lines(diff)
        assert "plain.py" in result


# ---------------------------------------------------------------------------
# TestValidateReviewJson
# ---------------------------------------------------------------------------

_VALID_DATA = {
    "general_comment": "Looks good overall.",
    "inline_comments": {
        "src/foo.py": {
            "2": "Good use of sys here.",
        }
    },
}


class TestValidateReviewJson:
    def test_valid_data_returns_no_errors(self) -> None:
        errors = validate_review_json(_VALID_DATA, SIMPLE_DIFF)
        assert errors == []

    def test_empty_inline_comments_is_valid(self) -> None:
        data = {"general_comment": "Fine.", "inline_comments": {}}
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert errors == []

    def test_non_dict_root_returns_error(self) -> None:
        errors = validate_review_json(["not", "a", "dict"], SIMPLE_DIFF)  # type: ignore[arg-type]
        assert len(errors) == 1
        assert "dict" in errors[0].lower() or "object" in errors[0].lower()

    def test_missing_general_comment(self) -> None:
        data: dict = {"inline_comments": {}}
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("general_comment" in e for e in errors)

    def test_general_comment_wrong_type(self) -> None:
        data = {"general_comment": 42, "inline_comments": {}}
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("general_comment" in e for e in errors)

    def test_missing_inline_comments(self) -> None:
        data: dict = {"general_comment": "ok"}
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("inline_comments" in e for e in errors)

    def test_inline_comments_wrong_type(self) -> None:
        data = {"general_comment": "ok", "inline_comments": "not a dict"}
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("inline_comments" in e for e in errors)

    def test_per_file_value_not_dict(self) -> None:
        data = {
            "general_comment": "ok",
            "inline_comments": {"src/foo.py": "not a dict"},
        }
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("src/foo.py" in e for e in errors)

    def test_file_path_not_in_diff(self) -> None:
        data = {
            "general_comment": "ok",
            "inline_comments": {
                "nonexistent/file.py": {"1": "some comment"},
            },
        }
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("nonexistent/file.py" in e for e in errors)

    def test_line_number_not_in_diff(self) -> None:
        data = {
            "general_comment": "ok",
            "inline_comments": {
                "src/foo.py": {"999": "way out of range"},
            },
        }
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("999" in e for e in errors)

    def test_line_key_not_an_integer_string(self) -> None:
        data = {
            "general_comment": "ok",
            "inline_comments": {
                "src/foo.py": {"abc": "bad key"},
            },
        }
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("abc" in e for e in errors)

    def test_comment_value_wrong_type(self) -> None:
        data = {
            "general_comment": "ok",
            "inline_comments": {
                "src/foo.py": {"2": 123},
            },
        }
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert any("src/foo.py" in e for e in errors)

    def test_multiple_errors_returned(self) -> None:
        # Both missing keys should produce errors
        errors = validate_review_json({}, SIMPLE_DIFF)
        assert len(errors) >= 2

    def test_valid_two_file_inline_comments(self) -> None:
        data = {
            "general_comment": "Some issues found.",
            "inline_comments": {
                "src/foo.py": {"2": "Nice import."},
                "src/bar.py": {"11": "Good comment."},
            },
        }
        errors = validate_review_json(data, TWO_FILE_DIFF)
        assert errors == []

    def test_line_1_valid_for_new_file(self) -> None:
        data = {
            "general_comment": "Fresh file.",
            "inline_comments": {
                "new_module.py": {"1": "First line of new file."},
            },
        }
        errors = validate_review_json(data, NEW_FILE_DIFF)
        assert errors == []

    def test_deleted_file_path_rejected(self) -> None:
        # old.py was deleted (target is /dev/null) so it has no new-file lines
        data = {
            "general_comment": "ok",
            "inline_comments": {
                "old.py": {"1": "this file was deleted"},
            },
        }
        errors = validate_review_json(data, DELETE_FILE_DIFF)
        assert any("old.py" in e for e in errors)

    def test_context_lines_are_valid_targets(self) -> None:
        # Line 1 in SIMPLE_DIFF is a context line ("import os") — still valid
        data = {
            "general_comment": "ok",
            "inline_comments": {
                "src/foo.py": {"1": "comment on context line"},
            },
        }
        errors = validate_review_json(data, SIMPLE_DIFF)
        assert errors == []
