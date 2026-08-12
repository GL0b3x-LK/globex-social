"""Employee work-anniversary milestones — 20+ years ONLY."""

MILESTONE_PROMPT = """TASK: Write an employee work-anniversary post for Globex.

ONLY for 20+ year milestones (the context provides the employee name, title, and years of service). This is about tenure and loyalty at an established company — NOT a birthday, NOT a generic employee feature.

template_variant: "milestone"

- Lead with the achievement: N years with Globex. Tie it to Globex's own longevity and the strength of its team.
- Warm and genuine, but still professional and on-brand — this is a public B2B post, not an internal card.
- Use the person's name and role as given. Do not invent personal details.
- 3-6 hashtags including #GlobexInternational. No birthday language, no cake/party emoji.

The on-image subtitle (the `subhead` field) is set out as three parts divided by
pipes, and carries NO sentence punctuation — no full stops, commas or dashes:

    Name | Role | short phrase about what they do

e.g. "Lana Petrenko | Accounting Manager | Keeping the numbers moving". Written
as a running sentence it reads as a caption competing with the caption; the
divided form is the house style for milestone cards."""
