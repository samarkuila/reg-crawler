TASK: EXTRACT facts from one regulatory document so the Compliance team can pre-fill a form.

Item:
- Title: [[title]]
- Authority (from the source): [[authority]]
- Listing information (from the website listing page; this is the only place the publish date may
  come from):
[[listing]]

Text of the document:
[[document]]

For EACH field return {value, evidence_quote, location, confidence}. The evidence_quote must be an
exact quote from the listing information or the text that shows the value. If it is not stated,
return null for all four parts of that field.

Fields:
- ref_no: the reference or notification number, written as in the text.
- issue_date: the date the document was issued, as YYYY-MM-DD.
- publish_date: the date shown on the listing, as YYYY-MM-DD (listing information only).
- effective_date: the date it comes into effect, as YYYY-MM-DD, with effective_date_rule:
  EXPLICIT   the text states a date ("with effect from 1 December 2026"): use that date.
  IMMEDIATE_EFFECT   the text says "with immediate effect" or "effective immediately": use the
                     issue date.
  DEFAULT_ISSUE_DATE the text says nothing: use the issue date (the quote may be the issue date).
  For effective_date_rule, value is one of these three words; its quote is the same as for the date.
- authority: the issuing authority named in the text.
- document_type: Direction, Circular, Notification, Master Direction, FAQ, Press release, Gazette,
  Sanctions change, or Other.
- impact_summary: three to six plain sentences on what this means for a lender like TVS Credit, in
  the style of "Impact on TVSCS". Use only what the text says. Quote one sentence that supports it.
