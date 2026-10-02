---
name: smooth-navigation
description: Optimize perceived and measured page navigation latency in an existing web application through framework-native routing, safe next-hop preloading, bounded caches, short View Transitions, and navigation performance verification.
---

# Smooth Navigation

Deliver useful destination content promptly. Keep animation separate from network work: transition a real loading state immediately when data is slow. Do not claim that animation makes a slow API fast. Preserve existing business logic, authorization, and network configuration.

## Inspect before adapting

Locate client and server entry points, framework/version, route configuration, navigation buttons/cards/menus, loaders, auth boundaries, static serving, build scripts and existing skills conventions. Read applicable AGENTS.md. Inventory ordinary navigation separately from submissions, login/logout, payment, enrollment, subscription changes, downloads and network control.

Record the unchanged checkout's cold and warm navigation timings before editing. Save a bounded snapshot or isolated build rather than altering the user's working tree to switch versions. Keep matched browser, viewport, route, account, data volume, motion setting, latency and CPU conditions; report distributions and cache/network counts. Never substitute a historical metric for a matched baseline.

## Choose the existing framework's mechanism

| Existing stack | Preferred integration |
| --- | --- |
| Astro | ClientRouter, lifecycle hooks and matching transition names; use Astro prefetch facilities |
| Angular with Router | RouterLink, route resolvers/preloading, withViewTransitions; cancel subscriptions on destruction |
| Angular with existing signal-selected views | Keep its shell; one typed navigation adapter, detectChanges inside the transition callback; do not introduce a second competing router |
| React/Next | Framework Link/router and supported route/data prefetch; preserve server component/auth cache rules |
| Vue/Nuxt | RouterLink/router, native route loaders and transitions; reuse existing query cache |
| Svelte/SvelteKit | Link/data preloading and framework navigation/scroll lifecycle |
| Vanilla JS/Framework7 | Registered local views with a single History API controller; Framework7 accordion/view APIs for its DOM lifecycle |
| Separate server documents | Native anchors and progressive cross-document View Transitions where supported; explicitly allowlist public read-only document prefetch |

Do not install Astro to obtain its animation. Avoid a custom fetch-and-replace router when the framework already owns routing or when documents use different authentication scopes.

## One navigation entry point

Make navigation elements real anchors with usable hrefs. Send same-origin registered local views and programmatic navigation through one controller. Preserve modifier-click, new tabs, downloads, external links, hash anchors and unsupported destinations as browser navigation. Keep separate documents as native document navigations; their browser history, auth and script startup must remain intact.

For local views, preserve existing history.state fields; never put credentials, tokens or cached payloads there. Read the route from the URL on startup. Handle popstate without pushing a new entry. Save and restore the actual scroll container per history entry; refresh should reconstruct the destination, and a new destination starts at the top. Focus the new heading/main without accidentally scrolling to the navigation menu. Add aria-current and retain keyboard semantics.

Preserve drafts, filters, selection and expensive read-only view state where useful. Keep a subscription editor mounted while hidden instead of recreating it on every visit. Do not keep hidden terminal/socket/polling views alive indiscriminately. Unsubscribe hidden read consumers and stop unnecessary polling. Clear credentials and identity-scoped caches at login/logout, mutation and authorization failure; changes of subscription/account must not reuse another identity's data.

## Safe next-hop preparation

Use explicit route-to-loader allowlists. Trigger small read-only loads on pointer intent, keyboard focus and near-viewport visibility. Skip background/hidden tabs, Save-Data and slow connections. Debounce intent (around 70 ms); cap speculative concurrency (one group, at most two APIs) and cache entries (eight small payloads by default). Avoid unlimited prefetch over customer rows or menus. Target the likely next view; already bundled local views need data preparation, not a second HTML request.

Preload route modules/assets with the framework or known public resources. Request independent page/data dependencies concurrently after authentication, not serially after a click. Reuse a pending read for the foreground consumer. Handle speculative failures silently, then surface actual foreground failures normally. Preload neither a one-use enrollment URL nor any action endpoint merely because it uses GET.

Never prefetch or automatically retry a form submission, login/logout, payment, subscription update/selection/removal, lease/connect/disconnect, authentication logout, source-proxy start or a state-changing diagnostic. Keep their existing explicit confirmation, CSRF, origin/token checks, busy guards and ambiguous-timeout warnings. Do not deduplicate writes: ensure the UI submits them once and let existing business idempotency rules govern retries.

## Bounded reads, caching and cancellation

Separate public static caching, identity-scoped in-memory read caching and authorization state. Use validators (ETag/Last-Modified) for versionless static files, immutable caching only for genuinely versioned resources. HTML shells and confidential APIs may remain no-store. Never put signed device requests/nonces in shared caches or reuse their HTTP responses across identities.

An in-memory read cache should use keyed single-flight requests, a short freshness window, bounded size, copied payloads and an invalidation epoch. Abort obsolete speculative reads and discard their late responses. A cancelled observer must not cancel a foreground consumer of the same shared request. If a write starts, clear relevant entries before and after it; if its result is ambiguous, keep mutation controls locked until a fresh read resolves uncertainty.

Stale-while-revalidate is appropriate for display-only labels and summaries with a clear stale indicator. It must not determine whether an authorization, payment, quota, enrollment or network control is allowed. If stale display is retained after failure, lock affected mutations and require a fresh snapshot. Prefer forced-fresh reads for live availability and accounting; do not add a stale TTL to a security decision to satisfy a cache-hit target.

Keep a monotonically increasing request/view generation. Cancel route-owned GETs on exit when no consumer needs them; ignore obsolete successes/errors. Never cancel or replay an already submitted write in response to navigation. If the backend shares simultaneous global health probes, retain only the running task, not a TTL of authorization/proxy readiness, and discard its slot around writes.

## Continuous but short visuals

Use startViewTransition or the framework equivalent only around a pure DOM/state commit. Give semantic heading/shared elements one unique transition name per visible snapshot. For a card/detail title, derive a stable safe name from the entity and assign it to the corresponding destination title; avoid duplicate names when both nodes are visible. Use a 100–160 ms ease-out transition; never await fetch inside a snapshot callback.

Feature-detect browser support, honor prefers-reduced-motion in JS and CSS, and commit synchronously when motion is reduced or unavailable. Skip previous transitions on rapid clicks; protect the newest commit with a generation. Bound snapshot preparation with a short watchdog (around 80 ms), and fall back to the pure commit once. Handle rejected transition promises. Framework accordions should retain their own short transition rather than stacking multiple route animations.

## Lifecycle and fallback

Use Astro after-swap/page-load hooks or the actual framework's lifecycle; do not attach duplicate document handlers after every visit. A delegated local-view controller should install listeners once, observe newly rendered links explicitly, dispatch a completion event, and expose destroy() for cleanup. Retained DOM keeps its original business event handlers.

If the navigation asset fails, use native href navigation or a bounded synchronous local fallback. If data fails, preserve readable previous data, report the real error and retain the existing security locks. Test no-JS static shell and ordinary links. Do not claim that an existing JS-only authenticated application now supports its business workflow without JS; that would require separately scoped SSR/form endpoints. Keep login fields from appearing in a GET query by using POST forms, with existing server-side rejection when JS/CSRF is absent. Public SEO pages must retain actual SSR/static content, title and canonical metadata; private admin pages do not need fabricated SEO content.

## Server work and acceptance

Move blocking filesystem/SQLite/probe work off an async event loop where thread safety permits. Read source inventories once per response. Parallelize independent probes, reuse simultaneous safe global reads, keep auth checks ahead of all shared work and keep existing response contracts. Do not silently omit fields, replace live status with stale success or cache across tenants. Add Server-Timing for expensive loaders without exposing secrets. Apply compression/streaming only when it fits the existing deployment and can be validated.

Use [references/acceptance.md](references/acceptance.md) for measurable budgets and the test matrix. Run the project's lint, targeted tests and production builds. If there is no configured lint tool, use the existing formatter/syntax and type checks and state exactly what ran. Separate source/build success from actual browser and deployment verification. Report changed files, architecture, representative code, results, matched before/after measurements and remaining bottlenecks; do not promise zero latency.
