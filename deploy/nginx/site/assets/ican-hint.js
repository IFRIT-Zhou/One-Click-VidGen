(function () {
  "use strict";
  const message = "ICAN 号池不稳定，如无法使用，可优先尝试稳定版。";
  const tip = document.createElement("div");
  tip.id = "ican-channel-tooltip"; tip.className = "ican-channel-tooltip";
  tip.setAttribute("role", "tooltip"); tip.textContent = message; tip.hidden = true;
  document.body.append(tip);
  let active = null;
  function setup() {
    for (const option of document.querySelectorAll('select option[value="ican"]')) option.title = message;
  }
  function hide() {
    if (active?.getAttribute("aria-describedby") === tip.id) active.removeAttribute("aria-describedby");
    active = null; tip.hidden = true;
  }
  function show(target) {
    const select = target.closest?.("select");
    hide();
    if (!select || select.value !== "ican" || !select.querySelector('option[value="ican"]')) return;
    active = select; select.setAttribute("aria-describedby", tip.id); tip.hidden = false;
    const rect = select.getBoundingClientRect();
    tip.style.left = `${Math.max(8, Math.min(rect.left, innerWidth - tip.offsetWidth - 8))}px`;
    tip.style.top = `${rect.bottom + tip.offsetHeight + 12 > innerHeight ? Math.max(8, rect.top - tip.offsetHeight - 8) : rect.bottom + 8}px`;
  }
  document.addEventListener("pointerover", e => { if (e.target.closest?.("select")) show(e.target); });
  document.addEventListener("pointerout", e => { if (active === e.target) hide(); });
  document.addEventListener("focusin", e => show(e.target));
  document.addEventListener("focusout", hide);
  document.addEventListener("change", e => { if (e.target.matches?.("select")) show(e.target); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") hide(); });
  window.addEventListener("scroll", () => {
    if (active && document.activeElement === active) show(active);
    else hide();
  }, true);
  window.addEventListener("resize", hide);
  setup(); new MutationObserver(setup).observe(document.body, { childList: true, subtree: true });
})();
