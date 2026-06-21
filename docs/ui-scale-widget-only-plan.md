# Plan: confine "UI scale" to the widget only

**Status:** proposed (not started)
**Author:** design note for a future change
**Related:** the off-screen-at-high-DPI fix in `UsageWidget._clamp_to_visible_screen`
(separate, already shipped)

## Problem

The **UI scale** setting (Settings → General, default 100%, range 75–400%) is
applied through Qt's global `QT_SCALE_FACTOR` environment variable. That
variable is a **process-wide multiplier latched once when `QApplication` is
constructed**, so it scales *everything* Qt draws — not just the floating
widget, but also the Settings dialog, the tray right-click menu, message boxes,
and the sign-in window. There is no Qt mechanism to exempt a single top-level
window from `QT_SCALE_FACTOR`.

Two consequences users notice:

- The Settings dialog and tray menu render oversized when UI scale > 100% (and,
  separately, when the OS display scale is high — that part is normal and is
  **not** in scope here).
- Changing the UI scale requires an app **restart**, because `QT_SCALE_FACTOR`
  cannot be changed on a live `QApplication`.

We want UI scale to enlarge/shrink **only the widget**, leaving dialogs and menus
at their native (OS-driven) size, and ideally to apply **live** without a
restart.

> Out of scope: the OS display-scale (Windows 175%/200%) scaling of dialogs.
> That is correct, expected platform behavior and should not be defeated.

## Current wiring (what to unwind)

The `QT_SCALE_FACTOR` path touches these points:

- `config.py:209` `qt_scale_factor_env(config)` — turns `ui_scale` into the env
  string (or `None` at 1.0).
- `config.py:77` `WindowState.ui_scale` field (keep this — it stays the source of
  truth, just consumed differently).
- `app.py:1028` `_apply_ui_scale_env()` — sets `os.environ["QT_SCALE_FACTOR"]`
  before `QApplication` is built (`app.py:1057`).
- `app.py:801` `App.restart()` and `app.py:895` the "Restart to apply scale"
  prompt in `_on_settings_finished` — the restart dance exists *only* to re-latch
  `QT_SCALE_FACTOR`.
- `settings_dialog.py:421-442, 991-993` — the `ui_scale_combo` and
  `ui_scale_changed` flag. Keep the combo; the `ui_scale_changed`/restart
  handshake can be replaced by a live apply.

## The hard part: the widget is built from hardcoded pixels

The widget hardcodes pixel sizes in two forms, both of which must become
scale-aware:

1. **Inline Qt stylesheets** with `font-size:Npx`, `padding`, `border-radius`,
   `width`, e.g. `widget.py:570, 582, 718, 1068, 1217` and ~25 more. Grep:
   `grep -nE "px|font-size" src/aigauge/widget.py`.
2. **Numeric geometry calls** — `setFixedSize/Width/Height`, `setMinimumWidth`,
   `setPixelSize`, layout `setContentsMargins`/`setSpacing`, and the module
   constants:
   - `widget.py:60-63` `ROW_BAR_HEIGHT=8`, `PACE_TICK_OVERHANG=2`,
     `CHIP_NOTCH_HEIGHT=4`, `CHIP_NOTCH_HALF_WIDTH=3.5`
   - `config.py:18-21` `WINDOW_WIDTH=340`, `WINDOW_MIN_HEIGHT=80`,
     `WINDOW_MAX_HEIGHT=420`, `WINDOW_COLLAPSED_HEIGHT=58`
   - custom painters that take a `size` arg: `_render_refresh_pixmap`,
     `_SummaryChip` notch geometry, `_PaceProgressBar`/`_PaceTickOverlay`.

There is no single font/point size to bump — fonts are pinned in px precisely so
the compact layout stays tight, which is exactly why a naive `setFont` won't
work.

## Recommended approach: intrinsic scaling via two helpers

Keep the existing window/drag/scroll/paint architecture; make the **metrics**
scale-aware. This avoids the window-flag and hit-testing pitfalls of a
`QGraphicsView` wrapper (see Alternatives) and unlocks live re-scaling.

### 1. A scale value owned by the widget

```python
class UsageWidget(QWidget):
    def __init__(self, config, parent=None):
        self._scale = float(getattr(config.window, "ui_scale", 1.0) or 1.0)
        ...
    def px(self, n: float) -> int:
        return max(1, round(n * self._scale))
    def qss(self, sheet: str) -> str:
        # Rewrite every "<int>px" in an inline stylesheet by the scale factor.
        return re.sub(r"(\d+)px",
                      lambda m: f"{max(1, round(int(m.group(1)) * self._scale))}px",
                      sheet)
```

The `qss()` regex turns the stylesheet rewrite into a **mechanical wrap**: every
`x.setStyleSheet("…px…")` becomes `x.setStyleSheet(self.qss("…px…"))`. It
covers `font-size`, `padding`, `border-radius`, and `width` in one shot.

Child classes (`_ProviderTile`, `_MetricRow`, `_SummaryChip`) need access to the
same scale — pass it down through their constructors (they are only ever created
by `UsageWidget`), or hang a small `Scale` object off the widget and thread it
in. Prefer an explicit `scale: float` constructor arg so the classes stay
testable in isolation.

### 2. Mechanical conversion pass

- Wrap every inline stylesheet string in `self.qss(...)`.
- Replace every literal in `setFixedSize/Width/Height`, `setMinimumWidth`,
  `setMaximumWidth`, `setPixelSize`, `setContentsMargins`, `setSpacing` with
  `self.px(...)`.
- Turn the `WINDOW_*` constants and `ROW_BAR_HEIGHT` etc. into scaled values at
  use sites (e.g. `self.px(WINDOW_WIDTH)`); keep the raw constants as the
  100% baseline. Audit every reader of `WINDOW_WIDTH`/`WINDOW_MAX_HEIGHT`
  (`_do_refit_height`, `_refresh_collapsed_summary`, `set_collapsed`,
  `_clamp_to_visible_screen`, drag/close save handlers) so width is no longer
  assumed constant.
- Custom painters: pass `self._scale` (or pre-scaled sizes) into
  `_render_refresh_pixmap`, the chip notch (`CHIP_NOTCH_*`), and the pace
  bars. These already accept a `size`, so multiply at the call site.

### 3. Persisted geometry stays in logical px

`config.window.width/height/x/y` should remain **unscaled logical** values where
practical. Simplest: persist the *baseline* (unscaled) height by dividing back
out, or just persist actual pixels and clamp on load. Decide and document one
convention; the existing height clamp (`_clamp_height`) and the new
`_clamp_to_visible_screen` must both use the scaled bounds.

### 4. Live apply, no restart

With scaling internal to the widget, a UI-scale change can rebuild the widget's
metrics instead of relaunching:

- Add `UsageWidget.apply_ui_scale(scale)` that updates `self._scale`, re-applies
  all stylesheets and fixed sizes, and calls `_refit_height()`.
- In `_on_settings_finished` (`app.py:895`), replace the `ui_scale_changed`
  restart prompt with `self._widget.apply_ui_scale(new_scale)`.
- Delete `_apply_ui_scale_env` (`app.py:1028`), the `QT_SCALE_FACTOR` line in
  `main()` (`app.py:1057`), `qt_scale_factor_env` (`config.py:209`), and the
  scale rationale in `App.restart()` (keep `restart()` only if something else
  still needs it; otherwise remove it and the instance-lock relaunch plumbing).

The simplest first cut, if `apply_ui_scale` re-styling proves fiddly, is to
**re-create** the widget on scale change (build a fresh `UsageWidget`, move it to
the saved position, re-attach signals) — still no process restart.

## Alternatives considered

- **`QGraphicsView` + `QGraphicsProxyWidget` with `view.scale(s, s)`.** One-line
  visual scaling of everything including custom paint, text stays crisp. Rejected
  as the primary because the top-level window becomes the view: frameless /
  translucent / always-on-top flags, the rounded-background `paintEvent`, the
  drag-to-move math (`mousePressEvent` uses `globalPosition()`/`frameGeometry()`),
  and the `QScrollArea` all need rework to operate through the proxy. Higher
  architectural risk than the mechanical pass. Worth revisiting if the inline-px
  surface ever grows much larger. Keep documented as the fallback.
- **`setFont` on the root widget.** Rejected: fonts are pinned in px on purpose;
  a single base font does not reproduce the per-element 10/11/12/13px hierarchy
  and would not scale bars, icons, margins, or fixed widths.
- **Counter-scaling the dialog under global `QT_SCALE_FACTOR`.** Rejected: Qt has
  no per-window scale factor; emulating one by dividing every dialog metric is
  more fragile than scaling the one widget we actually want scaled.

## Risks & gotchas

- **Layout drift:** missing one `px()`/`qss()` wrap leaves an element at 100%
  while neighbors scale, causing misalignment. Mitigate with the grep checklist
  below and visual checks at 75% / 150% / 300%.
- **Rounding:** `round(n*scale)` can make a 1px border vanish; floor at 1
  (`max(1, …)`).
- **Width is no longer constant:** several methods assume `WINDOW_WIDTH`. Each
  must read the scaled width. This is the most error-prone part.
- **Interaction with OS DPI:** removing `QT_SCALE_FACTOR` means the widget is
  scaled by `ui_scale` *on top of* the OS device-pixel-ratio (which Qt applies
  automatically). That is the desired behavior — the OS handles crispness, we add
  a widget-only zoom — but verify on a high-DPI monitor that `ui_scale` and OS
  scale compose sensibly.
- **Off-screen clamp:** `_clamp_to_visible_screen` must use the scaled
  `self.width()/height()` (it already reads them live, so it should keep working
  once width is scale-aware).

## Test / verification

- Unit: extend `tests/test_widget.py` to build `UsageWidget` at scale 1.5 / 0.75
  and assert `WINDOW_WIDTH`-derived width and a sample `setFixedWidth` scale
  proportionally; assert `qss()` rewrites `font-size:10px` → `15px` at 1.5.
- Unit: assert dialogs are unaffected (Settings dialog metrics unchanged across
  scale values).
- Manual: at OS 100% and OS 200%, set UI scale to 75/100/150/300% and confirm
  only the widget changes size, alignment holds, and no restart is needed.

## Rough effort

Medium. ~1 focused session: most of it is the mechanical `px()`/`qss()` wrap and
auditing every `WINDOW_WIDTH` reader, plus deleting the `QT_SCALE_FACTOR`
plumbing and wiring `apply_ui_scale`. Custom-painter scaling is small.

## Implementation checklist

- [ ] Add `px()` / `qss()` helpers and thread `scale` into `_ProviderTile`,
      `_MetricRow`, `_SummaryChip`.
- [ ] Wrap all inline stylesheets in `qss()`
      (`grep -nE "setStyleSheet" src/aigauge/widget.py`).
- [ ] Wrap all `setFixed*/setMinimum*/setMaximum*/setPixelSize/setContentsMargins/
      setSpacing` literals in `px()`.
- [ ] Make every `WINDOW_*` / `ROW_BAR_HEIGHT` reader use the scaled value; audit
      `_do_refit_height`, `_refresh_collapsed_summary`, `set_collapsed`,
      drag/close save handlers, `_clamp_to_visible_screen`.
- [ ] Scale custom painters (`_render_refresh_pixmap`, chip notch, pace bars).
- [ ] Add `apply_ui_scale()`; wire it into `_on_settings_finished`.
- [ ] Remove `_apply_ui_scale_env`, the `QT_SCALE_FACTOR` env line,
      `qt_scale_factor_env`, and the restart-for-scale prompt; drop `restart()`
      if nothing else needs it.
- [ ] Replace `ui_scale_changed` restart handshake in `settings_dialog.py`.
- [ ] Tests + manual matrix (75/100/150/300% × OS 100/200%).
- [ ] CHANGELOG entry.
