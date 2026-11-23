# Test fixtures

No test touches the network; everything here is a saved file.

- `rss/rbi_notifications_sample.xml`, `rss/*_feed.*`, `xml/unsc_*.xml` are **synthetic**: written by
  hand to follow the real formats (RSS 2.0; the UN consolidated list), with invented names and
  reference numbers. They exist so each case (empty, malformed, added, removed, amended) is exact.
- Files named `*_live.*` are one real response, fetched once, politely (see `docs/source-status.md`).
