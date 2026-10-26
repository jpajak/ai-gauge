# Plan: let Settings pages scroll instead of clipping

**Status:** built in 3b0b6c5 (unreleased). The mouse wheel over combo and spin boxes (see Risks) is not handled.
**Found while:** moving the provider enable checkboxes onto their own pages
(2026-10-08)
**Size:** one commit

## Problem

A Settings page that is shorter than its content does not scroll. Qt squeezes
the word-wrapped hint labels instead, and their text is cut off top and bottom.

The GitHub Copilot page is the tallest. With Segoe UI at 100% it needs 523 px
of page height. Adding the enable checkbox and its hint cost 52 px, which
clipped three hints at the old default dialog size of 800 x 620. The default
went to 800 x 680 to make room, which leaves about 20 px of slack.

That is a fix for one font at one size. The clipping returns when:

- the dialog is resized below 680 px high (the minimum is 420),
- the font is wider than Segoe UI, as on macOS, Linux, or the offscreen
  platform CI uses, where the same page needs 632 px,
- a provider page gains another row.

## Current wiring

- Default and minimum size: `src/aigauge/settings_dialog.py:798-799`.
- The six provider pages, General and MCP are plain `QWidget`s with a
  `QVBoxLayout`, built at `settings_dialog.py:1250-1311`, and go into the
  `QStackedWidget` as they are.
- Hints are `QLabel`s with word wrap on: `_hint_label` at
  `settings_dialog.py:325`. A wrapped label reports a `minimumSizeHint` far
  below the height it needs at its real width (Copilot page: 380 px reported,
  523 px needed), so the layout never refuses the squeeze.
- The Local usage page already avoids this. It sits in a `QScrollArea` with
  `setWidgetResizable(True)` and no frame: `settings_dialog.py:1374`.
- `page_widget(key)` at `settings_dialog.py:1575` returns whatever was added
  to the stack. Tests use it with `isAncestorOf` and `findChild`, both of which
  still hold when the page is wrapped.

## Recommended approach

Wrap every page in a `QScrollArea` the way Local usage is wrapped, through one
small helper so the pages are built the same way. A resizable scroll area asks
the inner layout for its height-for-width, so the page gets the height its
wrapped labels need and a vertical scrollbar appears only when the viewport is
shorter than that.

Leave the default size at 800 x 680 so the common case shows no scrollbar.

## Alternatives considered

- **Keep raising the default height.** Rejected: it tracks one font, and
  800 x 680 is already close to what a 768 px tall screen can show.
- **Give each hint a minimum height from `heightForWidth`.** Rejected: it has
  to be recomputed on every resize for every label, and it stops the dialog
  shrinking instead of letting it scroll.
- **Shorten the Copilot hints.** Rejected: it moves the threshold without
  removing it, and the text is there because users needed it.

## Risks

- The scroll area's viewport paints its own background. Check it matches the
  page colour under the dark stylesheet, as Local usage does.
- The horizontal scrollbar should stay off, or a long unbroken string such as
  the MCP command line could make a page scroll sideways.
- Focus and wheel behaviour inside combo boxes and spin boxes on a scrolling
  page: a wheel turn over a combo box changes its value rather than scrolling.

## Test strategy

- Unit: with the dialog at its minimum height, the Copilot page's scroll area
  reports a vertical scrollbar maximum above zero.
- Compare the page's height with `layout().heightForWidth(width)` rather than
  with a pixel constant. The offscreen platform measures text wider than a
  desktop session, so a hardcoded height passes locally and fails on CI.
- Render check: grab the Copilot page at 800 x 680 and at 800 x 420 and read
  both images.
