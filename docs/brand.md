# Brand Reference — Livestorm

> Source: https://brand.livestorm.co
> This app is a Livestorm-adjacent tool and should feel visually native to the Livestorm ecosystem.
> Do not invent colors, fonts, or styles. Use only what is defined here.

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

- White logo on dark backgrounds (`#12262B` or `#0B42C3`)
- Blue logo on light backgrounds
- Never stretch, recolor, or place on clashing backgrounds
- Logo SVG: `https://cdn.prod.website-files.com/60ad0f9314e628baa6971a76/60c70e483f9c48981582e18b_Logo-Livestorm-2021-White.svg`

---

## What this app is NOT

- Not an official Livestorm product — do not use the Livestorm logo as the app logo
- Use "StormIQ" as the app name
- The app should feel brand-adjacent, not brand-impersonating
