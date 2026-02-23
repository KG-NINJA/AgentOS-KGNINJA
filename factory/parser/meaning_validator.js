/**
 * @typedef {Object} MeaningDataField
 * @property {string} name
 * @property {string} type
 */

/**
 * @typedef {Object} MeaningUiPreference
 * @property {string} [theme]
 * @property {string} [layout]
 * @property {string} [density]
 * @property {string} [navigation]
 */

/**
 * @typedef {Object} MeaningSpec
 * @property {string[]} observations
 * @property {string[]} recommendedActions
 * @property {string|null} patternInterpretation
 * @property {string|null} riskLevel
 * @property {string|null} functionalIntent
 * @property {MeaningDataField[]} dataModel
 * @property {MeaningUiPreference} uiPreference
 */

"use strict";

const HEADING_MAP = new Map([
  ["Observations", "observations"],
  ["Recommended Actions", "recommendedActions"],
  ["Pattern Interpretation", "patternInterpretation"],
  ["Risk Level", "riskLevel"],
  ["Functional Intent", "functionalIntent"],
  ["Data Model", "dataModel"],
  ["UI Preference", "uiPreference"],
]);

const REQUIRED_SECTIONS = new Set(["observations", "recommendedActions"]);
const ALLOWED_DATA_TYPES = new Set(["number", "string", "boolean", "array", "object"]);
const MAX_DATA_MODEL_FIELDS = 20;
const MAX_UI_PREFERENCES = 10;
const UI_PREFERENCE_RULES = {
  theme: new Set(["light", "dark"]),
  layout: new Set(["dashboard", "panel", "minimal"]),
  density: new Set(["compact", "comfortable"]),
  navigation: new Set(["sidebar", "topbar", "none"]),
};

/**
 * Build a deterministic, human-readable validation error.
 * @param {string} message
 * @param {number|null} [lineNumber]
 * @returns {Error}
 */
function validationError(message, lineNumber) {
  if (typeof lineNumber === "number") {
    return new Error(`Meaning markdown validation failed at line ${lineNumber}: ${message}`);
  }
  return new Error(`Meaning markdown validation failed: ${message}`);
}

/**
 * Parse a level-2 markdown heading and return its label.
 * @param {string} line
 * @returns {string|null}
 */
function parseLevel2Heading(line) {
  const match = line.match(/^##\s+(.*?)\s*$/);
  if (!match) return null;
  return match[1];
}

/**
 * Normalize multi-line section text into a single deterministic string.
 * @param {string[]} lines
 * @returns {string|null}
 */
function normalizeSectionText(lines) {
  const parts = [];
  for (const raw of lines) {
    const trimmed = raw.trim();
    if (!trimmed) continue;
    const bullet = trimmed.match(/^-+\s+(.+)$/);
    if (bullet) {
      parts.push(bullet[1].trim());
    } else {
      parts.push(trimmed);
    }
  }
  return parts.length > 0 ? parts.join("\n") : null;
}

/**
 * Parse and validate Data Model section lines.
 * @param {string[]} lines
 * @returns {MeaningDataField[]}
 */
function parseDataModel(lines) {
  if (!Array.isArray(lines)) {
    return [];
  }

  const fields = [];
  const seen = new Set();

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      continue;
    }

    const match = line.match(/^-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z]+)\s*$/);
    if (!match) {
      throw validationError(
        "Data Model line must match '- field_name: type' with a supported type"
      );
    }

    const name = match[1];
    const type = match[2].toLowerCase();

    if (!ALLOWED_DATA_TYPES.has(type)) {
      throw validationError(
        `Data Model type '${type}' is not allowed; use one of: number, string, boolean, array, object`
      );
    }

    if (seen.has(name)) {
      throw validationError(`duplicated Data Model field '${name}'`);
    }

    seen.add(name);
    fields.push({ name, type });

    if (fields.length > MAX_DATA_MODEL_FIELDS) {
      throw validationError(`Data Model exceeds maximum field count (${MAX_DATA_MODEL_FIELDS})`);
    }
  }

  if (lines.length > 0 && fields.length === 0) {
    throw validationError("Data Model section must contain at least one '- field_name: type' line");
  }

  return fields;
}

/**
 * Parse and validate UI Preference section lines.
 * @param {string[]} lines
 * @returns {MeaningUiPreference}
 */
function parseUiPreference(lines) {
  if (!Array.isArray(lines)) {
    return {};
  }

  const prefs = {};
  const seen = new Set();
  let count = 0;

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      continue;
    }

    const match = line.match(/^-\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_][A-Za-z0-9_]*)\s*$/);
    if (!match) {
      throw validationError("UI Preference line must match '- key: value'");
    }

    const key = match[1];
    const value = match[2].toLowerCase();

    if (!Object.prototype.hasOwnProperty.call(UI_PREFERENCE_RULES, key)) {
      throw validationError(`UI Preference key '${key}' is not allowed`);
    }

    if (seen.has(key)) {
      throw validationError(`duplicated UI Preference key '${key}'`);
    }

    if (!UI_PREFERENCE_RULES[key].has(value)) {
      throw validationError(`UI Preference value '${value}' is invalid for key '${key}'`);
    }

    seen.add(key);
    prefs[key] = value;
    count += 1;

    if (count > MAX_UI_PREFERENCES) {
      throw validationError(`UI Preference exceeds maximum key count (${MAX_UI_PREFERENCES})`);
    }
  }

  return prefs;
}

/**
 * Validate and parse an Insight Markdown document into a MeaningSpec.
 * The parser is deterministic and uses strict heading contracts.
 *
 * @param {string} md Insight Markdown content.
 * @returns {MeaningSpec}
 * @throws {Error} If markdown violates required structure or section constraints.
 */
function validateMeaningMarkdown(md) {
  if (typeof md !== "string") {
    throw validationError("input must be a string");
  }

  const lines = md.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  /** @type {Map<string, string[]>} */
  const sectionLines = new Map();
  /** @type {string|null} */
  let currentSection = null;

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const lineNumber = i + 1;
    const trimmed = line.trim();

    if (/^##\s*$/.test(line)) {
      throw validationError("empty heading text is not allowed", lineNumber);
    }

    if (line.startsWith("##")) {
      const label = parseLevel2Heading(line);
      if (!label) {
        throw validationError("malformed heading; expected format '## <Heading>'", lineNumber);
      }

      const key = HEADING_MAP.get(label);
      if (!key) {
        throw validationError(
          `invalid heading '${label}'; allowed headings are: ${Array.from(HEADING_MAP.keys()).join(", ")}`,
          lineNumber
        );
      }

      if (sectionLines.has(key)) {
        throw validationError(`duplicated section '${label}'`, lineNumber);
      }

      sectionLines.set(key, []);
      currentSection = key;
      continue;
    }

    if (!currentSection) continue;
    sectionLines.get(currentSection).push(line);

    if (/^#+/.test(trimmed) && !/^##\s+/.test(trimmed)) {
      throw validationError(
        "invalid markdown structure inside a meaning section; nested/other heading levels are not allowed",
        lineNumber
      );
    }
  }

  for (const requiredKey of REQUIRED_SECTIONS) {
    if (!sectionLines.has(requiredKey)) {
      const requiredLabel =
        requiredKey === "observations" ? "## Observations" : "## Recommended Actions";
      throw validationError(`missing required section '${requiredLabel}'`);
    }
  }

  /**
   * Extract bullet items from a section.
   * @param {string} key
   * @param {string} label
   * @returns {string[]}
   */
  function extractBullets(key, label) {
    const raw = sectionLines.get(key) || [];
    const items = [];
    for (let i = 0; i < raw.length; i += 1) {
      const line = raw[i].trim();
      const match = line.match(/^-\s+(.+)$/);
      if (match) items.push(match[1].trim());
    }
    if (items.length === 0) {
      throw validationError(`section '${label}' must contain at least one bullet item ('- item')`);
    }
    return items;
  }

  const observations = extractBullets("observations", "## Observations");
  const recommendedActions = extractBullets("recommendedActions", "## Recommended Actions");
  const patternInterpretation = normalizeSectionText(sectionLines.get("patternInterpretation") || []);
  const riskLevel = normalizeSectionText(sectionLines.get("riskLevel") || []);
  const functionalIntent = normalizeSectionText(sectionLines.get("functionalIntent") || []);
  const dataModel = parseDataModel(sectionLines.get("dataModel") || []);
  const uiPreference = parseUiPreference(sectionLines.get("uiPreference") || []);

  return {
    observations,
    recommendedActions,
    patternInterpretation,
    riskLevel,
    functionalIntent,
    dataModel,
    uiPreference,
  };
}

module.exports = {
  validateMeaningMarkdown,
};
