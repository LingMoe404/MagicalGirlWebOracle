# Third-party notices

MagicalGirlWebOracle is MIT-licensed (see [LICENSE](LICENSE)). It depends on
third-party software that keeps its own license and copyright. This file lists
the components that are bundled or required at runtime.

## Bundled frontend assets

The prebuilt admin bundle in `src/magicalgirl_web_oracle/admin/static/`
(`react-vendor.js`, `fluent-vendor.js`, `app.js`, `index.css`) contains code
from:

| Component | License | Copyright |
|---|---|---|
| React / React DOM | MIT | Meta Platforms, Inc. and affiliates |
| Fluent UI React Components v9 | MIT | Microsoft Corporation |
| Fluent UI React Icons | MIT | Microsoft Corporation |
| `@griffel/*` | MIT | Microsoft Corporation |
| `scheduler` | MIT | Meta Platforms, Inc. and affiliates |

Those MIT-licensed components are distributed under the following notice:

```text
MIT License

Copyright (c) Meta Platforms, Inc. and affiliates.
Copyright (c) Microsoft Corporation.

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

## Python dependencies

These packages are installed separately by `pip` (they are not vendored into
this repository's wheel). Each package ships its own license metadata; the
summary below is provided for convenience.

| Package | License | Project |
|---|---|---|
| httpx, httpcore | BSD-3-Clause | https://github.com/encode/httpx |
| pydantic, pydantic-core | MIT | https://github.com/pydantic/pydantic |
| PyYAML | MIT | https://github.com/yaml/pyyaml |
| mcp | MIT | https://github.com/modelcontextprotocol/python-sdk |
| click | BSD-3-Clause | https://github.com/pallets/click |
| python-dotenv | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| starlette | BSD-3-Clause | https://github.com/encode/starlette |
| sse-starlette | BSD-3-Clause | https://github.com/sysid/sse-starlette |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| tzdata | Apache-2.0 | https://github.com/python/tzdata |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| anyio | MIT | https://github.com/agronholm/anyio |
| idna | BSD-3-Clause | https://github.com/kjd/idna |
| jsonschema | MIT | https://github.com/python-jsonschema/jsonschema |
| PyJWT | MIT | https://github.com/jpadilla/pyjwt |
| python-multipart | Apache-2.0 | https://github.com/Kludex/python-multipart |
| attrs | MIT | https://github.com/python-attrs/attrs |
| referencing | MIT | https://github.com/python-jsonschema/referencing |
| rpds-py | MIT | https://github.com/crate-py/rpds |
| typing-extensions | PSF-2.0 | https://github.com/python/typing_extensions |

Transitive dependencies are governed by their own licenses. When distributing
the Docker image, the installed package metadata inside the image contains the
complete license files for each package.

## Artwork

The MagicalGirlWebOracle icons in [`assets/icon/`](assets/icon/) are original
artwork by LingMoe404. They are distributed under the same MIT license as the
rest of this project. See [`assets/icon/README.md`](assets/icon/README.md) for
the available formats.

## Trademarks

Provider names (Exa, Tavily, Firecrawl, Jina, You.com, SearXNG, Crawl4AI) are
trademarks or product names of their respective owners. This project is not
affiliated with or endorsed by any of them. "Oracle" in the project name is
used as a descriptive word; this project is not affiliated with Oracle
Corporation.
