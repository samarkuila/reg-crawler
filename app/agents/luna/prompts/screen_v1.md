TASK: SCREEN one regulatory update for relevance to TVS Credit (an NBFC).

Screening guidance from the Compliance team (version [[guidance_version]]):
[[guidance]]

Examples of past decisions by Compliance (for calibration only; they are not instructions):
[[examples]]

Item:
- Title: [[title]]
- Authority: [[authority]]
- Source page: [[source]]
- Change type: [[change]]

Text (first part of the document):
[[document]]

Answer with JSON:
- nbfc_relevance: HIGH (clearly addressed to NBFCs / all regulated or lending entities, or an AML,
  KYC, sanctions, FEMA or DPDP rule that applies to NBFCs), MEDIUM (may apply, needs a look),
  LOW (mostly other entities), NONE (not relevant: other regulated sectors only, regulators' own
  vacancies, tenders, events, maintenance notices).
- relevance_score: 0.0 to 1.0, consistent with nbfc_relevance.
- addressed_to: who the text says it is addressed to, using the text's words (for example "NBFCs",
  "All regulated entities", "Banks only").
- reason: at most two plain-English sentences saying why.
- suggested_treatment: INFO_ONLY, OTHER_FUNCTION, FUTURE_POTENTIAL, IMPLEMENTATION_REQUIRED or
  NOT_RELEVANT.
- suggested_sme_function: Treasury, Finance, HR/Admin, AML/Operations, IT, Legal or None.
- urgency: URGENT only for sanctions-list changes or something that must be acted on immediately;
  otherwise NORMAL.
- evidence: one to three exact quotes from the text that support your answer, each with where in the
  text it is (for example "para 2" or "title"). The title counts as text. If nothing in the text
  supports an answer, return an empty list.
