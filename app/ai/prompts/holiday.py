"""Holiday posts: date-specific holidays and month-long observances."""

HOLIDAY_PROMPT = """TASK: Write a holiday / observance post for Globex.

The context gives the holiday name, category (general / food_industry / cultural), date, and whether it's a month-long observance.

Pick the template_variant:
- "holiday"            — a date-specific day (e.g. New Year's Day, National Poultry Day, Lunar New Year, Memorial Day).
- "holiday_month_long" — a month-long observance (e.g. National Beef Month, National Seafood Month).

Tone by category:
- food_industry (National Poultry Day, National Seafood Month, etc.): tie naturally to Globex's trade in that protein/category — a confident nod to the part of the food world Globex moves daily.
- cultural (Lunar New Year, Ramadan, etc.): warm, respectful, globally aware. Globex serves 90+ countries — acknowledge that without tokenism.
- general (New Year's, Memorial Day, etc.): brief, professional, human.

Never religious overreach, never preachy. No recipes. 3-6 hashtags including #GlobexInternational."""
