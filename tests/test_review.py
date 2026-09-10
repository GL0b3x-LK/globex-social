"""The client review page: gated by the link, lists the batch, stores feedback."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.review import router as review

BATCH = "sep2026"


def _post(n: int, **extra):
    row = {
        "id": f"00000000-0000-0000-0000-00000000000{n}",
        "caption": f"Caption {n}",
        "hashtags": ["#Globex"],
        "template_type": "ts_p3_editorial",
        "image_url": f"https://img.test/{n}.png",
        "render_meta": {
            "review_batch": BATCH,
            "calendar_seq": n - 1,
            "publish_on": "2026-09-14",
            "caption_locked": n == 1,
            "calendar": {"title": f"Post {n} title", "category": "tradeshow"},
            "generated": {"headline": f"Headline {n}"},
        },
    }
    row.update(extra)
    return row


@pytest.fixture
def client(monkeypatch):
    store = {p["id"]: p for p in (_post(1), _post(2))}
    saved: list[dict] = []
    monkeypatch.setattr(review.posts, "list_review_batch", lambda batch: list(store.values()))
    monkeypatch.setattr(review.posts, "get", lambda pid: store.get(pid))
    monkeypatch.setattr(review.post_feedback, "list_for_batch", lambda batch: list(saved))

    def upsert(**kw):
        saved[:] = [
            s for s in saved if not (s["post_id"] == kw["post_id"] and s["author"] == kw["author"])
        ]
        saved.append(kw)
        return kw

    monkeypatch.setattr(review.post_feedback, "upsert", upsert)
    monkeypatch.setattr(review, "review_token", lambda: "secret-token")
    app = FastAPI()
    app.include_router(review.router)
    c = TestClient(app)
    c.saved = saved  # type: ignore[attr-defined]
    return c


def test_without_the_token_the_page_does_not_exist(client):
    assert client.get(f"/review/{BATCH}").status_code == 404
    assert client.get(f"/review/{BATCH}?k=wrong").status_code == 404


def test_the_page_lists_every_post_in_the_batch_with_its_image(client):
    resp = client.get(f"/review/{BATCH}?k=secret-token")
    assert resp.status_code == 200
    html = resp.text
    assert "Post 1 title" in html and "Post 2 title" in html
    assert "https://img.test/1.png" in html
    assert "Your caption, verbatim" in html  # post 1 carries the client's caption


def test_feedback_is_saved_against_a_post_in_the_batch(client):
    resp = client.post(
        f"/review/{BATCH}/feedback",
        json={
            "k": "secret-token",
            "post_id": _post(2)["id"],
            "author": "Karen",
            "verdict": "changes",
            "note": "Swap the photo for the Miami booth shot.",
        },
    )
    assert resp.status_code == 200, resp.text
    assert client.saved[0]["verdict"] == "changes"
    assert client.saved[0]["author"] == "Karen"


def test_feedback_cannot_be_attached_to_a_post_outside_the_batch(client):
    resp = client.post(
        f"/review/{BATCH}/feedback",
        json={"k": "secret-token", "post_id": "not-in-batch", "author": "Len", "note": "x"},
    )
    assert resp.status_code == 404


def test_a_bad_verdict_is_rejected(client):
    resp = client.post(
        f"/review/{BATCH}/feedback",
        json={"k": "secret-token", "post_id": _post(1)["id"], "author": "Len", "verdict": "meh"},
    )
    assert resp.status_code == 422


def test_the_markdown_digest_groups_notes_by_post(client):
    client.post(
        f"/review/{BATCH}/feedback",
        json={
            "k": "secret-token",
            "post_id": _post(1)["id"],
            "author": "Len",
            "verdict": "approved",
        },
    )
    client.post(
        f"/review/{BATCH}/feedback",
        json={
            "k": "secret-token",
            "post_id": None,
            "author": "Karen",
            "note": "Less emoji overall.",
        },
    )
    md = client.get(f"/review/{BATCH}/feedback.md?k=secret-token").text
    assert "## General" in md and "Less emoji overall." in md
    assert "## 1/2 Post 1 title" in md and "**Len** (looks good)" in md
    assert "1 of 2 posts have feedback" in md
