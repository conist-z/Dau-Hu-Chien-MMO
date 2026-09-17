// Compiles Kaetram-Open's ORIGINAL game SCSS (cloned in ../_kaetram_ref)
// into plain CSS for our web client — same technique as Kaetram's own
// astro.config.ts: custom sass functions width()/height() read the real
// PNG sizes so the sprite sheets are sliced EXACTLY like the original game.
//
// The compiled file is committed as web_client/src/kaetram_ui.css and
// imported by index.html. Asset URLs are rewritten:
//   /img/interface/  -> /ui/kaetram/interface/
// Font families (Fibberish, KerrieFont, Monogram, AdvoCut) are mapped to
// fonts bundled under /fonts/ (see styles.css @font-face).
import * as sass from "sass";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(__dirname, "../../_kaetram_ref/packages/client");
const IMG = path.join(REPO, "public/img/interface");
const OUT = path.resolve(__dirname, "../src/kaetram_ui.css");

// The full original game stylesheet (game/_index.scss already @uses all
// impl files: buttons, slice, quests, achievements, settings, leaderboards,
// bank, crafting, trade, equipments, map, profile, guilds, friends...).
const ENTRY = path.join(REPO, "scss/game/_index.scss");

// Media queries Kaetram defines via @custom-media — sass doesn't process
// those, so we pre-inject them as plain CSS custom media emulation. Their
// scss uses `@media (--portrait)` inside impl files; we register the same
// custom media via postcss would be needed. Simplest: replace the tokens
// in the source before compiling.
function preprocess(src) {
  return src
    .replaceAll("@media (--portrait)", "@media (orientation: portrait)")
    .replaceAll("@media (--lg)", "@media (min-width: 1501px)")
    .replaceAll("@media (--md)", "@media (max-width: 1500px), (max-height: 870px)")
    .replaceAll("@media (--sm)", "@media (max-width: 1000px)");
}

const imageCache = new Map();
function getImageSize(image) {
  if (!imageCache.has(image)) {
    const p = path.join(IMG, `${image}.png`);
    const buf = fs.readFileSync(p);
    // PNG: width @ 16..20, height @ 20..24 (big endian)
    imageCache.set(image, {
      width: buf.readUInt32BE(16),
      height: buf.readUInt32BE(20),
    });
  }
  return imageCache.get(image);
}

const result = sass.compile(ENTRY, {
  loadPaths: [path.join(REPO, "scss"), path.join(REPO, "scss/game")],
  functions: {
    "width($image)"([image]) {
      return new sass.SassNumber(getImageSize(image.text).width);
    },
    "height($image)"([image]) {
      return new sass.SassNumber(getImageSize(image.text).height);
    },
  },
  importers: [
    {
      findFileUrl(url) {
        // Allow the raw scss tree to resolve its own relative @use paths.
        return null;
      },
    },
  ],
  quietDeps: true,
});

let css = result.css;
css = css.replaceAll("/img/interface/", "/ui/kaetram/interface/");

// ---- SCOPE ----
const GLOBAL_SELECTORS = [
  /^html, body$/, /^body, div,/, /^body, button,/, /^h1, h2, h3,?$/,
  /^canvas$/, /^::-webkit-scrollbar/, /^select$/, /^button, html \[type=button\]/,
  /^audio:not\(\[controls\]\)$/, /^\[hidden\]$/, /^b, strong$/, /^i, em$/,
];

// Parse top-level blocks and filter/scope them.
function transformCss(source) {
  let out = "";
  let i = 0;
  const n = source.length;
  while (i < n) {
    // find selector block or at-rule
    const braceStart = source.indexOf("{", i);
    if (braceStart === -1) break;
    const selector = source.slice(i, braceStart).trim();
    // find matching close brace
    let depth = 1, j = braceStart + 1;
    while (j < n && depth > 0) {
      if (source[j] === "{") depth++;
      else if (source[j] === "}") depth--;
      j++;
    }
    const body = source.slice(braceStart + 1, j - 1);

    if (selector.startsWith("@media") || selector.startsWith("@supports")) {
      // recurse into the at-rule body
      out += `${selector} {${transformCss(body)}}\n`;
    } else if (selector.startsWith("@")) {
      // keyframes / font-face / custom-media — keep as-is
      out += `${selector} {${body}}\n`;
    } else {
      const sel = selector.replace(/\s+/g, " ").trim();
      const isGlobal = GLOBAL_SELECTORS.some((re) => re.test(sel)) ||
        /^body$/.test(sel) || /^html$/.test(sel) || /^\*$/.test(sel);
      if (!isGlobal) {
        // Scope every comma-separated selector under #hud-hub.
        const scoped = sel
          .split(",")
          .map((s) => {
            s = s.trim();
            if (!s) return s;
            if (s.startsWith("#hud-hub")) return s;
            // keyframes percentage selectors or bare tokens — leave
            if (/^(from|to|\d+%|\d+\.\d+%)$/.test(s)) return s;
            return `#hud-hub ${s}`;
          })
          .join(", ");
        out += `${scoped} {${body}}\n`;
      }
    }
    i = j;
    // skip whitespace between blocks
    while (i < n && /\s/.test(source[i])) i++;
  }
  return out;
}

css = transformCss(css);

// The bar itself IS inside #hud-hub; font families for scoped elements come
// from their own rules. Re-add a minimal font scope so the Kaetram pages
// render with their own typefaces without touching the rest of the app.
css = `#hud-hub { font-family: Monogram, "AdvoCut Fallback", arial, sans-serif; }
#hud-hub h1, #hud-hub h2, #hud-hub h3 { font-family: Fibberish, Monogram, AdvoCut, arial, sans-serif; }
${css}`;

// Also compile the BASE layer for its @font-face declarations ONLY —
// the base reset (body/button/h1 global rules) is deliberately NOT merged
// because it would restyle our own HUD; fonts are what the game scss needs.
const BASE = path.join(REPO, "scss/base/impl/_fonts.scss");
const fontResult = sass.compile(BASE, { quietDeps: true });
const fontCss = fontResult.css;

// ---- VISUAL FIDELITY LAYER ----
// The game's look depends on base/_utils.scss (.stroke pixel text-shadow,
// .text-green/.text-yellow/.text-red, .row/.col utilities) and
// app/_check.scss (sprite checkbox). These define classes used INSIDE the
// pages, so we compile them and scope them to #hud-hub like everything
// else — original look, zero leak.
function scopedStyles(source) {
  return transformCss(source.replaceAll("/img/interface/", "/ui/kaetram/interface/"));
}
const utilsResult = sass.compile(path.join(REPO, "scss/base/impl/_utils.scss"), { quietDeps: true });
// defaults.scss: div/ul flex-column default + heading sizes — the quests /
// settings internals depend on this stack. Elements are scoped, [hidden]
// variant kept so the original hidden-attribute toggling still works.
const defaultsResult = sass.compile(path.join(REPO, "scss/base/impl/defaults.scss"), { quietDeps: true });
const checkResult = sass.compile(path.join(REPO, "scss/app/impl/_check.scss"), {
  loadPaths: [path.join(REPO, "scss"), path.join(REPO, "scss/app")],
  functions: {
    "width($image)"([image]) {
      return new sass.SassNumber(getImageSize(image.text).width);
    },
    "height($image)"([image]) {
      return new sass.SassNumber(getImageSize(image.text).height);
    },
  },
  quietDeps: true,
});
// app/_check.scss references app/_index context; compile standalone works
// because it only @uses abstracts/sprite.
const fidelity = [
  scopedStyles(defaultsResult.css),
  scopedStyles(utilsResult.css),
  scopedStyles(checkResult.css),
].join("\n");

fs.writeFileSync(
  OUT,
  `/* GENERATED from Kaetram-Open scss (MPL-2.0) by scripts/compile_kaetram_css.mjs — do not edit by hand. Re-run: node scripts/compile_kaetram_css.mjs */\n${fontCss}\n${css}\n/* ---- base utils + check sprite, scoped ---- */\n${fidelity}`
);
console.log(`merged font-faces (${fontCss.length}) + game (${css.length}) + utils/check (${fidelity.length})`);
