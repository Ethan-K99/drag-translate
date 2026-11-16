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

## truststore

Verifies HTTPS against the operating system's trust store (Windows CryptoAPI, macOS
Security framework) instead of a bundled CA list, so DragTranslate keeps working on a
network that inspects TLS with a private root certificate.
Licence: MIT. https://github.com/sethmlarson/truststore

## certifi

Fallback CA bundle, used when truststore is unavailable.
Licence: MPL-2.0. https://github.com/certifi/python-certifi

## Ollama and Hy-MT2 (optional, not bundled)

With `"engine": "ollama"`, DragTranslate talks to a separately installed Ollama
(https://ollama.com, MIT) running Tencent Hy-MT2-1.8B
(https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF, Apache-2.0). Neither is shipped with
this project; you install and download them yourself, under their own licences.
Model: Hy-MT2-1.8B, Q4_K_M GGUF, by Tencent Hunyuan. Paper: Zheng et al., "Hy-MT2: A
Family of Fast, Efficient and Powerful Multilingual Translation Models in the Wild",
arXiv:2605.22064 (2026), https://arxiv.org/abs/2605.22064
