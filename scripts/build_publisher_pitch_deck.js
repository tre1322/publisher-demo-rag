// Build the Grand Network publisher pitch deck.
// Outputs docs/publisher_pitch_deck.pptx
//
// Palette ("Midnight + Masthead"):
//   INK       #0A1628  primary dark background
//   INK_DEEP  #050D1C  cover / section covers
//   CREAM     #F5EFE6  light surfaces + body text on dark
//   BODY_DARK #EAE3D2  body text on dark
//   RULE_RED  #C4302B  newspaper masthead red (accent rule + urgent callouts)
//   SLATE     #6B7A8A  muted caption text
//   GOLD      #C9A961  subtle secondary accent for numbers
//
// Motif: a 0.08" vertical red "column rule" at x=0.35 on every content slide.

const pptxgen = require("pptxgenjs");
const React = require("react");
const ReactDOMServer = require("react-dom/server");
const sharp = require("sharp");
const {
  FaLock, FaShieldAlt, FaRegNewspaper, FaBolt, FaNetworkWired,
  FaDatabase, FaHandshake, FaChartLine, FaRoute, FaUsers,
  FaDoorOpen, FaArrowRight, FaExclamationTriangle, FaSearch
} = require("react-icons/fa");

const COLOR = {
  INK:       "0A1628",
  INK_DEEP:  "050D1C",
  CREAM:     "F5EFE6",
  BODY_DARK: "EAE3D2",
  RULE_RED:  "C4302B",
  SLATE:     "6B7A8A",
  GOLD:      "C9A961",
  CREAM_MUTE:"C9C1B3",
};

const FONT_HEAD = "Georgia";
const FONT_BODY = "Calibri";

function renderIconSvg(Icon, color, size = 256) {
  return ReactDOMServer.renderToStaticMarkup(
    React.createElement(Icon, { color, size: String(size) })
  );
}
async function iconPng(Icon, color, size = 256) {
  const svg = renderIconSvg(Icon, color.startsWith("#") ? color : "#" + color, size);
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

// Fresh shadow factory — never share shadow objects between calls
const makeShadow = () => ({
  type: "outer", color: "000000", blur: 12, offset: 3, angle: 135, opacity: 0.35,
});

// ---- Per-slide chrome (red column rule + footer) ---------------------------
function addChrome(slide, pageNumber, totalPages, dark = true) {
  // Vertical masthead rule
  slide.addShape("rect", {
    x: 0.35, y: 0.35, w: 0.06, h: 4.95,
    fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
  });
  // Footer
  const footerColor = dark ? COLOR.SLATE : COLOR.SLATE;
  slide.addText("GRAND NETWORK", {
    x: 0.5, y: 5.3, w: 3, h: 0.25,
    fontFace: FONT_BODY, fontSize: 9, color: footerColor,
    charSpacing: 6, bold: true, align: "left", margin: 0,
  });
  slide.addText(`${pageNumber} / ${totalPages}`, {
    x: 8.5, y: 5.3, w: 1.2, h: 0.25,
    fontFace: FONT_BODY, fontSize: 9, color: footerColor,
    align: "right", margin: 0,
  });
}

async function build() {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9"; // 10" × 5.625"
  pres.author = "Grand Network";
  pres.title  = "What if your archive was the answer?";

  const TOTAL = 13;

  // Pre-render icons (parallel)
  const [
    iconSearchCream, iconLockCream, iconShieldGold,
    iconDoorCream, iconDoorGold, iconNetCream, iconDataCream,
    iconHandCream, iconChartCream, iconRouteCream, iconUsersCream,
    iconBoltRed, iconWarnRed, iconArrowRed, iconPaperCream,
  ] = await Promise.all([
    iconPng(FaSearch,   COLOR.CREAM),
    iconPng(FaLock,     COLOR.CREAM),
    iconPng(FaShieldAlt,COLOR.GOLD),
    iconPng(FaDoorOpen, COLOR.CREAM),
    iconPng(FaDoorOpen, COLOR.GOLD),
    iconPng(FaNetworkWired, COLOR.CREAM),
    iconPng(FaDatabase, COLOR.CREAM),
    iconPng(FaHandshake, COLOR.CREAM),
    iconPng(FaChartLine, COLOR.CREAM),
    iconPng(FaRoute,    COLOR.CREAM),
    iconPng(FaUsers,    COLOR.CREAM),
    iconPng(FaBolt,     COLOR.RULE_RED),
    iconPng(FaExclamationTriangle, COLOR.RULE_RED),
    iconPng(FaArrowRight, COLOR.RULE_RED),
    iconPng(FaRegNewspaper, COLOR.CREAM),
  ]);

  // =========================================================================
  // SLIDE 1 — TITLE
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK_DEEP };

    // Red column rule
    s.addShape("rect", {
      x: 0.35, y: 0.35, w: 0.06, h: 4.95,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });

    // Eyebrow
    s.addText("A PRIVATE PITCH   ·   APRIL 2026", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.GOLD,
      charSpacing: 10, bold: true, margin: 0,
    });

    // Huge headline — split into two lines for impact
    s.addText("What if your archive", {
      x: 0.6, y: 1.5, w: 9.2, h: 1.0,
      fontFace: FONT_HEAD, fontSize: 54, color: COLOR.CREAM,
      bold: false, italic: true, margin: 0,
    });
    s.addText("was the answer?", {
      x: 0.6, y: 2.45, w: 9.2, h: 1.0,
      fontFace: FONT_HEAD, fontSize: 54, color: COLOR.CREAM,
      bold: true, margin: 0,
    });

    // Underline accent (short red bar under headline)
    s.addShape("rect", {
      x: 0.6, y: 3.6, w: 0.8, h: 0.04,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });

    // Subtitle
    s.addText("Grand Network   |   Trevor Slette, John Draper, Nate Groebner", {
      x: 0.6, y: 3.75, w: 9.2, h: 0.4,
      fontFace: FONT_BODY, fontSize: 14, color: COLOR.BODY_DARK, margin: 0,
    });

    // Tagline bottom
    s.addText("Built by publishers. For publishers. Before it's too late.", {
      x: 0.6, y: 4.9, w: 9.2, h: 0.4,
      fontFace: FONT_HEAD, italic: true, fontSize: 13, color: COLOR.CREAM_MUTE, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 2 — THE OPENING QUESTION
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK };
    addChrome(s, 2, TOTAL);

    s.addText("ASK YOURSELF", {
      x: 0.6, y: 0.55, w: 6, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText([
      { text: "When someone in your town wants to know ",
        options: { color: COLOR.CREAM } },
      { text: "anything —",
        options: { color: COLOR.GOLD, italic: true, breakLine: true } },
      { text: "who do they ask now?",
        options: { color: COLOR.CREAM, bold: true } },
    ], {
      x: 0.6, y: 1.3, w: 9, h: 3.2,
      fontFace: FONT_HEAD, fontSize: 44, margin: 0, valign: "top",
    });

    // (removed orphan magnifying-glass icon — was floating next to footer)
  }

  // =========================================================================
  // SLIDE 3 — THE DATA
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK };
    addChrome(s, 3, TOTAL);

    s.addText("THEY DIDN'T ASK YOU", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText("The traffic is already gone.", {
      x: 0.6, y: 0.9, w: 9, h: 0.6,
      fontFace: FONT_HEAD, fontSize: 28, color: COLOR.CREAM, bold: true, margin: 0,
    });

    // Three stat columns
    const stats = [
      { num: "−60%", label: "Google referrals to small publishers, 2023→2025", src: "Chartbeat / ALM Corp" },
      { num: "−43%", label: "Additional traffic drop publishers expect by 2029", src: "Reuters Institute / Oxford" },
      { num: "−25%", label: "Referral loss attributable to AI Overviews alone", src: "Digiday" },
    ];
    const colW = 2.9, gap = 0.15, startX = 0.6;
    stats.forEach((stat, i) => {
      const x = startX + i * (colW + gap);
      // Dark card
      s.addShape("rect", {
        x, y: 1.75, w: colW, h: 3.0,
        fill: { color: COLOR.INK_DEEP }, line: { color: COLOR.INK_DEEP },
      });
      // Accent bar on top
      s.addShape("rect", {
        x, y: 1.75, w: colW, h: 0.05,
        fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
      });
      // Big number
      s.addText(stat.num, {
        x, y: 1.95, w: colW, h: 1.4,
        fontFace: FONT_HEAD, fontSize: 70, color: COLOR.CREAM,
        bold: true, align: "center", margin: 0,
      });
      // Label
      s.addText(stat.label, {
        x: x + 0.2, y: 3.4, w: colW - 0.4, h: 0.9,
        fontFace: FONT_BODY, fontSize: 12, color: COLOR.BODY_DARK,
        align: "center", valign: "top", margin: 0,
      });
      // Source
      s.addText(stat.src, {
        x: x + 0.2, y: 4.35, w: colW - 0.4, h: 0.3,
        fontFace: FONT_BODY, italic: true, fontSize: 9, color: COLOR.SLATE,
        align: "center", margin: 0,
      });
    });
  }

  // =========================================================================
  // SLIDE 4 — THE REFRAME
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.CREAM };

    // Red rule (on light bg)
    s.addShape("rect", {
      x: 0.35, y: 0.35, w: 0.06, h: 4.95,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    // Light footer
    s.addText("GRAND NETWORK", {
      x: 0.5, y: 5.3, w: 3, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE,
      charSpacing: 6, bold: true, align: "left", margin: 0,
    });
    s.addText(`4 / ${TOTAL}`, {
      x: 8.5, y: 5.3, w: 1.2, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE, align: "right", margin: 0,
    });

    s.addText("THE REFRAME", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText("The readers didn't leave.", {
      x: 0.6, y: 0.95, w: 9, h: 0.7,
      fontFace: FONT_HEAD, fontSize: 36, color: COLOR.INK, bold: false, margin: 0,
    });
    s.addText("The layer moved.", {
      x: 0.6, y: 1.55, w: 9, h: 0.7,
      fontFace: FONT_HEAD, fontSize: 36, color: COLOR.RULE_RED, bold: true, italic: true, margin: 0,
    });

    // Before / After two-column
    const colY = 2.7;
    const colH = 2.0;

    // BEFORE
    s.addShape("rect", {
      x: 0.6, y: colY, w: 4.1, h: colH,
      fill: { color: "FFFFFF" }, line: { color: COLOR.CREAM_MUTE, width: 0.75 },
    });
    s.addText("BEFORE · 2016", {
      x: 0.8, y: colY + 0.15, w: 3.8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.SLATE, charSpacing: 8, bold: true, margin: 0,
    });
    s.addText("Readers searched.", {
      x: 0.8, y: colY + 0.5, w: 3.8, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 22, color: COLOR.INK, bold: true, margin: 0,
    });
    s.addText("Google sent them to you. You owned the click, the ad, the subscription prompt.", {
      x: 0.8, y: colY + 1.05, w: 3.8, h: 0.9,
      fontFace: FONT_BODY, fontSize: 12, color: "3A4655", margin: 0, valign: "top",
    });

    // Arrow (centered between the two card body-content zones)
    s.addImage({ data: iconArrowRed, x: 4.85, y: colY + 0.55, w: 0.5, h: 0.5 });

    // AFTER
    s.addShape("rect", {
      x: 5.5, y: colY, w: 4.1, h: colH,
      fill: { color: COLOR.INK }, line: { color: COLOR.INK },
    });
    s.addText("NOW · 2026", {
      x: 5.7, y: colY + 0.15, w: 3.8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.GOLD, charSpacing: 8, bold: true, margin: 0,
    });
    s.addText("Readers ask.", {
      x: 5.7, y: colY + 0.5, w: 3.8, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 22, color: COLOR.CREAM, bold: true, margin: 0,
    });
    s.addText("ChatGPT answers. Google AI Overviews answer. Your archive is invisible.", {
      x: 5.7, y: colY + 1.05, w: 3.8, h: 0.9,
      fontFace: FONT_BODY, fontSize: 12, color: COLOR.BODY_DARK, margin: 0, valign: "top",
    });

    s.addText("Fix the layer, you get them back.", {
      x: 0.6, y: 4.75, w: 9, h: 0.35,
      fontFace: FONT_HEAD, italic: true, fontSize: 14, color: COLOR.INK, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 5 — INVISIBLE ARCHIVE (the quiet sting)
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK_DEEP };
    addChrome(s, 5, TOTAL);

    s.addText("THE UNCOMFORTABLE TRUTH", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText([
      { text: "50 years ", options: { color: COLOR.GOLD, italic: true } },
      { text: "of reporting on your town.", options: { color: COLOR.CREAM, breakLine: true } },
      { text: "Who got married. Who won state. Who ran for council.", options: { color: COLOR.BODY_DARK } },
    ], {
      x: 0.6, y: 1.2, w: 9, h: 1.8,
      fontFace: FONT_HEAD, fontSize: 24, margin: 0, valign: "top",
    });

    // One-line gut punch
    s.addShape("rect", {
      x: 0.6, y: 3.3, w: 8.8, h: 1.2,
      fill: { color: "1A2840" }, line: { color: "1A2840" },
    });
    s.addShape("rect", {
      x: 0.6, y: 3.3, w: 0.1, h: 1.2,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addText("Invisible to the system that now answers every question.", {
      x: 0.9, y: 3.45, w: 8.4, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 20, color: COLOR.CREAM, bold: true, margin: 0,
    });
    s.addText("Not Yelp. Not Google. Not ChatGPT. Nobody has what you have — and nobody can find it.", {
      x: 0.9, y: 3.95, w: 8.4, h: 0.5,
      fontFace: FONT_BODY, fontSize: 13, color: COLOR.BODY_DARK, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 6 — WHAT WE BUILT (LIVE DEMO)
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.CREAM };

    s.addShape("rect", {
      x: 0.35, y: 0.35, w: 0.06, h: 4.95,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addText("GRAND NETWORK", {
      x: 0.5, y: 5.3, w: 3, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE,
      charSpacing: 6, bold: true, margin: 0,
    });
    s.addText(`6 / ${TOTAL}`, {
      x: 8.5, y: 5.3, w: 1.2, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE, align: "right", margin: 0,
    });

    s.addText("ALREADY RUNNING", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText("Two papers. One platform. Live today.", {
      x: 0.6, y: 0.9, w: 9, h: 0.6,
      fontFace: FONT_HEAD, fontSize: 28, color: COLOR.INK, bold: true, margin: 0,
    });

    // Two publisher cards
    const cards = [
      {
        name: "Cottonwood County Citizen", market: "Windom, MN",
        url: "/windom", paper: "Trevor's paper · Pilot publisher #1",
      },
      {
        name: "Pipestone Star", market: "Pipestone, MN",
        url: "/pipestone", paper: "John's paper · Pilot publisher #2",
      },
    ];
    cards.forEach((c, i) => {
      const x = 0.6 + i * 4.5;
      s.addShape("rect", {
        x, y: 1.7, w: 4.2, h: 2.6,
        fill: { color: "FFFFFF" }, line: { color: COLOR.CREAM_MUTE, width: 0.75 },
        shadow: makeShadow(),
      });
      // Top accent bar
      s.addShape("rect", {
        x, y: 1.7, w: 4.2, h: 0.08,
        fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
      });
      // darker ink-on-cream icon for contrast — render fresh per card
      s.addShape("rect", {
        x: x + 0.25, y: 1.9, w: 0.55, h: 0.55,
        fill: { color: COLOR.INK }, line: { color: COLOR.INK },
      });
      s.addImage({ data: iconPaperCream, x: x + 0.3, y: 1.95, w: 0.45, h: 0.45 });
      s.addText(c.name, {
        x: x + 0.95, y: 1.9, w: 3.1, h: 0.55,
        fontFace: FONT_HEAD, fontSize: 15, color: COLOR.INK, bold: true, margin: 0, valign: "middle",
      });
      s.addText(c.market, {
        x: x + 0.3, y: 2.5, w: 3.7, h: 0.3,
        fontFace: FONT_BODY, fontSize: 11, color: COLOR.SLATE, margin: 0, italic: true,
      });
      s.addText(c.paper, {
        x: x + 0.3, y: 2.85, w: 3.7, h: 0.3,
        fontFace: FONT_BODY, fontSize: 11, color: COLOR.INK, margin: 0,
      });
      s.addText(c.url, {
        x: x + 0.3, y: 3.25, w: 3.7, h: 0.4,
        fontFace: "Consolas", fontSize: 12, color: COLOR.RULE_RED, bold: true, margin: 0,
      });
      // bottom meta row
      s.addText([
        { text: "✓ Homepage cards  ", options: { color: COLOR.INK } },
        { text: "✓ AI chatbot  ", options: { color: COLOR.INK } },
        { text: "✓ Podcast audio", options: { color: COLOR.INK } },
      ], {
        x: x + 0.3, y: 3.75, w: 3.9, h: 0.35,
        fontFace: FONT_BODY, fontSize: 10, margin: 0,
      });
    });

    // Footnote / proof
    s.addText("Upload a PDF at 6 am → homepage updates before coffee. Live URL available on request.", {
      x: 0.6, y: 4.5, w: 9, h: 0.3,
      fontFace: FONT_HEAD, italic: true, fontSize: 12, color: COLOR.INK, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 7 — THE TWO DOORS PROMISE
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK };
    addChrome(s, 7, TOTAL);

    s.addText("THE PROMISE", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });
    s.addText("We do not touch your subscribers. Ever.", {
      x: 0.6, y: 0.9, w: 9, h: 0.6,
      fontFace: FONT_HEAD, fontSize: 28, color: COLOR.CREAM, bold: true, margin: 0,
    });

    // Two doors
    // DOOR 1 — yours (untouched)
    s.addShape("rect", {
      x: 0.6, y: 1.75, w: 4.2, h: 3.2,
      fill: { color: "1A2840" }, line: { color: "1A2840" },
    });
    s.addShape("rect", {
      x: 0.6, y: 1.75, w: 4.2, h: 0.08,
      fill: { color: COLOR.CREAM }, line: { color: COLOR.CREAM },
    });
    s.addImage({ data: iconDoorCream, x: 0.85, y: 2.0, w: 0.5, h: 0.5 });
    s.addText("DOOR 1", {
      x: 1.5, y: 2.0, w: 2.8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.CREAM, charSpacing: 8, bold: true, margin: 0, valign: "top",
    });
    s.addText("Your paper.", {
      x: 1.5, y: 2.25, w: 2.8, h: 0.4,
      fontFace: FONT_HEAD, fontSize: 20, color: COLOR.CREAM, bold: true, margin: 0,
    });
    s.addText([
      { text: "Your domain", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "Your paywall", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "Your print edition", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "Your subscriber list", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "Your advertisers", options: { bullet: true, color: COLOR.BODY_DARK } },
    ], {
      x: 0.9, y: 2.9, w: 3.8, h: 1.9,
      fontFace: FONT_BODY, fontSize: 13, margin: 0, valign: "top", paraSpaceAfter: 4,
    });
    s.addText("UNTOUCHED", {
      x: 0.9, y: 4.6, w: 3.6, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.GOLD, charSpacing: 10, bold: true, margin: 0,
    });

    // DOOR 2 — Grand Network (new)
    s.addShape("rect", {
      x: 5.2, y: 1.75, w: 4.2, h: 3.2,
      fill: { color: COLOR.INK_DEEP }, line: { color: COLOR.RULE_RED, width: 1 },
    });
    s.addShape("rect", {
      x: 5.2, y: 1.75, w: 4.2, h: 0.08,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addImage({ data: iconDoorGold, x: 5.45, y: 2.0, w: 0.5, h: 0.5 });
    s.addText("DOOR 2", {
      x: 6.1, y: 2.0, w: 2.8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.GOLD, charSpacing: 8, bold: true, margin: 0, valign: "top",
    });
    s.addText("Grand Network.", {
      x: 6.1, y: 2.25, w: 2.8, h: 0.4,
      fontFace: FONT_HEAD, fontSize: 20, color: COLOR.CREAM, bold: true, margin: 0,
    });
    s.addText([
      { text: "Separate brand + domain", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "AI assistant across the whole region", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "New consumer audience", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "New revenue stack", options: { bullet: true, breakLine: true, color: COLOR.BODY_DARK } },
      { text: "You earn share of all of it", options: { bullet: true, color: COLOR.BODY_DARK } },
    ], {
      x: 5.5, y: 2.9, w: 3.8, h: 1.9,
      fontFace: FONT_BODY, fontSize: 13, margin: 0, valign: "top", paraSpaceAfter: 4,
    });
    s.addText("ADDITIVE", {
      x: 5.5, y: 4.6, w: 3.6, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED, charSpacing: 10, bold: true, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 8 — SOVEREIGN AI
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK_DEEP };
    addChrome(s, 8, TOTAL);

    s.addText("WHAT NOBODY ELSE IS PITCHING YOU", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText("Your content never leaves servers we control.", {
      x: 0.6, y: 0.95, w: 9, h: 0.7,
      fontFace: FONT_HEAD, fontSize: 26, color: COLOR.CREAM, bold: true, margin: 0,
    });

    // Big shield
    s.addImage({ data: iconShieldGold, x: 7.4, y: 2.3, w: 1.9, h: 1.9 });

    // Three promises with icons
    const promises = [
      { line: "No API call to OpenAI or Anthropic anywhere in the publisher path." },
      { line: "Self-hosted open-weight models on an endpoint we operate." },
      { line: "Read-only audit log you can log into — every question, every chunk." },
    ];
    promises.forEach((p, i) => {
      const y = 2.2 + i * 0.75;
      s.addShape("rect", {
        x: 0.6, y, w: 0.08, h: 0.5,
        fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
      });
      s.addText(p.line, {
        x: 0.85, y, w: 6.3, h: 0.55,
        fontFace: FONT_BODY, fontSize: 14, color: COLOR.BODY_DARK, margin: 0, valign: "middle",
      });
    });

    s.addText("Not a ToS promise. Architecture. Enforced by a CI rule that fails the build if anyone re-adds a vendor call.", {
      x: 0.6, y: 4.55, w: 9, h: 0.4,
      fontFace: FONT_HEAD, italic: true, fontSize: 12, color: COLOR.GOLD, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 9 — THE REVENUE STACK
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.CREAM };

    s.addShape("rect", {
      x: 0.35, y: 0.35, w: 0.06, h: 4.95,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addText("GRAND NETWORK", {
      x: 0.5, y: 5.3, w: 3, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE,
      charSpacing: 6, bold: true, margin: 0,
    });
    s.addText(`9 / ${TOTAL}`, {
      x: 8.5, y: 5.3, w: 1.2, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE, align: "right", margin: 0,
    });

    s.addText("SEVEN NEW LINES", {
      x: 0.6, y: 0.45, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });
    s.addText("Revenue you cannot reach alone.", {
      x: 0.6, y: 0.75, w: 9, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 24, color: COLOR.INK, bold: true, margin: 0,
    });

    // Table data
    const headerFill = { fill: { color: COLOR.INK } };
    const headerText = { color: COLOR.CREAM, bold: true, fontFace: FONT_BODY, fontSize: 11 };
    const bodyFont = { fontFace: FONT_BODY, fontSize: 11, color: "1F2937" };
    const rows = [
      [
        { text: "LINE", options: { ...headerFill, ...headerText } },
        { text: "WHAT IT IS", options: { ...headerFill, ...headerText } },
        { text: "YOUR SHARE", options: { ...headerFill, ...headerText, align: "center" } },
      ],
      [
        { text: "Network Pass", options: { bold: true, ...bodyFont } },
        { text: "Consumer sub — all papers, one price", options: { ...bodyFont } },
        { text: "70%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
      [
        { text: "Main Street OS", options: { bold: true, ...bodyFont } },
        { text: "$30 / $75 / $150 SaaS for local businesses", options: { ...bodyFont } },
        { text: "60%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
      [
        { text: "LLM Licensing", options: { bold: true, ...bodyFont } },
        { text: "Frontier labs pay the network for local corpus", options: { ...bodyFont } },
        { text: "50%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
      [
        { text: "Regional Ad Buys", options: { bold: true, ...bodyFont } },
        { text: "One buy covers every paper; share by reach", options: { ...bodyFont } },
        { text: "80%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
      [
        { text: "Classified Verticals", options: { bold: true, ...bodyFont } },
        { text: "Jobs · Real estate · Ag · Auto — AI-native", options: { ...bodyFont } },
        { text: "70%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
      [
        { text: "Sponsored Answers", options: { bold: true, ...bodyFont } },
        { text: "Disclosed, capped — businesses pay to be the answer", options: { ...bodyFont } },
        { text: "60%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
      [
        { text: "Events & Tickets", options: { bold: true, ...bodyFont } },
        { text: "Every local event becomes sellable inventory", options: { ...bodyFont } },
        { text: "70%", options: { bold: true, color: COLOR.RULE_RED, align: "center", fontFace: FONT_HEAD, fontSize: 13 } },
      ],
    ];
    s.addTable(rows, {
      x: 0.6, y: 1.4, w: 8.8,
      colW: [1.9, 5.3, 1.6],
      rowH: 0.4,
      border: { pt: 0.5, color: COLOR.CREAM_MUTE },
      fill: { color: "FFFFFF" },
    });

    s.addText("All seven routed through your ad-sales relationship and your P&L. We're the plumbing.", {
      x: 0.6, y: 4.85, w: 9, h: 0.35,
      fontFace: FONT_HEAD, italic: true, fontSize: 12, color: COLOR.INK, margin: 0,
    });
  }

  // =========================================================================
  // SLIDE 10 — THE MATH
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK };
    addChrome(s, 10, TOTAL);

    s.addText("THE MATH FOR YOUR PAPER", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });
    s.addText("Year two. Fifteen-paper network. Modeled, not promised.", {
      x: 0.6, y: 0.9, w: 9, h: 0.5,
      fontFace: FONT_HEAD, fontSize: 20, color: COLOR.CREAM, margin: 0,
    });

    // Huge stat — looser tracking so the $ doesn't collide with the +
    s.addText("+ $8,000", {
      x: 0.6, y: 1.7, w: 9, h: 1.7,
      fontFace: FONT_HEAD, fontSize: 96, color: COLOR.CREAM, bold: true,
      align: "center", margin: 0, charSpacing: 2,
    });
    s.addText("per month. New revenue. On top of everything you already have.", {
      x: 0.6, y: 3.45, w: 9, h: 0.4,
      fontFace: FONT_HEAD, italic: true, fontSize: 16, color: COLOR.GOLD,
      align: "center", margin: 0,
    });

    // Breakdown mini-chips
    const chips = [
      { label: "Network Pass", val: "$2.8k" },
      { label: "Main Street OS", val: "$1.8k" },
      { label: "Regional Ads", val: "$1.8k" },
      { label: "Classifieds + More", val: "$1.6k" },
    ];
    const chipW = 2.0, gap = 0.2, totalW = chipW * chips.length + gap * (chips.length - 1);
    const startX = (10 - totalW) / 2;
    chips.forEach((c, i) => {
      const x = startX + i * (chipW + gap);
      s.addShape("rect", {
        x, y: 4.25, w: chipW, h: 0.75,
        fill: { color: COLOR.INK_DEEP }, line: { color: COLOR.RULE_RED, width: 0.5 },
      });
      s.addText(c.val, {
        x, y: 4.3, w: chipW, h: 0.4,
        fontFace: FONT_HEAD, fontSize: 16, color: COLOR.CREAM, bold: true, align: "center", margin: 0,
      });
      s.addText(c.label, {
        x, y: 4.7, w: chipW, h: 0.3,
        fontFace: FONT_BODY, fontSize: 10, color: COLOR.CREAM_MUTE, align: "center", margin: 0, charSpacing: 3,
      });
    });
  }

  // =========================================================================
  // SLIDE 11 — THE MOAT
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK_DEEP };
    addChrome(s, 11, TOTAL);

    s.addText("THE MOAT", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });
    s.addText("Why the fifteenth paper in is worth more than the first.", {
      x: 0.6, y: 0.9, w: 9, h: 0.6,
      fontFace: FONT_HEAD, fontSize: 22, color: COLOR.CREAM, bold: true, margin: 0,
    });

    // 2x2 grid
    const quads = [
      { icon: iconDataCream, title: "DATA",       body: "Hyperlocal structured data no national tool has. Every paper compounds it." },
      { icon: iconLockCream, title: "TRUST",      body: "Per-publisher sovereign agents, audit logs, one-click portability." },
      { icon: iconRouteCream,title: "DISTRIBUTION",body: "The papers ARE the distribution. No paid acquisition. Organic into every Main Street." },
      { icon: iconNetCream,  title: "NETWORK",    body: "Three-year regional exclusivity. Once 15 papers are in, cloners can't enter." },
    ];
    const qw = 4.3, qh = 1.45, gap = 0.2, startX = 0.6, startY = 1.75;
    quads.forEach((q, i) => {
      const col = i % 2, row = Math.floor(i / 2);
      const x = startX + col * (qw + gap);
      const y = startY + row * (qh + 0.25);
      s.addShape("rect", {
        x, y, w: qw, h: qh,
        fill: { color: "1A2840" }, line: { color: "1A2840" },
      });
      s.addShape("rect", {
        x, y, w: 0.08, h: qh,
        fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
      });
      s.addImage({ data: q.icon, x: x + 0.25, y: y + 0.25, w: 0.45, h: 0.45 });
      s.addText(q.title, {
        x: x + 0.85, y: y + 0.2, w: qw - 1, h: 0.35,
        fontFace: FONT_BODY, fontSize: 11, color: COLOR.GOLD, charSpacing: 8, bold: true, margin: 0,
      });
      s.addText(q.body, {
        x: x + 0.25, y: y + 0.75, w: qw - 0.5, h: 0.65,
        fontFace: FONT_BODY, fontSize: 12, color: COLOR.BODY_DARK, margin: 0, valign: "top",
      });
    });
  }

  // =========================================================================
  // SLIDE 12 — WHO'S IN (SCARCITY)
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.CREAM };

    s.addShape("rect", {
      x: 0.35, y: 0.35, w: 0.06, h: 4.95,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addText("GRAND NETWORK", {
      x: 0.5, y: 5.3, w: 3, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE,
      charSpacing: 6, bold: true, margin: 0,
    });
    s.addText(`12 / ${TOTAL}`, {
      x: 8.5, y: 5.3, w: 1.2, h: 0.25,
      fontFace: FONT_BODY, fontSize: 9, color: COLOR.SLATE, align: "right", margin: 0,
    });

    s.addText("WHO'S IN", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });
    s.addText("Southern Minnesota pilot — first ten papers.", {
      x: 0.6, y: 0.9, w: 9, h: 0.55,
      fontFace: FONT_HEAD, fontSize: 24, color: COLOR.INK, bold: true, margin: 0,
    });

    // Ten seat indicators
    const seatStartX = 0.6, seatY = 1.9, seatW = 0.82, seatH = 0.82, seatGap = 0.13;
    for (let i = 0; i < 10; i++) {
      const x = seatStartX + i * (seatW + seatGap);
      const filled = i < 2;       // Cottonwood + Pipestone
      const reserved = i === 2;   // 3rd target
      s.addShape("rect", {
        x, y: seatY, w: seatW, h: seatH,
        fill: { color: filled ? COLOR.INK : (reserved ? "E6DBC4" : "FFFFFF") },
        line: { color: filled ? COLOR.INK : COLOR.CREAM_MUTE, width: 1 },
      });
      if (filled) {
        s.addText("✓", {
          x, y: seatY, w: seatW, h: seatH,
          fontFace: FONT_HEAD, fontSize: 28, color: COLOR.GOLD, bold: true, align: "center", valign: "middle", margin: 0,
        });
      } else if (reserved) {
        s.addText("…", {
          x, y: seatY, w: seatW, h: seatH,
          fontFace: FONT_HEAD, fontSize: 28, color: COLOR.RULE_RED, bold: true, align: "center", valign: "middle", margin: 0,
        });
      }
      s.addText(String(i + 1), {
        x, y: seatY + seatH + 0.05, w: seatW, h: 0.25,
        fontFace: FONT_BODY, fontSize: 10, color: COLOR.INK, bold: true, align: "center", margin: 0,
      });
    }

    // Legend + names
    s.addShape("rect", {
      x: 0.6, y: 3.4, w: 8.8, h: 1.35,
      fill: { color: "FFFFFF" }, line: { color: COLOR.CREAM_MUTE, width: 0.75 },
    });
    s.addShape("rect", {
      x: 0.6, y: 3.4, w: 0.08, h: 1.35,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addText([
      { text: "Cottonwood County Citizen", options: { bold: true, color: COLOR.INK } },
      { text: "  ·  Windom, MN  ·  live since April 2026",
        options: { color: COLOR.SLATE, breakLine: true } },
      { text: "Pipestone Star", options: { bold: true, color: COLOR.INK } },
      { text: "  ·  Pipestone, MN  ·  live since April 2026",
        options: { color: COLOR.SLATE, breakLine: true } },
      { text: "Seven seats remain.", options: { italic: true, color: COLOR.RULE_RED, bold: true } },
      { text: "  Three other publishers in active conversation this month.",
        options: { color: COLOR.INK } },
    ], {
      x: 0.95, y: 3.5, w: 8.3, h: 1.2,
      fontFace: FONT_BODY, fontSize: 13, margin: 0, valign: "top",
      paraSpaceAfter: 6,
    });
  }

  // =========================================================================
  // SLIDE 13 — THE ASK / CLOSE
  // =========================================================================
  {
    const s = pres.addSlide();
    s.background = { color: COLOR.INK_DEEP };
    addChrome(s, 13, TOTAL);

    s.addText("THE ASK", {
      x: 0.6, y: 0.55, w: 8, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.RULE_RED,
      charSpacing: 10, bold: true, margin: 0,
    });

    s.addText([
      { text: "Start with ", options: { color: COLOR.CREAM } },
      { text: "Summary Only", options: { color: COLOR.GOLD, italic: true } },
      { text: ".", options: { color: COLOR.CREAM, breakLine: true } },
      { text: "We index your archive.", options: { color: COLOR.BODY_DARK, breakLine: true } },
      { text: "Every answer links back to your site.", options: { color: COLOR.BODY_DARK } },
    ], {
      x: 0.6, y: 1.0, w: 9, h: 2.0,
      fontFace: FONT_HEAD, fontSize: 32, bold: true, margin: 0, valign: "top",
    });

    // Promise bar
    s.addShape("rect", {
      x: 0.6, y: 3.3, w: 8.8, h: 0.85,
      fill: { color: COLOR.RULE_RED }, line: { color: COLOR.RULE_RED },
    });
    s.addText("90 days. If no traffic flows back to you, you're out. No hard feelings.", {
      x: 0.8, y: 3.3, w: 8.4, h: 0.85,
      fontFace: FONT_HEAD, italic: true, fontSize: 16, color: COLOR.CREAM, bold: true,
      align: "center", valign: "middle", margin: 0,
    });

    // Sign-off
    s.addText("Keep everything you have. Add what none of us can reach alone.", {
      x: 0.6, y: 4.35, w: 9, h: 0.4,
      fontFace: FONT_HEAD, italic: true, fontSize: 15, color: COLOR.CREAM,
      align: "center", margin: 0,
    });
    s.addText("TREVOR SLETTE  ·  JOHN DRAPER  ·  NATE GROEBNER", {
      x: 0.6, y: 4.85, w: 9, h: 0.3,
      fontFace: FONT_BODY, fontSize: 10, color: COLOR.GOLD, charSpacing: 4, bold: true,
      align: "center", margin: 0,
    });
  }

  await pres.writeFile({ fileName: "docs/publisher_pitch_deck.pptx" });
  console.log("Wrote docs/publisher_pitch_deck.pptx");
}

build().catch(e => { console.error(e); process.exit(1); });
