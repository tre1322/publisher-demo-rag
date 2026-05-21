"""Seed the SQLite DB with Westbrook Auto & Tire — the demo business.

Every value here was translated verbatim from the hardcoded mock data block
in dashboard.html lines 186–455. If you change one, change both — the smoke
test compares /api/bootstrap against the structure dashboard.html expects.
"""
from __future__ import annotations

from .db import SessionLocal
from .models import (
    Business,
    ChatTurn,
    Connection,
    DashboardNotices,
    MarketingPlan,
    PerformanceSummary,
    Post,
    Approval,
    Review,
    ReviewAggregate,
    SettingsRow,
)


def seed_if_empty() -> bool:
    """Insert Westbrook if no business exists yet. Returns True if inserted."""
    db = SessionLocal()
    try:
        if db.query(Business).first() is not None:
            return False

        biz = Business(
            id=1,
            slug="westbrook",
            name="Westbrook Auto & Tire",
            owner="Dale Henderson",
            owner_initials="DH",
            location="Westbrook, MN",
            publisher="Pipestone County Star",
            phone="(507) 555-0143",
            tier=2,
            tier_label="Tier 2 — Marketing Agent",
            monthly_price=299,
            joined_days_ago=47,
            joined_date="March 19, 2026",
            voice_interview="complete",
            tech_name="Mike",
            years_in_town=22,
            ase_certified=True,
        )
        db.add(biz)
        db.flush()

        # Posts — verbatim from dashboard.html:257-309
        post_seed = [
            ("p1",  "2026-05-01", "gbp", "published",
             "Memorial Day weekend hours",
             "Heads up: closed Monday May 25 for Memorial Day. Open regular hours Tue–Sat. Heading to the lake? Stop in Friday or Saturday for a $59 pre-trip safety check.",
             "GBP holiday-hours posts get 4× the tap-to-call rate of feed posts in your data. Pairing the closure with the pre-trip service turns the announcement into something actionable."),
            ("p2",  "2026-05-02", "fb",  "published",
             "Free Saturday brake check",
             "Brake pedal feel a little soft? Pull in any Saturday morning — we'll take a look at your brakes for free, no appointment. Coffee's on, kids get a sticker.",
             "Saturday-morning posts (8–10am) outperform other windows by 41% for your audience. Free-inspection framing lowers commitment for first-time customers."),
            ("p3",  "2026-05-04", "fb",  "published",
             "Winter→summer tire swap",
             "Snow tires off, summer rubber on. Book your seasonal swap this week — we've got Saturday slots open. Bring 'em in or have them stored with us for next winter, $40 off either way.",
             "Seasonal urgency post timed to the swap window. Storage upsell pulled from your March voice memo — you mentioned 18 storage slots still empty."),
            ("p4",  "2026-05-05", "ig",  "pending",
             "BTS: Mike rebuilds a 2014 Silverado front end",
             "Two days, fresh ball joints, control arms, tie rods, and an alignment. This Silverado is going back on the road tighter than when it left the factory. (Behind-the-scenes reel — Mike narrates.)",
             "Behind-the-scenes content has been your strongest IG format — 2.3× the engagement of static posts. Mike's narration adds the personal-craft angle your reviews praise."),
            ("p5",  "2026-05-06", "fb",  "pending",
             "Oil change special — coffee's on",
             "Oil change while you wait. 30 minutes, $49.95 most vehicles, fresh coffee in the lobby. No appointment needed before noon any weekday.",
             "Promotional anchor post for the slow weekday-morning window. Pulled the price point from your March pricing sheet."),
            ("p6",  "2026-05-07", "fb",  "pending",
             "Section playoffs — go Chargers",
             "Big game tonight — good luck to the WWG Chargers at sectionals. Stop in tomorrow on your way home from the game and we'll knock $10 off any service in your blue-and-white spirit gear. 🏈",
             "Local sports tie-ins drive 2.3× engagement vs other posts. Tying it to a service discount gives the post a measurable hook."),
            ("p7",  "2026-05-08", "fb",  "pending",
             "Memorial Day pre-trip safety check",
             "Headed to the lake this Memorial Day weekend? $59 pre-trip safety check covers oil, tire pressure, brakes, and fluid top-off. We'd rather you find a problem in our bay than 80 miles into your drive. Book Friday or Saturday.",
             "Lake-bound traffic is the dominant Memorial Day signal in Westbrook — flat sales underperform here. Safety-check angle reframes the same week as a service occasion."),
            ("p8",  "2026-05-09", "fb",  "draft",
             "Farm-tire field service",
             "Out in the field and got a flat on the tractor or a trailer? We come to you. Call (507) 555-0143 — Mike or I will load up the truck and head out. 22 years doing this.",
             "Differentiator pulled from your interview (\"nobody else around here will come out to the field\"). Reaches farmers who don't scroll FB casually but call when they're stuck."),
            ("p9",  "2026-05-12", "fb",  "approved",
             "\"Did you know\" — proper tire balancing",
             "Most chain shops static-balance your tires. We dynamic-balance every wheel on a road-force machine. That's the difference between a tire that hums at 65 and one that doesn't.",
             "Educational post differentiating your shop from chains. Pulled language from your \"we don't do shortcuts\" voice memo."),
            ("p10", "2026-05-14", "gbp", "approved",
             "Memorial Day pre-trip — GBP version",
             "$59 Memorial Day pre-trip safety check. Oil, tires, brakes, fluids. Open Tue–Sat, closed Memorial Day Monday. Call (507) 555-0143.",
             "GBP-specific phrasing with phone number front and center for tap-to-call. Same offer as the FB post, formatted for search-result preview."),
            ("p11", "2026-05-15", "fb",  "approved",
             "Spring suspension check",
             "Pothole season was rough on everyone. Free 5-minute suspension check this week — drive in, no appointment. We'll tell you if anything's loose, leaking, or worn. No upsell, no pressure.",
             "\"No upsell\" language is verbatim from your reviews — the customer language repository pulled it as a top-3 phrase. Free-check format consistent with your brake post."),
            ("p12", "2026-05-16", "meta", "approved",
             "Memorial Day boost — paid",
             "Boosted version of the May 8 pre-trip safety check post. $30 over 3 days, ages 30–65, 25-mile radius around Westbrook.",
             "You set the parameters in chat May 5. Estimated reach 3,500. Posting Friday 9am — strongest Friday window historically."),
            ("p13", "2026-05-18", "ig",  "draft",
             "Customer testimonial repost — Mike P.",
             "\"Honest mechanic. Won't sell you what you don't need.\" — Mike P., Google review last week. That's the goal, every time. Thanks Mike. 🙏",
             "Repurposing your strongest recent 5★ review as social proof. Pulled the line that matches your brand voice best."),
            ("p14", "2026-05-20", "fb",  "draft",
             "Mid-week reminder — coffee + oil change",
             "Wednesday is the slowest day in the bay, which means you're in and out in 25 minutes. $49.95 oil change, fresh coffee, no waiting room scrum.",
             "Capacity-balancing post for your slowest day. Frame the slow-day weakness as a customer benefit (faster turnaround)."),
            ("p15", "2026-05-22", "gbp", "draft",
             "Holiday hours reminder",
             "Memorial Day weekend reminder: open Tue–Sat regular hours, closed Monday May 25. Friday and Saturday filling up with pre-trip checks — call ahead.",
             "Final reminder before the holiday closure. GBP search visibility peaks the week of the holiday."),
            ("p16", "2026-05-25", "fb",  "draft",
             "Memorial Day — closed, thanks vets",
             "Closed today. Thank you to the men and women who served, and to the families who waited at home. We'll be back Tuesday.",
             "Sincere closure post. Avoiding promotional language on the holiday — your pattern in past years and consistent with your reviews' voice."),
            ("p17", "2026-05-29", "ig",  "draft",
             "Saturday coffee reminder",
             "Reminder: free coffee, free brake check, no appointment needed Saturday mornings. Bring your dog, the kids get stickers, we'll have you out before lunch.",
             "Reinforces the \"Saturday mornings = walk-in friendly\" pattern. Fits the Saturday-morning engagement window."),
        ]
        for ext_id, date, platform, status, title, draft, reasoning in post_seed:
            db.add(Post(
                business_id=biz.id, external_id=ext_id, date=date, platform=platform,
                status=status, title=title, draft=draft, reasoning=reasoning,
            ))

        # Approvals queue — verbatim from dashboard.html:311-329
        approvals_seed = [
            ("a1", "fb", "post",
             "Winter tire stack-out sale",
             "Spring cleaning the tire room — we've got studded snows in 16\", 17\", and 18\" at 30–40% off retail. First come, first served, and yes we'll mount and balance. Stop in or call (507) 555-0143.",
             "Pulled this from your inventory voice memo on March 28 — you mentioned the back wall was full of takeoffs. Promo timing aligned with the seasonal-swap traffic this week.",
             None),
            ("a2", "ig", "post",
             "60-second oil change tour (reel)",
             "60 seconds inside the bay 🛠️ Mike walks you through what actually happens during your oil change. (Spoiler: we check 14 things before you drive off.) Book online or call us. #WestbrookMN #LocalShop #HonestMechanic",
             "Behind-the-scenes content has been your strongest IG format — 2.3× engagement vs static posts. Reel format recommended over photo carousel.",
             None),
            ("a3", "gbp", "post",
             "Memorial Day weekend hours",
             "Memorial Day weekend hours: closed Monday May 25. Open regular hours Tue–Sat. Heading to the lake? Stop in Friday or Saturday for a quick pre-trip safety check — $59 covers oil, tires, brakes, fluids.",
             "GBP holiday-hours posts get a 4× higher tap-to-call rate than feed posts. Pairing the closure note with the pre-trip service brings the closure into a useful announcement.",
             None),
            ("a4", "gbp", "review",
             "Reply to Karen B., 2★ Google review",
             "Karen, that's on us — when we say 30 minutes we should mean it. I'm sorry your morning got blown up. I'd like to make it right: bring your vehicle back any time before May 31 and we'll take care of your next oil change on the house. Give me a call directly at (507) 555-0143 and ask for Dale. Thanks for telling us — that's how we get better.",
             "Negative-review responses with a direct phone number from the owner have a 67% callback rate. Drafted in your voice based on prior responses. Recommend approving today — Google's response-time tier resets at 48 hours.",
             "Wait was longer than told. Said 30 min for an oil change, took 1.5 hours. Disappointed."),
        ]
        for ext_id, platform, kind, title, draft, note, original in approvals_seed:
            db.add(Approval(
                business_id=biz.id, external_id=ext_id, platform=platform, kind=kind,
                title=title, draft=draft, note=note, original_review_text=original,
            ))

        # Reviews — verbatim from dashboard.html:363-393
        karen_review = Review(
            business_id=biz.id, external_id="r3", platform="gbp", stars=2, when_label="4 days ago",
            author="Karen B.",
            body="Wait was longer than told. Said 30 min for an oil change, took 1.5 hours. Disappointed.",
            ai_draft_response="Karen, that's on us — when we say 30 minutes we should mean it. I'm sorry your morning got blown up. I'd like to make it right: bring your vehicle back any time before May 31 and we'll take care of your next oil change on the house. Give me a call directly at (507) 555-0143 and ask for Dale. Thanks for telling us — that's how we get better.",
            response_note="Owner-signed apologies with a direct phone number have a 67% callback rate.",
            is_pinned=True, flagged=True, response_status="draft",
        )
        db.add(karen_review)

        other_reviews = [
            ("r1", "gbp", 5, "2 days ago", "Tom L.",
             "Quick brake job, fair price. Dale walked me through what was wrong before doing the work. Will be back.",
             "Tom — appreciate you taking the time. Glad we could get you sorted before the holiday weekend. — Dale", "approved"),
            ("r2", "fb",  5, "3 days ago", "Sarah J.",
             "Had a flat heading out of town for my daughter's graduation. They got me in same day, no extra fuss. Saved my whole weekend.",
             "Sarah — congrats to your daughter! Glad we could keep the day on track. Drive safe. — Westbrook Auto & Tire", "approved"),
            ("r4", "gbp", 5, "1 week ago", "Mike P.",
             "Honest mechanic. Won't sell you what you don't need. Told me my brakes had another season in them when the dealership wanted to replace them now. Lifetime customer.",
             "Mike — we'd rather you trust us next year than upsell you today. Thanks for the kind words. — Dale", "approved"),
            ("r5", "fb",  4, "1 week ago", "Jenna H.",
             "Service was great. Only complaint is I wish you took online appointments. Other than that, top notch.",
             "Jenna — you're not alone, we've heard that a few times now. Online booking is on the list for this summer. Until then, (507) 555-0143 will get you on the calendar. — Dale", "approved"),
        ]
        for ext_id, platform, stars, when, author, body, response, status in other_reviews:
            db.add(Review(
                business_id=biz.id, external_id=ext_id, platform=platform, stars=stars,
                when_label=when, author=author, body=body, owner_response=response,
                response_status=status, is_pinned=False, flagged=False,
            ))

        db.add(ReviewAggregate(
            business_id=biz.id,
            aggregate=4.8,
            total=162,
            sparkline_json=[4.6, 4.7, 4.7, 4.7, 4.8, 4.8],
            sparkline_labels_json=["Dec", "Jan", "Feb", "Mar", "Apr", "May"],
        ))

        # Performance — dashboard.html:333-361
        db.add(PerformanceSummary(
            business_id=biz.id,
            reach_value=14200, reach_prev=10750, reach_delta="+32%",
            engagement_value=1189, engagement_prev=928, engagement_delta="+28%",
            followers_value="+47", followers_prev="+18", followers_delta="+161%",
            ctr_value="3.1%", ctr_prev="2.4%", ctr_delta="+0.7pp",
            channel_mix_json=[
                {"platform": "fb",  "organic": 5200, "paid": 2100},
                {"platform": "ig",  "organic": 2800, "paid":  600},
                {"platform": "gbp", "organic": 3400, "paid":    0},
            ],
            top_posts_json=[
                {"platform": "fb", "title": "\"Big game tonight — good luck Chargers\"",
                 "metric": "412 likes · 38 shares · 18 comments", "note": "Saturday 8:14am"},
                {"platform": "fb", "title": "Winter tire stack-out tease",
                 "metric": "287 likes · 14 shares", "note": "Friday 9:02am"},
                {"platform": "ig", "title": "Mike rebuilds a 2014 Silverado front end (reel)",
                 "metric": "1.2k views · 89 likes · 12 comments", "note": "Wednesday 4:48pm"},
            ],
            insights_json=[
                {"kind": "win",  "title": "Local sports tie-ins",
                 "body": "Posts referencing the Chargers, the rink, or the school average 2.3× the engagement of your other posts. Keep this in the rotation when there's a real game on the calendar."},
                {"kind": "win",  "title": "Saturday-morning timing",
                 "body": "Posts published 8–10am Saturday outperform any other window by 41%. The \"free brake check Saturday\" anchor doubles down on this rhythm."},
                {"kind": "lose", "title": "Static product shots",
                 "body": "Stock-style tire photos and clip-art ads underperform. In-shop and in-vehicle photos drive the engagement — keep Mike's phone in his pocket and pull from his shots."},
                {"kind": "lose", "title": "Meta budget shape",
                 "body": "Your $30/day Meta boost hits frequency cap by day 4 — meaning the same people see it 4+ times before the run ends. Try $20/day for 6 days: same total spend, 31% more unique reach."},
            ],
            daily_reach_current_json=[430,380,510,290,340,620,580,410,360,480,290,320,540,610,460,380,420,290,350,580,670,510,440,390,480,320,410,590,640,520],
            daily_reach_prev_json=[310,280,360,220,260,440,410,290,250,340,220,240,380,430,320,270,300,220,260,410,470,360,310,280,340,230,290,410,450,360],
        ))

        # Marketing plan — dashboard.html:395-420
        db.add(MarketingPlan(
            business_id=biz.id,
            audience="Westbrook & Cottonwood County drivers — primarily owner-operators of one or two vehicles they can't afford to lose. Skews 35–65: farmers (truck and trailer work), commuters into Marshall and Worthington, and lake-bound weekend travelers heading toward Currents Lake and Spirit Lake.",
            value_prop="Honest small-town mechanic. We tell you what's wrong, what can wait, and we don't upsell. 22 years in Westbrook, ASE-certified, and we'll come to your farm for tire repairs.",
            switching_json={
                "pulls": ["Honest diagnostics", "Fair price", "Fast turnaround", "Local trust", "ASE-certified tech", "Farm-tire field service"],
                "pushes": ["Chain shops \"finding\" extra work", "Dealership prices", "Slow appointments", "No farm service nearby", "Up-charge culture"],
            },
            customer_language_json=["\"Mike's a straight shooter\"", "\"fair price\" (3×)", "\"in and out\"", "\"didn't gouge me\"", "\"saved my weekend\"", "\"honest mechanic\""],
            proof_points_json=[
                {"label": "4.8 ★ Google rating", "detail": "162 reviews"},
                {"label": "22 years in Westbrook", "detail": "second-longest tenure of any auto shop in Cottonwood County"},
                {"label": "ASE-certified tech", "detail": "Mike — engine, suspension, brakes"},
                {"label": "Farm-tire field service", "detail": "rare in the county; pulls out-of-town farm work"},
            ],
            channels_json=[
                {"platform": "fb",   "pct": 35, "color": "#1B4F9A"},
                {"platform": "ig",   "pct": 20, "color": "#9F2A6E"},
                {"platform": "gbp",  "pct": 30, "color": "#1F6E3D"},
                {"platform": "paid", "pct": 15, "color": "#0E5E6F", "label": "Paid (Meta + Google)"},
            ],
            q3_goals_json=[
                {"label": "GBP profile views per week",     "target": 200, "current": 142, "unit": "views/wk"},
                {"label": "New-customer reviews per month", "target": 8,   "current": 7,   "unit": "reviews/mo"},
                {"label": "Paid social CTR",                "target": 3.0, "current": 3.1, "unit": "%", "exceeded": True},
            ],
        ))

        # Settings + connections — dashboard.html:448-455
        db.add(SettingsRow(business_id=biz.id, cadence="weekly"))
        for platform, account, last in [
            ("fb",  "@westbrookautoandtire",      "verified May 4"),
            ("ig",  "@westbrookauto",             "verified May 4"),
            ("gbp", "Westbrook Auto & Tire — GBP", "verified May 4"),
        ]:
            db.add(Connection(
                business_id=biz.id, platform=platform, account_label=account,
                status="connected", last_verified_text=last,
            ))

        # Dashboard notices: attention feed + week recap — dashboard.html:219-240
        db.add(DashboardNotices(
            business_id=biz.id,
            attention_json=[
                {"kind": "pending", "title": "3 posts pending your approval",
                 "detail": "Memorial Day pre-trip post, Insta reel script, GBP holiday hours.",
                 "cta": "Review queue", "icon": "inbox", "tone": "amber", "target": "approvals"},
                {"kind": "review", "title": "2★ review from Karen B. on Google",
                 "detail": "\"Wait was longer than told.\" A response is drafted and waiting.",
                 "cta": "View & respond", "icon": "alert", "tone": "red", "target": "reviews"},
                {"kind": "gap", "title": "Content gap surfaced from chatbot queries",
                 "detail": "5 customers asked the Westbrook chatbot about online appointment booking this month. The Knowledge Gap Detector recommends a \"how to book\" GBP post and a placeholder booking page.",
                 "cta": "See suggestion", "icon": "sparkles", "tone": "teal", "target": "plan"},
                {"kind": "spend", "title": "Spend reallocation recommendation",
                 "detail": "Shift $30 from the Eagles boost (frequency capped) to a GBP promotion. Estimated 3.1× expected ROAS.",
                 "cta": "See details", "icon": "trending", "tone": "teal", "target": "performance"},
            ],
            week_recap_json=[
                {"when": "Mon, Apr 28", "text": "Drafted 4 Facebook posts (2 awaiting your sign-off)"},
                {"when": "Tue, Apr 29", "text": "Drafted 3 Instagram posts including a behind-the-scenes reel script"},
                {"when": "Wed, Apr 30", "text": "Updated Google Business Profile twice — winter→summer hours and a Memorial Day note"},
                {"when": "Thu, May 1",  "text": "Drafted responses to 6 reviews; 5 auto-approved per your weekly batch setting, Karen B. flagged for your eyes"},
                {"when": "Fri, May 2",  "text": "Flagged frequency cap on the $30/day Eagles boost — $20/day across 6 days is the cleaner buy"},
            ],
            stats_overrides_json={
                "posts":      {"value": 12, "prev": 8, "delta": "+4", "helper": "across FB, IG, GBP"},
                "engagement": {"value": "+28%", "helper": "vs prior 30 days", "positive": True},
                "spend":      {"used": 112, "budget": 150, "helper": "$200 / mo recommended"},
                "chatbot":    {"value": 34, "helper": "Tier 3 preview"},
            },
        ))

        # Pre-seeded AI Agent chat — dashboard.html:422-446
        chat_seed = [
            ("owner", "Today, 9:14am", "Should I run a Memorial Day sale?", None),
            ("agent", "Today, 9:14am",
             "Memorial Day weekend in Westbrook is mostly about people heading to the lake — Currents, Spirit Lake, and the river — not about car shopping. A flat \"sale\" usually underperforms here. The angle that works in your data is **pre-trip safety**: a $59 inspection covering oil, tire pressure, brakes, and a fluid top-off. Want me to draft the post and a small boost?",
             None),
            ("owner", "Today, 9:15am", "Yes please. What's a reasonable boost?", None),
            ("agent", "Today, 9:15am",
             "Drafted. For the boost: $40 over 3 days, targeting ages 35–65 within 25 miles of Westbrook. Estimated reach 4,200 with around 130 link clicks. Memorial Day weekend is high-intent for travel prep, so cost-per-click should run about 20% lower than your monthly average. Posting Friday at 9am — your strongest Friday window historically.",
             {"kind": "boost-card", "budget": "$40 / 3 days", "ages": "35–65",
              "radius": "25 mi from Westbrook", "reach": "4,200 estimated",
              "clicks": "~130 estimated", "post": "Friday 9:00am"}),
            ("owner", "Today, 9:16am", "Make it $30 and target 30–65", None),
            ("agent", "Today, 9:16am",
             "Done — $30 over 3 days, ages 30–65, same 25-mile radius. Adjusted reach estimate: 3,500. The post and the boost are queued in your **Approvals** tab for sign-off. Anything else I can pull together?",
             {"kind": "boost-card", "budget": "$30 / 3 days", "ages": "30–65",
              "radius": "25 mi from Westbrook", "reach": "3,500 estimated",
              "clicks": "~110 estimated", "post": "Friday 9:00am"}),
        ]
        for who, when, text, attachment in chat_seed:
            db.add(ChatTurn(
                business_id=biz.id, conversation_id="seed",
                who=who, when_label=when, text=text, attachment_json=attachment,
            ))

        db.commit()
        return True
    finally:
        db.close()
