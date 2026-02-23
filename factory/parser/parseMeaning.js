"use strict";

const fs = require("fs");
const path = require("path");
const { validateMeaningMarkdown } = require("./meaning_validator");
const { mapMeaningToSpec } = require("./meaning_to_spec_map");
const { applyArtifactBlueprint, applyUiPreferenceVariation } = require("./spec_to_artifact_map");
const { canonicalStringify, canonicalHash } = require("../utils/canonical_json");

const ROOT_DIR = path.resolve(__dirname, "..", "..");
const RUNTIME_SPEC_PATH = path.join(ROOT_DIR, "runtime", "spec.json");
const RUNTIME_SPEC_TMP_PATH = path.join(ROOT_DIR, "runtime", ".spec.tmp");
const MAX_MEANING_BYTES_DEFAULT = 10 * 1024;
const MAX_SPEC_BYTES_DEFAULT = 100 * 1024;
const MAX_DATA_MODEL_FIELDS = 20;
const ALLOWED_DATA_TYPES = new Set(["number", "string", "boolean", "array", "object"]);
const FORBIDDEN_TOKENS = ["../", "/etc/", "process.exit", "require(", "import ", "<script>", "</script>"];
const TASK_UI_DEFAULTS = {
  focus_stability_dashboard: "desktop_gui",
  behavior_anomaly_monitor: "alert_panel",
  recovery_pattern_analysis: "dashboard",
  habit_tracker: "dashboard",
  sleep_monitor: "dashboard",
  productivity_timer: "timer_ui",
  api_service_mode: "api",
  offline_local_app: "desktop_gui",
  realtime_stream_processor: "stream_dashboard",
  physiological_monitor_app: "dashboard",
  meaning_analysis: "dashboard",
};

function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

function safeUnlink(filePath) {
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch (err) {
    console.error("failed to remove file:", filePath, err.message);
  }
}

/**
 * Parse positive integer env override, fallback to default.
 * @param {string} envKey
 * @param {number} fallback
 * @returns {number}
 */
function readPositiveIntEnv(envKey, fallback) {
  const raw = process.env[envKey];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return parsed;
}

/**
 * Build sorted data_model and data_schema from validated data model fields.
 * @param {{name:string,type:string}[]} dataModel
 * @returns {{data_model: Record<string,string>, data_schema: Record<string,unknown>}}
 */
function buildDataModelArtifacts(dataModel) {
  const sorted = dataModel.slice().sort((a, b) => a.name.localeCompare(b.name));
  const dataModelOut = {};
  const properties = {};
  const required = [];

  for (const field of sorted) {
    dataModelOut[field.name] = field.type;
    properties[field.name] = { type: field.type };
    required.push(field.name);
  }

  return {
    data_model: dataModelOut,
    data_schema: {
      type: "object",
      properties,
      required,
    },
  };
}

/**
 * Build sorted ui preference object.
 * @param {{theme?:string,layout?:string,density?:string,navigation?:string}} uiPreference
 * @returns {{theme?:string,layout?:string,density?:string,navigation?:string}}
 */
function buildUiPreferences(uiPreference) {
  const out = {};
  const keys = Object.keys(uiPreference).sort();
  for (const key of keys) {
    const value = uiPreference[key];
    if (typeof value === "string" && value) {
      out[key] = value;
    }
  }
  return out;
}

/**
 * Build final canonical spec with stable hash.
 * Hash is computed on canonicalized spec excluding meaning_origin.hash itself.
 *
 * @param {Record<string, unknown>} spec
 * @returns {{spec: Record<string, unknown>, canonical: string}}
 */
function buildCanonicalSpec(spec) {
  const seedSpec = JSON.parse(canonicalStringify(spec));
  const forHash = JSON.parse(canonicalStringify(seedSpec));

  if (!forHash.meaning_origin || typeof forHash.meaning_origin !== "object") {
    forHash.meaning_origin = {};
  }
  delete forHash.meaning_origin.hash;

  seedSpec.meaning_origin.hash = canonicalHash(forHash);
  const canonical = canonicalStringify(seedSpec);
  return { spec: JSON.parse(canonical), canonical };
}

/**
 * Atomically write runtime/spec.json with idempotent content check.
 * On write failure, leaves existing runtime/spec.json untouched.
 *
 * @param {string} canonicalContent
 */
function writeSpecAtomically(canonicalContent) {
  ensureDir(path.dirname(RUNTIME_SPEC_PATH));

  if (fs.existsSync(RUNTIME_SPEC_PATH)) {
    const existing = fs.readFileSync(RUNTIME_SPEC_PATH, "utf8");
    if (existing === canonicalContent) {
      return;
    }
  }

  safeUnlink(RUNTIME_SPEC_TMP_PATH);
  let fd = null;
  try {
    fd = fs.openSync(RUNTIME_SPEC_TMP_PATH, "w");
    fs.writeFileSync(fd, canonicalContent, "utf8");
    fs.fsyncSync(fd);
    fs.closeSync(fd);
    fd = null;

    if (process.env.FACTORY_SIMULATE_SPEC_WRITE_FAILURE === "1") {
      throw new Error("Simulated spec write failure");
    }

    fs.renameSync(RUNTIME_SPEC_TMP_PATH, RUNTIME_SPEC_PATH);
  } catch (err) {
    if (fd !== null) {
      try {
        fs.closeSync(fd);
      } catch (_ignored) {
      }
    }
    safeUnlink(RUNTIME_SPEC_TMP_PATH);
    throw err;
  }
}

/**
 * Throw if meaning includes forbidden content patterns.
 * @param {string} rawMeaning
 */
function enforceForbiddenContent(rawMeaning) {
  const lower = rawMeaning.toLowerCase();
  for (const token of FORBIDDEN_TOKENS) {
    const needle = token.toLowerCase();
    if (lower.includes(needle)) {
      throw new Error(`Forbidden content detected: ${token}`);
    }
  }
}

/**
 * Re-validate Data Model types after validator pass.
 * @param {{name:string,type:string}[]} dataModel
 */
function enforceDataModelTypeHardening(dataModel) {
  for (const field of dataModel) {
    const type = String(field.type || "").toLowerCase();
    if (!ALLOWED_DATA_TYPES.has(type)) {
      throw new Error(`Data Model contains forbidden type: ${field.type}`);
    }
    if (type.includes("[]")) {
      throw new Error(`Data Model contains forbidden nested type: ${field.type}`);
    }
    if (type === "function" || type === "class" || type === "any" || type === "undefined") {
      throw new Error(`Data Model contains forbidden type: ${field.type}`);
    }
  }
}

/**
 * Resolve ai_task override from data model fields in strict priority.
 * Priority: heart_rate > sleep_duration > habit/streak > event_stream.
 *
 * @param {{name:string,type:string}[]} dataModel
 * @returns {string|null}
 */
function resolveAiTaskOverrideFromDataModel(dataModel) {
  const names = new Set(dataModel.map((x) => String(x.name || "").toLowerCase()));
  if (names.has("heart_rate")) return "physiological_monitor_app";
  if (names.has("sleep_duration")) return "sleep_monitor";

  for (const name of names) {
    if (name.includes("habit") || name.includes("streak")) {
      return "habit_tracker";
    }
  }

  if (names.has("event_stream")) return "realtime_stream_processor";
  return null;
}

function main() {
  const [, , sourcePath] = process.argv;
  if (!sourcePath) {
    console.error("usage: node parseMeaning.js /path/to/meaning.md");
    process.exit(1);
  }

  const maxMeaningBytes = readPositiveIntEnv("FACTORY_MEANING_SIZE_LIMIT_BYTES", MAX_MEANING_BYTES_DEFAULT);
  const maxSpecBytes = readPositiveIntEnv("FACTORY_SPEC_SIZE_LIMIT_BYTES", MAX_SPEC_BYTES_DEFAULT);

  let md;
  try {
    md = fs.readFileSync(sourcePath, "utf8");
  } catch (err) {
    safeUnlink(RUNTIME_SPEC_TMP_PATH);
    safeUnlink(RUNTIME_SPEC_PATH);
    console.error("failed to read markdown:", err.message);
    process.exit(1);
  }

  try {
    if (Buffer.byteLength(md, "utf8") > maxMeaningBytes) {
      throw new Error("Meaning size exceeds limit");
    }

    enforceForbiddenContent(md);

    const meaning = validateMeaningMarkdown(md);
    if (meaning.dataModel.length > MAX_DATA_MODEL_FIELDS) {
      throw new Error(`Data Model exceeds maximum field count (${MAX_DATA_MODEL_FIELDS})`);
    }

    enforceDataModelTypeHardening(meaning.dataModel);

    const mappedSpec = mapMeaningToSpec(meaning);
    const overrideTask = resolveAiTaskOverrideFromDataModel(meaning.dataModel);
    if (overrideTask) {
      mappedSpec.ai_task = overrideTask;
      mappedSpec.ui_type = TASK_UI_DEFAULTS[overrideTask] || mappedSpec.ui_type || "dashboard";
    }

    const spec = applyArtifactBlueprint({
      ...mappedSpec,
      origin: path.relative(ROOT_DIR, path.resolve(sourcePath)),
    });

    if (meaning.dataModel.length > 0) {
      const dataArtifacts = buildDataModelArtifacts(meaning.dataModel);
      spec.data_model = dataArtifacts.data_model;
      spec.data_schema = dataArtifacts.data_schema;
    }

    if (meaning.uiPreference && Object.keys(meaning.uiPreference).length > 0) {
      const uiPrefs = buildUiPreferences(meaning.uiPreference);
      spec.ui_preferences = uiPrefs;
      applyUiPreferenceVariation(spec, uiPrefs);
    }

    const canonicalSpec = buildCanonicalSpec(spec);
    if (Buffer.byteLength(canonicalSpec.canonical, "utf8") > maxSpecBytes) {
      throw new Error("Spec size exceeds limit");
    }

    try {
      writeSpecAtomically(canonicalSpec.canonical);
    } catch (writeErr) {
      safeUnlink(RUNTIME_SPEC_TMP_PATH);
      if (writeErr instanceof Error) {
        console.error(writeErr.message);
      } else {
        console.error("spec write failed");
      }
      process.exit(1);
    }

    process.exit(0);
  } catch (err) {
    safeUnlink(RUNTIME_SPEC_TMP_PATH);
    safeUnlink(RUNTIME_SPEC_PATH);
    if (err instanceof Error) {
      console.error(err.message);
    } else {
      console.error("meaning parser failed");
    }
    process.exit(1);
  }
}

if (require.main === module) {
  main();
}
