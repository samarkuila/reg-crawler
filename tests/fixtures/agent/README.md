# Luna golden set

`golden.jsonl` has one JSON object per line: `id`, `title`, `authority`, `text`, `nbfc_relevant`,
`expected_level` (HIGH / MEDIUM / LOW / NONE) and `expected` (ref_no, issue_date, effective_date,
authority; null when the text does not say).

**This file is SYNTHETIC (12 invented items).** It is enough to prove the evaluation machinery works.
The real golden set (50 or more items labelled by Compliance from Divya's past emails and rejected
items) must replace it before any recall or precision number means anything (agent spec §9).
