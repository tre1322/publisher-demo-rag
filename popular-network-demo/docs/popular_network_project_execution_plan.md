# Popular Network — Project Execution Plan

**View:** CEO breakdown
**Scope:** Marketing Agent v1 launch through Southwest Peach pilot
**Date:** May 2026

---

## What I'm Holding as CEO

Three things stay on my desk and don't get delegated:

- **The publisher relationships.** The licensing model is the moat. John Draper is the first; the next 2–3 publishers we sign in year one are strategic-level decisions, not sales-channel decisions.
- **The capital plan and pace.** How much we invest, how fast, and what the runway looks like through pilot and beyond.
- **The pilot's go/no-go decision at month 4.** Five customers either prove the model works or tell us where to pivot. That call is mine.

Everything else gets a workstream owner.

---

## Critical Path to Launch

This is the dependency chain. Anything off this path waits.

1. **Legal entity formed** (without it, no contracts) → blocks publisher agreement, customer contracts, banking
2. **Publisher agreement with Pipestone signed** → blocks pilot customer recruitment in Pipestone
3. **Homer build of v1 platform shippable** → blocks onboarding any pilot customer
4. **Sales kit complete** → blocks pilot customer recruitment
5. **Five pilot customers signed** → starts the clock on the 90-day pilot
6. **Grand Network chatbot launched with consumer marketing** → must run in parallel with pilot, not after
7. **First case study at month 4** → blocks territory expansion sales

If any one of these slips, the whole timeline slips.

---

## Workstream 1: Technology & Engineering

**Owner:** Nate (technical lead), Homer (build executor)

### Platform Infrastructure
- Multi-tenant database (per existing project specification)
- API layer
- DigitalOcean Gradient AI Platform with Llama 3.3 70B
- Authentication and identity
- Stripe billing integration
- Backend language decision (still open from prior spec)

### AI Agent System — Sub-Agent Architecture
- Sub-agent framework (each agent: own system prompt, tools, model, context)
- Named sub-agents for v1:
  - Voice interview agent
  - Product Marketing Context generator
  - Hook writer
  - Content drafter (per platform: Facebook, Instagram, Google Business Profile)
  - SEO/AI-SEO content block generator
  - Reviews response drafter
  - Source verifier (hallucination guard)
  - Knowledge gap detector (pulls from Grand chatbot queries)
  - Content audit agent (quarterly)
  - Quality monitoring meta-agent
  - Ticket triage (for owner escalations)
- Approval workflow service
- Per-business knowledge file storage and retrieval

### Integrations
- Meta Business Manager (read-only v1, full OAuth v2 for autonomous ad spend)
- Google Business Profile API
- Google Ads (read-only v1, full OAuth v2)
- Stripe billing
- Voice transcription pipeline
- Email/SMS (v2)

### Grand Network Chatbot
- Knowledge base architecture (per-business records)
- General discovery mode
- Business-focused conversation mode (Tier 3 handoff)
- Owner notification path (SMS/push when chatbot escalates)
- Query-back loop into marketing agent (intent feedback)

### Machine-Readable Presence (Network Moat)
- Auto-generated `/pricing.md` and `/llms.txt` for every business profile
- Structured content blocks (definition, step-by-step, comparison, FAQ, statistics)
- Schema markup on all business pages

### Security and Data
- PII handling and storage policy
- Voice interview data retention and encryption
- Chatbot conversation logs
- Encryption at rest and in transit
- Backup and disaster recovery

### Open Decisions Before Build
- Backend language
- Database technology
- Hosting topology beyond DigitalOcean
- Sub-agent orchestration framework

---

## Workstream 2: Product and UX Design

**Owner:** Needs assignment. Trevor leads until designer/contractor brought in.

### Surfaces to Design

**Business owner dashboard**
- Approval queue (thumbs up/down with edit option)
- Plan and content calendar view
- Performance reporting (weekly summary, monthly deep-dive)
- Settings, integrations, team
- "Talk to a human" escalation button
- Billing and subscription management

**Voice interview experience**
- Conversational flow with adaptive follow-up
- Real-time transcription with owner confirmation
- Generated Product Marketing Context file presented for owner review
- Re-interview / supplement option

**Onboarding flow**
- Sign-up → contract → voice interview → plan generation → owner approval → integrations → live
- Concierge setup add-on alternate path
- Setup-failure detection at day 14 with auto-route to human

**Consumer-facing Grand chatbot**
- Web interface for the network
- Mobile-responsive
- Discovery to focused-business-conversation transition
- AI disclosure handled gracefully

**Business public profile / website**
- Default template for businesses without an existing site
- Integration mode for businesses with existing sites
- AI-citation-ready content blocks built in

**Sales demo**
- Live demo flow that publisher reps and sales staff can run
- Recorded demo for self-service prospects

### Design Principles
- Minimal friction for non-technical owners
- Clear, never-buried approval points
- Mobile-first for owners
- Graceful AI disclosure
- Brand consistency across surfaces

---

## Workstream 3: Business and Finance

**Owner:** Trevor and strategic partner

### Entity and Capital Structure
- Legal entity formation (separate from Quadd.ai — already flagged)
- Founder and partner equity split
- Capitalization plan: bootstrapped vs. raised
- Capital required for v1 build (target: range from current cost modeling)
- Initial runway target: minimum 18 months

### Financial Operations
- Banking and operating accounts
- Accounting system selection (QuickBooks or equivalent)
- Revenue recognition policy (especially around setup fees credited back at day 90)
- Publisher payout infrastructure (monthly, automated)
- Tax structure and entity registration
- Insurance: E&O, cyber, general liability

### Financial Planning
- Detailed v1 build cost modeling (validate the illustrative numbers in the business plan)
- Unit economics validation
- Pricing finalization
- 24-month financial model
- Break-even sensitivity analysis

### Decisions Tomorrow
- Bootstrap or raise
- Founder/partner equity split
- Initial budget envelope for v1 build

---

## Workstream 4: Legal and Compliance

**Owner:** Trevor with outside counsel

### Documents to Draft
- Entity formation and governance documents
- Founder agreement
- Publisher licensing agreement template
- Customer T&Cs and contract template
- Setup fee credit-back terms (operationally important)
- Privacy policy
- Data Processing Agreement template (publishers as processors)
- AI content disclosure and chatbot disclosure language
- Independent contractor agreements (for first hires)

### Compliance Setup
- Industry guardrails enforcement (excluded verticals list, sell-with-care list)
- AI content disclosure standards
- AI chatbot disclosure to consumers
- FTC endorsement compliance (no fabricated testimonials)
- State-specific advertising rules where applicable
- Trademark search and brand protection

### Insurance and Risk Transfer
- Errors and omissions
- Cyber liability
- General liability
- Director and officer (when entity formed)

---

## Workstream 5: Go-to-Market — Sales

**Owner:** Trevor and publisher partners

### Sales Kit (Required Before Pilot Recruitment)
- Pricing sheet
- Live demo flow + recorded demo backup
- Pitch script
- Objection handling guide
- Sample voice interview snippet
- Sample monthly performance report
- Contract package with T&Cs
- Setup fee credit-back explainer

### Sales Channel Strategy
- Pilot phase: Trevor and publishers co-selling
- Post-pilot: publisher sales staff with co-selling on first 5 customers per territory
- Publisher enablement program: training, materials, ongoing support, monthly check-ins

### CRM and Pipeline
- CRM selection (HubSpot Free or similar to start)
- Deal stages defined
- Pipeline reporting cadence (weekly during pilot, then monthly)

### Sales Metrics
- Demos booked
- Demos to close conversion rate
- Average deal size by tier
- Time to close
- Publisher-by-publisher performance

---

## Workstream 6: Go-to-Market — Our Own Marketing

**Owner:** Trevor (in absence of marketing hire)

### The Critical Dependency: Consumer-Side Chatbot Adoption

The marketing agent's value proposition collapses if the Grand chatbot has no consumers using it. This work runs in parallel with the pilot, not after.

**Target:** 1,000+ monthly active users on the chatbot in pilot territory within 90 days of launch.

### Tactics
- In-paper promotion with publisher partners (front-page banner, weekly editorial mention)
- Local social media campaigns (Facebook, Instagram, community groups)
- Partnerships with local institutions: schools, chambers, libraries, churches, community centers
- "Coffee and chatbot" demo events for both consumers and businesses
- PR push at chatbot launch
- Local radio / podcast sponsorships if available

### Brand and Positioning
- Naming clarity: Popular Network = the platform; marketing service may operate under publisher brand or have its own
- Visual identity (logo, color, typography)
- Messaging hierarchy: who we are, what we do, why we matter
- Voice and tone guide for the agent itself

---

## Workstream 7: Operations and Customer Success

**Owner:** Trevor initially; first dedicated hire at month 6 (target ~25 customers)

### Operating Model
- AI-default with three escalation triggers (already designed in business plan)
- Quality monitoring meta-agent reviews every customer's outputs
- Owner-initiated "talk to human" available within one business day
- Concierge setup contractor available on demand
- Setup-failure auto-route at day 14

### Customer Success Cadence
- Onboarding playbook (week 1, week 2, week 4)
- 30/60/90 day proactive check-ins during pilot
- Quarterly business reviews for Tier 3
- Renewal conversation 90 days before contract end
- Expansion conversations (Tier 1 → 2 → 3 upgrade triggers)
- Churn save plays for at-risk accounts

### Knowledge Operations
- Industry guardrails library (build and maintain)
- Content templates per vertical
- Compliance filter rules
- Approved and excluded vertical lists with periodic review
- Customer language repository (verbatim from reviews, support, social)

### Support and Escalation
- Crisis communications protocol (local tragedy, business crisis, ad account flag)
- Negative review response workflow
- Ad account compliance monitoring
- Failed payment / collections handling

---

## Workstream 8: Pilot Execution

**Owner:** Trevor with publisher partners

The pilot is the proof point for everything else. It's not "operations" — it's its own concentrated workstream for the first four months.

### Recruitment
- 5 businesses total: 3 Windom + 2 Pipestone
- All from the easy-verticals list (retail, restaurants, salons, auto repair, home services)
- Mix of tiers if possible (e.g., 1 Tier 1, 3 Tier 2, 1 Tier 3)
- Selection criteria: owner who will engage, willingness to provide feedback, business with at least basic existing assets (photos, hours, Facebook page)

### Pilot Operating Model
- Weekly cycle reviews with each pilot customer
- Internal weekly retro across the pilot cohort
- Daily quality monitoring
- Direct line from Trevor to each pilot owner

### Success Criteria (Define With Each Customer at Signing)
- Customer-defined business outcome (e.g., "10 more new customers per month")
- Engagement metrics on social and chatbot
- Owner satisfaction (qualitative + NPS)
- Time savings (owner reports hours per week saved)
- Renewal intent at day 60

### Decision Gates
- Day 30: build is functioning, customers onboarded, no critical fires
- Day 60: customers seeing value, agent quality passing review
- Day 90: case studies forming, renewal intent clear, channel decisions validated
- Day 120: go/no-go on broader territory expansion

### Learning Capture
- Weekly retro notes
- What's working / what's broken document
- Voice interview transcript review for common patterns
- Industry-specific tweaks needed
- v2 prioritization input

---

## Workstream 9: Data, Analytics, and Reporting

**Owner:** Nate (initially); customer-success lead after hire

### Owner-Facing Reporting (the dashboard)
- Per-business performance metrics (engagement, reach, conversions where measurable)
- Plan vs. actual content cadence
- Reviews monitoring summary
- Chatbot interactions (Tier 3)
- Recommended actions for the owner

### Internal Operational Dashboards
- Customer health scores
- Agent quality scorecard
- Escalation rate by trigger type
- Onboarding cycle time
- Per-publisher performance

### Business KPIs
- ARR, MRR
- CAC by channel and publisher
- LTV
- Gross retention, net retention
- NPS
- Setup fee credit-back rate (early churn signal)

### Attribution and ROI
- Where leads come from for each business customer
- ROI estimation for paid social spend
- Organic vs. paid contribution

---

## Workstream 10: Strategic Partnerships

**Owner:** Trevor

### Tier 1 — Critical
- **Publisher partners.** Pipestone County Star (signed). Next 2–3 publishers in year one. Identify and approach by month 6.
- **DigitalOcean.** Already in use. Negotiate volume pricing as we scale.
- **Meta and Google.** Get into Business Partner programs. Establish technical contacts for ad account issues.

### Tier 2 — Important
- **Stripe.** Standard payments partnership.
- **Local chambers of commerce** in pilot territory. Free distribution channel for awareness and credibility.

### Tier 3 — Helpful
- **Photography and video referral network.** For businesses without usable assets. Could be local photographers in each territory we license.
- **Trade associations** for specific verticals (auto repair, restaurants, etc.) — content and trust.
- **Local educational institutions.** Possible interns, trust-building.

---

## Workstream 11: People and Org

**Owner:** Trevor

### V1 Team
- Trevor: CEO, business lead, publisher relations, pilot owner
- Nate: CTO, technical lead
- Homer: AI build executor (always-on)
- John Draper: pilot publisher partner / advisor
- Strategic partner (current open question): potential co-founder / COO
- Contractor: human concierge / quality reviewer (target month 1–2)

### Hiring Plan
- Month 6: customer success lead (full-time or fractional) at ~25 active customers
- Month 9: business development / publisher partnerships lead
- Month 12: second engineer
- Designer: contracted as needed; possibly full-time at month 12

### Advisors Needed
- Outside legal counsel
- CPA / fractional CFO
- Marketing strategist with agency-side experience (the bridge between us and the publisher partners' world)
- Technical advisor on AI safety and quality

---

## Workstream 12: Risk Management

**Owner:** Trevor (CEO accountability); per-risk owners assigned below

| Risk | Severity | Owner | Review |
|------|----------|-------|--------|
| Grand chatbot consumer cold start | High | Trevor / GTM | Weekly during pilot |
| Publisher sales execution | High | Trevor + publisher | Weekly during pilot |
| AI quality at scale | Medium | Nate / Operations | Weekly |
| Regulatory shifts on AI | Medium | Legal | Quarterly |
| Ad platform compliance | Medium | Operations | Monthly |
| Unit economics worse than modeled | Medium | Finance | Monthly |
| Key person risk (Trevor, Nate) | Medium | Trevor | Quarterly |

Risk register reviewed monthly through pilot, quarterly after.

---

## Operating Cadence

- **Daily:** Trevor ↔ Nate sync on build status (15 min)
- **Weekly:** Strategic partner review (45 min); pilot customer cycle reviews (during pilot)
- **Weekly:** Publisher partner call (during pilot phase)
- **Monthly:** All-hands operating review; risk register review
- **Quarterly:** Strategic review with advisors; financial close; OKR scoring

---

## What Goes to the Partner Meeting Tomorrow

Three things to walk in with a position on:

1. **Capital and equity:** how much do we put in, how do we split it, do we raise outside money?
2. **Workstream ownership:** which of the 12 workstreams does each of us own primarily?
3. **The 90-day pilot decision gate:** what does success look like quantitatively, and what do we do if we miss it?

Everything else can be sequenced, but those three are blocking.
