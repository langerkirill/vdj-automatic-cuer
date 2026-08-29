---
name: Music Sorter
description: Night-pool hi-fi cassette deck for cueing and filing tracks.
colors:
  bg: "#0a1214"
  bg-elevated: "#121614"
  text: "#f3ead6"
  muted: "#c9bba0"
  accent: "#ffb84a"
  pool: "#3ecfc8"
  peach: "#f0a07a"
  wood: "#563c2d"
  chrome: "#c5cdd4"
  good: "#7dffc2"
  bad: "#ff6b5a"
typography:
  display:
    fontFamily: "Libre Bodoni, Bodoni MT, Didot, serif"
    fontSize: "36px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "0"
  body:
    fontFamily: "Barlow Condensed, Avenir Next, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 800
    letterSpacing: "0.02em"
  label:
    fontFamily: "Barlow Condensed, sans-serif"
    fontSize: "15px"
    fontWeight: 800
    letterSpacing: "0.04em"
  mono:
    fontFamily: "Share Tech Mono, SF Mono, ui-monospace, monospace"
    fontSize: "16px"
    letterSpacing: "0.08em"
rounded:
  sm: "3px"
  md: "4px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "24px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#1a0e04"
    rounded: "{rounded.sm}"
    padding: "9px 14px"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
---

# Design System: Music Sorter

## Overview

**Creative North Star: "The Pool Deck"**

A Nakamichi-style cassette faceplate sitting at a Palm Springs pool after sunset. The operator cues tracks on warm walnut and black brushed aluminum while teal water and peach stucco glow behind the glass. VHS is the clock language, not a sticker pack.

**Key Characteristics:**
- Walnut side rails, black night faceplate or linen day faceplate, amber VFD
- Waveform lives in a smoked cassette window
- Ghost modes; the live mode is struck amber
- Song titles in Italiana; deck labels in condensed sans
- Palm silhouettes, slow caustics, pool photograph. No VHS snow.

## Colors

Night water, peach dusk, amber strike. Cue marker colors stay VirtualDJ-literal.

### Primary
- **VFD Amber** (#ffb84a): struck selection, PLAY key, times, the Sort CTA.

### Neutral
- **Pool night** (#0a1214): field.
- **Faceplate** (#121614): panels.
- **Paper ivory** (#f3ead6): titles.
- **Sand mute** (#c9bba0): meta.

### Named Rules
**The Struck Rule.** Unselected options sit as dim ghosts. The live control is the only thing that glows.

**The Cue Color Rule.** Blue / green / purple / yellow / orange still mean melodic / drums / vocals.

## Typography

**Display Font:** Libre Bodoni 700 (song titles)
**Body Font:** Barlow Condensed 800
**Label/Mono Font:** Share Tech Mono (VCR times, badges)

### Named Rules
**The Twelve Rule.** Functional text never drops below 12px.
**The Nameplate Rule.** Deck chrome (modes, buttons) is condensed uppercase. The track title is the only serif, and it is never all-caps.

## Layout

Walnut 16px cheeks, then crate / cassette window / destination rail. Waveform remains on the first screen of Add Cues.

## Elevation & Depth

Tungsten lamp: inset highlight plus a real offset shadow. No neon halo. Cassette well is inset smoked plastic.

## Shapes

Squared hi-fi: 3–4px on controls, 6px on the cassette window. No pills except transport is rectangular keys.

## Components

### Buttons
Chunky cassette keys. Primary is amber fill with a 2px physical lip. Ghost is a dark key with a chrome hairline.

### Waveform
Signature object: smoked cassette well, scanlines, amber LED ticks along the bottom.

### Navigation
Segmented like a source selector. Inactive is ghost; active is struck amber.

## Do's and Don'ts

### Do:
- **Do** keep the waveform inside the cassette window.
- **Do** use Libre Bodoni 700 only on the now-playing title.
- **Do** let the pool photograph stay behind a wash so the task remains readable (dusk wash at night, linen wash by day).

### Don't:
- **Don't** vaporwave this into purple neon.
- **Don't** put scanlines on body copy.
- **Don't** round the deck into 16px blobs.
- **Don't** hide cue colors under the theme accent.
