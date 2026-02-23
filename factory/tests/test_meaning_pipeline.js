"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT_DIR = path.resolve(__dirname, "..", "..");
const PARSE_MEANING_PATH = path.join(ROOT_DIR, "factory", "parser", "parseMeaning.js");
const RUNTIME_SPEC_PATH = path.join(ROOT_DIR, "runtime", "spec.json");
const TMP_DIR = path.join(ROOT_DIR, "factory", "tests", "tmp");

/**
 * Ensure a directory exists.
 * @param {string} dirPath
 */
function ensureDir(dirPath) {
  if (!fs.existsSync(dirPath)) {
    fs.mkdirSync(dirPath, { recursive: true });
  }
}

/**
 * Delete file if it exists.
 * @param {string} filePath
 */
function safeUnlink(filePath) {
  try {
    if (fs.existsSync(filePath)) {
      fs.unlinkSync(filePath);
    }
  } catch (err) {
    throw new Error(`failed to remove file '${filePath}': ${err.message}`);
  }
}

/**
 * Parse JSON file.
 * @param {string} filePath
 * @returns {Record<string, unknown>}
 */
function readJson(filePath) {
  const raw = fs.readFileSync(filePath, "utf8");
  return JSON.parse(raw);
}

/**
 * Deterministic assert helper.
 * @param {boolean} condition
 * @param {string} message
 */
function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

/**
 * Write markdown to temp file and run parseMeaning.js.
 * @param {string} filename
 * @param {string} markdown
 * @returns {{status:number|null, stdout:string, stderr:string}}
 */
function runParseMeaning(filename, markdown) {
  return runParseMeaningWithEnv(filename, markdown, {});
}

/**
 * Write markdown to temp file and run parseMeaning.js with custom env.
 * @param {string} filename
 * @param {string} markdown
 * @param {Record<string,string>} extraEnv
 * @returns {{status:number|null, stdout:string, stderr:string}}
 */
function runParseMeaningWithEnv(filename, markdown, extraEnv) {
  const filePath = path.join(TMP_DIR, filename);
  fs.writeFileSync(filePath, markdown, "utf8");

  return spawnSync(process.execPath, [PARSE_MEANING_PATH, filePath], {
    cwd: ROOT_DIR,
    encoding: "utf8",
    env: {
      ...process.env,
      ...extraEnv,
    },
  });
}

/**
 * Remove spec before each test to avoid hidden state.
 */
function resetSpec() {
  ensureDir(path.dirname(RUNTIME_SPEC_PATH));
  safeUnlink(RUNTIME_SPEC_PATH);
}

/**
 * Run parser and return generated spec.
 * @param {string} filename
 * @param {string} markdown
 * @returns {Record<string, unknown>}
 */
function runSuccessAndReadSpec(filename, markdown) {
  const result = runParseMeaning(filename, markdown);
  assert(result.status === 0, `expected exit code 0, got ${result.status}. stderr=${result.stderr.trim()}`);
  assert(fs.existsSync(RUNTIME_SPEC_PATH), "expected runtime/spec.json to exist");
  return readJson(RUNTIME_SPEC_PATH);
}

/**
 * @param {string} name
 * @param {() => void} fn
 * @returns {boolean}
 */
function runTest(name, fn) {
  try {
    resetSpec();
    fn();
    console.log(`[PASS] ${name}`);
    return true;
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    console.log(`[FAIL] ${name} - ${reason}`);
    return false;
  }
}

function testValidMeaningMarkdown() {
  const filename = "valid_meaning.md";
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- Resting heart rate increased",
    "- Sleep duration decreased",
    "",
    "## Recommended Actions",
    "- Notify user when threshold exceeded",
    "",
    "## Pattern Interpretation",
    "Recovery imbalance is likely.",
    "",
    "## Risk Level",
    "high",
    "",
    "## Functional Intent",
    "- recovery_analysis",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec(filename, md);
  assert(typeof spec.origin === "string", "expected spec.origin to be a string");
  assert(spec.origin.includes(filename), `expected spec.origin to include '${filename}', got '${spec.origin}'`);
  assert(typeof spec.meaning_origin === "object" && spec.meaning_origin !== null, "expected meaning_origin object");
}

function testMissingRequiredSection() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- Only observations exist",
    "",
  ].join("\n");

  const result = runParseMeaning("missing_required.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist after failure");
}

function testDuplicateSection() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- First",
    "",
    "## Observations",
    "- Duplicate",
    "",
    "## Recommended Actions",
    "- Action",
    "",
  ].join("\n");

  const result = runParseMeaning("duplicate_section.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist after duplicate section failure");
}

function testMalformedHeading() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- Observation",
    "",
    "## Recommendation",
    "- Invalid heading label",
    "",
  ].join("\n");

  const result = runParseMeaning("malformed_heading.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist after malformed heading failure");
}

function testRequiredSectionWithoutBullets() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "This section has no bullet item.",
    "",
    "## Recommended Actions",
    "- Action exists",
    "",
  ].join("\n");

  const result = runParseMeaning("required_without_bullets.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist when required bullets are missing");
}

function testOldSpecReuseProtection() {
  const validName = "reuse_valid.md";
  const validMd = [
    "# Insight",
    "",
    "## Observations",
    "- O1",
    "",
    "## Recommended Actions",
    "- A1",
    "",
  ].join("\n");

  const first = runParseMeaning(validName, validMd);
  assert(first.status === 0, `expected first run success, got ${first.status}`);
  assert(fs.existsSync(RUNTIME_SPEC_PATH), "expected spec after first valid run");

  const specAfterFirst = readJson(RUNTIME_SPEC_PATH);
  assert(
    typeof specAfterFirst.origin === "string" && specAfterFirst.origin.includes(validName),
    "expected first spec.origin to contain first filename"
  );

  const invalidMd = [
    "# Insight",
    "",
    "## Observations",
    "- O1",
    "",
  ].join("\n");

  const second = runParseMeaning("reuse_invalid.md", invalidMd);
  assert(second.status !== 0, `expected second run failure, got ${second.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must be deleted after failed second run");
}

function testMeaningDiffChangesAiTask() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- steady focus sessions",
    "",
    "## Recommended Actions",
    "- reduce interruptions",
    "",
    "## Functional Intent",
    "- focus_monitoring",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("meaning_diff_a.md", mdA);

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- frequent behavior spikes",
    "",
    "## Recommended Actions",
    "- increase anomaly alerts",
    "",
    "## Functional Intent",
    "- anomaly_detection",
    "",
  ].join("\n");

  const specB = runSuccessAndReadSpec("meaning_diff_b.md", mdB);

  assert(specA.ai_task !== specB.ai_task, "expected ai_task to differ across functional intent variants");
}

function testOriginHashChanges() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- heart rate increased",
    "",
    "## Recommended Actions",
    "- reduce late work",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("hash_change_a.md", mdA);

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- heart rate increased slightly",
    "",
    "## Recommended Actions",
    "- reduce late work",
    "",
  ].join("\n");

  const specB = runSuccessAndReadSpec("hash_change_b.md", mdB);

  assert(
    specA.meaning_origin && specB.meaning_origin && specA.meaning_origin.hash !== specB.meaning_origin.hash,
    "expected meaning_origin.hash to change when observations change"
  );
}

function testObservationFeatureInjection() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- heart load increased during work blocks",
    "",
    "## Recommended Actions",
    "- reduce high intensity context switching",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("feature_injection_observation.md", md);
  const featureIntents = spec.capabilities && spec.capabilities.feature_intents;
  assert(Array.isArray(featureIntents), "expected capabilities.feature_intents array");
  assert(
    featureIntents.includes("physiological_monitor"),
    "expected feature_intents to include physiological_monitor"
  );
}

function testActionFeatureInjection() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- interruptions increased",
    "",
    "## Recommended Actions",
    "- reduce context switching",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("feature_injection_action.md", md);
  assert(spec.behavior_feedback === true, "expected behavior_feedback === true");
}

function testAiTaskBlueprintChange() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- screen focus drift detected",
    "",
    "## Recommended Actions",
    "- reduce context switching",
    "",
    "## Functional Intent",
    "- focus_monitoring",
    "",
  ].join("\n");
  const specA = runSuccessAndReadSpec("blueprint_change_a.md", mdA);

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- screen spikes and interrupt events",
    "",
    "## Recommended Actions",
    "- increase alert response cadence",
    "",
    "## Functional Intent",
    "- anomaly_detection",
    "",
  ].join("\n");
  const specB = runSuccessAndReadSpec("blueprint_change_b.md", mdB);

  const filesA = JSON.stringify(specA.contracts && specA.contracts.files);
  const filesB = JSON.stringify(specB.contracts && specB.contracts.files);
  assert(filesA !== filesB, "expected contracts.files blueprint to differ by ai_task");
}

function testBlueprintDeterminism() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- heart and interrupt variation observed",
    "",
    "## Recommended Actions",
    "- reduce high variance windows",
    "",
    "## Functional Intent",
    "- recovery_analysis",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("blueprint_determinism_a.md", md);
  const specB = runSuccessAndReadSpec("blueprint_determinism_b.md", md);
  const filesA = JSON.stringify(specA.contracts && specA.contracts.files);
  const filesB = JSON.stringify(specB.contracts && specB.contracts.files);
  assert(filesA === filesB, "expected deterministic contracts.files for identical meaning input");
}

function testDefaultFallbackBlueprint() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline routine",
    "",
    "## Recommended Actions",
    "- reduce late sessions",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("blueprint_default_fallback.md", md);
  const expected = ["README.md", "core/app.js", "core/public/index.html"];
  const files = spec.contracts && spec.contracts.files;
  assert(Array.isArray(files), "expected contracts.files array");
  assert(JSON.stringify(files) === JSON.stringify(expected), "expected default fallback blueprint files");
}

function testBlueprintIntegrity() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- interrupt spikes across sessions",
    "",
    "## Recommended Actions",
    "- increase anomaly awareness",
    "",
    "## Functional Intent",
    "- anomaly_detection",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("blueprint_integrity.md", md);
  const files = spec.contracts && spec.contracts.files;
  assert(Array.isArray(files), "expected contracts.files array");
  assert(files.includes("core/monitor.js"), "expected core/monitor.js in behavior_anomaly_monitor blueprint");
}

function testIdempotentSpecGeneration() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- heart and screen variance observed",
    "",
    "## Recommended Actions",
    "- reduce interruptions",
    "",
    "## Functional Intent",
    "- focus_monitoring",
    "",
  ].join("\n");

  const spec1 = runSuccessAndReadSpec("idempotent.md", md);
  const spec2 = runSuccessAndReadSpec("idempotent.md", md);
  const spec3 = runSuccessAndReadSpec("idempotent.md", md);

  const s1 = JSON.stringify(spec1);
  const s2 = JSON.stringify(spec2);
  const s3 = JSON.stringify(spec3);
  assert(s1 === s2 && s2 === s3, "expected identical spec.json content across repeated runs");
}

function testCanonicalOrderStability() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- screen jitter",
    "- heart load",
    "",
    "## Recommended Actions",
    "- increase break cadence",
    "- reduce task switching",
    "",
    "## Functional Intent",
    "- anomaly_detection",
    "",
  ].join("\n");

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- heart load",
    "- screen jitter",
    "",
    "## Recommended Actions",
    "- reduce task switching",
    "- increase break cadence",
    "",
    "## Functional Intent",
    "- anomaly_detection",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("canonical_order.md", mdA);
  const specB = runSuccessAndReadSpec("canonical_order.md", mdB);
  assert(JSON.stringify(specA) === JSON.stringify(specB), "expected identical canonical spec despite bullet reordering");
}

function testHashStability() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- interrupt bursts in afternoon",
    "",
    "## Recommended Actions",
    "- reduce late meetings",
    "",
    "## Functional Intent",
    "- recovery_analysis",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("hash_stability.md", md);
  const specB = runSuccessAndReadSpec("hash_stability.md", md);
  assert(
    specA.meaning_origin && specB.meaning_origin && specA.meaning_origin.hash === specB.meaning_origin.hash,
    "expected stable meaning_origin.hash for identical meaning input"
  );
}

function testHashChangeOnMeaningChange() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- interrupt bursts in afternoon",
    "",
    "## Recommended Actions",
    "- reduce late meetings",
    "",
    "## Functional Intent",
    "- recovery_analysis",
    "",
  ].join("\n");

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- interrupt bursts in afternoon with higher spike",
    "",
    "## Recommended Actions",
    "- reduce late meetings",
    "",
    "## Functional Intent",
    "- recovery_analysis",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("hash_change_meaning_a.md", mdA);
  const specB = runSuccessAndReadSpec("hash_change_meaning_b.md", mdB);
  assert(
    specA.meaning_origin && specB.meaning_origin && specA.meaning_origin.hash !== specB.meaning_origin.hash,
    "expected meaning_origin.hash to change when meaning content changes"
  );
}

function testDataModelInjection() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- heart_rate: number",
    "- user_name: string",
    "- is_alerting: boolean",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("data_model_injection.md", md);
  assert(spec.data_model && typeof spec.data_model === "object", "expected spec.data_model object");
  assert(spec.data_schema && typeof spec.data_schema === "object", "expected spec.data_schema object");
  assert(
    spec.data_schema.properties && Object.keys(spec.data_schema.properties).length === 3,
    "expected 3 data_schema.properties entries"
  );
}

function testFieldOrderStability() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- alpha: string",
    "- beta: number",
    "- gamma: boolean",
    "",
  ].join("\n");

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- gamma: boolean",
    "- alpha: string",
    "- beta: number",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("field_order_stability.md", mdA);
  const specB = runSuccessAndReadSpec("field_order_stability.md", mdB);
  assert(JSON.stringify(specA) === JSON.stringify(specB), "expected identical spec for reordered Data Model fields");
}

function testTypeChangeAffectsHash() {
  const mdA = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- score: number",
    "",
  ].join("\n");

  const mdB = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- score: string",
    "",
  ].join("\n");

  const specA = runSuccessAndReadSpec("type_change_hash.md", mdA);
  const specB = runSuccessAndReadSpec("type_change_hash.md", mdB);
  assert(
    specA.meaning_origin && specB.meaning_origin && specA.meaning_origin.hash !== specB.meaning_origin.hash,
    "expected meaning_origin.hash to differ when Data Model type changes"
  );
}

function testDuplicateFieldRejected() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- score: number",
    "- score: string",
    "",
  ].join("\n");

  const result = runParseMeaning("duplicate_field_rejected.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist after duplicate Data Model field");
}

function testTooManyFieldsRejected() {
  const fields = [];
  for (let i = 1; i <= 21; i += 1) {
    fields.push(`- f${i}: number`);
  }

  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    ...fields,
    "",
  ].join("\n");

  const result = runParseMeaning("too_many_fields_rejected.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist when Data Model exceeds 20 fields");
}

function testMeaningTooLarge() {
  const bigObservation = "x".repeat(11 * 1024);
  const md = [
    "# Insight",
    "",
    "## Observations",
    `- ${bigObservation}`,
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
  ].join("\n");

  const result = runParseMeaning("meaning_too_large.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist when meaning is too large");
}

function testSpecTooLarge() {
  const fields = [];
  for (let i = 1; i <= 20; i += 1) {
    fields.push(`- field_${i}: string`);
  }

  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    ...fields,
    "",
  ].join("\n");

  const result = runParseMeaningWithEnv("spec_too_large.md", md, {
    FACTORY_SPEC_SIZE_LIMIT_BYTES: "300",
  });
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist when spec exceeds limit");
}

function testForbiddenContent() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- suspicious path ../secret",
    "",
    "## Recommended Actions",
    "- reduce risk",
    "",
  ].join("\n");

  const result = runParseMeaning("forbidden_content.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist when forbidden content is present");
}

function testScriptInjectionAttempt() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- <script>alert(1)</script>",
    "",
    "## Recommended Actions",
    "- reduce risk",
    "",
  ].join("\n");

  const result = runParseMeaning("script_injection.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist when script injection is present");
}

function testNoPartialWriteOnFailure() {
  const mdBase = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Functional Intent",
    "- focus_monitoring",
    "",
  ].join("\n");

  const baseSpec = runSuccessAndReadSpec("no_partial_base.md", mdBase);
  const baselineContent = JSON.stringify(baseSpec);

  const mdNext = [
    "# Insight",
    "",
    "## Observations",
    "- changed signal profile",
    "",
    "## Recommended Actions",
    "- increase responsiveness",
    "",
    "## Functional Intent",
    "- anomaly_detection",
    "",
  ].join("\n");

  const failed = runParseMeaningWithEnv("no_partial_fail.md", mdNext, {
    FACTORY_SIMULATE_SPEC_WRITE_FAILURE: "1",
  });
  assert(failed.status !== 0, `expected non-zero exit code, got ${failed.status}`);
  assert(fs.existsSync(RUNTIME_SPEC_PATH), "existing runtime/spec.json must remain after write failure");

  const afterSpec = readJson(RUNTIME_SPEC_PATH);
  const afterContent = JSON.stringify(afterSpec);
  assert(afterContent === baselineContent, "runtime/spec.json changed despite simulated write failure");
}

function testHeartRateAutoOverride() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline physiological variance",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- heart_rate: number",
    "- user_name: string",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("override_heart_rate.md", md);
  assert(spec.ai_task === "physiological_monitor_app", "expected ai_task override to physiological_monitor_app");
}

function testSleepFieldOverride() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline sleep variance",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Data Model",
    "- sleep_duration: number",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("override_sleep.md", md);
  assert(spec.ai_task === "sleep_monitor", "expected ai_task override to sleep_monitor");
}

function testHabitBlueprint() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline behavior",
    "",
    "## Recommended Actions",
    "- reduce friction",
    "",
    "## Data Model",
    "- habit: string",
    "- streak_count: number",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("override_habit_blueprint.md", md);
  const files = spec.contracts && spec.contracts.files;
  assert(Array.isArray(files), "expected contracts.files array");
  assert(files.includes("core/habit.js"), "expected habit_tracker blueprint to include core/habit.js");
}

function testApiModeBlueprint() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- api calls trend",
    "",
    "## Recommended Actions",
    "- increase endpoint coverage",
    "",
    "## Functional Intent",
    "- api_service_mode",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("api_mode_blueprint.md", md);
  const files = spec.contracts && spec.contracts.files;
  assert(Array.isArray(files), "expected contracts.files array");
  assert(files.includes("core/api.js"), "expected api_service_mode blueprint to include core/api.js");
}

function testDeterministicOverridePriority() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- mixed signals",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## Functional Intent",
    "- habit_tracker",
    "",
    "## Data Model",
    "- heart_rate: number",
    "- habit: string",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("override_priority.md", md);
  assert(
    spec.ai_task === "physiological_monitor_app",
    "expected data model override priority to win over functional intent"
  );
}

function testUiPreferenceInjection() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## UI Preference",
    "- theme: dark",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("ui_pref_injection.md", md);
  assert(spec.ui_preferences && typeof spec.ui_preferences === "object", "expected spec.ui_preferences object");
  assert(spec.ui_preferences.theme === "dark", "expected spec.ui_preferences.theme === 'dark'");
}

function testLayoutVariation() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## UI Preference",
    "- layout: minimal",
    "",
  ].join("\n");

  const spec = runSuccessAndReadSpec("ui_layout_variation.md", md);
  const files = spec.contracts && spec.contracts.files;
  assert(Array.isArray(files), "expected contracts.files array");
  assert(files.includes("core/public/minimal.html"), "expected minimal layout file in contracts.files");
}

function testHashChangesOnUiChange() {
  const mdLight = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## UI Preference",
    "- theme: light",
    "",
  ].join("\n");

  const mdDark = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## UI Preference",
    "- theme: dark",
    "",
  ].join("\n");

  const specLight = runSuccessAndReadSpec("ui_hash_change.md", mdLight);
  const specDark = runSuccessAndReadSpec("ui_hash_change.md", mdDark);
  assert(
    specLight.meaning_origin &&
      specDark.meaning_origin &&
      specLight.meaning_origin.hash !== specDark.meaning_origin.hash,
    "expected meaning_origin.hash to change on UI preference change"
  );
}

function testInvalidUiKeyRejected() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## UI Preference",
    "- palette: neon",
    "",
  ].join("\n");

  const result = runParseMeaning("ui_invalid_key.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist after invalid UI key");
}

function testDuplicateUiKeyRejected() {
  const md = [
    "# Insight",
    "",
    "## Observations",
    "- baseline signal",
    "",
    "## Recommended Actions",
    "- reduce noise",
    "",
    "## UI Preference",
    "- theme: light",
    "- theme: dark",
    "",
  ].join("\n");

  const result = runParseMeaning("ui_duplicate_key.md", md);
  assert(result.status !== 0, `expected non-zero exit code, got ${result.status}`);
  assert(!fs.existsSync(RUNTIME_SPEC_PATH), "runtime/spec.json must not exist after duplicate UI key");
}

function main() {
  ensureDir(TMP_DIR);
  assert(fs.existsSync(PARSE_MEANING_PATH), `missing parser entrypoint: ${PARSE_MEANING_PATH}`);

  const results = [
    runTest("Valid Meaning Markdown", testValidMeaningMarkdown),
    runTest("Missing Required Section", testMissingRequiredSection),
    runTest("Duplicate Section", testDuplicateSection),
    runTest("Malformed Heading", testMalformedHeading),
    runTest("Required Section Without Bullets", testRequiredSectionWithoutBullets),
    runTest("Old Spec Reuse Protection", testOldSpecReuseProtection),
    runTest("Meaning Diff Changes ai_task", testMeaningDiffChangesAiTask),
    runTest("Origin Hash Changes", testOriginHashChanges),
    runTest("Observation Feature Injection", testObservationFeatureInjection),
    runTest("Action Feature Injection", testActionFeatureInjection),
    runTest("ai_task Blueprint Change", testAiTaskBlueprintChange),
    runTest("Blueprint Determinism", testBlueprintDeterminism),
    runTest("Default Fallback", testDefaultFallbackBlueprint),
    runTest("Blueprint Integrity", testBlueprintIntegrity),
    runTest("Idempotent Spec Generation", testIdempotentSpecGeneration),
    runTest("Canonical Order Stability", testCanonicalOrderStability),
    runTest("Hash Stability", testHashStability),
    runTest("Hash Change on Meaning Change", testHashChangeOnMeaningChange),
    runTest("Data Model Injection", testDataModelInjection),
    runTest("Field Order Stability", testFieldOrderStability),
    runTest("Type Change Affects Hash", testTypeChangeAffectsHash),
    runTest("Duplicate Field Rejected", testDuplicateFieldRejected),
    runTest("Too Many Fields Rejected", testTooManyFieldsRejected),
    runTest("Meaning Too Large", testMeaningTooLarge),
    runTest("Spec Too Large", testSpecTooLarge),
    runTest("Forbidden Content", testForbiddenContent),
    runTest("Script Injection Attempt", testScriptInjectionAttempt),
    runTest("No Partial Write on Failure", testNoPartialWriteOnFailure),
    runTest("Heart Rate Auto Override", testHeartRateAutoOverride),
    runTest("Sleep Field Override", testSleepFieldOverride),
    runTest("Habit Blueprint", testHabitBlueprint),
    runTest("API Mode Blueprint", testApiModeBlueprint),
    runTest("Deterministic Override Priority", testDeterministicOverridePriority),
    runTest("UI Preference Injection", testUiPreferenceInjection),
    runTest("Layout Variation", testLayoutVariation),
    runTest("Hash Changes on UI Change", testHashChangesOnUiChange),
    runTest("Invalid UI Key Rejected", testInvalidUiKeyRejected),
    runTest("Duplicate UI Key Rejected", testDuplicateUiKeyRejected),
  ];

  const failed = results.filter((x) => !x).length;
  process.exit(failed === 0 ? 0 : 1);
}

main();
