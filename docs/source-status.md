# Source status

What is known about each source's live behaviour. Updated when a source is first fetched and
whenever it blocks us. A blocked source is recorded here and left alone: no workaround
(CLAUDE.md rule 1).

| Source | Channel | Live sample | Status | Notes |
|---|---|---|---|---|
| RBI Notifications | RSS `https://rbi.org.in/notifications_rss.xml` | **Not fetched yet** | Pending | Spider `rss` is tested on a synthetic fixture only. The real feed's field names, description HTML and date format are unconfirmed. |
| RBI Press Releases | RSS `https://rbi.org.in/pressreleases_rss.xml` | **Not fetched yet** | Pending | Same spider; not yet tried. |
| UNSC consolidated list | XML `https://scsanctions.un.org/resources/xml/en/consolidated.xml` | **Not fetched yet** | Pending | Spider `unsc_xml` is tested on a synthetic file that follows the documented element names (`INDIVIDUAL`, `ENTITY`, `REFERENCE_NUMBER`, `*_ALIAS/ALIAS_NAME`). The real file's structure is unconfirmed. |

## Why no live samples yet (23.11.2026, Prompt 10)

The live fetch was deliberately postponed. The crawler's User-Agent must carry a real contact
address (CLAUDE.md rule 2), and `crawler.contact` in `config/settings.yaml` is still the
`example.invalid` placeholder. Nothing was sent to any of the sites above.

**To finish:** set `crawler.contact`, then run each spider once against the live source (see
`docs/architecture.md`, "RSS and UNSC spiders"), save the answer as
`tests/fixtures/rss/rbi_notifications_live.xml` and `tests/fixtures/xml/unsc_consolidated_live.xml`,
add tests for them, and update the table above. If a site blocks or refuses the fetch, stop for
that source and write the date, the status code or page and what was seen here.
