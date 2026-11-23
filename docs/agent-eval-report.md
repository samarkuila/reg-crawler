# Luna evaluation report

Generated 23.11.2026 · golden set `tests/fixtures/agent/golden.jsonl` (12 items) · prompt v1 · guidance version - · model gpt-6-luna · client: fake (keyword stand-in, no model)

> **FAKE CLIENT: these numbers prove the evaluation runs, not that Luna works.** Run again against the approved endpoint (no `--fake`) for a real result.

> Only 12 items: the spec asks for 50 or more labelled by Compliance. Treat the numbers as a smoke test.

| Metric | Result | Target | Verdict |
|---|---|---|---|
| Recall on NBFC-relevant items (MEDIUM/HIGH) | 80% | ≥ 98% | not meaningful (fake client) |
| Precision of HIGH | 80% | ≥ 80% | not meaningful (fake client) |
| Quote validity (returned quotes found in the text) | 100% | 100% | not meaningful (fake client) |
| Field accuracy (validated ref no., dates, authority) | n/a | ≥ 95% | not meaningful (fake client) |
| Cost | ₹0.00 for 12 items (₹0.00 each) | within the daily budget | see Settings |

Sweep false alarms (≤ 2 a day across 5 sources) can only be measured in the shadow run.

## Per item

| Id | Title | Expected | Luna | Quotes (rejected) | Fields right | Note |
|---|---|---|---|---|---|---|
| g01 | Master Direction - Know Your Customer (KYC) Direction, 2026 | HIGH | HIGH | 1 (0) | 0/0 |  |
| g02 | Digital Lending Directions: amendment | HIGH | HIGH | 1 (0) | 0/0 |  |
| g03 | UN Security Council sanctions list: entry amended | HIGH | HIGH | 1 (0) | 0/0 |  |
| g04 | Foreign Exchange Management (Overseas Investment) Amendment  | MEDIUM | NONE | 1 (0) | 0/0 |  |
| g05 | Digital Personal Data Protection: guidance for data fiduciar | MEDIUM | HIGH | 1 (0) | 0/0 |  |
| g06 | Master Circular on Prudential Norms for Urban Co-operative B | LOW | HIGH | 1 (0) | 0/0 |  |
| g07 | Auction of Government of India Dated Securities | NONE | NONE | 1 (0) | 0/0 |  |
| g08 | Recruitment of Grade B officers | NONE | NONE | 1 (0) | 0/0 |  |
| g09 | Variable Rate Repo auction results | NONE | NONE | 1 (0) | 0/0 |  |
| g10 | Telecom Consumer Protection (Amendment) Regulations, 2026 | LOW | NONE | 1 (0) | 0/0 |  |
| g11 | Scheduled maintenance of the XBRL portal | NONE | NONE | 1 (0) | 0/0 |  |
| g12 | Insurance corporate agents: revised commission guidelines | LOW | NONE | 1 (0) | 0/0 |  |
