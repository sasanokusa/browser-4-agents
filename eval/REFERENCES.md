# Benchmark reference answers

The 20 `current` questions in `tasks.jsonl` are snapshots **as of 2026-09-23**. Their expected answers are frozen for comparable runs; do not silently update the answer checks when running the benchmark later. Reverify and version the dataset if a new snapshot is needed. Release-series questions ask for the feature/major series; release-version questions ask for the full patch version. Each cited link is an official project, standards-body, package-index, or government source inspected for this snapshot.

| ID | Expected answer | Official source and observation |
| --- | --- | --- |
| f01 | Gecko (Firefox rendering engine) | [Mozilla Firefox source docs](https://firefox-source-docs.mozilla.org/overview/gecko.html) |
| f03 | Python Software Foundation (nonprofit support and advancement) | [Python Software Foundation about page](https://www.python.org/psf/about/). |
| c01 | Python 3.14 feature series | [Python 3.13.15 release page](https://www.python.org/downloads/release/python-31315/) explicitly identifies 3.14 as the latest feature series. |
| c02 | Firefox 156 | [MDN Firefox 156 release notes](https://developer.mozilla.org/en-US/docs/Mozilla/Firefox/Releases/156) give the 2026-09-15 stable release date. |
| c03 | Node.js 26, Current, not LTS | [Node.js release table](https://nodejs.org/en/about/previous-releases) lists v26 as Current and v24 as LTS. |
| c04 | `ubuntu-latest` → Ubuntu 24.04 LTS | Inferred from [GitHub's 26.04 migration announcement](https://github.com/actions/runner-images/issues/14748), which schedules that migration to start 2026-10-19, and [the prior 24.04 migration announcement](https://github.com/actions/runner-images/issues/10636). |
| c05 | Django 6.1 feature series | [Django 6.1 release announcement](https://www.djangoproject.com/weblog/2026/aug/05/django-61-released/) dated 2026-08-05. |
| c06 | 高市早苗 / Sanae Takaichi | [Prime Minister's Office cabinet roster](https://www.kantei.go.jp/jp/105/meibo/index.html), cabinet formed 2026-09-17. |
| c07 | Ubuntu 26.04 LTS | [Ubuntu desktop download](https://ubuntu.com/download/desktop) identifies 26.04.1 LTS as latest. |
| c08 | `html.spec.whatwg.org` | [WHATWG HTML Living Standard](https://html.spec.whatwg.org/multipage/), last updated 2026-09-21 when checked. |
| c09 | Go 1.27 series | [Go release history](https://go.dev/doc/devel/release) records Go 1.27.1 on 2026-09-01. |
| c10 | MDN Firefox for Developers 156 | [MDN Firefox 156 release notes](https://developer.mozilla.org/en-US/docs/Mozilla/Firefox/Releases/156). |
| c11 | ECMAScript 2026 / ECMA-262 17th edition | [Ecma International standard page](https://ecma-international.org/publications-and-standards/standards/ecma-262/) identifies the June 2026 edition. |
| c12 | IANA tzdb 2026d | [IANA release listing](https://www.iana.org/time-zones/releases) dates it 2026-09-11 and marks it latest. |
| c13 | PostgreSQL 18.6 stable; 19 was beta | [PostgreSQL 18 documentation](https://www.postgresql.org/docs/18/) lists 18.6 as current and 19 as development. |
| c14 | Playwright Python 1.63.0 | [PyPI Playwright 1.63.0](https://pypi.org/project/playwright/1.63.0/), released 2026-09-15. |
| c15 | Rust 1.98.1 stable | [Rust release announcement](https://blog.rust-lang.org/releases/latest/) dated 2026-09-03. |
| c16 | Node.js v24 LTS, Krypton | [Node.js release table](https://nodejs.org/en/about/previous-releases). |
| c17 | SQLite 3.53.4 | [SQLite home page](https://www.sqlite.org/index.html) lists it as the latest release dated 2026-07-24. |
| c18 | Chrome desktop Stable 154 | [Chrome release announcement](https://chromereleases.googleblog.com/2026/09/stable-channel-update-for-desktop_0856730748.html) dated 2026-09-22. |
| c19 | Firefox ESR 153 | [Mozilla Firefox 156 enterprise release notes](https://firefox-admin-docs.mozilla.org/release-notes/version/firefox-156/) identify ESR 153 as current. |
| c20 | `POST /v1/responses` | [OpenAI Responses migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses) gives the method and path. |

Comparison-task claims use these primary sources: [Selenium WebDriver](https://www.selenium.dev/documentation/webdriver/) and [Playwright browser support](https://playwright.dev/docs/browsers) (m01); [Mozilla on Firefox](https://developer.mozilla.org/en-US/docs/Glossary/Mozilla_Firefox) and [Chrome team on Chromium](https://developer.chrome.com/blog/we-are-chrome-for-developers) (m02); [SQLite deployment guidance](https://www.sqlite.org/whentouse.html) and [PostgreSQL architecture](https://www.postgresql.org/docs/17/tutorial-arch.html) (m03); [HTTP/2 RFC 9113](https://www.rfc-editor.org/info/rfc9113/) (m04); [MDN JavaScript introduction](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Introduction) and [Pyodide browser runtime](https://pyodide.org/en/0.18.0/) (m05); [SearXNG documentation](https://docs.searxng.org/) and [DuckDuckGo help](https://duckduckgo.com/duckduckgo-help-pages/results/sources) (m06); [OpenAPI specification](https://swagger.io/specification/) and [MCP tools specification](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) (m07); [HTTP semantics RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html) (m08); [Mozilla Gecko documentation](https://firefox-source-docs.mozilla.org/overview/gecko.html) and [Chrome Blink documentation](https://developer.chrome.com/docs/web-platform/blink) (m09); [Camoufox project source](https://github.com/daijro/camoufox) and [Playwright Firefox documentation](https://playwright.dev/docs/browsers) (m10).

The checks are deterministic text heuristics, not a semantic judge. For comparison tasks, a model can still make a contradictory statement containing the required terms, and a correct paraphrase may be missed. The stricter patterns reduce the earlier false positives, while retaining the runner's existing `regex`/`any` check format.
