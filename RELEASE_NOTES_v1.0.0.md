# DragTranslate v1.0.0

**Select text anywhere on Windows and a translation popup appears right next to it.**
No hotkey, no copy-paste, no switching to a browser tab.

This is the first public release.

## Install

1. Download **`DragTranslate-v1.0.0.zip`** below and extract it somewhere permanent,
   for example `C:\DragTranslate`.
2. Double-click **`install.bat`**.

That is all. The installer runs hidden and shows a single dialog when it is done. It finds
your Python or downloads and installs it per-user from python.org, installs the required
packages, registers DragTranslate to start when you log in, and launches it.

**No administrator rights required.** Windows 10 or 11.

On first launch a settings window opens so you can choose your languages and colours.

## What it does

- Works in **every Windows application** — Outlook, Word, Excel, PDF readers, chat apps,
  browsers — anywhere you can select text.
- **100+ languages** through Google Translate. No API key, no account, no cost.
- **Configure as many languages as you like.** Set your own language once, then list every
  language you want to read. English, Spanish and Japanese can all translate into Korean,
  and DragTranslate works out which is which on every selection. A language you never
  configured is auto-detected and brought into your language anyway.
- **Per-language colours.** Text you read takes the colour of the language it was written
  in, so Spanish and Japanese stay distinct even though both land in your language; text
  you wrote takes your own language's colour, so the direction is obvious too.
- **Fits any screen.** The popup widens instead of growing endlessly downward, shrinks to
  fit, and always lands fully inside the monitor you are working on. Multi-monitor aware.
- **Never in the way.** It appears on the opposite side of the monitor from your cursor and
  disappears the moment you click elsewhere or start typing.
- **Automatic vocabulary log** — every lookup is saved to a local SQLite file
  (`vocabulary.db`) with source text, translation, languages and timestamp.
- **Key verb breakdown** for English source text, useful when you are learning.
- **Tray icon** to toggle it on and off, open settings, or quit. Runs at log-in with no
  console window.

## The Ctrl+C trick, done carefully

Windows has no API for reading another application's selection, so DragTranslate sends a
synthetic `Ctrl+C` and reads the clipboard. Done naively that causes real damage, so this
release guards against each failure mode:

- **Never fires in a terminal.** In cmd, PowerShell, Windows Terminal, PuTTY, WSL, ConEmu,
  Hyper, Alacritty and WezTerm, `Ctrl+C` means "kill the running process". The foreground
  application is checked first. Extend the list via `blocked_apps` in `config.json`.
- **Copied files and images survive.** All restorable clipboard formats — files
  (`CF_HDROP`), images (`CF_DIB`), HTML, RTF and text — are snapshotted as raw bytes and
  restored, instead of being replaced with plain text.
- **Skips while Shift, Alt or Win is held**, so the injected key never becomes
  `Ctrl+Shift+C` and opens your browser's developer tools.
- **Leaves the clipboard completely untouched when nothing was selected**, using the
  Windows clipboard sequence number to detect whether a copy actually happened.
- **Adapts to slow applications.** It polls for the clipboard update rather than sleeping a
  fixed amount: about 20 ms in a responsive app, up to 600 ms for Excel or a remote desktop
  session.

## Known issues and notes

- **Your antivirus may warn you.** DragTranslate installs a global mouse and keyboard hook,
  reads the clipboard and sends synthetic keystrokes — the same things a keylogger does.
  There is no way to build select-to-translate without them. The keyboard hook exists only
  to notice that *a* key was pressed so it can close the popup; the key itself is never
  inspected, stored or transmitted. Everything is in a single readable Python file, so
  please read it before you run it.
- **Selecting text does nothing in an elevated app** (some corporate Outlook setups).
  Windows blocks a normal-privilege process from sending keystrokes to an elevated window.
  Right-click `install.bat` and choose *Run as administrator* once; it will then register a
  scheduled task that starts elevated at log-in with no UAC prompt each boot.
- **Windows Clipboard History (`Win+V`) and cloud clipboard sync will record what you
  select**, because the copy is performed by the target application, not by DragTranslate.
  Turn them off in Settings → System → Clipboard if that matters to you.
- **Text you select is sent to Google Translate** in order to be translated. Do not use
  this on confidential material unless that is acceptable to you and your organisation.
  Everything else — settings, vocabulary, logs — stays on your machine. No telemetry, no
  account.
- **The free Google Translate endpoint is unofficial** and can occasionally rate-limit.
  Selecting again after a moment usually works. For commercial use, switch to an official
  paid API or a local engine such as Argos Translate.
- The English verb breakdown is English-source only, because it relies on NLTK's English
  tagger.
- Applications that block copying (DRM-protected PDFs, some remote sessions) cannot be read.

## Uninstalling

Run `uninstall.bat`. It stops the app and removes it from autostart, keeping your settings
and vocabulary. Delete the folder afterwards to remove those too.

## Licence

MIT for this project's own code. It depends on `pynput` and `pystray`, which are LGPL-3.0
and are installed separately by pip rather than bundled. See `THIRD_PARTY_NOTICES.md` for
the full dependency list.
