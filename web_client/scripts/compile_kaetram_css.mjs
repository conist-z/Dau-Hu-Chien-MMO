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

// The entry @use's files directly — preprocess must run per-file, so if any
// --portrait tokens survived, fix them in the output (they'd error earlier).
css = css
  .replaceAll("/img/interface/", "/ui/kaetram/interface/")
  // Kaetram's reset hides cursors / sets absolute positioning for their
  // canvas stack — those rules are game-scoped under #game-container etc.
  // They are harmless for us but we keep them for fidelity.
  ;

fs.writeFileSync(OUT, `/* GENERATED from Kaetram-Open scss/game (MPL-2.0) by scripts/compile_kaetram_css.mjs — do not edit by hand. Re-run: node scripts/compile_kaetram_css.mjs */\n${css}`);
console.log(`compiled ${css.length} bytes -> ${OUT}`);

// Also compile the BASE layer (fonts + reset + utils) and prepend it —
// the game scss relies on its font-faces and box-sizing defaults.
const BASE = path.join(REPO, "scss/base/_index.scss");
const baseResult = sass.compile(BASE, {
  loadPaths: [path.join(REPO, "scss"), path.join(REPO, "scss/base")],
  quietDeps: true,
});
const fontCss = baseResult.css
  .replaceAll("/fonts/", "/fonts/")
  .replaceAll("url('/fonts/advocut/", "url('/fonts/advocut/");
fs.writeFileSync(
  OUT,
  `/* GENERATED from Kaetram-Open scss (MPL-2.0) by scripts/compile_kaetram_css.mjs — do not edit by hand. Re-run: node scripts/compile_kaetram_css.mjs */\n${fontCss}\n${css}`
);
console.log(`merged base fonts+reset (${fontCss.length}) + game (${css.length})`);
