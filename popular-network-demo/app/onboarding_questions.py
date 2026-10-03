"""The onboarding wizard's questions (Phase 2). Trevor owns this file.

Every new owner answers these once, and Claude turns the answers (plus
their website) into the voice brief the agent writes from. The questions
decide the quality of every brief, so edit them freely: the wizard page and
the drafting prompt both read this list, and nothing else needs to change.

Rules the list follows (from the Quadd interview and the 2026-05 PMC work):
  - ASK what owners know reliably: what they sell, who buys, what to push,
    what never to say, their busy seasons, their proof.
  - OBSERVE how they sound. Owners describe themselves as warmer than they
    write, so the voice comes from the writing sample and the website, never
    from a "describe your tone" question.
  - The three piles (grow / keep steady / play down) are the owner's own
    call. The brief uses their sorting as given.

Fields:
  id        stable key stored with the answers (don't rename once live)
  step      which wizard screen shows it (1-3)
  kind      "text" one line · "long" a paragraph · "channels" checkboxes
  required  the wizard won't draft until it's answered
  feeds     which brief fields it mostly shapes (shown to Claude)
"""
from __future__ import annotations

from typing import Any

# Post platforms the wizard offers. Keys match the dashboard's post
# platforms (Post.platform); drafts are only written for these.
CHANNEL_CHOICES: list[dict[str, str]] = [
    {"key": "fb", "label": "Facebook"},
    {"key": "ig", "label": "Instagram"},
    {"key": "gbp", "label": "Google Business Profile"},
    {"key": "web", "label": "Website or blog"},
]

STEPS: list[dict[str, Any]] = [
    {"step": 1, "title": "Your business", "blurb": "What you sell and who buys it."},
    {"step": 2, "title": "What to push", "blurb": "Where you want the agent to spend its words."},
    {"step": 3, "title": "How you talk", "blurb": "So posts sound like you, not like an ad agency."},
]

QUESTIONS: list[dict[str, Any]] = [
    {
        "id": "what_you_do",
        "step": 1,
        "kind": "long",
        "required": True,
        "label": "In a sentence or two, what does your business do?",
        "help": "Say it the way you'd tell a neighbor.",
        "placeholder": "We fix cars and light trucks in Windom, mostly brakes, tires and anything with a check-engine light.",
        "feeds": "value_prop",
    },
    {
        "id": "favorite_customer",
        "step": 1,
        "kind": "long",
        "required": True,
        "label": "Describe your favorite kind of customer.",
        "help": "Who they are, what they need, and why you like working with them.",
        "placeholder": "Farm families and commuters who want it fixed right the first time and don't want to be upsold.",
        "feeds": "audience",
    },
    {
        "id": "trigger",
        "step": 1,
        "kind": "long",
        "required": False,
        "label": "What usually happens that makes someone finally call you or walk in?",
        "help": "The moment they decide they need you. Use the words they use.",
        "placeholder": "Something starts grinding, or the dealer quoted them twice what it should cost.",
        "feeds": "customer_language, audience",
    },
    {
        "id": "why_you",
        "step": 1,
        "kind": "long",
        "required": False,
        "label": "Why do customers pick you over the other options? Name a competitor if you like.",
        "help": "",
        "placeholder": "We explain what's wrong before we touch it, and the dealer in Worthington is an hour away.",
        "feeds": "value_prop, proof_points",
    },
    {
        "id": "proof",
        "step": 1,
        "kind": "long",
        "required": False,
        "label": "What proof do you have that you're good at it?",
        "help": "Years in business, reviews, awards, certifications, numbers.",
        "placeholder": "Third generation, 31 years on Main Street, 4.9 stars on Google from about 200 reviews.",
        "feeds": "proof_points",
    },
    {
        "id": "grow",
        "step": 2,
        "kind": "long",
        "required": True,
        "label": "Which products or services do you want more of?",
        "help": "The work you'd happily do all week. One per line.",
        "placeholder": "Brake jobs\nTire packages\nFleet maintenance contracts",
        "feeds": "amplify",
    },
    {
        "id": "steady",
        "step": 2,
        "kind": "long",
        "required": False,
        "label": "Which ones are fine as they are?",
        "help": "Keep mentioning them now and then, no push. One per line.",
        "placeholder": "Oil changes\nAlignments",
        "feeds": "maintain",
    },
    {
        "id": "play_down",
        "step": 2,
        "kind": "long",
        "required": False,
        "label": "Anything you'd rather not advertise, or would refer out?",
        "help": "Low-margin work, jobs you're too busy for, things you're phasing out.",
        "placeholder": "Transmission rebuilds (we send those to Fairmont)",
        "feeds": "mute",
    },
    {
        "id": "offers",
        "step": 2,
        "kind": "long",
        "required": False,
        "label": "Any offers you're comfortable promoting?",
        "help": "Free estimates, discounts, trials, guarantees. Leave it blank if you don't run offers.",
        "placeholder": "Free brake inspection. 10% off for seniors on Tuesdays.",
        "feeds": "amplify, constraints",
    },
    {
        "id": "never_say",
        "step": 2,
        "kind": "long",
        "required": False,
        "label": "What should your marketing never say or do?",
        "help": "Words, topics, or styles that would make you cringe.",
        "placeholder": "No 'family-owned' cliches. Never knock other shops by name. No emojis.",
        "feeds": "mute, constraints",
    },
    {
        "id": "seasons",
        "step": 2,
        "kind": "long",
        "required": False,
        "label": "When are you busy, and when is it slow?",
        "help": "",
        "placeholder": "Swamped before winter and in spring pothole season. Slow in late summer.",
        "feeds": "seasonal_patterns",
    },
    {
        "id": "channels",
        "step": 3,
        "kind": "channels",
        "required": True,
        "label": "Where do you want to post?",
        "help": "Your first drafts are written for these.",
        "feeds": "plan channels",
    },
    {
        "id": "writing_sample",
        "step": 3,
        "kind": "long",
        "required": True,
        "label": "A customer asks, \"Why should I come to you?\" Write your answer the way you'd say it.",
        "help": "Don't polish it. A few sentences in your own words is how the agent learns your voice.",
        "placeholder": "Honestly? Because I'll tell you what's actually wrong...",
        "feeds": "voice",
    },
    {
        "id": "anything_else",
        "step": 3,
        "kind": "long",
        "required": False,
        "label": "Anything else the agent should know?",
        "help": "Local events you sponsor, your team, your story.",
        "placeholder": "",
        "feeds": "notes",
    },
]

QUESTION_IDS = frozenset(q["id"] for q in QUESTIONS)
REQUIRED_IDS = tuple(q["id"] for q in QUESTIONS if q["required"])
CHANNEL_KEYS = tuple(c["key"] for c in CHANNEL_CHOICES)
MAX_ANSWER_CHARS = 2000
