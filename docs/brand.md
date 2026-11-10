# Brand

StormIQ is a Livestorm-adjacent tool: it uses Livestorm's design language (colours, radii, voice) and should feel native to the Livestorm ecosystem, but it has its own name and mark and is not a Livestorm product.

Two designs live in the app while the redesign ships screen by screen:

- **v1** (the redesign, opt-in): described first, below. New UI is built only with it.
- **Classic** (what users see by default): the [Livestorm brand reference](#livestorm-brand-reference-classic-design) further down, implemented in `frontend/src/styles.css`.

---

## The v1 design

### One design

StormIQ has one design, this one, for everyone: there is no switch and no classic layout. The app shows the top bar; the pages not rebuilt yet (the session's older tabs, Admin, Cross Analysis) appear unchanged inside a dark frame until each is rebuilt. `/design` shows every token and component in the current theme, with fictional sample content; it is not indexed.

### Typeface: Sora

Sora (SIL Open Font License 1.1) is the free typeface closest to Object Sans: at 400 it has the same line width, and unlike Object Sans it has tabular figures, so tables and counters line up. One variable file, `frontend/public/fonts/Sora-Variable-latin.woff2` (weights 100–800, Latin, Latin-1, Œ/œ and €, so every French character), with its licence in `Sora-OFL.txt` beside it. Stack: `"Sora", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif` (`--siq-font`). Use weights 400, 500 and 600 only. The classic design keeps its own font stack.

### Colour tokens

Every colour is a CSS custom property in `frontend/src/design/tokens.css`, named `--siq-<role>`. Components use roles, never raw colours. Light is the default; dark applies with `data-theme="dark"` on `<html>`, or when the system prefers dark and the user hasn't chosen light. Text roles pass WCAG 4.5:1 on the surfaces they're used on; borders, focus rings and meaningful fills pass 3:1.

| Role | Used for | Light | Dark |
|---|---|---|---|
| `page` | page background | `#F6F8F9` | `#091316` |
| `card` | content cards | `#FFFFFF` | `#0F1B1F` |
| `card-shadow` | card elevation, with a 1px `hairline` inset | `0 1px 2px rgba(13,22,38,0.04), 0 4px 12px rgba(13,22,38,0.05)` | `0 0 0 0 transparent`: no shadow, the hairline carries the edge. Never `none`: components write `var(--siq-card-shadow), inset 0 0 0 1px …`, and `none` inside a list makes the whole declaration invalid, so the hairline would go too |
| `card-hover` | hover rows inside a card | `#F6F8F9` | `#182427` |
| `ground` | chart backgrounds off a card | `#FFFFFF` | `#091316` |
| `surface` | panels, popovers, sunken areas | `#F6F8F9` | `#0F1B1F` |
| `surface-2` | tracks, count badges, hover on controls | `#EAEEF1` | `#182427` |
| `topbar` | top bar (plus a bottom hairline) | `#FFFFFF` | `#0F1B1F` |
| `input` | field backgrounds | `#FFFFFF` | `#0F1B1F` |
| `selected-bg` / `selected-ink` | icon tiles, avatar, selected segment | `#F0F4FF` / `#0B42C3` | `#1C2A2E` / `#FFFFFF` |
| `ink` | headings, body | `#12262B` | `#FFFFFF` |
| `ink-2` | secondary text, inactive nav and tabs | `#4D5B66` | `#CED0D0` |
| `muted` | captions, labels, placeholders | `#5D6D79` | `#A4B5C1` |
| `border` / `border-hover` | control borders | `#8094A3` / `#5D6D79` | `#5D6D79` / `#CED0D0` |
| `hairline`, `hairline-row`, `hairline-head` | dividers, table rows, table headers | `rgba(18,38,43,0.10 / 0.08 / 0.14)` | `rgba(255,255,255,0.08 / 0.06 / 0.12)` |
| `nontext` | rings, empty tracks | `#8094A3` | `#8094A3` |
| `cta` / `cta-hover` / `cta-ink` | primary button | `#0B42C3` / `#05299E` / white | `#477580` / `#335D67` / white |
| `accent` / `accent-hover` | links, active tab and nav indicator | `#0B42C3` / `#05299E` | `#88C0CD` / `#B7D9E1` |
| `focus` | focus ring (2px, offset 3px) | `#0B42C3` | `#88C0CD` |
| `notice` | quiet notices, the "Next time" tip bar | `#F0F4FF` | `rgba(136,192,205,0.08)` |
| `good` / `good-fill` / `good-tint` | status text / marks / backgrounds | `#0C7C59` / `#17A679` / `#F3FCFB` | `#59D4C0` / `#2EB89A` / `rgba(46,184,154,0.14)` |
| `fair` / `fair-fill` / `fair-tint` | | `#916308` / `#D3A20D` / `#FFF4D1` | `#FED348` / `#FED348` / `rgba(254,211,72,0.14)` |
| `low` / `low-fill` / `low-tint` | | `#BE3A37` / `#BE3A37` / `#FDF7F7` | `#EB9E9F` / `#E26F71` / `rgba(226,111,113,0.14)` |
| `s1`, `s2`, `s3`, `other` | chart series: audience, questions, host team, reference and "Other" | `#0B54FE`, `#DE7835`, `#0C7C59`, `#A4B5C1` | `#447CFD`, `#CB4A1A`, `#0C7C59`, `#8094A3` |
| `logo-on-light` / `logo-on-dark` | which Livestorm logo file the lockup shows (`display` values) | `block` / `none` | `none` / `block` |
| `lockup-ink` / `lockup-rule` | the lockup's "for" and its divider | `muted` / `hairline-head` | `muted` / `hairline-head` (classic frame: `#A4B5C1` / `rgba(255,255,255,0.12)`) |
| `mark-fill` / `mark-iq` / `mark-ink` | the "IQ" monogram's disc, the wordmark's "IQ", the monogram's letters | `#0B42C3` / `#0B42C3` / white | `#447CFD` / `#88C0CD` / white |
| `ridge-fill`, `img-outline` | the session ridge, outlines on images | `rgba(18,38,43,0.07)`, `rgba(0,0,0,0.1)` | `rgba(255,255,255,0.10)`, `rgba(255,255,255,0.1)` |

Rules: on light surfaces CTAs and links are Livestorm Blue; on dark surfaces CTAs are Winter Green 700. The light `fair-fill` is below 3:1 on white, so a status fill never stands alone: it always has its value and an icon + word beside it. Charts use at most three series plus "Other", with direct labels; text is never in a series colour. An entity keeps its colour everywhere.

### Type, shape, space, motion

| Token | Size / line height | Used for |
|---|---|---|
| `--siq-text-display` | 40/48, 400 | page and session titles, big numbers in stat cards |
| `--siq-text-headline` | 28/36 | finding headlines |
| `--siq-text-section` | 24/32 | section titles; a finding's headline inside a card |
| `--siq-text-stat` | 32/40 | stat callouts |
| `--siq-text-card-title` | 18/24, 500 | card titles |
| `--siq-text-body` | 18/30 | summaries |
| `--siq-text-lead` | 16/24 | lead lines |
| `--siq-text-ui` | 14/20 | UI text |
| `--siq-text-small` | 13/18 | labels, sub-lines |
| `--siq-text-caption` | 12/16 | captions, source lines; uppercase labels with `--siq-label-tracking` |
| `--siq-text-nav` | 15/20, 500 | top-bar navigation |
| `--siq-text-tag` | 11/16, 500 | the "Beta" tag |
| `--siq-text-reading-lh` | 22 | line height for 14px text read as prose ("How we know") |

Numbers that line up use `font-variant-numeric: tabular-nums`; headings use `text-wrap: balance`. Radii: 12px controls (`--siq-radius-control`), 10px icon tiles, 16px cards, 999px chips, 6px around inline links and timestamps (`--siq-radius-inline`, so their focus ring is rounded). Spacing steps: 4, 8, 12, 16, 20, 24, 32, 48, 64 (`--siq-space-*`). The main container is 1200px wide with 32px gutters (16px on phones). Anything clickable is at least 40px tall (`--siq-target`), timestamps in evidence rows included. Transitions name their properties (colour, background, border, shadow, opacity) over 200ms `cubic-bezier(0,0,0.2,1)`, never `transition: all`, and drop to 0ms under `prefers-reduced-motion`.

### Cards, not walls of text

Users read long reports, so the v1 screens lean on cards, icons and collapsible sections:

- **Cards** (`Card`): `card` background, 16px radius, `card-shadow` plus a 1px `hairline` inset, 24px padding (32px for the page's lead card). 16–24px between cards. Never a card inside a card: inside one, separate parts with a hairline and space.
- **Card header** (`CardHeader`): a 36×36 icon tile (`selected-bg`, glyph in `selected-ink`), the title (18/24, 500), and on the right a status word, a count badge or actions.
- **Stat cards** (`StatCard`): icon tile, label, one big tabular number, a one-line sub-line (two lines on phones), and an info button that opens how the number is computed, with its status word. 24px padding, like every card.
- **Collapsible sections** (`Collapsible`): real `<details>`/`<summary>`. As a card, the summary row is the card header plus a one-line takeaway and a key-number chip; a chevron points down when closed and up when open. On phones the takeaway gets two lines and the chip moves under it. As a link ("How we know", "Evidence (3)"), it is a quiet accent toggle. The card title is a heading inside `<summary>`; some screen readers flatten a summary's content, so the title may be missing from heading navigation there (it is still announced as the toggle's name). Where the page outline matters, put a section heading above a group of collapsibles.
- **A card that asks for your check** (`Card tone="fair"`): `fair-tint` background; the icon tile and the "Needs your check" pill inside it sit on the `card` colour so they keep their shape.
- **Text budget**: no visible paragraph longer than two lines on a report's front page; anything longer goes into a collapsible. Prefer stat callouts (a big number and a short label) and icon bullets (icon + one line) to sentences full of numbers.
- **"Next time"** (`TipBar`): `notice` background, 12px radius, a target icon and one line.
- **Status words** (`StatusWord`), always icon + word, never colour alone: **Measured** (check, `good`), **Estimated** (info, `muted`), **Needs your check** (shield, `fair` on `fair-tint`).
- **Chips and badges** (`Chip`): small pills, outlined (`neutral`), count badges (`count`), key numbers (`accent`) and status tints.
- **Buttons** (`UiButton`): primary (`cta`, one per view), secondary (outlined), ghost (accent text) and icon-only (40×40, with an `aria-label`).

### Icons

Livestorm's icon font is proprietary and can't ship in this public repository, so v1 uses [Lucide](https://lucide.dev) (ISC licence; portions from Feather, MIT): a stroke icon set close to Livestorm's in weight and shape. The icons are vendored as SVG data in `frontend/src/components/ui/Icon.vue` (no runtime dependency), with the licence in `LICENSE-lucide.txt` beside it. Icons inherit `currentColor` and are decorative (`aria-hidden`) unless given a label. One meaning per icon, used the same way everywhere:

| Meaning | Icon | Meaning | Icon |
|---|---|---|---|
| questions | `message-circle` | chapters | `list` |
| open question | `help-circle` | the record | `file-text` |
| audience | `users` | "Next time" | `target` |
| show-up | `user-check` | evidence, timestamp | `play` |
| time in the room, wait gap | `timer` | method | `settings` |
| the moment, a surge | `zap` | checked, Measured | `check` |
| the Pulse | `activity` | Estimated, definitions | `info` |
| channels | `share-2` | download / copy | `download` / `copy` |
| lead time, sessions | `calendar` | Surprise Me! | `star` |
| segments | `pie-chart` | wrote in chat or Q&A | `pen-line` |
| the room | `presentation` | Needs your check | `shield` |
| view as table | `table` | | |

Plus the UI basics: chevrons, `x`, `plus`, `search`, `lock`, `menu`, `refresh-cw`, `external-link`, `more-horizontal`, `log-in`, `log-out`, `sun`, `moon`.

### Light and dark mode

The page follows the system (`prefers-color-scheme`) until the user clicks the theme toggle in the top bar; that saves an explicit choice in the browser (`stormiq:theme`), which wins over the system until cleared. The toggle is a 40×40 icon button whose label names the action ("Switch to dark mode" / "Switch to light mode"). An inline script in `index.html` applies a saved choice before first paint, so there is no flash. `color-scheme` follows the theme so form controls and scrollbars match. Images keep their colours in both modes, with the `img-outline`. Printing and PDF exports stay light, even after an explicit dark choice.

In Windows High Contrast (`forced-colors: active`) the browser removes box-shadows, which v1 uses for card edges, the active tab and the account menu, so each component draws real borders there instead (`CanvasText`, and `Highlight` for the current page or tab).

### The "StormIQ for Livestorm" lockup

The headers show StormIQ as a co-brand: the wordmark "Storm" + "IQ" (no disc), the "Beta" tag in the v1 top bar, then a 1px divider, the word "for" and the official Livestorm logo (`ForLivestorm.vue`). In the classic sidebar the wordmark sits above "for" and the logo, without the divider. PDFs are headed by the same lockup, with the on-light logo (`pdf.py`, `livestorm_app/assets/livestorm-logo-on-light.png`, rendered unchanged from the kit's SVG; PDFs are always light). Spots that only fit a square show the "IQ" monogram instead (below).

The logo files in `frontend/src/assets/livestorm/` are the brand kit's own (brand.livestorm.co), with only a `viewBox` added so they scale: `logo-on-light.svg` (Winter Green) on light surfaces, `logo-on-dark.svg` (white) on dark ones. The `logo-on-light` and `logo-on-dark` tokens pick the file, so it follows the theme, and the classic frame always shows the on-dark file. Follow Livestorm's logo rules: a solid surface behind it, clear space around it, never stretched, recoloured or shown without its box, and never replaced by "Livestorm" set in type. The lockup says what StormIQ is for; it never makes StormIQ look like a Livestorm product, so the "not an official Livestorm product" notice stays.

### The "IQ" monogram and the wordmark

The wordmark is "Storm" + "IQ" (IQ in `mark-iq`), Sora 600 at 18/24; it opens the lockup. Where only a square fits, StormIQ shows its monogram: a circle (`mark-fill`) with the wordmark's own "I" and "Q" in white (`mark-ink`): Sora 600, outlined, 28×28 units, drawn from the font the app ships by `scripts/make_favicon.py`, which writes `favicon.svg`, `favicon.ico` and `apple-touch-icon.png` and prints the letters' path for `StormIqMark.vue`. It is a circle on purpose, so it never echoes Livestorm's rounded-square box. Both are drawn by `StormIqMark.vue` (the monogram in the collapsed classic sidebar, the compact top bar and `/design`; the wordmark alone, `:mark="false"`, in the lockup) and the monogram by `frontend/public/favicon.svg` as the favicon, with `favicon.ico` (16, 32, 48 px) and `apple-touch-icon.png` (180 px on white) rendered from that file for browsers and home screens that don't take SVG. The classic design is dark only, so there they always use their on-dark colours (`#447CFD` disc, `#88C0CD` IQ) whatever the theme. Neither is ever the Livestorm logo or icon.

---

## Livestorm brand reference (the older pages)

> Source: https://brand.livestorm.co
> The pages not rebuilt yet (`frontend/src/styles.css`, shown in the dark frame) follow this reference. Do not invent colors, fonts, or styles there; use only what is defined here. The v1 design above takes its values from the same palette.

---

## Typography

**Font family: Object Sans**
- A contemporary sans serif combining Swiss neo-grotesque and geometric qualities
- Import via: `https://use.typekit.net` or self-host if licensed — fallback to `Inter` only if unavailable
- Tailwind config: set `fontFamily.sans` to `['Object Sans', 'Inter', 'sans-serif']`

**Type scale**

| Token | Size | Line height | Usage |
|---|---|---|---|
| `text-h1` | 60px / 3.75rem | 80px / 5rem | Page heroes |
| `text-h2` | 48px / 3rem | 64px / 4rem | Section titles |
| `text-h3` | 32px / 2rem | 40px / 2.5rem | Card headers |
| `text-h4` | 24px / 1.5rem | 32px / 2rem | Sub-headers |
| `text-h5` | 20px / 1.25rem | 28px / 1.75rem | Labels, emphasis |
| `text-body` | 18px / 1.125rem | 28px / 1.75rem | Body copy |
| `text-subtitle` | 14px / 0.875rem | 18px / 1.125rem | Captions, meta, badges |

---

## Colors

### Core palette

| Name | HEX | Usage |
|---|---|---|
| Livestorm Blue | `#0B42C3` | Primary CTA, links, focus rings, active states |
| Winter Green | `#12262B` | Dark backgrounds, nav, deep text |
| White | `#FFFFFF` | Backgrounds, text on dark |

### Secondary palette

| Name | HEX | Usage |
|---|---|---|
| Spring Green | `#0C7C59` | Success states, positive badges |
| Lightning Yellow | `#F8F32B` | Highlights, accent (use sparingly) |
| Sandstorm Yellow | `#FED348` | Warnings, secondary accent |
| Live Red | `#943124` | Destructive actions, error states |
| Sunburst Red | `#FAC9B8` | Error backgrounds, soft alerts |
| Sirocco | `#EFBC9B` | Warm accents, illustrations |

### Livestorm Blue shades

| Shade | HEX | Usage |
|---|---|---|
| Blue 50 | `#F0F4FF` | Hover backgrounds, selected state bg |
| Blue 100 | `#DCE6FE` | Light badges, tinted surfaces |
| Blue 200 | `#B3CAFE` | Borders on blue surfaces |
| Blue 300 | `#7BA2FE` | Secondary blue elements |
| Blue 400 | `#447CFD` | — |
| Blue 500 | `#0B54FE` | — |
| **Blue 600** | **`#0B42C3`** | **Primary — main brand blue** |
| Blue 700 | `#05299E` | Pressed/active state |
| Blue 900 | `#051752` | Deep blue text |

### Grey (Nimbus Grey)

| Shade | HEX | Usage |
|---|---|---|
| Grey 50 | `#F6F7F9` | Page backgrounds |
| Grey 100 | `#EAEEF1` | Card backgrounds, dividers |
| Grey 200 | `#C8D3DA` | Borders, disabled |
| Grey 300 | `#A4B5C1` | Placeholder text |
| Grey 400 | `#8094A3` | Secondary text |
| Grey 500 | `#5D6D79` | Body text secondary |
| Grey 600 | `#4D5B66` | — |
| Grey 700 | `#3F4950` | — |
| Grey 900 | `#232B2F` | Near-black text |

### Spring Green shades (success)

| Shade | HEX |
|---|---|
| 50 | `#F3FCFB` |
| 100 | `#DFF7F3` |
| 500 | `#2EB89A` |
| **600** | **`#0C7C59`** |
| 900 | `#032B1F` |

### Live Red shades (error/destructive)

| Shade | HEX |
|---|---|
| 50 | `#FDF7F7` |
| 400 | `#DA494B` |
| **600** | **`#943124`** |
| 900 | `#2D0F0B` |

---

## Tailwind config mapping

Add to `tailwind.config.ts`:

```ts
colors: {
  brand: {
    blue: '#0B42C3',
    'blue-light': '#DCE6FE',
    'blue-dark': '#05299E',
    green: '#0C7C59',
    'green-light': '#DFF7F3',
    yellow: '#FED348',
    red: '#943124',
    'red-light': '#FAC9B8',
    dark: '#12262B',
  },
  nimbus: {
    50: '#F6F7F9',
    100: '#EAEEF1',
    200: '#C8D3DA',
    300: '#A4B5C1',
    400: '#8094A3',
    500: '#5D6D79',
    700: '#3F4950',
    900: '#232B2F',
  }
}
```

---

## Component patterns

### Buttons
- **Primary**: `bg-brand-blue text-white hover:bg-brand-blue-dark` — use for main CTAs only
- **Secondary**: `border border-brand-blue text-brand-blue hover:bg-brand-blue-light`
- **Ghost**: `text-nimbus-500 hover:text-nimbus-900 hover:bg-nimbus-50`
- **Destructive**: `bg-brand-red text-white`
- Border radius: `rounded-lg` (8px)
- Font weight: `font-medium`

### Cards
- Background: `bg-white` on grey page, `bg-nimbus-50` on white page
- Border: `border border-nimbus-100`
- Radius: `rounded-xl` (12px)
- Shadow: `shadow-sm`

### Badges / Pills
- Success: `bg-green-light text-brand-green`
- Warning: `bg-yellow-50 text-amber-700`
- Error: `bg-red-light text-brand-red`
- Info: `bg-brand-blue-light text-brand-blue-dark`
- Font size: `text-subtitle` (14px), `font-medium`

### Inputs
- Border: `border border-nimbus-200 focus:border-brand-blue focus:ring-1 focus:ring-brand-blue`
- Radius: `rounded-lg`
- Background: `bg-white`
- Placeholder: `text-nimbus-300`

### Page backgrounds
- Default page: `bg-nimbus-50`
- Sections / cards: `bg-white`
- Dark surfaces (nav, hero): `bg-[#12262B]` (Winter Green)

---

## Tone

- Professional, genuine, friendly — not corporate, not startup-casual
- Direct action-oriented copy: "Join your room", "Create room", "Save assignments"
- Avoid: "Please don't hesitate to...", "Feel free to...", "In order to..."
- Error messages should be helpful, not technical: "We couldn't find your registration — check your email address" not "404 registrant not found"

---

## Logo usage

- The official files, from Livestorm's brand kit (brand.livestorm.co), are in `frontend/src/assets/livestorm/`: `logo-on-dark.svg` (white, the Primary logo, on Winter Green or other dark surfaces) and `logo-on-light.svg` (Winter Green, the Secondary logo, on white or light surfaces)
- A solid surface behind it and clear space around it, at least the height of the storm
- Never stretch, recolour, or show the storm without its box, and never set "Livestorm" in type instead of the logo
- StormIQ shows it only in the "for Livestorm" lockup, never as its own logo

---

## What this app is NOT

- Not an official Livestorm product. The app's headers show the "StormIQ for Livestorm" lockup, with the official Livestorm logo after "for"; PDFs carry the same lockup. The Livestorm logo or icon is never StormIQ's own logo or app icon: square spots (the favicon) use the "IQ" monogram
- Use "StormIQ" as the app name
- The app should feel brand-adjacent, not brand-impersonating
- PDF exports are headed by the "StormIQ for Livestorm" lockup (`pdf.py`): the wordmark "Storm" + "IQ" (IQ in `#0B42C3`), a thin divider, "for" and the official on-light Livestorm logo. Their footer repeats the "not an official Livestorm product" notice (in French for French exports)
- No real person's photo or likeness in the UI: decorative visuals are CSS shapes or StormIQ illustrations (the Surprise Me! tab shows a CSS spotlight)
- Exported files are named `stormiq-…`, never `livestorm-…`
