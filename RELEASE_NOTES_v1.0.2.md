# DragTranslate v1.0.2

**Fixes `CERTIFICATE_VERIFY_FAILED` on company networks**, and stops error popups from
dumping a wall of raw exception text at you.

Upgrading: quit from the tray icon, extract over your folder, run `install.bat` again. Your
settings and vocabulary are kept.

## HTTPS now verifies against the operating system's trust store

The symptom is a popup full of this:

```
[Translation failed: HTTPSConnectionPool(host='translate.google.com', port=443):
Max retries exceeded with url: /m?tl=en&sl=ko&q=%EB%B3%B8%EB%93%9C... (Caused by
SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate
verify failed: unable to get local issuer certificate (_ssl.c:1082)')))]
```

It reads like Google blocked you. It isn't. **The connection never reached Google.**

Most company networks run an appliance — Zscaler, Netskope, Fortinet, Palo Alto, Sophos —
that terminates every HTTPS connection, inspects it, and re-signs it with the company's own
root certificate. Browsers accept this because IT installed that root in the Windows
certificate store. Python does not look there: `requests` ships its own CA list (`certifi`)
and ignores the OS entirely, so the re-signed certificate looks forged and the connection is
dropped before a byte is sent.

DragTranslate now picks its trust source in this order:

1. **`ca_bundle` in `config.json`**, or the `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`
   environment variables — for an exported company root, when policy rules out installing
   packages.
2. **`truststore`**, which routes verification through the OS trust store (Windows
   CryptoAPI, macOS Security). It trusts exactly what the browser on the same machine
   trusts, and survives IT rotating the certificate. `install.bat` now installs it.
3. **`certifi`** — the normal public CA list, correct on a home network.

On an existing install you do not need to reinstall; `py -m pip install truststore` and a
restart is enough.

Verified against a real TLS-inspecting proxy: `certifi` reproduces the exact
`CERTIFICATE_VERIFY_FAILED`, `truststore` connects.

Certificate verification is never disabled. That would only hide the warning while making
the connection genuinely unsafe.

## `--check`, so you can see the cause instead of guessing

```
py dragtranslate.py --check
```

Prints your Python, which trust source is active, whether `truststore` and `certifi` are
installed, any proxy variables — and, most usefully, **the issuer of the certificate
`translate.google.com` actually presents on your network**. If that names your employer or a
security vendor instead of a public CA, the diagnosis is settled. It finishes with a live
translation attempt.

## Error popups are readable now

Failures used to print the raw exception, which embeds the entire URL-encoded request — on a
long selection the actual reason scrolled off the popup. Errors are now one plain sentence
naming the likely cause and the fix, for certificate failures, rate limiting, DNS, proxy
rejections, timeouts and plain connection loss. The full exception still goes to
`dragtranslate.log` when **Write a debug log** is on.

## Also in this release

The minimum drag distance moves to 12 px for anyone upgrading with `min_drag_px: 4` saved in
`config.json` from v1.0.0. A settings save writes the whole file, so that value is the old
default rather than a choice — and leaving it would keep window drags and scrollbars firing
a `Ctrl+C`, which is what v1.0.1 set out to stop. Anything you set deliberately is left
alone. (Config migrations also no longer skip a `config.json` that has no `version` key.)

## Licence

MIT for this project's own code. `pynput` and `pystray` are LGPL-3.0, `certifi` is MPL-2.0,
and all are installed by pip rather than bundled. See `THIRD_PARTY_NOTICES.md`.
