"use strict";

const crypto = require("crypto");

/**
 * @typedef {Object} ParsedMeaning
 * @property {string[]} observations
 * @property {string[]} recommendedActions
 * @property {string|null} patternInterpretation
 * @property {string|null} riskLevel
 * @property {string|null} functionalIntent
 * @property {{name:string,type:string}[]} [dataModel]
 */

const TASK_PROFILE = {
  focus_stability_dashboard: { ui_type: "desktop_gui", feature_intents: ["focus_monitor"] },
  behavior_anomaly_monitor: { ui_type: "alert_panel", feature_intents: ["monitor", "alert"] },
  recovery_pattern_analysis: { ui_type: "dashboard", feature_intents: ["analysis", "behavior_feedback"] },
  habit_tracker: { ui_type: "dashboard", feature_intents: ["habit", "streak_tracking"] },
  sleep_monitor: { ui_type: "dashboard", feature_intents: ["sleep_monitoring", "analysis"] },
  productivity_timer: { ui_type: "timer_ui", feature_intents: ["focus_timer", "session_tracking"] },
  api_service_mode: { ui_type: "api", feature_intents: ["service", "endpoint"] },
  offline_local_app: { ui_type: "desktop_gui", feature_intents: ["local_storage", "offline_first"] },
  realtime_stream_processor: { ui_type: "stream_dashboard", feature_intents: ["stream", "event_processing"] },
  physiological_monitor_app: { ui_type: "dashboard", feature_intents: ["physiological_monitor", "alert"] },
  meaning_analysis: { ui_type: "dashboard", feature_intents: [] },
};

const FUNCTIONAL_INTENT_RULES = [
  { token: "focus_monitoring", ai_task: "focus_stability_dashboard" },
  { token: "anomaly_detection", ai_task: "behavior_anomaly_monitor" },
  { token: "recovery_analysis", ai_task: "recovery_pattern_analysis" },
  { token: "habit_tracker", ai_task: "habit_tracker" },
  { token: "sleep_monitor", ai_task: "sleep_monitor" },
  { token: "productivity_timer", ai_task: "productivity_timer" },
  { token: "api_service_mode", ai_task: "api_service_mode" },
  { token: "offline_local_app", ai_task: "offline_local_app" },
  { token: "realtime_stream_processor", ai_task: "realtime_stream_processor" },
  { token: "physiological_monitor_app", ai_task: "physiological_monitor_app" },
];

/**
 * Build deterministic SHA256 hash for meaning payload.
 * @param {ParsedMeaning} parsedMeaning
 * @returns {string}
 */
function buildMeaningHash(parsedMeaning) {
  const raw = JSON.stringify(parsedMeaning);
  return crypto.createHash("sha256").update(raw, "utf8").digest("hex");
}

/**
 * Return true if any entry includes a case-insensitive token.
 * @param {string[]} values
 * @param {string} token
 * @returns {boolean}
 */
function includesToken(values, token) {
  const needle = token.toLowerCase();
  return values.some((entry) => entry.toLowerCase().includes(needle));
}

/**
 * Deduplicate and sort feature intents for deterministic output.
 * @param {string[]} featureIntents
 * @returns {string[]}
 */
function normalizeFeatureIntents(featureIntents) {
  return Array.from(new Set(featureIntents.filter(Boolean))).sort();
}

/**
 * Resolve ai_task from functional intent in deterministic order.
 * @param {string} functionalIntentLower
 * @returns {string}
 */
function resolveAiTaskFromFunctionalIntent(functionalIntentLower) {
  for (const rule of FUNCTIONAL_INTENT_RULES) {
    if (functionalIntentLower.includes(rule.token)) {
      return rule.ai_task;
    }
  }
  return "meaning_analysis";
}

/**
 * Map validated Meaning markdown payload into runtime spec contract.
 * @param {ParsedMeaning} parsedMeaning
 * @returns {{
 *   project_type: "desktop_app",
 *   ai_task: string,
 *   ui_type: string,
 *   capabilities: { feature_intents: string[] },
 *   behavior_feedback?: boolean,
 *   adaptive_suggestion?: boolean,
 *   meaning_origin: { functional_intent: string|null, risk_level: string|null, hash: string }
 * }}
 */
function mapMeaningToSpec(parsedMeaning) {
  if (!parsedMeaning || typeof parsedMeaning !== "object") {
    throw new Error("mapMeaningToSpec requires a parsedMeaning object");
  }

  const observations = Array.isArray(parsedMeaning.observations) ? parsedMeaning.observations : [];
  const recommendedActions = Array.isArray(parsedMeaning.recommendedActions)
    ? parsedMeaning.recommendedActions
    : [];
  const functionalIntentRaw =
    typeof parsedMeaning.functionalIntent === "string" ? parsedMeaning.functionalIntent : "";
  const functionalIntent = functionalIntentRaw.toLowerCase();

  const aiTask = resolveAiTaskFromFunctionalIntent(functionalIntent);
  const taskProfile = TASK_PROFILE[aiTask] || TASK_PROFILE.meaning_analysis;
  const uiType = taskProfile.ui_type;
  const featureIntents = [];
  featureIntents.push(...taskProfile.feature_intents);

  if (includesToken(observations, "heart")) {
    featureIntents.push("physiological_monitor");
  }
  if (includesToken(observations, "screen")) {
    featureIntents.push("screen_monitor");
  }
  if (includesToken(observations, "interrupt")) {
    featureIntents.push("interruption_tracking");
  }

  const spec = {
    project_type: "desktop_app",
    ai_task: aiTask,
    ui_type: uiType,
    capabilities: {
      feature_intents: normalizeFeatureIntents(featureIntents),
    },
    meaning_origin: {
      functional_intent: parsedMeaning.functionalIntent || null,
      risk_level: parsedMeaning.riskLevel || null,
      hash: buildMeaningHash(parsedMeaning),
    },
  };

  if (includesToken(recommendedActions, "reduce")) {
    spec.behavior_feedback = true;
  }
  if (includesToken(recommendedActions, "increase")) {
    spec.adaptive_suggestion = true;
  }

  return spec;
}

module.exports = {
  mapMeaningToSpec,
  resolveAiTaskFromFunctionalIntent,
};
