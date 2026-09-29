"use strict";
// Shared by Tabler, Framework7 and the Angular shell. Only explicit local views
// are intercepted; documents, downloads and side-effect actions stay native.
window.SmoothNavigation = (() => {
  const canPrefetch = () =>
    !document.hidden &&
    !navigator.connection?.saveData &&
    !/^(slow-2g|2g)$/.test(navigator.connection?.effectiveType || "");

  function createReadCache({
    allowed,
    load,
    maxAge = 1500,
    staleAge = 15000,
    maxEntries = 8,
  }) {
    const entries = new Map(),
      pending = new Map();
    const stats = {
      hits: 0,
      staleHits: 0,
      misses: 0,
      deduplicated: 0,
      cancelled: 0,
    };
    let epoch = 0;
    function invalidate() {
      epoch++;
      entries.clear();
      for (const { controller } of pending.values()) {
        controller.abort();
        stats.cancelled++;
      }
      pending.clear();
    }
    async function read(key, { fresh = false, stale = false } = {}) {
      if (!allowed.includes(key))
        throw new Error("Read cache requires an explicit safe GET allowlist");
      const entry = entries.get(key),
        age = entry ? performance.now() - entry.at : Infinity;
      if (!fresh && age < maxAge) {
        stats.hits++;
        return structuredClone(entry.value);
      }
      if (!fresh && stale && age < staleAge) {
        stats.staleHits++;
        void read(key, { fresh: true }).catch(() => {});
        return structuredClone(entry.value);
      }
      if (pending.has(key)) {
        stats.deduplicated++;
        return structuredClone(await pending.get(key).task);
      }
      const controller = new AbortController(),
        version = epoch;
      stats.misses++;
      const task = Promise.resolve()
        .then(() => load(key, controller.signal))
        .then((value) => {
          if (version !== epoch || controller.signal.aborted)
            throw new DOMException("Obsolete read", "AbortError");
          entries.delete(key);
          entries.set(key, {
            value: structuredClone(value),
            at: performance.now(),
          });
          if (entries.size > maxEntries)
            entries.delete(entries.keys().next().value);
          return value;
        })
        .finally(() => {
          if (pending.get(key)?.task === task) pending.delete(key);
        });
      pending.set(key, { controller, task });
      return structuredClone(await task);
    }
    return { read, invalidate, stats };
  }

  function create({
    views,
    initial,
    render,
    canNavigate = () => true,
    prefetch = () => {},
    documents = [],
    onError = () => {},
    scrollElement = () => window,
    focusTarget = () => null,
    toggleToInitial = false,
    preserveScroll = false,
    animate = true,
  }) {
    const routes = new Set(views),
      positions = new Map();
    let current = routes.has(new URL(location.href).searchParams.get("view"))
      ? new URL(location.href).searchParams.get("view")
      : initial;
    let sequence = 0,
      transition = null,
      destroyed = false;
    // LAN HTTP deployments are not secure contexts; history IDs aren't secrets.
    const newId = () =>
      crypto.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    let entryId = history.state?.smoothNavigation?.id || newId();
    let prefetchTimer,
      queuedView,
      prefetchRunning = false;
    const warmedDocuments = new Set();
    const url = (view) => {
      const target = new URL(location.href);
      target.searchParams.set("view", view);
      return target;
    };
    const getPosition = () => {
      const el = scrollElement();
      return el === window
        ? { x: scrollX, y: scrollY }
        : { x: el.scrollLeft, y: el.scrollTop };
    };
    const restore = (point) => {
      const el = scrollElement();
      el.scrollTo({
        left: point?.x || 0,
        top: point?.y || 0,
        behavior: "instant",
      });
    };
    history.replaceState(
      {
        ...history.state,
        smoothNavigation: {
          ...history.state?.smoothNavigation,
          id: entryId,
          view: current,
        },
      },
      "",
      location.href,
    );
    const previousRestoration = history.scrollRestoration;
    history.scrollRestoration = "manual";
    if (history.state?.smoothNavigation?.position)
      positions.set(entryId, history.state.smoothNavigation.position);
    const saveScroll = () => {
      const position = getPosition();
      positions.set(entryId, position);
      if (history.state?.smoothNavigation?.id === entryId)
        history.replaceState(
          {
            ...history.state,
            smoothNavigation: { id: entryId, view: current, position },
          },
          "",
          location.href,
        );
    };
    document.addEventListener("scroll", saveScroll, true);
    window.addEventListener("scroll", saveScroll);

    function warm(view) {
      if (
        !routes.has(view) ||
        !canNavigate(view) ||
        !canPrefetch() ||
        destroyed
      )
        return;
      queuedView = view;
      clearTimeout(prefetchTimer);
      prefetchTimer = setTimeout(async () => {
        if (prefetchRunning || destroyed || !canPrefetch()) return;
        prefetchRunning = true;
        const target = queuedView;
        queuedView = null;
        try {
          await prefetch(target);
        } catch {
          /* Intent failure never blocks navigation. */
        } finally {
          prefetchRunning = false;
          if (queuedView) warm(queuedView);
        }
      }, 70);
    }

    function navigate(
      view,
      { replace = false, pop = false, focus = true } = {},
    ) {
      if (!routes.has(view) || !canNavigate(view) || destroyed)
        return Promise.resolve(false);
      if (!pop && view === current) return Promise.resolve(true);
      const start = performance.now(),
        ticket = ++sequence;
      transition?.skipTransition();
      if (!pop) {
        saveScroll();
        const outgoingPosition = getPosition();
        entryId = replace ? entryId : newId();
        if (preserveScroll) positions.set(entryId, outgoingPosition);
        history[replace ? "replaceState" : "pushState"](
          { ...history.state, smoothNavigation: { id: entryId, view } },
          "",
          url(view),
        );
      } else entryId = history.state?.smoothNavigation?.id || newId();
      current = view;
      // Data is never awaited inside a transition snapshot. Render the real
      // loading/old-data state immediately; the caller owns data freshness.
      let committed = false;
      const update = () => {
        if (ticket !== sequence || destroyed || committed) return;
        committed = true;
        render(view);
        if (focus) {
          const target = focusTarget(view);
          if (target) {
            target.setAttribute("tabindex", "-1");
            target.focus({ preventScroll: true });
          }
        }
        restore(positions.get(entryId));
        document.dispatchEvent(
          new CustomEvent("smooth:navigated", { detail: { view, pop } }),
        );
      };
      // Shell swaps are short; nested expansions already have framework motion.
      const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
      let done;
      let watchdog;
      try {
        if (document.startViewTransition && !reduced && animate) {
          transition = document.startViewTransition(update);
          const activeTransition = transition;
          const boundedCommit = new Promise((resolve) => {
            watchdog = setTimeout(() => {
              if (!committed && ticket === sequence) {
                update();
                activeTransition.skipTransition();
              }
              resolve();
            }, 80);
          });
          transition.ready.catch(() => {});
          transition.finished.catch(() => {});
          done = Promise.race([
            transition.updateCallbackDone,
            boundedCommit,
          ]).catch((error) => {
            onError(error);
            update();
          });
        } else {
          update();
          done = Promise.resolve();
        }
      } catch (error) {
        onError(error);
        update();
        done = Promise.resolve();
      }
      warm(view);
      return done
        .finally(() => clearTimeout(watchdog))
        .then(
          () =>
            new Promise((resolve) =>
              requestAnimationFrame(() => {
                if (ticket === sequence && committed) {
                  performance.mark("smooth-navigation-visible");
                  const detail = {
                    view,
                    contentVisibleMs: performance.now() - start,
                  };
                  document.dispatchEvent(
                    new CustomEvent("smooth:measured", { detail }),
                  );
                }
                resolve(committed);
              }),
            ),
        );
    }

    function routeFor(target) {
      const link =
        target instanceof Element
          ? target.closest("a[data-smooth-view]")
          : null;
      if (
        !link ||
        link.hasAttribute("download") ||
        (link.target && link.target !== "_self") ||
        link.dataset.noPrefetch !== undefined
      )
        return null;
      const dest = new URL(link.href, location.href),
        here = new URL(location.href);
      if (
        dest.origin !== here.origin ||
        dest.pathname !== here.pathname ||
        !routes.has(link.dataset.smoothView)
      )
        return null;
      return { link, view: link.dataset.smoothView };
    }
    function warmDocument(target) {
      const link =
        target instanceof Element
          ? target.closest("a[data-smooth-document]")
          : null;
      if (
        !link ||
        !canPrefetch() ||
        link.target ||
        link.hasAttribute("download")
      )
        return;
      const dest = new URL(link.href, location.href);
      if (
        dest.origin !== location.origin ||
        dest.search ||
        dest.hash ||
        !documents.includes(dest.pathname) ||
        warmedDocuments.has(dest.href)
      )
        return;
      warmedDocuments.add(dest.href);
      const hint = document.createElement("link");
      hint.rel = "prefetch";
      hint.href = dest.href;
      document.head.append(hint);
    }
    const click = (event) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      )
        return;
      const route = routeFor(event.target);
      if (!route || !canNavigate(route.view)) return;
      event.preventDefault();
      event.stopPropagation();
      void navigate(
        toggleToInitial && route.view === current ? initial : route.view,
      ).catch(onError);
    };
    const intent = (event) => {
      const route = routeFor(event.target);
      if (route) warm(route.view);
      else warmDocument(event.target);
    };
    const popstate = () => {
      const view = new URL(location.href).searchParams.get("view") || initial;
      void navigate(routes.has(view) ? view : initial, {
        pop: true,
        focus: false,
      }).catch(onError);
    };
    document.addEventListener("click", click, true);
    document.addEventListener("pointerover", intent);
    document.addEventListener("focusin", intent);
    window.addEventListener("popstate", popstate);
    const observer =
      typeof IntersectionObserver === "function"
        ? new IntersectionObserver(
            (items) => {
              for (const item of items)
                if (item.isIntersecting) {
                  warm(item.target.dataset.smoothView);
                  observer.unobserve(item.target);
                }
            },
            { rootMargin: "120px" },
          )
        : null;
    const observe = () =>
      document
        .querySelectorAll("a[data-smooth-view]")
        .forEach((el) => observer?.observe(el));
    observe();
    return {
      navigate,
      warm,
      observe,
      url: (view) => url(view).pathname + url(view).search + url(view).hash,
      get current() {
        return current;
      },
      restore: () => {
        render(current);
        requestAnimationFrame(() => restore(positions.get(entryId)));
      },
      restoreScroll: () =>
        requestAnimationFrame(() => restore(positions.get(entryId))),
      destroy() {
        destroyed = true;
        sequence++;
        clearTimeout(prefetchTimer);
        transition?.skipTransition();
        observer?.disconnect();
        document.removeEventListener("click", click, true);
        document.removeEventListener("pointerover", intent);
        document.removeEventListener("focusin", intent);
        document.removeEventListener("scroll", saveScroll, true);
        window.removeEventListener("scroll", saveScroll);
        window.removeEventListener("popstate", popstate);
        history.scrollRestoration = previousRestoration;
      },
    };
  }
  return { create, createReadCache, canPrefetch };
})();
