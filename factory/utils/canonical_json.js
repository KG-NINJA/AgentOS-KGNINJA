"use strict";

const crypto = require("crypto");

/**
 * Return true when every array element is a string.
 * @param {unknown[]} arr
 * @returns {boolean}
 */
function isStringArray(arr) {
  return arr.every((item) => typeof item === "string");
}

/**
 * Convert input into a canonicalized JSON-safe value.
 * - Object keys sorted alphabetically
 * - Undefined values removed
 * - String arrays sorted alphabetically
 * - Object arrays canonicalized while preserving element order
 *
 * @param {unknown} value
 * @returns {unknown}
 */
function canonicalize(value) {
  if (value === undefined) {
    return undefined;
  }

  if (value === null || typeof value === "number" || typeof value === "string" || typeof value === "boolean") {
    return value;
  }

  if (Array.isArray(value)) {
    if (isStringArray(value)) {
      return value.slice().sort();
    }

    const out = [];
    for (const item of value) {
      const canonicalItem = canonicalize(item);
      if (canonicalItem !== undefined) {
        out.push(canonicalItem);
      }
    }
    return out;
  }

  if (typeof value === "object") {
    const src = /** @type {Record<string, unknown>} */ (value);
    const out = {};
    const keys = Object.keys(src).sort();
    for (const key of keys) {
      const canonicalValue = canonicalize(src[key]);
      if (canonicalValue !== undefined) {
        out[key] = canonicalValue;
      }
    }
    return out;
  }

  return undefined;
}

/**
 * Deterministically stringify data with canonical key and array handling.
 * @param {unknown} obj
 * @returns {string}
 */
function canonicalStringify(obj) {
  return JSON.stringify(canonicalize(obj));
}

/**
 * Deterministically hash object using canonical JSON representation.
 * @param {unknown} obj
 * @returns {string}
 */
function canonicalHash(obj) {
  const payload = canonicalStringify(obj);
  return crypto.createHash("sha256").update(payload, "utf8").digest("hex");
}

module.exports = {
  canonicalStringify,
  canonicalHash,
};

