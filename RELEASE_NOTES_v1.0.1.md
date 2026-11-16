# DragTranslate v1.0.1

A maintenance release about one thing: **how often DragTranslate touches your clipboard.**

Nothing about translation, languages, colours or the popup has changed. If v1.0.0 works
fine for you, this release is still worth taking — the clipboard fix below prevents real
data loss.

Upgrading: quit the running copy from the tray icon, extract this ZIP over your existing
folder (or into a new one), and run `install.bat` again. Your `config.json` and
`vocabulary.db` are kept.

## Choose how often a synthetic `Ctrl+C` is sent

Every capture sends one synthetic `Ctrl+C`. In v1.0.0 that happened on *any* drag past
4 pixels — dragging a window by its title bar, dragging a scrollbar, moving a slider, all
of it. None of those are selections, and all of them cost a clipboard round-trip. It also
collided with clipboard managers and with mouse macros bound to copy.

**Settings → Translate when** now offers three levels:

| Mode | Fires on | Clipboard use |
| --- | --- | --- |
| **Any drag** (default, v1.0.0 behaviour) | every drag over the minimum distance | highest |
| **Hold Ctrl while selecting** | only drags released with Ctrl held down | much lower |
| **Select, then press a hotkey** | never on a drag; only `Ctrl+Shift+T` | lowest |

Ctrl is the modifier for the middle mode deliberately. The key being injected *is*
`Ctrl+C`, so holding Ctrl leaves it unchanged — whereas Shift or Alt would turn it into
`Ctrl+Shift+C` or `Ctrl+Alt+C` and fire something else entirely.

Two limits now apply in **every** mode:

- The minimum drag distance is **12 px** instead of 4, so a click that wobbled no longer
  costs a `Ctrl+C`. Adjustable in Settings. If you ever opened Settings on v1.0.0 your
  `config.json` has the old 4 saved in it, so upgrading raises it for you — unless you
  had deliberately picked something else, which is left alone.
- Captures are rate-limited to one per **0.4 s**, so a burst of quick drags cannot
  machine-gun the clipboard. Adjustable via `capture_cooldown_sec` in `config.json`.

## Fixed: your own `Ctrl+C` could stop working

A capture takes a few hundred milliseconds, and v1.0.0 restored the previous clipboard
contents unconditionally at the end of it. Anything that copied inside that window — a
clipboard manager, a mouse macro bound to `Ctrl+C`, or you pressing `Ctrl+C` yourself —
had its copy silently replaced with the old contents. The symptom is that copying appears
to stop working altogether, which is very hard to trace back to a translation tool.

The clipboard is now restored only when **both** checks pass: the Windows clipboard
sequence number advanced by exactly one (so our copy was the only one), and the contents
still match what we read. Otherwise it is left alone. Worst case that leaves the selected
text on the clipboard instead of destroying something you meant to keep.

Reported by a user whose Logitech G502 copy macro stopped working. Thank you.

## Fixed: DragTranslate refusing to start for no visible reason

The single-instance lock binds a loopback port, and it used 47653 — a number inherited
from an earlier prototype. If anything else on the machine held that port, DragTranslate
concluded it was already running and exited silently. Moved to **47821**, and the reason
the port exists is now documented in the source.

If you also have an older `hover_translate_*` prototype registered in your Startup folder
(`Win+R` → `shell:startup`), remove it. Two copies both sending `Ctrl+C` is exactly the
problem this release is trying to solve.

## Everything else

Unchanged from v1.0.0: 100+ languages through Google Translate with no API key, any number
of configured languages with per-language popup colours, multi-monitor-aware popup
placement, the terminal blocklist, files/images/HTML/RTF clipboard preservation, the
SQLite vocabulary log, the tray icon, and the hidden non-admin installer.

The known issues from v1.0.0 all still apply — antivirus warnings, elevated apps needing
one admin install, Windows Clipboard History recording selections, the unofficial Google
endpoint occasionally rate-limiting, and English-only verb hints. See the v1.0.0 notes.

## Licence

MIT for this project's own code. `pynput` and `pystray` are LGPL-3.0 and are installed by
pip rather than bundled. See `THIRD_PARTY_NOTICES.md`.
