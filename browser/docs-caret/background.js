// The extension's privileged half. It forwards only a fixed, geometry-only
// schema to Chrome's native-messaging host; it never receives or forwards a
// document URL, title, selected text, or keyboard data.

const HOST_NAME = "org.violet.docs_caret";
let nativePort = null;

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function isSession(value) {
  return typeof value === "string" && /^[A-Za-z0-9_-]{8,96}$/.test(value);
}

function sanitize(message) {
  if (!message || typeof message !== "object") {
    return null;
  }
  if (message.type === "clear" && message.version === 1) {
    return { type: "clear", version: 1 };
  }
  if (message.type !== "caret" || message.version !== 1 || message.focused !== true || message.visible !== true) {
    return null;
  }
  if (!Number.isSafeInteger(message.sequence) || message.sequence < 0 || !isSession(message.session)) {
    return null;
  }
  const values = [
    message.left,
    message.top,
    message.right,
    message.bottom,
    message.viewport_width,
    message.viewport_height,
  ];
  if (!values.every(finite)) {
    return null;
  }
  // Rebuild the object field-by-field. Do not forward accidental additions
  // such as a document title, URL, clipboard value, or selection text.
  return {
    type: "caret",
    version: 1,
    session: message.session,
    sequence: message.sequence,
    left: message.left,
    top: message.top,
    right: message.right,
    bottom: message.bottom,
    viewport_width: message.viewport_width,
    viewport_height: message.viewport_height,
    focused: true,
    visible: true,
  };
}

function ensurePort() {
  if (nativePort) {
    return nativePort;
  }
  try {
    const port = chrome.runtime.connectNative(HOST_NAME);
    port.onDisconnect.addListener(() => {
      if (nativePort === port) {
        nativePort = null;
      }
    });
    nativePort = port;
    return port;
  } catch (_) {
    // Violet remains fully usable with the normal Windows accessibility and
    // click-placement fallback when its optional native host is unavailable.
    return null;
  }
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (sender.id !== chrome.runtime.id) {
    return;
  }
  const payload = sanitize(message);
  if (!payload) {
    return;
  }
  const port = ensurePort();
  if (!port) {
    return;
  }
  try {
    port.postMessage(payload);
  } catch (_) {
    if (nativePort === port) {
      nativePort = null;
    }
  }
});
