# Amplora — Project Phases

*A sequenced view of how the platform comes online, layered, and scales. The full business plan lives at [amplora_business_plan.md](amplora_business_plan.md); this is the sequencing lens — what comes first, what depends on what, and what the gate is between each step.*

---

## At a glance

| Phase | What it is | Months | Goal |
|---|---|---|---|
| **Phase 0** | Foundation: ads-to-chatbot MVP | 0–2 | 1,000+ chatbot users in pilot territory |
| **Phase 1** | Marketing Agent pilot | 2–4 | 5 paying customers, all retain past day 90 |
| **Phase 2** | SW Peach expansion | 4–8 | 35–50 customers, $105–150k ARR |
| **Phase 3** | Display ad network amplification | 8–12 | $5–15k incremental ad revenue, 1–2 new publishers |
| **Phase 4** | Tier 4 inventory engineering | 12–18 | Auto-vertical platform shipped, 3–5 dealer prospects |
| **Phase 5** | Tier 4 inventory soft launch | 18–24 | 5–10 active Tier 4 customers, ag integration starting |
| **Phase 6** | Multi-vertical, multi-territory scale | 24–36 | $1.35M–$3M ARR across 5–7 territories |

Three things to hold in mind across all phases:

- **The chatbot is the moat.** Every phase either feeds the chatbot (with data) or earns from it (via consumer audiences). Phase 0 is the foundation everything else stands on.
- **Sequencing matters more than speed.** The phases are dependent — you can't run Phase 3 (ad amplification across territories) without first running Phase 0 (a useful chatbot exists at all) and Phase 2 (multiple publishers operational).
- **Each phase has a decision gate.** We commit to the next phase only after the current one hits its goal — and we write down the kill criteria before we start, not after we miss.

---

## Pre-Launch Foundations

**These happen before Phase 0 begins. They are admin prerequisites, not phases of product progress, but Phase 0 cannot start without them.**

- Legal entity formed (separate from any prior entity); founder/partner equity split; governance documents
- Publisher partnership agreement signed with Pipestone County Star
- Homer build of v1 platform shippable — chatbot + ad ingestion pipeline + vision extraction + dashboard
- Sales kit drafted (for Phase 1 use): pricing sheet, demo, contract package, T&Cs, privacy policy, setup-fee credit-back explainer
- Pre-launch legal review complete
- Banking, accounting, insurance (E&O, cyber, general liability) operational

If any of these slip, Phase 0 slips with them.

---

## Phase 0 — Foundation: ads-to-chatbot MVP

> **Goal:** Prove the basic data-and-discovery loop works in the pilot territory. Get consumers using the chatbot before any SMB pays a dollar.

**Months:** 0–2 (6–8 weeks)

### What ships

- **Bulk-ingest the publisher's existing publication content** — display ads, classifieds, business directory listings, editorial mentions, and archived editions
- **Vision pipeline extracts structured business information** from each ad: business name, services and products, hours, location, contact, prices, photos
- **Index everything into the Amplora chatbot** — vector embeddings plus structured metadata so the chatbot can answer questions about local businesses with grounded, citable answers
- **Auto-generate `/pricing.md` and `/llms.txt`** for every business profile so external AI assistants (ChatGPT, Perplexity, Google AI Overviews, Claude) can cite Amplora businesses too
- **Deploy the public-facing chatbot** in the pilot territory (Windom + Pipestone)
- **Launch the consumer-side adoption push** — in-paper promotion (front-page banner, weekly editorial mention), local social campaigns, partnerships with schools and chambers and libraries and civic groups, "coffee and chatbot" demo events, PR push, local radio sponsorships

### Why this comes first

Without consumers using the chatbot, the entire platform's value proposition collapses. The marketing agent doesn't sell because the chatbot listing has no audience. Display ad reach is worthless because there are no chatbot impressions to deliver. Tier 4 inventory has no shoppers searching it.

**Phase 0 is how the chatbot becomes the local discovery layer before there's anything to sell.** The publisher's existing ad inventory is the seed data — it's already there, already paid for, already structured-ish. Vision extraction turns it into knowledge-base content. Consumer marketing turns the knowledge base into a working product. By the time Phase 1 starts, an SMB asked to subscribe is being asked to plug into a chatbot that already has consumers using it daily.

### Success criteria

- 1,000+ active monthly chatbot users in the pilot territory by week 8
- Coverage: every active local business with an existing ad or listing in the publication is indexed
- Query quality: at least 80% of consumer queries return a useful local result
- Zero hallucinated business information — all answers grounded in real ad or edition source data

### Owners

Amplify Media Group platform team (technical execution) plus pilot publisher's editorial and sales teams (consumer adoption push). Trevor co-runs the consumer launch.

---

## Phase 1 — Marketing Agent pilot

> **Goal:** Sign and successfully run the first 5 SMB customers on Tier 2/3 subscriptions. Validate the model at small scale.

**Months:** 2–4 (8 weeks of onboarding plus 12 weeks of operating to the day-120 decision gate)

### What ships

- Recruit 5 pilot customers (3 Windom, 2 Pipestone) from the easy-verticals list — retail, restaurants, salons, auto repair, home services
- AI voice-interview onboarding (~30–45 min per customer)
- Custom marketing plan generation, owner approval in dashboard
- Daily content drumbeat: posts drafted, GBP updates, review responses, owner thumbs-up/down
- Weekly cycle reviews with each customer
- Internal weekly retro across the cohort
- Document case studies and testimonials
- Quality monitoring AI watches every customer's outputs; human concierge reviews flagged work

### Why this comes after Phase 0

You can't sell a chatbot listing to an SMB if the chatbot has no audience. Phase 0 builds the audience; Phase 1 starts monetizing through SMB subscriptions. With Phase 0 hitting 1,000+ MAU, the value proposition to the SMB is real, not theoretical.

### Decision gates

- **Day 30:** Build is functioning, customers onboarded, no critical fires
- **Day 60:** Customers are seeing value; agent quality passing review
- **Day 90:** Case studies forming; renewal intent clear; channel decisions validated
- **Day 120:** Go/no-go on broader territory expansion. **This decision belongs to the CEO.**

### Success criteria

- 5/5 pilot customers operating successfully through day 90
- All 5 pass the day-90 retention window (the contract opt-out)
- Documented case studies for each
- Quantitative success criteria written down per customer at signing — and quantitative kill criteria written down before launch

### Owners

Trevor and publisher partner co-selling. Customer success contractor monitors AI quality.

---

## Phase 2 — Southwest Peach expansion

> **Goal:** Scale marketing agent sales from 5 pilot customers to 35–50 active customers across the full Southwest Peach territory. Validate that the publisher's sales team can run the motion solo.

**Months:** 4–8

### What ships

- Open marketing agent sales to all SW Peach businesses
- Target: 25–40 active customers by month 8
- Hire or contract first dedicated customer success person around month 6
- Refine pricing based on pilot data (validate or adjust the $99 / $299 / $499 tiering)
- **Begin Phase 3 ad-reach engineering in parallel** so Phase 3 ships on schedule

### Why this comes now

Phase 1 proved the model with 5 customers. Phase 2 validates that it scales — that the publisher's sales team can run the motion solo, that the platform supports more customers without breaking, that customer success is sustainable, and that the unit economics in the business plan ($90–110/month contribution margin per Tier 2 customer) hold up in real operations.

### Success criteria

- 25–40 active customers by month 8
- 80%+ retention on the original pilot 5 (renewing or near-renewing)
- Unit economics confirmed against the modeled numbers
- Publisher sales team running the motion solo by mid-phase, with reduced co-selling support

### Owners

Pilot publisher's sales team running solo by mid-phase. Customer success lead onboarded around month 6. Amplify Media Group engineering working on Phase 3 buildout in parallel.

---

## Phase 3 — Display ad network amplification

> **Goal:** Launch tiered ad reach across multiple licensed territories. First incremental ad-revenue line on top of marketing agent subscriptions.

**Months:** 8–12

### What ships

- Phase 3 ad amplification goes live across SW Peach territory
- Sales reps trained on the reach upsell motion
- Local-First Algorithm enforced in chatbot ranking — local results always surface first; out-of-territory paid-reach results appear only when the advertiser paid for that location AND local supply doesn't fully answer the query
- Cross-territory revenue tracking and attribution dashboard live
- Reach Calculator deployed in sales kit (project impressions and pricing across territories)
- Sign 1–2 additional publisher partners — so cross-territory reach has somewhere to reach
- Year-end target: 35–50 marketing agent customers in SW Peach, 75–100 across all territories

### Why this comes now

Display ad reach only works if there ARE multiple licensed territories with chatbot consumer audiences. Phase 2 validated SW Peach; Phase 3 lights up multiple territories simultaneously. The Local-First Algorithm is the protection mechanism that makes multi-territory ad reach honest — without it, the network would cannibalize local advertisers and the publisher's trust evaporates.

### Success criteria

- $5–15k incremental ad revenue in months 9–12 (Year 1 partial-year contribution)
- 15–25% reach-upsell rate on eligible display-ad transactions
- 1–2 additional publisher partners signed and operational
- Zero cross-territory revenue disputes — allocation rules clear and written before launch
- Local-First Algorithm verified in production: local results always surface above paid-reach results in test queries

### Owners

Pilot publisher leads sales-motion training across territories. New partner publishers onboarded with Amplify Media Group co-selling support.

---

## Phase 4 — Tier 4 inventory engineering

> **Goal:** Build the multi-vertical inventory infrastructure. First vertical: auto. Set up the customer pipeline for Phase 5.

**Months:** 12–18

### What ships

- Inventory feed integration framework (one supported management system per vertical, expandable)
- First vertical integration: auto inventory management systems — DealerCenter, vAuto, AutoBase, AutoUplink
- Faceted search UX in the chatbot — consumers query like a search engine: *"any 4-bedroom houses near Marshall under $300k with a basement on at least an acre?"*
- Multi-location handling for dealers with multiple stores
- Real-time sync, photo handling at scale, status tracking (available/sold/pending)
- Identify and approach first 3–5 Tier 4 dealer prospects for soft-launch onboarding
- Hire regional sales lead for Tier 4

### Why this comes now

This is the heaviest engineering investment in the entire roadmap — estimated $150–300k incremental engineering and operating cost. It cannot start until the platform has proven enterprise-grade reliability through Phases 1 through 3, and until there's a concrete customer pipeline ready to sign on. Building inventory infrastructure speculatively, before the platform is otherwise revenue-positive, is exactly how this kind of project burns through capital and stalls.

### Success criteria

- Auto-vertical integration shipped and working with at least 2 management systems
- 3–5 Tier 4 dealer prospects identified and in active discussion
- Regional sales lead hired and onboarded
- Faceted search UX tested with consumer queries; query-to-listing-match quality > 85%

### Owners

Amplify Media Group engineering (heaviest engineering load of any phase). Solution sales lead recruited for Phase 5.

---

## Phase 5 — Tier 4 inventory soft launch

> **Goal:** Onboard the first 5–10 Tier 4 inventory dealers in the auto vertical. Begin a second vertical (ag equipment).

**Months:** 18–24

### What ships

- Onboard first 5–10 auto dealers in SW Peach territory
- $799–$1,999+/month subscriptions, annual contracts, $999–2,499 setup fees credited back at day 90
- Begin ag equipment integration (TractorHouse plus major ag dealer management systems)
- Real estate / MLS scoping starts toward the end of the phase
- 100–150 marketing agent customers across 2–3 territories (carryover from Phase 2/3 trajectory)

### Why this comes now

Phase 4 built the platform. Phase 5 monetizes it. Soft launch means we are NOT committing to a multi-vertical Phase 5 in a single quarter — we sequence the verticals: auto first, then ag, then real estate. This protects against the "Tier 4 integration cost overrun" risk where chasing too many verticals at once burns engineering capacity faster than the revenue can absorb.

### Success criteria

- 5–10 active Tier 4 customers (auto vertical) by end of Phase 5
- Tier 4 contribution margin confirmed against the modeled $250–300/month per customer
- Ag equipment integration in progress and approaching launch
- $50–200k Year 2 ARR contribution from Tier 4 alone
- Total Year 2 ARR (across all three product lines): $380–730k

### Owners

Regional Tier 4 sales lead. Engineering supports new vertical integrations. Customer success scaled to handle higher-touch enterprise relationships.

---

## Phase 6 — Multi-vertical, multi-territory scale

> **Goal:** Operate the full platform — marketing agent + ad reach + Tier 4 inventory — across 5–7 licensed territories. Hit $1.35M–$3M total ARR across all three product lines.

**Months:** 24–36 (Year 3)

### What ships

- Tier 4 across auto, ag, and real estate (with RV/boat/powersports and lumber yards in scoping)
- Marketing agent: 300–500 active customers across the network
- Phase 3 ad reach fully operational in 5–7 territories
- 30–80 Tier 4 customers
- Build out V2 marketing agent features:
  - Autonomous ad spend management (agent OAuth into Meta Ads Manager and Google Ads, executes within approved budgets)
  - Email and SMS marketing
  - Photography and video referral network for businesses without usable assets
  - Multi-tenant learning across customers (anonymized insights propagate by vertical and territory)
- Hire dedicated sales, customer success, and engineering capacity per territory

### Why this comes now

Year 3 is the scale year. Everything before this is "prove and refine"; Year 3 is "deploy and grow." The V2 marketing agent features (autonomous ad spend, email/SMS, photo network) require a stable foundation — they layer onto a working product, not in lieu of one. The biggest risk in Phase 6 is moving too fast and breaking the customer success motion that's been carefully built; the discipline that worked in Phases 1–5 has to scale, not be abandoned.

### Success criteria

- $1.35M–$3M total ARR across all three product lines
- 300–500 marketing agent customers, with retention at or above 80%
- 30–80 Tier 4 customers, with the ~36-month average tenure validated
- 5–7 active publisher territories, all running solo without continuous Amplify Media Group co-selling
- V2 marketing agent features shipped and adopted by at least 25% of eligible customers

### Owners

Distributed across publisher partners (running their own territories) plus Amplify Media Group engineering and operations (building V2 features and supporting the network).

---

## How to read this document

If you're reading this top-to-bottom: **the through-line is the chatbot.** Phase 0 builds it. Phase 1 sells the first paying access to it. Phase 2 scales that access. Phase 3 monetizes the audience around it. Phase 4 builds the search engine inside it. Phase 5 monetizes the search engine. Phase 6 deploys the whole stack across the network.

If you're reading this to plan: **focus on the gates between phases.** A phase doesn't end because the calendar says so — it ends when the success criteria land. A phase doesn't start because the calendar says so — it starts when the prior phase has cleared its gate and the prerequisite work is done. The dates are guides; the gates are how we actually decide.

If you're reading this to evaluate risk: **most of the risk is concentrated in Phases 0, 1, and 4.** Phase 0 because consumer cold-start is a real risk (no chatbot users = no value chain). Phase 1 because customer cold-start is real (5 pilot customers either prove the model or tell us where to pivot). Phase 4 because the engineering investment is heaviest and the customer pipeline isn't yet earning it back. Phases 2, 3, 5, and 6 are mostly execution risk — important, but lower variance than the foundation phases.

---

*Companion materials: [amplora_business_plan.md](amplora_business_plan.md) · [amplora_partner_brief.md](amplora_partner_brief.md) · [amplora_one_pager.md](amplora_one_pager.md) · [amplora_pitch_script.md](amplora_pitch_script.md) · [amplora_pitch_deck_outline.md](amplora_pitch_deck_outline.md).*
