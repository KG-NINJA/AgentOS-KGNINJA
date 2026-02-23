"use strict";

(function boot() {
  if (typeof document === "undefined") {
    return;
  }

  const root = document.createElement("main");
  root.textContent = "Factory fallback app is ready.";
  document.body.appendChild(root);
})();
