"""Relevance scoring, FR-34 effective dates and the FR-35 applicability hint."""

from datetime import date

import pytest

from app.schemas.enums import Applicability
from app.services import relevance
from app.services.normalise import html_to_text
from app.services.prefill_rules import applicability_hint, effective_date

INCLUDE = ["NBFC", "KYC", "non-banking", "digital lending"]
EXCLUDE = ["auction", "tender", "bid"]


# --- relevance ---------------------------------------------------------------------------------


def test_matching_include_words_rank_higher_and_say_why() -> None:
    r = relevance.score("Master Direction on KYC for lenders", INCLUDE, EXCLUDE)
    assert r.include_hits == ("KYC",) and r.score == 0.75 and r.percent == 75
    assert r.reason == "Mentions 'KYC'; ranked higher."


def test_more_hits_rank_higher_but_stay_below_one() -> None:
    one = relevance.score("KYC", INCLUDE, [])
    two = relevance.score("KYC for NBFC", INCLUDE, [])
    three = relevance.score("KYC for NBFC in digital lending", INCLUDE, [])
    assert one.score < two.score < three.score < 1


def test_exclude_words_rank_lower_but_never_zero() -> None:
    r = relevance.score("Auction of Government Securities", INCLUDE, EXCLUDE)
    assert r.exclude_hits == ("auction",) and 0 < r.score < 0.5
    assert "usually not relevant" in r.reason and "ranked lower" in r.reason


def test_a_mixed_title_lands_in_the_middle_with_both_reasons() -> None:
    r = relevance.score("Tender for KYC software", INCLUDE, EXCLUDE)
    assert "'KYC'" in r.reason and "'tender'" in r.reason
    assert (
        relevance.score("Tender", INCLUDE, EXCLUDE).score
        < r.score
        < relevance.score("KYC", INCLUDE, EXCLUDE).score
    )


def test_no_match_is_neutral_and_asks_for_a_read() -> None:
    r = relevance.score("A circular about something", INCLUDE, EXCLUDE)
    assert r.score == 0.5 and "neutral" in r.reason


def test_whole_words_only() -> None:
    assert relevance.score("forbidden words", [], ["bid"]).exclude_hits == ()
    assert relevance.score("The NBFCs list", ["NBFC"], []).include_hits == ()


def test_case_does_not_matter_and_phrases_work() -> None:
    assert relevance.score("DIGITAL LENDING norms", INCLUDE, []).include_hits == (
        "digital lending",
    )


def test_the_start_of_the_text_counts_too() -> None:
    r = relevance.score("Circular", INCLUDE, EXCLUDE, text="This applies to all NBFC entities.")
    assert r.include_hits == ("NBFC",)


def test_empty_keyword_lists_and_blank_words_are_safe() -> None:
    assert relevance.score("Anything", [], []).score == 0.5
    assert relevance.score("Anything", ["", "  "], [""]).score == 0.5


# --- FR-34 effective date ----------------------------------------------------------------------

ISSUE, PUBLISH = date(2026, 11, 20), date(2026, 11, 22)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("These Directions shall come into force with effect from 01.12.2026.", date(2026, 12, 1)),
        ("This is effective from 1st December 2026 for all entities", date(2026, 12, 1)),
        ("w.e.f. 15-01-2027", date(2027, 1, 15)),
        ("The rules come into effect on December 5, 2026.", date(2026, 12, 5)),
        ("Applicable with effect from 2026-12-31", date(2026, 12, 31)),
        ("shall come into force on the 1 Dec 2026", date(2026, 12, 1)),
    ],
)
def test_an_explicit_date_phrase_wins(text, expected) -> None:
    found = effective_date(text, ISSUE, PUBLISH)
    assert found.value == expected and found.rule.startswith("Date stated in the text")


def test_the_rule_quotes_the_words_it_used() -> None:
    found = effective_date("Effective from 01.12.2026.", ISSUE, PUBLISH)
    assert "Effective from 01.12.2026" in found.rule


@pytest.mark.parametrize(
    "phrase", ["with immediate effect", "effective immediately", "Effective Immediately"]
)
def test_immediate_effect_means_the_issue_date(phrase) -> None:
    found = effective_date(f"These directions are {phrase}.", ISSUE, PUBLISH)
    assert (
        found.value == ISSUE and "issue date" in found.rule and phrase.lower() in found.rule.lower()
    )


def test_a_date_that_is_not_after_an_effective_phrase_is_ignored() -> None:
    # "dated 05.11.2026" is the issue date of an earlier circular, not an effective date
    found = effective_date("Please refer to the circular dated 05.11.2026 on KYC.", ISSUE, PUBLISH)
    assert found.value == ISSUE and found.rule == "No effective date found: using the issue date"


def test_nothing_stated_uses_the_issue_date_else_the_publish_date() -> None:
    assert effective_date("Plain text", ISSUE, PUBLISH).value == ISSUE
    none_found = effective_date("Plain text", None, PUBLISH)
    assert none_found.value == PUBLISH and "publish date" in none_found.rule
    assert effective_date(None, None, None).value is None


def test_a_un_notice_uses_the_publish_date_unless_the_text_says_otherwise() -> None:
    plain = effective_date("Entry amended: alias added", ISSUE, PUBLISH, "UNSC")
    assert plain.value == PUBLISH and "UN notice" in plain.rule
    stated = effective_date("Effective from 05.12.2026", ISSUE, PUBLISH, "UNSC")
    assert stated.value == date(2026, 12, 5)


def test_impossible_dates_are_not_taken() -> None:
    found = effective_date("with effect from 45.13.2026", ISSUE, PUBLISH)
    assert found.value == ISSUE and found.rule.startswith("No effective date")


def test_line_breaks_inside_the_phrase_do_not_matter() -> None:
    found = effective_date("shall come into force\n   with effect from\n01.12.2026", ISSUE, PUBLISH)
    assert found.value == date(2026, 12, 1)


# --- FR-35 applicability hint ------------------------------------------------------------------

PROFILE = {
    "items": [
        {"name": "NBFC registered with RBI", "held": True},
        {"name": "Payment system participant", "held": False},
        {"name": "Insurance corporate agent", "held": None},
        {
            "name": "Reporting entity under PMLA",
            "held": True,
            "aliases": ["intermediaries under the PML"],
        },
    ]
}


def test_system_participants_with_a_profile_saying_no_suggests_not_applicable() -> None:
    hint = applicability_hint(
        "This circular is applicable to all system participants under the PSS Act.", PROFILE
    )
    assert hint.suggested == Applicability.NO
    assert hint.text == "Not applicable — not a payment system participant"
    assert hint.phrase.startswith("applicable to system participants")
    assert hint.profile_item == "Payment system participant"


def test_a_role_the_company_holds_suggests_applicable() -> None:
    hint = applicability_hint("Directions applicable to all NBFCs in the Upper Layer.", PROFILE)
    assert hint.suggested == Applicability.YES and "an NBFC registered with RBI" in hint.text


def test_an_unknown_profile_answer_is_to_be_confirmed() -> None:
    hint = applicability_hint("Applicable to corporate agents of insurers.", PROFILE)
    assert hint.suggested == Applicability.TBC and "does not say" in hint.text


def test_profile_aliases_are_used() -> None:
    hint = applicability_hint("This applies to intermediaries under the PMLA 2002.", PROFILE)
    assert hint.suggested == Applicability.YES


def test_no_phrase_or_no_match_gives_no_hint() -> None:
    assert applicability_hint("A notice about holidays.", PROFILE) is None
    assert applicability_hint("Applicable to cooperative banks.", PROFILE) is None
    assert applicability_hint(None, PROFILE) is None
    assert applicability_hint("Applicable to NBFCs", {"items": []}) is None
    assert applicability_hint("Applicable to NBFCs", None) is None


def test_the_placeholder_profile_never_claims_yes_or_no() -> None:
    placeholder = {"items": [{"name": "Payment system participant", "held": None}]}
    hint = applicability_hint("Applicable to system participants.", placeholder)
    assert hint.suggested == Applicability.TBC


# --- html to text ------------------------------------------------------------------------------


def test_html_to_text_keeps_lines_and_drops_scripts() -> None:
    text = html_to_text("<p>One &amp; two</p><script>bad()</script><div>Three<br>Four</div>")
    assert text == "One & two\nThree\nFour"
    assert html_to_text(None) == "" and html_to_text("") == ""
