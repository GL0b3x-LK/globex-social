"""Free-form path: Karen sends an open request (maybe a photo); the model picks the
best template and writes the post. Used by the on-demand WhatsApp flow, where the
content category isn't known up front (the scheduler path knows it and uses the
category-specific prompts instead).

The choice is the four approved finals and nothing else. This prompt used to
offer the demo-era set — stats, promotional, quote_card, custom — which meant a
post written from scratch could not land on an approved template even by
accident: the names simply weren't on the menu. It also came out in the wrong
typeface, because the old templates render from _base.css (Montserrat) while the
finals render from _final.html (Poppins, and Inter on MS-3). One cause, both
complaints. The calendar never had the problem because its Template column is
authoritative; only the from-scratch path was choosing.
"""

FREEFORM_PROMPT = """TASK: Karen sent a free-form request (and maybe a photo). Pick the right approved template, then write the post.

Choose ONE template_variant. These four are the ONLY templates Globex publishes — there is no other option, and every one of them is built around a photograph:

- "TS-p1-bolddip_4x5" — photo-first with a bold rounded dip at the photo's bottom-left; ALL-CAPS letterspaced headline, optional cyan booth pill. The workhorse: use it for trade shows, products, capability and brand messages, stats, holidays and anything that doesn't clearly call for one of the others.
- "TS-p2-cut-navyborder_4x5" — square-cut photo in a thin navy frame with a full-width cyan divider, bold title-case headline, centered logo. Use when the message is calmer and more editorial than TS-p1: a considered statement, a thank-you, a market or partnership note.
- "TS-p3-editorial_4x5" — editorial masthead on top (headline plus a date/location/booth meta line with cyan bullets), photo below. Use when the post carries REAL specifics that belong in a meta line — a date, a city, a booth number. Do not choose it if you have none: an empty meta line is what makes it look wrong.
- "MS-3-anniv-photo_4x5" — full-bleed portrait over a navy panel with a cyan years badge. ONLY for a person: a work anniversary, a named individual, a team member being recognised.

Write the on-image text to suit the one you picked:
- headline — short and punchy; TS-p1 sets it in caps, so don't shout in the text itself.
- subhead — the supporting line. On TS-p3 write it as the meta line ("21-24 April - Singapore, Singapore"). On MS-3 it becomes the line under the name.
- eyebrow — the small light-blue label above the headline.

Never invent a specific. If Karen names a show, partner, city, date or number, use it exactly; if she doesn't, write around it rather than filling the gap — a made-up booth number is worse than no booth number.

Use ONLY the specifics Karen provided."""
