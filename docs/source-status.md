# Source status

What is known about each source's live behaviour. Updated when a source is first fetched and
whenever it blocks us. A blocked source is recorded here and left alone: no workaround
(CLAUDE.md rule 1).

| Source | Channel | Live sample | Status | Notes |
|---|---|---|---|---|
| RBI Notifications | RSS `https://rbi.org.in/notifications_rss.xml` | **Not fetched yet** | Pending | Spider `rss` is tested on a synthetic fixture only. The real feed's field names, description HTML and date format are unconfirmed. |
| RBI Press Releases | RSS `https://rbi.org.in/pressreleases_rss.xml` | **Not fetched yet** | Pending | Same spider; not yet tried. |
| UNSC consolidated list | XML `https://scsanctions.un.org/resources/xml/en/consolidated.xml` | **Not fetched yet** | Pending | Spider `unsc_xml` is tested on a synthetic file that follows the documented element names (`INDIVIDUAL`, `ENTITY`, `REFERENCE_NUMBER`, `*_ALIAS/ALIAS_NAME`). The real file's structure is unconfirmed. |

### Wave 1 HTML listing pages (Prompt 11)

| Source | Page | Live sample | Selectors | Status |
|---|---|---|---|---|
| RBI Notifications | `https://www.rbi.org.in/Scripts/NotificationUser.aspx` | Not fetched | Placeholder (`TODO-prompt-11`) | Pending |
| RBI Press Releases | `https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx` | Not fetched | Placeholder | Pending |
| TRAI press releases | `https://trai.gov.in/notifications/press-release` | Not fetched | Placeholder | Pending |
| IRDAI circulars/guidelines | `https://policyholder.gov.in/` (circulars page to be confirmed) | Not fetched | Placeholder | Pending |
| eGazette extraordinary gazettes | `https://egazette.gov.in` (never the search form) | Not fetched | Placeholder | Pending |

The generic `listing` spider is built and tested on synthetic pages (`tests/fixtures/html/generic/`).
No real selector can be written without the real page, so the selectors in `config/sources.yaml`
are still placeholders and the spider reports them as such. Nothing was fetched from these sites.

## Why no live samples yet (23.11.2026, Prompt 10)

The live fetch was deliberately postponed. The crawler's User-Agent must carry a real contact
address (CLAUDE.md rule 2), and `crawler.contact` in `config/settings.yaml` is still the
`example.invalid` placeholder. Nothing was sent to any of the sites above.

**To finish:** set `crawler.contact`, then run each spider once against the live source (see
`docs/architecture.md`, "RSS and UNSC spiders"), save the answer as
`tests/fixtures/rss/rbi_notifications_live.xml` and `tests/fixtures/xml/unsc_consolidated_live.xml`,
add tests for them, and update the table above. If a site blocks or refuses the fetch, stop for
that source and write the date, the status code or page and what was seen here.
