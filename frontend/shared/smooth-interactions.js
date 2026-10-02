// BEGIN SNA SMOOTH INTERACTIONS (generated from frontend/shared; do not hand-edit copies)
const smoothUI = (() => {
  let generation = 0, activeTransition = null, activeUpdate = null;
  const pendingCounts = new WeakMap();
  const reduced = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  function reveal(element) {
    if (!element?.animate || reduced() || element.hidden) return;
    element.getAnimations?.().forEach(animation => animation.cancel());
    element.animate([{opacity: 0, transform: 'translateY(4px)'}, {opacity: 1, transform: 'none'}],
      {duration: 140, easing: 'cubic-bezier(.2,.7,.2,1)'});
  }
  function change(update) {
    const version = ++generation;
    activeTransition?.skipTransition();
    let applied = false;
    const apply = () => {
      if (applied || version !== generation) return;
      applied = true;
      update();
    };
    activeUpdate = apply;
    if (!document.startViewTransition || reduced() || document.hidden) {
      apply(); return;
    }
    let transition;
    try { transition = document.startViewTransition(apply); }
    catch { apply(); return; }
    activeTransition = transition;
    // Snapshot preparation must never hold local navigation hostage.
    const watchdog = setTimeout(() => {
      if (!applied) { apply(); transition.skipTransition(); }
    }, 80);
    transition.ready.catch(() => {});
    transition.updateCallbackDone.catch(error => console.error('UI update failed', error));
    transition.finished.catch(() => {}).finally(() => {
      clearTimeout(watchdog);
      if (activeTransition === transition) { activeTransition = null; activeUpdate = null; }
    });
  }
  function settle() {
    // Security boundaries finish pending pure UI updates synchronously.
    activeUpdate?.(); ++generation; activeTransition?.skipTransition();
    activeUpdate = null; activeTransition = null;
  }
  function pending(element = document.activeElement) {
    let button = element?.closest?.('button');
    if (!button) button = element?.closest?.('form')?.querySelector('button[type="submit"]');
    if (!button || button.closest('[data-window],#window-controls,[data-workspace]')) return () => {};
    const previous = pendingCounts.get(button);
    const record = previous || {count: 0, aria: button.getAttribute('aria-busy')};
    record.count++; pendingCounts.set(button, record);
    button.classList.add('sna-busy'); button.setAttribute('aria-busy', 'true');
    let ended = false;
    return () => {
      if (ended) return; ended = true;
      if (--record.count) return;
      pendingCounts.delete(button); button.classList.remove('sna-busy');
      if (record.aria === null) button.removeAttribute('aria-busy');
      else button.setAttribute('aria-busy', record.aria);
    };
  }
  function text(element, value) {
    if (element.textContent !== String(value)) element.textContent = value;
  }
  if (window.MutationObserver && document.body) {
    new window.MutationObserver(records => {
      for (const {target, attributeName, oldValue} of records) {
        if (attributeName === 'hidden' && oldValue !== null && !target.hidden &&
            target.matches('#service-form,#service-detail,#egress-form,#result,#notice')) reveal(target);
        if (attributeName === 'open' && oldValue === null && target.open && target.matches('dialog,details')) {
          reveal(target.matches('dialog') ? target : target.querySelector(':scope > :not(summary)'));
        }
      }
    }).observe(document.body, {subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ['hidden', 'open']});
  }
  return {change, settle, pending, reveal, text};
})();
// END SNA SMOOTH INTERACTIONS
