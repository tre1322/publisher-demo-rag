# Ad Integration Guidelines

## Geographic Targeting

### Advertiser Tier Selection

- **Hyperlocal**: Single publisher only (e.g., Joe's Pizza → Duluth News Tribune only)
- **Regional**: Publishers within DMA or multi-county area
- **Statewide**: All publishers in state
- **National**: All publishers in network

### User Location

- Primary: Publisher site as location proxy (user on Duluth News Tribune → assume Duluth area)
- Secondary: IP geolocation for refinement
- Query-based adjustment: "downtown" → strongly prefer hyperlocal; "weekend events" → hyperlocal to regional

## Intent-Based Targeting

### Commercial Intent Classification

**Serve ads only for:**

- **Strong commercial intent**: "deals", "coupons", "specials", "sales", "discounts", "where to buy"
- **Moderate commercial intent**: "restaurants near me", "best prices on", "looking for"

**Never serve ads for:**

- News/information queries
- Explanatory questions
- General topic research

### Query-Category Matching

- "coffee shops" → Food & Dining
- "home improvement deals" → Home & Garden
- "weekend events" → Entertainment
- Match query intent to advertiser categories

## Sponsor Disclosure

**Note: These are suggested approaches, not legal advice. Consult an advertising attorney specializing in FTC compliance and native advertising to determine specific disclosure requirements for your implementation.**

### Suggested Disclosure Elements

**Per-response:**

```text
[Sponsored]
• Advertiser Name has [offer] [dates]. [View details]

These are sponsored offers from our advertising partners.
```

**Individual item tags:**
Each advertiser listing must include [Sponsored] or similar tag

**Persistent UI element:**
Chat interface displays: "Commercial recommendations may include sponsored content"

### Legal Requirements

- FTC compliance: advertising must be clearly disclosed
- Both item-level AND response-level disclosure recommended
- Avoid ambiguous phrasing like "recommended" without "sponsored"

## Ad Hallucination Mitigation

### Critical Strategy: Structured Data + Templates

**Ad data structure:**

```json
{
  "advertiser": "Home Depot",
  "offer": "20% off all paint and supplies",
  "valid_dates": "December 9-15",
  "location": "Rochester store, 2500 Highway 52 N",
  "link": "[tracking URL]",
  "category": "Home Improvement",
  "geography": "Rochester, MN"
}
```

**Template approach:**

```text
{advertiser} has {offer} {valid_dates} at their {location}. [link]
```

LLM assembles exact fields from structured data - NO creative generation of ad content.

### System Prompt Requirements

```text
CRITICAL: When including sponsored offers:
- Use ONLY exact offer text from ad_data
- Use ONLY exact dates from ad_data
- Use ONLY exact location from ad_data
- NEVER infer, estimate, or create details
- If information missing, say "Details at [link]"

WRONG: "Home Depot has great deals on paint"
RIGHT: "Home Depot has 20% off all paint and supplies"
```

### Two-Stage Approach

**Stage 1 (LLM):** Relevance decision

- Should this ad be shown for this query? (yes/no)
- Which ads from inventory are relevant?
- Priority ordering

**Stage 2 (Template rendering):** You construct response

- Pull exact fields from structured data
- Insert into fixed template
- No LLM generation of critical details

### Validation Checks

**Pre-display verification:**

- Numeric values match source data exactly
- Dates match source data exactly
- Advertiser name matches approved list
- Flag discrepancies for review

**Post-launch monitoring:**

- Log every ad response
- Compare to source data programmatically
- Track advertiser complaints
- Monitor for drift over time

### Fallback for Low Inventory

When no relevant ads available:

- "I don't have current advertiser information for [category]"
- OR "Check [Publisher's] business directory for local [category] listings"
- Never broaden inappropriately or force poor matches

## Implementation Priority

1. **MVP**: Structured data + templates with required fields
2. **Safety**: System prompt + post-generation verification
3. **Refinement**: Two-stage approach if needed
4. **Optional**: Advertiser review workflow for high-value campaigns

**Risk tolerance checkpoint**: If occasional misrepresentations are unacceptable, remove LLM from ad content generation entirely - use it only for relevance/selection decisions.

## Display Ad Alternative

### Architecture

- Ad widget separate from chat interface on publisher page
- Analyzes chat queries to select relevant ads from inventory
- Displays standard ad creative (image/text provided by advertiser)
- No LLM generation of ad content

### Contextual Targeting Approach

**Progressive refinement within session:**

- Session start: Show ads based on publisher geography + general categories
- After 1-2 queries: Classify general interest areas
- After 3-5 queries: Build clear intent profile and match categories

**LLM role:**

- Query classification and intent detection
- Category extraction from queries
- Interest profile summarization
- Does NOT generate ad content

### Advantages

- No hallucination risk (advertiser provides exact creative)
- Standard ad tech measurement (impressions, CTR, viewability)
- Clear separation from editorial content
- Simpler legal compliance
- Standard disclosure practices
- Easier advertiser quality control

### Privacy Considerations

**Session-based only (recommended for MVP):**

- Build context within single session
- Reset on browser close/session end
- No persistent tracking
- Simpler privacy compliance

**Cross-session profiles (requires legal review):**

- GDPR/CCPA compliance required
- Cookie consent flows
- Data storage/security infrastructure
- Third-party cookie deprecation issues
- Publisher privacy policy implications

## Format Comparison

| Factor | In-Chat Native Ads | Display Ads |
|--------|-------------------|-------------|
| **Hallucination risk** | High | None |
| **Legal complexity** | High | Medium |
| **Measurement** | Difficult | Standard |
| **User trust** | Potential negative impact | Neutral |
| **Implementation** | Complex | Moderate |
| **Ad quality control** | Difficult | Easy |
| **Engagement** | Potentially higher | Unknown |
| **Relevance timing** | Immediate (if commercial query) | Progressive (builds context) |
| **Low/no inventory** | Graceful: no ad shown or deflect | Always show ad (random/default if needed) |

### Key Unknown

Does conversational context provide better targeting than traditional page context? Requires experimentation.

## Experimental Approach

### Phase 1: Format Comparison

**Test conditions:**

1. In-chat native ads (commercial intent queries only, with disclosure)
2. Display ad widget updated by chat context

Both use chat query history for targeting.

**Measure:**

- CTR for both formats
- Chat engagement (queries per session, session length)
- User feedback/complaints
- Advertiser feedback
- Hallucination incidents (in-chat format)

**Success criteria:**

- CTR meets minimum threshold for economic viability
- Chat usage not harmed by ad presence
- Low/zero hallucination incidents
- Advertiser willingness to pay

**Decision point:**

- If in-chat shows significantly better engagement AND hallucinations are manageable → proceed with in-chat
- If display shows comparable CTR with lower risk → proceed with display
- If neither meets viability threshold → pivot

### Phase 2: Optimization (based on Phase 1 winner)

**For in-chat format:**

- Test disclosure variations
- Test response templates
- Refine commercial intent classification
- Validate hallucination mitigation

**For display format:**

- Test ad placement variations
- Test context window (how many queries to consider)
- A/B test session-only vs persistent profiles
- Optimize refresh timing

### Implementation Priority

1. **MVP**: Build both formats with minimal features
2. **Test**: Run Phase 1 with small publisher cohort
3. **Measure**: 2-4 week test period minimum
4. **Decide**: Choose format based on data, not assumptions
5. **Scale**: Refine chosen approach before broader rollout

### Failure Modes to Monitor

- Banner blindness (display format)
- User distrust (in-chat format)
- Insufficient query volume per session
- Query classification unreliable
- Ad presence reduces chat usage
- CPM insufficient to justify platform costs
- Hallucination incidents (in-chat format)
