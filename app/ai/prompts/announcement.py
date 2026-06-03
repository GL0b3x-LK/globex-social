"""General company news: new hires, partnerships, expansions."""

ANNOUNCEMENT_PROMPT = """TASK: Write a company announcement for Globex.

The context describes what's being announced (new partnership, market expansion, new hire in a senior role, capability, etc.). Use ONLY what's provided.

template_variant: "announcement"

- Clear and newsworthy without being a press release. State what's new and why it matters to partners/suppliers.
- Professional, confident, forward-looking. No hype stacking.
- This is company news ONLY — never news-based/current-events commentary about the wider world.
- 3-6 hashtags including #GlobexInternational."""
