# DragTranslate — select text anywhere on Windows, get an instant translation popup

**Highlight any text in any Windows app and a translation appears right next to it.**
No hotkey, no copy-paste, no switching to a browser tab. Works in Outlook, Word, Excel,
PDF readers, chat apps, terminals, games — anywhere you can select text.

100+ languages · no API key · free and open source · single Python file ·
**new in v1.3.0: fully offline translation with a local AI model** ([details](#translating-offline-with-a-local-model-ollama))

![DragTranslate popups: English, Japanese and Spanish each translated into Korean in their own colour, plus a Korean to English translation in the user's own colour](docs/screenshot-popups.png)

---

## Why this exists

Browser extensions that translate on hover are everywhere. But the moment you leave the
browser — a mail client, a spec sheet in Word, a PDF datasheet, a chat window — you are
back to copy, alt-tab, paste, read, alt-tab back.

DragTranslate is a small background app that fixes exactly that. Select text, read the
translation, carry on. It is the kind of tool that used to be filled by
[QTranslate](https://quest-app.appspot.com/) and similar utilities, rebuilt as a single
readable Python file you can inspect and modify.

## Features

- **Works in every application**, not just the browser
- **100+ languages** via Google Translate — no API key, no account, no cost
- **Or translate entirely on your own PC** with Tencent's open-source
  [Hy-MT2-1.8B](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF) model running in
  [Ollama](https://ollama.com) — no rate limit, works offline, the text never leaves the
  machine. Sub-second per sentence on an ordinary laptop CPU.
- **Set up as many languages as you like** — tell it your own language once, then list
  every language you want to read. English, Spanish and Japanese can all translate into
  Korean, and DragTranslate works out which is which on every selection. Nothing to switch.
- **Anything unlisted still works** — select a language you never configured and it is
  auto-detected and brought into your language anyway.
- **Per-language colours** — text you read is shown in the colour of the language it was
  written in, so Spanish and Japanese are instantly distinguishable even though both land
  in your language. Text *you* wrote uses your own language's colour, so the direction is
  obvious too. Fully customisable.
- **Reads well on any screen** — the popup widens rather than growing endlessly downward,
  shrinks to fit, and always lands fully inside the monitor you are working on. Correct on
  multi-monitor setups.
- **Never covers what you are reading** — it appears on the opposite side of the monitor
  from your cursor, and disappears the moment you click elsewhere or start typing.
- **Automatic vocabulary log** — every lookup is saved to a local SQLite file
  (`vocabulary.db`) with the source text, translation, languages and timestamp.
- **Key verb breakdown** (optional) — when the source is English, the popup also lists the
  main verbs with their translations. Handy when you are learning the language.
- **Tray icon** — toggle translation on/off, open settings, or quit. Like an antivirus:
  always there, off whenever you want.
- **Runs at log-in**, silently, with no console window.

## Install

**Requirements:** Windows 10 or 11. Nothing else — the installer takes care of Python.

1. **[Download the latest release](../../releases/latest)** and extract it somewhere
   permanent, such as `C:\DragTranslate`.
2. Double-click **`install.bat`**.

That is it. The installer runs hidden and shows a single dialog when it is finished. It
will find your Python, or download and install it per-user from python.org if you do not
have one, install the required packages, register DragTranslate to start when you log in,
and launch it.

Administrator rights are **not** required.

> **Windows may warn you** the first time — see [Why does my antivirus complain?](#why-does-my-antivirus-complain)
> below. This is expected for a tool that reads text selections system-wide, and the reason
> the whole thing is a single readable script you can check yourself.

<details>
<summary>Manual install (if you prefer, or you are not on Windows)</summary>

```bash
pip install -r requirements.txt
python dragtranslate.py
```

The translation and popup logic is portable, but the multi-monitor placement and the
"select in any app" trick use Windows APIs, so other platforms are untested.
</details>

## Using it

Select some text with the mouse. That is the whole interaction.

| Action | What happens |
| --- | --- |
| Drag-select text | Popup appears with the translation |
| Click anywhere / type | Popup disappears |
| Click the popup | Popup disappears |
| Right-click the tray icon | Toggle on/off, open settings, quit |

A plain click never triggers anything — you have to actually drag a selection, so normal
clicking around is unaffected.

### Choosing how often it uses the clipboard

Every capture sends one synthetic `Ctrl+C`. In the default mode that happens on *any*
drag — including dragging a window, a scrollbar or a slider — which is more clipboard
traffic than some people want. **Settings → Translate when** offers three levels:

| Mode | Fires on | Clipboard use |
| --- | --- | --- |
| **Any drag** (default) | every drag over the minimum distance | highest |
| **Hold Ctrl while selecting** | only drags released with Ctrl down | much lower |
| **Select, then press a hotkey** | never on a drag; only `Ctrl+Shift+T` | lowest |

Ctrl is deliberately the modifier for the middle option: the key we inject *is* `Ctrl+C`,
so holding Ctrl cannot turn it into a different shortcut the way Shift or Alt would.

Two limits apply in every mode: drags shorter than **12 px** are ignored (a click that
wobbled no longer costs a `Ctrl+C`), and captures are rate-limited to one per **0.4 s**.
Both are adjustable.

### Settings

The settings window opens automatically on first launch, and any time from the tray icon.

![DragTranslate settings window showing my language, a list of languages to read, an outgoing language and colour pickers](docs/screenshot-settings.png)

- **Auto (recommended)** — set **My language**, then build a list of **languages I read**.
  Everything in that list is translated into your language; when you select your *own*
  language it goes to the outgoing language you pick. A language that is not on the list is
  auto-detected and translated into your language too.
- **Fixed direction** — always translate from one language (or auto-detect) into another.
- **Colour...** assigns a colour to the selected language. Text you *read* takes the colour
  of the language it was written in (English blue, Spanish orange, Japanese pink…), while
  text you *wrote* takes your own language's colour — so both the language and the
  direction are visible at a glance. The swatch beside **My language** sets that one.
- **Popup duration**, **text size**, and the **English verb list** are all adjustable.
- **Debug log** is off by default. It records translated text, so leave it off unless you
  are chasing a problem.

Example — a Korean speaker who reads English and Spanish sets *My language* to Korean,
adds English and Spanish to the list, and picks English as the outgoing language. Selecting
English or Spanish gives Korean; selecting Korean gives English; selecting German (not on
the list) still gives Korean.

Settings live in `config.json` next to the script, so you can also edit them by hand.

### Your vocabulary

Everything you look up goes into `vocabulary.db` (SQLite) in the app folder. Open it with
any SQLite viewer, or export it:

```bash
python -c "import sqlite3,csv,sys; c=sqlite3.connect('vocabulary.db'); w=csv.writer(sys.stdout); w.writerow(['source','translation','from','to','when']); w.writerows(c.execute('select source_text,translated_text,source_lang,target_lang,created_at from entries'))" > vocabulary.csv
```

## How it works

Windows has no API for reading another application's text selection, so DragTranslate uses
the approach every tool in this category uses:

1. A global mouse hook watches for a drag (press → move → release).
2. On release it saves your clipboard, sends a synthetic `Ctrl+C`, reads what landed in the
   clipboard, then puts your original clipboard contents back.
3. The text is sent to Google Translate through
   [`deep-translator`](https://github.com/nidhaloff/deep-translator).
4. The result is drawn in a borderless Tkinter window positioned on your current monitor.

### Making the synthetic Ctrl+C safe

That trick is the fragile part of every select-to-translate tool, and done naively it
causes real damage. DragTranslate guards against each failure mode:

| Hazard | What DragTranslate does |
| --- | --- |
| **Ctrl+C kills the running process in a terminal** — it means SIGINT there, not "copy" | Reads the foreground process name and refuses to fire in cmd, PowerShell, Windows Terminal, PuTTY, WSL, ConEmu, Hyper, Alacritty, WezTerm and friends. Extend the list via `blocked_apps` in `config.json`. |
| **Copied files and images are destroyed** — a text-only save/restore cannot put back a file you copied in Explorer | Snapshots *all* restorable clipboard formats as raw bytes (`CF_HDROP` for files, `CF_DIB` for images, HTML, RTF, Unicode text) and restores them afterwards. Disable with `preserve_clipboard: false`. |
| **Holding Shift or Alt turns it into a different shortcut** — `Ctrl+Shift+C` opens developer tools in browsers | Checks the real modifier key state first and skips the capture entirely if Shift, Alt or Win is held. |
| **Clipboard clobbered even when nothing was selected** | Uses `GetClipboardSequenceNumber()` to detect whether the copy actually produced anything. If nothing was copied, the clipboard is never written to at all. |
| **Fixed sleeps are wrong for slow apps** | Polls the clipboard sequence number instead — finishes in ~20 ms in a fast app, waits up to 600 ms for Excel or a remote desktop session. |

Two things are outside its control and worth knowing about:

- **Windows Clipboard History (`Win+V`) and cloud clipboard sync** will record text you
  select, because the copy is performed by the *target application*, not by DragTranslate.
  If that matters to you, turn them off in Settings → System → Clipboard.
- **Applications that block copying** (DRM-protected PDFs, some remote sessions) simply
  return nothing, and DragTranslate stays quiet.

Working out which of your configured languages a selection is written in needs no extra
dependency: distinctive scripts (Hangul, Kana, Han, Cyrillic, Arabic, Thai, Devanagari,
Greek, Hebrew and more) are identified by Unicode range, and languages that share the Latin
alphabet are separated with a small built-in stopword table. Install the optional
`langdetect` package and it will be used for the Latin-script cases instead. If nothing
matches, the text is sent with `auto` as the source language and translated into yours.

## Troubleshooting

### Why does my antivirus complain?

Because DragTranslate does three things that also describe a keylogger: it installs a
global mouse and keyboard hook, it reads and writes the clipboard, and it sends synthetic
keystrokes. There is no way to build a select-to-translate tool without them.

What it does *not* do: the keyboard hook exists solely to notice that you pressed *a* key
so it can close the popup — the key itself is never inspected, stored or transmitted.
Nothing is sent anywhere except the text you deliberately select, which goes to Google
Translate to be translated.

Everything is in [`dragtranslate.py`](dragtranslate.py) — one file, no build step, no
obfuscation. Read it before you run it.

### Selecting text does nothing in Outlook (or another app)

That app is probably running elevated, and Windows blocks a normal-privilege process from
sending keystrokes to an elevated window. Right-click `install.bat` and choose **Run as
administrator** once. It will then register DragTranslate as a scheduled task that starts
elevated at log-in, with no UAC prompt each boot.

### The popup does not appear at all

Check `install_log.txt` in the app folder. If the app started but nothing happens, enable
**Write a debug log** in settings and look at `dragtranslate.log`.

### "Translation failed"

The free Google Translate endpoint is unofficial and occasionally rate-limits or changes.
Usually waiting a moment and selecting again is enough. If it persists, the endpoint may be
blocked on your network.

Run the built-in check, which names the cause instead of making you guess:

```
py dragtranslate.py --check
```

### Translating offline with a local model (Ollama)

Runs a translation model on your own PC instead of calling Google: free, no rate
limit, works without internet, and the text you select never leaves the machine.
This is the fix for *"Google is rate-limiting the address this network goes out
from"* on a shared company network.

![How the local engine works: the selected text goes from DragTranslate to Ollama on 127.0.0.1 and back, and nothing is sent to the internet](docs/local-engine.svg)

| English → Korean, with key verbs | Korean → English |
| --- | --- |
| ![Popup translating an English purchase-request sentence into Korean with the local Hy-MT2 model, with the verbs review and approve listed below](docs/popup-ollama-en-ko.png) | ![Popup translating a Korean server-maintenance notice into English with the local Hy-MT2 model](docs/popup-ollama-ko-en.png) |

*Both popups above were produced by DragTranslate v1.3.0 with the local engine, on a
laptop CPU (AMD Ryzen AI 7 PRO 350), no GPU.*

1. Install [Ollama](https://ollama.com) (`winget install Ollama.Ollama`). It starts
   with Windows and sits in the system tray.
2. Download the model once (about 1.1 GB):
   ```
   ollama pull hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M
   ```
3. In `config.json` set `"engine": "ollama"` and restart DragTranslate.
4. `py dragtranslate.py --check` confirms Ollama is running, the model is there,
   and shows how long a translation takes.

With this engine there is **no fallback to Google** — if Ollama is not running or the
model is missing, the popup says exactly that and nothing is sent anywhere.

#### The model used

| | |
| --- | --- |
| Model | **Hy-MT2-1.8B** by Tencent Hunyuan, a translation-only model (1.79 B parameters, `hunyuan-dense` architecture) |
| File | [`tencent/Hy-MT2-1.8B-GGUF`](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF), quantization **Q4_K_M** (1.1 GB) |
| Quantized from | [`tencent/Hy-MT2-1.8B`](https://huggingface.co/tencent/Hy-MT2-1.8B) |
| Ollama tag | `hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M` (tested: Ollama ID `33a3a4ab0f4e`, weights blob `sha256:dc5f44fcf1fa496ee7ad725982c0c8c553a4de00259b53af84c4b89fb0c06699`) |
| Licence | Apache-2.0 (per the model card) |
| Languages | 33 languages including Korean, English, Japanese, Chinese, Vietnamese, Spanish |
| Paper | [Hy-MT2: A Family of Fast, Efficient and Powerful Multilingual Translation Models in the Wild](https://arxiv.org/abs/2605.22064) (arXiv:2605.22064, 2026) |
| Source | [github.com/Tencent-Hunyuan/Hy-MT2](https://github.com/Tencent-Hunyuan/Hy-MT2) |

DragTranslate does not ship the model; Ollama downloads it from Hugging Face when you run
`ollama pull`. The prompt is the one recommended on the model card ("Translate the
following text into {language}. Note that you should only output the translated result
without any additional explanation: …", and its Chinese counterpart when Chinese is
involved), with the card's sampling settings (temperature 0.7, top_p 0.6, top_k 20,
repetition penalty 1.05).

**Why the prompt is sent raw.** When Ollama imports this GGUF it generates a broken chat
template that drops the user's message entirely, so a normal `/api/chat` call makes the
model answer an empty turn with noise. DragTranslate therefore builds the prompt in the
model's own format — taken from the `tokenizer.chat_template` stored inside the GGUF —
and sends it to `/api/generate` with `raw: true`. Any other model name in `ollama_model`
goes through the normal chat API.

If you use this model in your own work, cite the authors:

```bibtex
@misc{zheng2026hymt2familyfastefficient,
      title={Hy-MT2: A Family of Fast, Efficient and Powerful Multilingual Translation Models in the Wild},
      author={Mao Zheng and Zheng Li and Tao Chen and Bo Lv and Mingrui Sun and Mingyang Song and Jinlong Song and Hong Huang and Decheng Wu and Hai Wang and Yifan Song and Yanfeng Chen and Guanwei Zhang},
      year={2026},
      eprint={2605.22064},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2605.22064},
}
```

#### Speed and memory

Measured on a laptop with an AMD Ryzen AI 7 PRO 350, CPU only:

| | |
| --- | --- |
| One sentence, model already loaded | 0.2 – 0.9 s |
| First translation after the model was unloaded | about 9 s (loading 1.1 GB from disk) |
| Memory while loaded | about 1.5 GB in Ollama; DragTranslate itself about 150 MB |

Ollama unloads the model after `ollama_keep_alive` (default `10m`) without a
translation, which frees the memory; the next translation pays the loading time once.
Set it to `-1` to keep the model loaded, or `0` to unload after every request.

| setting | default |
| --- | --- |
| `ollama_model` | `hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M` |
| `ollama_url` | `http://127.0.0.1:11434` |
| `ollama_keep_alive` | `10m` (`-1` = keep loaded, `0` = unload right away) |
| `ollama_timeout_sec` | `60` |

### Using an API key instead

If the popup says Google is rate-limiting you, and `--check` reports the 429 came
from Google itself, no setting in this app will help. The free endpoint has no
account behind it, so the limit is counted against the **public IP address your
traffic leaves from**. On a company network that address is shared by everyone in
the building, and it can be exhausted by people and software that have nothing to
do with this app.

An API key moves the quota onto the key, which is yours alone. That is the only
change that actually decouples you from the shared address.

Add to `config.json`:

```json
"engine": "microsoft",
"api_key": "your key here",
"api_region": "koreacentral"
```

Then restart. `--check` prints which engine is in use and what is still missing.

| engine | cost | key |
| --- | --- | --- |
| `google` (default) | free | none — shares the network's rate limit |
| `microsoft` | free up to 2,000,000 characters/month | Azure portal → create a *Translator* resource, pricing tier **F0** |
| `libre` | free if you host it | your own instance's url and key |
| `ollama` | free, offline | none — see *Translating offline with a local model* |

Microsoft's free tier is generous, does not expire, and handles Korean well. The key
stays in `config.json` on your machine and is sent only to the service it belongs to.

**DeepL is not offered**, despite translating Korean well: the `deep-translator`
library validates the language against its own hardcoded list, which has no Korean
in it, so the request is rejected before it is ever sent.

If the engine is misconfigured, translation falls back to Google rather than
stopping, and `--check` says what is missing.

### Certificate errors on a company network

If the popup says **`CERTIFICATE_VERIFY_FAILED`** or **"unable to get local issuer
certificate"**, nothing is blocked, banned or rate-limited. Your connection never reached
Google at all.

Most company networks run an appliance — Zscaler, Netskope, Fortinet, Palo Alto, Sophos —
that terminates every HTTPS connection, inspects it, and re-signs it with the company's own
root certificate. Your browser accepts that because IT installed the root in the Windows
certificate store. Python does not: `requests` ships its own CA list (`certifi`) and ignores
Windows entirely, so the re-signed certificate looks forged and the connection is dropped
before a single byte is sent.

`--check` prints the issuer of the certificate actually being presented. If it names your
company or a security vendor rather than a public CA, that is your answer.

The fix is to verify against the Windows certificate store instead:

```
py -m pip install truststore
```

Restart DragTranslate. It uses `truststore` automatically when present, so it then trusts
exactly what your browser trusts — and keeps working when IT rotates the certificate.

`install.bat` installs `truststore` from v1.0.2 onward, so a fresh install needs none of
this.

If your IT policy rules that out, export the company root certificate as a `.pem` and point
the app at it — add `"ca_bundle": "C:\\path\\to\\company-root.pem"` to `config.json`, or set
the `REQUESTS_CA_BUNDLE` environment variable. Both are honoured ahead of everything else.

Do not disable certificate verification. That turns the warning off by making the connection
genuinely unsafe, and on a corporate machine it is likely a policy violation.

## Privacy

Text you select is sent to Google Translate over the network in order to be translated.
Do not use this on confidential material unless that is acceptable to you and your
organisation. Everything else — settings, vocabulary, logs — stays in the app folder on
your machine. There is no telemetry and no account.

## Limitations

- Windows only in practice (uses Win32 APIs for monitor placement and input hooks).
- Uses the free, unofficial Google Translate endpoint. Fine for personal use; if you are
  building something commercial, switch to an official paid API (Google Cloud Translation,
  DeepL, Papago) or the built-in local engine (`"engine": "ollama"`, see above).
- The local engine needs about 1.5 GB of free memory while the model is loaded, and the
  first translation after it was unloaded takes several seconds.
- The verb breakdown is English-source only, because it relies on NLTK's English tagger.
- Some applications (certain games, hardened/DRM windows) do not respond to `Ctrl+C` and
  cannot be read.

## Contributing

Issues and pull requests are welcome. The whole app is one file — start at `main()`.
Ideas that would help: an engine picker in the Settings window, a proper vocabulary browser,
macOS/Linux support, more languages in the stopword table.

## Licence

[MIT](LICENSE) for this project's own code.

It depends on `pynput` and `pystray`, which are **LGPL-3.0** — they are installed as
separate packages by pip and are not bundled or modified here. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the full list of dependencies and
their licences.

---

## 한국어

**윈도우 어디서든 텍스트를 드래그하면 그 자리에 번역 팝업이 뜨는 프로그램입니다.**

단축키도, 복사 붙여넣기도, 브라우저 전환도 필요 없습니다. 아웃룩, 워드, 엑셀, PDF 뷰어,
메신저 등 텍스트를 선택할 수 있는 모든 곳에서 동작합니다.

브라우저 확장 프로그램은 많지만 브라우저 밖으로 나가는 순간 쓸 수 없다는 점에서 출발한
도구입니다. 100개가 넘는 언어를 지원하고, API 키나 결제가 필요 없습니다.

### 설치

윈도우 10 또는 11이면 됩니다. 파이썬은 설치 파일이 알아서 처리합니다.

1. **[최신 릴리스를 내려받아](../../releases/latest)** `C:\DragTranslate` 같은 폴더에
   풀어주세요.
2. **`install.bat`** 을 더블클릭하세요.

검은 창 없이 조용히 설치되고, 끝나면 안내 창이 하나 뜹니다. 관리자 권한은 필요 없습니다.

### 사용법

마우스로 텍스트를 드래그하기만 하면 됩니다. 다른 곳을 클릭하거나 키를 누르면 팝업이
사라집니다. 그냥 클릭하는 것으로는 반응하지 않으니 평소 사용에 지장이 없습니다.

**여러 언어를 한 번에 등록할 수 있습니다.** 내 언어를 한국어로 지정하고, 읽고 싶은 언어
목록에 영어·스페인어·일본어를 넣어두면 그 언어들이 전부 한국어로 번역됩니다. 매번 방향을
바꿀 필요가 없고, 목록에 없는 언어를 드래그해도 자동으로 인식해서 한국어로 보여줍니다.
한국어를 드래그했을 때 어떤 언어로 번역할지는 따로 지정합니다.

언어마다 팝업 색을 다르게 지정할 수 있어서, 똑같이 한국어로 번역되더라도 원문이
스페인어였는지 일본어였는지 색만 보고 구분됩니다. 반대로 내가 쓴 한국어를 번역할 때는
내 언어의 색으로 떠서 방향도 한눈에 구분됩니다.

번역한 내용은 `vocabulary.db` 파일에 자동으로 쌓여서 나만의 단어장이 됩니다. 영어를
번역할 때는 주요 동사를 따로 뽑아서 함께 보여줍니다.

### 자주 겪는 문제

**백신이 경고해요** — 전역 마우스·키보드 후킹, 클립보드 접근, 가상 키 입력을 사용하기
때문입니다. 드래그 번역 기능을 만들려면 피할 수 없는 방식이고, 그래서 코드 전체를 파일
하나로 공개해 두었습니다. 키보드 후킹은 "키가 눌렸다"는 신호로 팝업을 닫는 데만 쓰고,
어떤 키인지는 확인하지도 저장하지도 않습니다.

**아웃룩에서 안 돼요** — 아웃룩이 관리자 권한으로 실행 중이면 일반 권한 프로그램이 키 입력을
보낼 수 없도록 윈도우가 막습니다. `install.bat` 을 우클릭해서 "관리자 권한으로 실행"을 한
번만 해주시면, 이후 부팅할 때마다 UAC 창 없이 관리자 권한으로 자동 실행됩니다.

**구글 없이 내 PC에서 번역하고 싶어요 (Ollama)** — 번역 모델을 내 컴퓨터에서 직접
돌립니다. 무료이고, 속도 제한이 없고, 인터넷 없이도 되며, 선택한 문장이 PC 밖으로 나가지
않습니다.

1. [Ollama](https://ollama.com) 설치 (`winget install Ollama.Ollama`). 윈도우 시작 시 트레이에서
   자동 실행됩니다.
2. 모델을 한 번 받기 (약 1.1GB): `ollama pull hf.co/tencent/Hy-MT2-1.8B-GGUF:Q4_K_M`
3. `config.json` 에서 `"engine": "ollama"` 로 바꾸고 DragTranslate 재시작
4. `py dragtranslate.py --check` 로 Ollama 실행 여부, 모델 설치 여부, 번역 속도를 확인

![로컬 엔진 구조: 선택한 문장이 DragTranslate에서 127.0.0.1의 Ollama로 갔다가 돌아오며, 인터넷으로는 아무것도 나가지 않음](docs/local-engine.svg)

| 영어 → 한국어 (주요 동사 포함) | 한국어 → 영어 |
| --- | --- |
| ![로컬 Hy-MT2 모델로 영어 구매 요청 문장을 한국어로 번역한 팝업](docs/popup-ollama-en-ko.png) | ![로컬 Hy-MT2 모델로 한국어 서버 점검 공지를 영어로 번역한 팝업](docs/popup-ollama-ko-en.png) |

**사용한 모델** — 텐센트 Hunyuan 팀의 번역 전용 모델 **Hy-MT2-1.8B**
([`tencent/Hy-MT2-1.8B-GGUF`](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF), 양자화
**Q4_K_M**, 1.1GB, Apache-2.0 라이선스). 논문은
[Hy-MT2: A Family of Fast, Efficient and Powerful Multilingual Translation Models in the Wild](https://arxiv.org/abs/2605.22064)
(arXiv:2605.22064)이고, 인용 정보(BibTeX)는 영문 섹션 *The model used* 에 있습니다. 모델은
이 프로젝트에 포함되어 있지 않고, `ollama pull` 할 때 Hugging Face에서 직접 내려받습니다.

**속도와 메모리** (AMD Ryzen AI 7 PRO 350 노트북, CPU만 사용)

- 모델이 올라가 있을 때: 문장당 0.2~0.9초
- 모델이 내려간 뒤 첫 번역: 약 9초 (1.1GB를 다시 읽어 들임)
- 메모리: 모델이 올라가 있는 동안 Ollama가 약 1.5GB, DragTranslate 자체는 약 150MB

`ollama_keep_alive`(기본 10분) 동안 번역하지 않으면 모델이 내려가서 메모리가 비워집니다.
`-1`이면 계속 올려 두고, `0`이면 번역할 때마다 바로 내립니다. 이 엔진에서는 구글로 넘어가지
않으므로, Ollama가 꺼져 있으면 팝업에 그렇게 표시됩니다.

**구글이 속도 제한을 건다고 나와요** — `--check` 결과에서 429를 구글이 직접 보낸 것으로
나오면, 이 프로그램의 어떤 설정으로도 해결되지 않습니다. 무료 엔드포인트는 계정이 없기
때문에 제한이 **트래픽이 나가는 공인 IP 주소** 기준으로 걸립니다. 회사 네트워크는 그 주소를
건물 전체가 공유하므로, 이 프로그램과 무관한 사람이나 소프트웨어 때문에 한도가 소진될 수
있습니다.

API 키를 쓰면 할당량이 **내 키**에 붙습니다. 공유 주소에서 벗어나는 유일한 방법입니다.

`config.json` 에 추가하세요:

```json
"engine": "microsoft",
"api_key": "발급받은 키",
"api_region": "koreacentral"
```

재시작하면 `--check` 가 어떤 엔진을 쓰는지, 빠진 설정이 뭔지 알려줍니다. 마이크로소프트
번역기는 **월 200만 자까지 무료**이고 기간 제한이 없으며 한국어 품질도 좋습니다. Azure
포털에서 *Translator* 리소스를 만들고 요금제를 **F0** 로 고르면 됩니다. 키는 내 컴퓨터의
`config.json` 에만 저장되고, 해당 서비스 외에는 어디로도 전송되지 않습니다.

DeepL 은 지원하지 않습니다. 번역 품질은 좋지만 `deep-translator` 라이브러리가 자체 언어
목록으로 검증하는데 거기에 한국어가 없어서, 요청을 보내기도 전에 거부됩니다.

**`CERTIFICATE_VERIFY_FAILED` 오류가 떠요** — 차단당한 것도, 사용량 제한에 걸린 것도
아닙니다. 애초에 구글까지 연결이 닿지도 못한 상태입니다.

회사 네트워크는 대부분 보안 장비(Zscaler, Netskope, Fortinet, Palo Alto, Sophos 등)가
모든 HTTPS 연결을 중간에서 풀어 검사한 뒤 **회사 자체 인증서로 다시 서명**해서 내보냅니다.
브라우저는 이걸 받아들입니다. IT팀이 그 루트 인증서를 윈도우 인증서 저장소에 미리 넣어
뒀기 때문입니다. 그런데 파이썬은 윈도우 저장소를 아예 보지 않고 `requests` 가 들고 다니는
자체 인증서 목록(`certifi`)만 봅니다. 그래서 회사 인증서가 위조된 것처럼 보여서 데이터를
한 바이트도 보내기 전에 연결을 끊어버립니다.

원인을 직접 확인하려면:

```
py dragtranslate.py --check
```

실제로 받은 인증서의 발급자가 출력됩니다. 공인 인증기관이 아니라 회사 이름이나 보안업체
이름이 찍히면 그게 원인입니다.

해결은 파이썬이 윈도우 인증서 저장소를 쓰도록 바꾸는 것입니다:

```
py -m pip install truststore
```

설치 후 DragTranslate 를 재시작하면 됩니다. `truststore` 가 있으면 자동으로 사용하므로,
브라우저가 신뢰하는 것과 똑같은 기준을 따르게 되고 IT팀이 인증서를 교체해도 계속
동작합니다. v1.0.2 부터는 `install.bat` 이 이걸 함께 설치하므로 새로 설치하는 경우에는
따로 할 일이 없습니다.

회사 정책상 설치가 어렵다면, 회사 루트 인증서를 `.pem` 으로 내보낸 뒤 `config.json` 에
`"ca_bundle": "C:\\경로\\company-root.pem"` 을 추가하거나 `REQUESTS_CA_BUNDLE` 환경변수를
지정하세요. 둘 다 다른 설정보다 우선 적용됩니다.

인증서 검증을 끄는 방법은 쓰지 마세요. 경고만 사라질 뿐 연결이 실제로 위험해지고, 회사
장비에서는 정책 위반이 될 수 있습니다.

**개인정보** — 드래그한 텍스트는 번역을 위해 구글 번역 서버로 전송됩니다. 회사 기밀 문서에
사용하실 때는 이 점을 고려해 주세요. 설정과 단어장은 전부 내 컴퓨터 안에만 저장됩니다.

---

<sub>Keywords: select to translate, drag to translate, translate text selection, popup
translator Windows, instant translation overlay, translate any application, screen
translator, QTranslate alternative, Google Translate desktop app, 드래그 번역, 마우스 드래그
자동 번역, 윈도우 번역 프로그램, 선택 번역, 팝업 번역기</sub>
