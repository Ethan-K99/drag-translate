# Third-party notices

DragTranslate itself is released under the [MIT licence](LICENSE).

It does **not** bundle, copy or modify any of the packages below. They are installed
separately by `pip` at install time and are loaded as ordinary Python imports. This matters
for the LGPL components: because they remain independent, replaceable packages in your
Python environment, you are free to swap in your own modified build of any of them.

| Package | Licence | Used for | Project |
| --- | --- | --- | --- |
| `pynput` | **LGPL-3.0** | Global mouse and keyboard hooks | https://github.com/moses-palmer/pynput |
| `pystray` | **LGPL-3.0** | System tray icon and menu | https://github.com/moses-palmer/pystray |
| `pyperclip` | BSD-3-Clause | Reading and restoring the clipboard | https://github.com/asweigart/pyperclip |
| `deep-translator` | MIT | Google Translate backend | https://github.com/nidhaloff/deep-translator |
| `Pillow` | MIT-CMU (HPND) | Drawing the tray icon | https://github.com/python-pillow/Pillow |
| `nltk` | Apache-2.0 | English part-of-speech tagging for the verb list | https://github.com/nltk/nltk |
| `langdetect` *(optional)* | Apache-2.0 | Better detection between Latin-script languages | https://github.com/Mimino666/langdetect |
| Python, Tkinter, SQLite | PSF / BSD-style / public domain | Runtime, GUI, vocabulary storage | https://www.python.org/ |

## LGPL-3.0 components

`pynput` and `pystray` are licensed under the GNU Lesser General Public License v3.0.

- Their full source is available at the project links above.
- A copy of the LGPL-3.0 text is at https://www.gnu.org/licenses/lgpl-3.0.html
- Neither package is modified by this project.
- To use your own build, install it into the same Python environment
  (`pip install ./my-pynput`) — DragTranslate will import it unchanged.

If you redistribute DragTranslate together with these packages (for example, frozen into a
single executable with PyInstaller), the LGPL's relinking requirement applies to that
bundle and you must make it possible for recipients to replace the LGPL parts. Distributing
the source as this repository does, and letting `pip` fetch the packages, avoids that
situation entirely.

## Language data

NLTK's `punkt` tokenizer and `averaged_perceptron_tagger` models are downloaded from the
NLTK data repository on first run and are covered by their own licences, listed at
https://www.nltk.org/nltk_data/

## Translation service

Translations are performed by Google Translate through `deep-translator`, which uses the
free public web endpoint rather than the official paid Cloud Translation API. That endpoint
is not covered by any licence granted to this project, and Google's Terms of Service govern
its use. It is suitable for personal use; for commercial products, switch to an official
paid API or a local translation engine.
