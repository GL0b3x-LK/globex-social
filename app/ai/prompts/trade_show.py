"""Trade show posts: pre-event, during-event (real-time, often with a photo), post-event."""

TRADE_SHOW_PROMPT = """TASK: Write a trade-show post for Globex.

The context gives the show name, dates, location, booth number (may be missing), and a variant hint (pre / during / post). Use ONLY the details provided.

Pick the template_variant:
- "trade_show_pre"    — before the show. Build anticipation; invite partners to meet Globex there. Mention dates/location/booth only if given.
- "trade_show_during" — live at the show (a photo is usually attached). Energetic but professional; reference being on the floor now.
- "trade_show_post"   — after the show. Thank attendees/partners; reinforce the relationships and scale.

If a booth number is not in the context, do NOT invent one — invite people to "find us on the floor" instead.
Keep it tight and partner-focused (B2B). 3-6 hashtags including the show's own tag if natural (e.g. #Gulfood2027) and #GlobexInternational."""
