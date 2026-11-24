// Google Docs renders the local caret as `.kix-cursor-caret` while keeping the
// editable iframe itself off-screen. This script reports the visible local
// rectangle only. It never calls Selection.toString(), inspects text nodes, or
// sends a document URL/title/keyboard event to the native side.

(() => {
  "use strict";

  const MIN_SEND_INTERVAL_MS = 80;
  const PERIODIC_REFRESH_MS = 240;
  const session = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "_");
  let sequence = 0;
  let lastSendAt = 0;
  let scheduled = false;
  let wasActive = false;

  function rectIsVisible(rect) {
    return Number.isFinite(rect.left) && Number.isFinite(rect.top) &&
      Number.isFinite(rect.right) && Number.isFinite(rect.bottom) &&
      rect.left >= -2 && rect.top >= -2 &&
      rect.right <= window.innerWidth + 2 && rect.bottom <= window.innerHeight + 2 &&
      rect.width <= 64 && rect.height >= 1 && rect.height <= 320;
  }

  function isRemoteCursor(element) {
    for (let node = element; node && node !== document.documentElement; node = node.parentElement) {
      const className = typeof node.className === "string" ? node.className : "";
      if (/remote|collaborator|other[-_]?user/i.test(className) ||
          node.hasAttribute("data-remote-cursor") || node.hasAttribute("data-collaborator")) {
        return true;
      }
    }
    return false;
  }

  function collapsedSelectionRect() {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount !== 1 || !selection.isCollapsed) {
      return null;
    }
    // Bounding a collapsed range obtains geometry only; it does not read the
    // selected content. Docs sometimes omits it, so it is only a local-cursor
    // disambiguator rather than the data source sent to Violet.
    const rect = selection.getRangeAt(0).getBoundingClientRect();
    return rectIsVisible(rect) ? rect : null;
  }

  function visibleLocalCaret() {
    const candidates = [];
    for (const element of document.querySelectorAll(".kix-cursor-caret")) {
      if (isRemoteCursor(element)) {
        continue;
      }
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden") {
        continue;
      }
      const rect = element.getBoundingClientRect();
      if (rectIsVisible(rect)) {
        candidates.push(rect);
      }
    }
    if (candidates.length === 1) {
      return candidates[0];
    }
    const selectionRect = collapsedSelectionRect();
    if (!selectionRect || candidates.length === 0) {
      return null;
    }
    const selectionX = (selectionRect.left + selectionRect.right) / 2;
    const selectionY = (selectionRect.top + selectionRect.bottom) / 2;
    let nearest = null;
    let nearestDistance = Infinity;
    for (const candidate of candidates) {
      const x = (candidate.left + candidate.right) / 2;
      const y = (candidate.top + candidate.bottom) / 2;
      const distance = Math.hypot(x - selectionX, y - selectionY);
      if (distance < nearestDistance) {
        nearest = candidate;
        nearestDistance = distance;
      }
    }
    // Never guess between collaborators. A collapsed selection gives a
    // geometry-only, local reference and must be close to the local caret.
    return nearestDistance <= 16 ? nearest : null;
  }

  function activeDocument() {
    return document.visibilityState === "visible" && document.hasFocus();
  }

  function sendClear() {
    if (wasActive) {
      wasActive = false;
      chrome.runtime.sendMessage({ type: "clear", version: 1 });
    }
  }

  function sendSnapshot() {
    scheduled = false;
    if (!activeDocument()) {
      sendClear();
      return;
    }
    wasActive = true;
    const rect = visibleLocalCaret();
    if (!rect) {
      return;
    }
    const now = performance.now();
    if (now - lastSendAt < MIN_SEND_INTERVAL_MS) {
      return;
    }
    lastSendAt = now;
    sequence += 1;
    chrome.runtime.sendMessage({
      type: "caret",
      version: 1,
      session,
      sequence,
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      focused: true,
      visible: true,
    });
  }

  function schedule() {
    if (!scheduled) {
      scheduled = true;
      requestAnimationFrame(sendSnapshot);
    }
  }

  for (const eventName of ["selectionchange", "keyup", "pointerup", "scroll", "resize", "focus"]) {
    window.addEventListener(eventName, schedule, true);
  }
  window.addEventListener("blur", sendClear, true);
  document.addEventListener("visibilitychange", () => {
    if (activeDocument()) {
      schedule();
    } else {
      sendClear();
    }
  });
  new MutationObserver(schedule).observe(document.documentElement, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style"],
  });
  window.setInterval(schedule, PERIODIC_REFRESH_MS);
  schedule();
})();
