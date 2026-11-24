# UX audit and review — November 2026

Scope: sign-in page and the signed-in workspace (Overview, Add records, Invoices, Cash flow, Settings) on desktop
(1280×800) and phone (390×844), light and dark, with the sample business loaded.

| # | Finding | Severity | Fix | Review |
| --- | --- | --- | --- | --- |
| 1 | Switching pages kept the old scroll position, so a page opened half-way down | High | Every page change scrolls to the top (deep links to a target still scroll to it) and enters with a short slide | Pass: `scrollY` is 0 after switching |
| 2 | Phone navigation had nine tabs in a scrolling strip; four were off-screen | High | Four main pages plus **More**, which opens a sheet with every page | Pass |
| 3 | The floating Assistant button covered figures on desktop and content on phones | High | Content reserves space beneath; on phones the button is a round icon above the tab bar | Pass |
| 4 | Three banners stood above the first figure; success messages never went away | Medium | Success messages fade after six seconds; the email reminder can be dismissed for good | Pass |
| 5 | Four equal header buttons | Medium | Search, PDF and Refresh are icon buttons with labels for screen readers; Refresh spins while loading | Pass |
| 6 | Forecast methodology notes in technical language were always open | Medium | Folded into "How this forecast works" | Pass |
| 7 | Chart dates in ISO format (2026-11-24) | Low | Short local dates (24 Nov) | Pass |
| 8 | Section labels were 10 px with wide letter-spacing | Low | 12 px, normal spacing | Pass |
| 9 | Little feedback or motion; no quick way to move around | Medium | Cash totals count up once; Ctrl/⌘+K opens a quick-jump palette for every page and common actions | Pass: "loan" + Enter opens Loans |
| 10 | 3D globe behind the sign-in demo competed with the text | Medium | Removed with its library; the typing demo stays | Pass |

All motion stops when the device asks for reduced motion. Checks: backend tests, web build, Vitest, i18n and CI
scripts, and the browser golden scenario.
