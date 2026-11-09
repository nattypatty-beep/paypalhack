# Third-party notices

MandatePay is licensed under the [MIT License](LICENSE). It depends on and
redistributes the following third-party software. This file lists direct
dependencies and the most relevant transitive packages. It is not a substitute
for the license files shipped by each project; when in doubt, consult the
upstream package.

The demo runs offline. No third-party CDN, font service or analytics script is
loaded at runtime.

---

## Vendored browser assets

### AG Grid Community v36.3.0

Files:

- `app/static/vendor/ag-grid-community.min.js`
- `app/static/vendor/ag-grid.css`
- `app/static/vendor/ag-theme-quartz.min.css`

License: **MIT**

```text
The MIT License

Copyright (c) 2015-2026 AG GRID LTD

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Upstream: <https://www.ag-grid.com/> · <https://github.com/ag-grid/ag-grid>

AG Grid Enterprise features are **not** included. The project uses the Community
edition only.

---

## Python runtime dependencies

| Package | License | Project |
| --- | --- | --- |
| FastAPI | MIT | <https://github.com/fastapi/fastapi> |
| Uvicorn | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| Pydantic | MIT | <https://github.com/pydantic/pydantic> |
| pydantic-core | MIT | <https://github.com/pydantic/pydantic-core> |
| annotated-types | MIT | <https://github.com/annotated-types/annotated-types> |
| typing-extensions | PSF-2.0 | <https://github.com/python/typing_extensions> |
| python-multipart | Apache-2.0 | <https://github.com/Kludex/python-multipart> |
| httpx | BSD-3-Clause | <https://github.com/encode/httpx> |
| httpcore | BSD-3-Clause | <https://github.com/encode/httpcore> |
| anyio | MIT | <https://github.com/agronholm/anyio> |
| h11 | MIT | <https://github.com/python-hyper/h11> |
| certifi | MPL-2.0 | <https://github.com/certifi/python-certifi> |
| idna | BSD-3-Clause | <https://github.com/kjd/idna> |
| sniffio | MIT | <https://github.com/python-trio/sniffio> |
| click | BSD-3-Clause | <https://github.com/pallets/click> |
| watchfiles | MIT | <https://github.com/samuelcolvin/watchfiles> |
| websockets | BSD-3-Clause | <https://github.com/python-websockets/websockets> |
| starlette | BSD-3-Clause | <https://github.com/encode/starlette> |

`uvicorn[standard]` pulls in `click`, `watchfiles`, `websockets`, `httptools` and
`uvloop` on supported platforms. Platform-specific optional packages keep their
own licenses; review the installed environment before redistribution.

---

## Development dependencies

| Package | License | Project |
| --- | --- | --- |
| pytest | MIT | <https://github.com/pytest-dev/pytest> |
| pytest-asyncio | Apache-2.0 | <https://github.com/pytest-dev/pytest-asyncio> |
| Ruff (optional) | MIT | <https://github.com/astral-sh/ruff> |

Development dependencies are not required to run the application.

---

## External services

The app can call the following services **only when configured**:

- **PayPal REST APIs** (sandbox or live). Use is governed by the
  [PayPal Developer Agreement](https://developer.paypal.com/api/rest/) and
  applicable PayPal terms. MandatePay is not affiliated with or endorsed by
  PayPal.
- **OpenAI API**, **Anthropic API** or a local **Ollama** server for optional
  planning/verification. Their terms and data-handling policies apply when you
  enable them.

No service is called by default. The offline demo uses the built-in mock PayPal
adapter and deterministic rules engines.

---

## Demo assets

The files under `samples/` (`concept-board.svg`, `logo-draft.svg`,
`logo-final.svg`, `brand-guide.md`) and `docs/screenshot.png` were created for
this project. They are licensed under the same MIT License as the rest of the
repository. They do not contain third-party trademarks or stock assets.

The "Hearth & Crumb" bakery used in the demo is fictional.
