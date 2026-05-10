"""Amplora W2 — Product Marketing Context (PMC) module.

Owns the canonical "who is this business" artifact that every downstream
agent (plan generator, content drafter, GBP manager, review responder)
reads from. Built from a pre-interview form (quantitative facts) + an
owner voice interview (qualitative narrative) → single LLM call → markdown.

W2.1 (this version) supports manual transcript paste only. W2.2 will
layer Twilio/LiveKit voice on top without changing the extraction pipeline.
"""
