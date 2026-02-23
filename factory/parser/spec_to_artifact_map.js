"use strict";

const BLUEPRINTS = {
  focus_stability_dashboard: [
    "core/dashboard.js",
    "core/heatmap.js",
    "core/public/dashboard.html",
    "core/public/app.js",
    "README.md",
  ],
  behavior_anomaly_monitor: [
    "core/monitor.js",
    "core/alert_engine.js",
    "core/public/alert_panel.html",
    "core/public/app.js",
    "README.md",
  ],
  recovery_pattern_analysis: [
    "core/analysis.js",
    "core/recovery_model.js",
    "core/public/recovery_dashboard.html",
    "core/public/app.js",
    "README.md",
  ],
  habit_tracker: [
    "core/habit.js",
    "core/streak_engine.js",
    "core/public/habit_dashboard.html",
    "core/public/app.js",
    "README.md",
  ],
  sleep_monitor: [
    "core/sleep_analysis.js",
    "core/sleep_model.js",
    "core/public/sleep_dashboard.html",
    "core/public/app.js",
    "README.md",
  ],
  productivity_timer: [
    "core/timer.js",
    "core/session_engine.js",
    "core/public/timer.html",
    "core/public/app.js",
    "README.md",
  ],
  api_service_mode: [
    "core/api.js",
    "core/router.js",
    "core/server.js",
    "README.md",
  ],
  offline_local_app: [
    "core/local_store.js",
    "core/app.js",
    "core/public/index.html",
    "README.md",
  ],
  realtime_stream_processor: [
    "core/stream.js",
    "core/event_handler.js",
    "core/public/stream_dashboard.html",
    "README.md",
  ],
  physiological_monitor_app: [
    "core/physio_monitor.js",
    "core/alert_engine.js",
    "core/public/physio_dashboard.html",
    "README.md",
  ],
  default: [
    "core/app.js",
    "core/public/index.html",
    "README.md",
  ],
};

/**
 * Build deterministic sorted unique file list.
 * @param {string[]} files
 * @returns {string[]}
 */
function normalizeFiles(files) {
  return Array.from(new Set(files.filter((x) => typeof x === "string" && x))).sort();
}

/**
 * Apply deterministic artifact blueprint based on spec.ai_task.
 * Backward-compatible: preserves existing keys while enforcing contracts fields.
 *
 * @param {Record<string, unknown>} spec
 * @returns {Record<string, unknown>}
 */
function applyArtifactBlueprint(spec) {
  if (!spec || typeof spec !== "object") {
    throw new Error("applyArtifactBlueprint requires a spec object");
  }

  const aiTask = typeof spec.ai_task === "string" ? spec.ai_task : "";
  const files = BLUEPRINTS[aiTask] || BLUEPRINTS.default;
  const existingContracts =
    spec.contracts && typeof spec.contracts === "object" ? spec.contracts : {};

  spec.contracts = {
    ...existingContracts,
    files: normalizeFiles(files.slice()),
    behaviors: ["emit local notification"],
    test_rules: ["syntax", "smoke"],
  };

  return spec;
}

/**
 * Apply optional UI preference variation to contracts.files.
 *
 * Rules:
 * - theme=dark => add core/public/theme-dark.css
 * - layout=panel => add core/public/panel_layout.js
 * - layout=minimal => replace core/public/*.html with core/public/minimal.html
 *
 * @param {Record<string, unknown>} spec
 * @param {{theme?:string,layout?:string,density?:string,navigation?:string}} uiPreference
 * @returns {Record<string, unknown>}
 */
function applyUiPreferenceVariation(spec, uiPreference) {
  if (!spec || typeof spec !== "object") {
    throw new Error("applyUiPreferenceVariation requires a spec object");
  }

  if (!uiPreference || typeof uiPreference !== "object") {
    return spec;
  }

  const contracts = spec.contracts && typeof spec.contracts === "object" ? spec.contracts : {};
  let files = Array.isArray(contracts.files) ? contracts.files.slice() : [];

  const theme = typeof uiPreference.theme === "string" ? uiPreference.theme : "";
  const layout = typeof uiPreference.layout === "string" ? uiPreference.layout : "";

  if (theme === "dark") {
    files.push("core/public/theme-dark.css");
  }

  if (layout === "panel") {
    files.push("core/public/panel_layout.js");
  }

  if (layout === "minimal") {
    files = files.filter((x) => !/^core\/public\/.*\.html$/.test(x));
    files.push("core/public/minimal.html");
  }

  spec.contracts = {
    ...contracts,
    files: normalizeFiles(files),
  };

  return spec;
}

module.exports = {
  applyArtifactBlueprint,
  applyUiPreferenceVariation,
};
