You MUST produce your review as a JSON object matching this schema:

{
  "general_comment": "<overall review as a string>",
  "inline_comments": {
    "<file path>": {
      "<line number as string>": "<comment text>",
      ...
    },
    ...
  }
}

Rules for inline_comments:
- Only include file paths that appear in the diff.
- Only include line numbers that appear in the diff for that file (use the
  new-file line numbers from the hunk headers).
- Line numbers must be JSON string keys (e.g. "42", not 42).
- If you have no inline comments, use an empty object {}.

Before finalising your response, call the validate_review tool with your
JSON to check it against the schema and the diff. Fix any errors it reports
and re-validate until there are no errors. Then output the final JSON object
and nothing else.
