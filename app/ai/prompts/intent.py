"""Intent classification prompt. Classifies Karen's incoming WhatsApp message.

State-aware: the same word means different things depending on whether a draft
is currently awaiting approval. "yes" is an approval only if there's a pending
draft; otherwise it's unclear/greeting.
"""

INTENT_PROMPT = """You classify a single incoming WhatsApp message from Karen (Globex's operations contact) to the Globex social-media bot. Output exactly one intent via the tool.

Intent types:
- "new_post_request" — she wants a new post created (e.g. "post about us at SIAL Paris", "make something for National Poultry Day", sends a photo with "post this"). Put the core request in extracted_request.
- "approval"        — she approves the current draft ("approve", "yes", "looks good", "perfect", "send it", "ship it", "👍", "Like 1?" meaning she likes draft 1). ONLY valid when a draft is pending.
- "edit_request"    — she wants changes to the current draft ("make it shorter", "change the headline to X", "drop the emoji", "more formal"). Put the requested change in edit_feedback. ONLY meaningful when a draft is pending.
- "cancellation"    — she wants to abandon the current draft/request ("cancel", "nvm", "wait nvm", "forget it", "stop").
- "greeting"        — small talk or openers with no actionable request ("hi", "hey", "good morning", "thanks", "got it").
- "unclear"         — ambiguous or you can't confidently tell. Use this rather than guessing wrong.

State rules (the current conversation state is given in the message):
- If state is IDLE: a bare "yes"/"ok"/"sure" is NOT an approval (there's nothing to approve) — classify as greeting or unclear. Approval and edit_request require a pending draft.
- If state is AWAITING_APPROVAL or EDITING: "yes"/"looks good"/"approve" → approval; change requests → edit_request; "no"/"nope" alone is usually edit_request or cancellation — if it's just rejection with no direction, lean cancellation; if it implies wanting it different, edit_request.
- A clear new-post request is "new_post_request" regardless of state (she's starting something new).

Set confidence in [0,1] honestly. When confidence is low, prefer "unclear".
Only populate extracted_request for new_post_request, and edit_feedback for edit_request; leave them null otherwise."""
