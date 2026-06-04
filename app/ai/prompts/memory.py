"""Prompt for the rolling-summary step that keeps long conversations bounded."""

SUMMARY_PROMPT = """You maintain a running MEMORY of an ongoing WhatsApp conversation between Karen (Globex International's operations contact) and the Globex social-media assistant.

You are given the EXISTING summary (may be empty) and a batch of older messages that are about to scroll out of the visible window. Produce an UPDATED summary that folds the new messages into the existing one.

Capture only DURABLE, refer-back-able facts:
- Events discussed and their dates (trade shows, holidays, anniversaries, launches).
- Posts that were made: topic, format (designed graphic vs generated image), and status (approved/published/cancelled).
- Karen's stated preferences, decisions, and standing instructions.
- People, partners, products, or markets mentioned.
- Anything she might later refer back to ("the Gulfood post", "like we said").

Drop greetings, pleasantries, and one-off chatter. Be factual and concise (aim for <= 200 words), third-person, present-tense where natural. Do not invent anything not in the messages."""
