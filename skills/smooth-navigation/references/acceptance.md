# Navigation acceptance

Starting budgets are configurable and must be measured on the target deployment:

- Warm local view: content-visible p50 <= 50 ms, p95 <= 100 ms with reduced motion; report normal-motion figures separately.
- Route animation: 100–160 ms. Snapshot preparation watchdog <= 80 ms. Slow data must not stretch this duration.
- New navigation code: <= 15 KiB uncompressed JS and <= 2 KiB CSS, excluding existing framework/vendor files.
- Intent debounce: about 70 ms; at most one speculative group/two independent APIs. Honor Save-Data/2g/document.hidden.
- In-memory reads: eight entries, default fresh lifetime <= 1.5 s. Force-fresh live authority/accounting. Display-only SWR requires explicit stale indicators and a bounded stale lifetime.
- Same safe key and auth epoch: one overlapping GET, zero speculative POSTs, zero automatic write retries. Mutation/logout invalidation prevents a late read from refilling the new epoch.
- Conditional static revisit: matching ETag returns 304 with no body; changed assets return a new validator and full body.
- A matched independent-dependency fixture should reduce first useful data time by at least 20% without worsening warm navigation beyond the above budget. Do not imply that an artificial-delay fixture is a production SLA.

Verify browser back/forward, deep links, refresh, actual scroll container, keyboard focus, modified clicks, external links, downloads, unavailable View Transition API, snapshot failure, reduced motion, rapid navigation, no-JS shells, asset failure, network timeout, unauthorized/expired identity, busy mutations, ambiguous POST timeout and logout during pending reads.

Check long customer names, addresses and credentials-output fields at mobile and desktop widths. Never truncate data permanently. Prefer wrapping and scrollable details to moving the user's viewport unexpectedly.

Measure cold first useful content and warm click-to-content with requestAnimationFrame around visible destination DOM. Report at least five cold contexts and twenty warm hops when feasible, median/p95, request counts, actual cache hits/misses and repeated-visit behavior. Use a real locally launched server with isolated stores for API/cache/auth tests, and an isolated intercepted browser fixture for mutation-prone panels. Real deployment runs require existing authorization and must not adjust the operator's network.

Official implementation references:

- [Angular View Transitions](https://angular.dev/guide/animations/route-animations)
- [Browser same-document View Transitions](https://developer.chrome.com/docs/web-platform/view-transitions/same-document)
- [aiohttp FileResponse and server responses](https://docs.aiohttp.org/en/stable/web_reference.html)

The current repository's runtime entry is `src/server_network_assist/subscription_admin_ui/smooth-navigation.js`; its CSS is adjacent. Angular's `frontend/prepare-navigation.cjs` generates the public copies. The customer HTTP handler serves the same authored files. `frontend/src/app/smooth-navigation.ts` describes the typed Angular adapter. Check the implementation report for test evidence instead of treating these pointers as proof of deployment.
