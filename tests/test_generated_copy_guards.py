"""Guards on what the model may hand the renderer: no markup, no over-long
headline, no shouted supporting line."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai import style
from app.ai.generator import GeneratedPost, headline_problem


def _post(**over) -> dict:
    base = {
        "caption": "c",
        "hashtags": ["#x"],
        "template_variant": "ts_p3_editorial",
        "headline": "HAPPY EASTER",
        "rationale": "r",
    }
    base.update(over)
    return base


def test_tool_scaffolding_leaked_into_a_field_is_rejected() -> None:
    # Post 15 of the first review batch carried exactly this in its headline.
    bad = 'HAPPY NEW YEAR 2027</headline>\n<parameter Name="eyebrow">NEW YEAR\'S DAY'
    with pytest.raises(ValidationError, match="markup"):
        GeneratedPost.model_validate(_post(headline=bad))
    with pytest.raises(ValidationError, match="markup"):
        GeneratedPost.model_validate(_post(subhead="<b>bold</b>"))


def test_ordinary_punctuation_is_not_mistaken_for_markup() -> None:
    post = GeneratedPost.model_validate(_post(caption="Wings <3 | 2 < 3 | A & B"))
    assert "<3" in post.caption


def test_a_headline_that_would_wrap_is_named_as_a_problem() -> None:
    assert headline_problem("TO THE PEOPLE WHO KEEP THE WORLD FED") is not None
    assert headline_problem("HERE'S TO THE WOMEN MOVING OUR WORLD") is not None
    assert headline_problem("HAPPY EASTER") is None
    assert headline_problem("CELEBRATING NATIONAL PORK MONTH") is None


def test_a_shouted_subhead_is_set_in_title_case_like_its_siblings() -> None:
    post = GeneratedPost.model_validate(
        _post(subhead="FROM OCEAN TO PORT | MOVED AT GLOBAL SCALE | SIAL PARIS")
    )
    style.enforce(post)
    assert post.subhead == "From Ocean to Port | Moved at Global Scale | SIAL Paris"


def test_an_explicit_case_instruction_leaves_the_subhead_alone() -> None:
    post = GeneratedPost.model_validate(_post(subhead="ALL CAPS PLEASE"))
    style.enforce(post, feedback="make the subhead all caps")
    assert post.subhead == "ALL CAPS PLEASE"
