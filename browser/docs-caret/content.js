// Google Docs keeps its actual keyboard target in an off-screen iframe, but
// renders the local insertion caret as #kix-current-user-cursor-caret. This
// script reports only that one rectangle. It never reads text nodes, document
// identity, selection text, clipboard data, or keystrokes.

(() => {
  "use strict";

  const LOCAL_CARET_ID = "kix-current-user-cursor-caret";
  const FRAME_READ_INTERVAL_MS = 32;
  const HEARTBEAT_INTERVAL_MS = 250;
  const RETAIN_HIDDEN_LOCAL_RECT_MS = 900;
  const session = (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`).replace(/[^A-Za-z0-9_-]/g, "_");
  let sequence = 0;
  let monitoring = false;
  let localCaret = null;
  let lastLocalRect = null;
  let lastLocalRectAt = 0;
  let lastSentRect = null;
  let lastSentAt = 0;
  let wasActive = false;

  function activeDocument() {
    return document.visibilityState === "visible" && document.hasFocus();
  }

  function copyRect(rect) {
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
    };
  }

  function validCaretShape(rect) {
    return Number.isFinite(rect.left) && Number.isFinite(rect.top) &&
      Number.isFinite(rect.right) && Number.isFinite(rect.bottom) &&
      rect.right >= rect.left && rect.bottom > rect.top &&
      rect.right - rect.left <= 64 && rect.bottom - rect.top <= 320;
  }

  function inViewport(rect) {
    return rect.left >= -2 && rect.top >= -2 &&
      rect.right <= window.innerWidth + 2 && rect.bottom <= window.innerHeight + 2;
  }

  function localCaretRect(now) {
    if (!localCaret || !localCaret.isConnected || localCaret.id !== LOCAL_CARET_ID) {
      localCaret = document.getElementById(LOCAL_CARET_ID);
    }
    // Never fall back to the shared .kix-cursor-caret class while Docs has an
    // explicit current-user node. That class also names collaborator cursors;
    // selecting it caused the bubble to oscillate between two different lines.
    if (!localCaret) {
      return null;
    }
    const rect = localCaret.getBoundingClientRect();
    const style = getComputedStyle(localCaret);
    if (validCaretShape(rect) && inViewport(rect) && style.display !== "none" && style.visibility !== "hidden") {
      lastLocalRect = copyRect(rect);
      lastLocalRectAt = now;
      return lastLocalRect;
    }
    // Docs blinks or briefly detaches/reparents its current-user node. Keep
    // only the last exact local geometry through that short presentation gap;
    // do not ever substitute a collaborator or selection rectangle.
    if (lastLocalRect && now - lastLocalRectAt <= RETAIN_HIDDEN_LOCAL_RECT_MS) {
      return lastLocalRect;
    }
    return null;
  }

  function sameGeometry(left, right) {
    return left && right &&
      Math.abs(left.left - right.left) < 0.25 &&
      Math.abs(left.top - right.top) < 0.25 &&
      Math.abs(left.right - right.right) < 0.25 &&
      Math.abs(left.bottom - right.bottom) < 0.25 &&
      left.viewportWidth === right.viewportWidth &&
      left.viewportHeight === right.viewportHeight;
  }

  function sendClear() {
    if (wasActive) {
      wasActive = false;
      lastSentRect = null;
      chrome.runtime.sendMessage({ type: "clear", version: 1, session, sent_at: Date.now() });
    }
  }

  function maybeSend(rect, now) {
    const changed = !sameGeometry(rect, lastSentRect);
    if (!changed && now - lastSentAt < HEARTBEAT_INTERVAL_MS) {
      return;
    }
    lastSentRect = rect;
    lastSentAt = now;
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
      viewport_width: rect.viewportWidth,
      viewport_height: rect.viewportHeight,
      focused: true,
      visible: true,
      // The privileged service worker uses this only to reject queued events
      // from a previously focused Docs tab; it is never forwarded to Violet.
      sent_at: Date.now(),
    });
  }

  function monitorFrame() {
    if (!activeDocument()) {
      monitoring = false;
      sendClear();
      return;
    }
    wasActive = true;
    const now = performance.now();
    const rect = localCaretRect(now);
    if (rect) {
      maybeSend(rect, now);
    }
    window.setTimeout(() => requestAnimationFrame(monitorFrame), FRAME_READ_INTERVAL_MS);
  }

  function startMonitoring() {
    if (monitoring) {
      return;
    }
    monitoring = true;
    requestAnimationFrame(monitorFrame);
  }

  for (const eventName of ["keyup", "pointerup", "scroll", "resize", "focus"]) {
    window.addEventListener(eventName, startMonitoring, true);
  }
  document.addEventListener("selectionchange", startMonitoring, true);
  window.addEventListener("blur", sendClear, true);
  document.addEventListener("visibilitychange", () => {
    if (activeDocument()) {
      startMonitoring();
    } else {
      sendClear();
    }
  });
  startMonitoring();
})();
