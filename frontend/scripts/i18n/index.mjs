#!/usr/bin/env node

import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND_ROOT = path.resolve(SCRIPT_DIR, "../..")
const SRC_DIR = path.join(FRONTEND_ROOT, "src")
const LOCALE_DIR = path.join(SRC_DIR, "i18n", "locales")

const LOCALES = [
  { id: "zh-CN", exportName: "zhCN", file: "zh-CN.ts", source: true },
  { id: "en", exportName: "en", file: "en.ts" },
  { id: "vi", exportName: "vi", file: "vi.ts" },
]

const USER_VISIBLE_ATTRS = new Set([
  "aria-label",
  "alt",
  "label",
  "placeholder",
  "title",
])

const USER_VISIBLE_PROPS = new Set([
  "description",
  "emptyText",
  "error",
  "helperText",
  "label",
  "message",
  "placeholder",
  "subtitle",
  "text",
  "title",
])

const EXTRACT_FILE_ALLOWLIST = new Set([
  // Demo novel names are source/demo content, not interface copy.
  "src/api/demoNovelMap.ts",
])

const COMMANDS = new Set(["extract", "check", "sync"])

function main() {
  const [command] = process.argv.slice(2)
  if (!COMMANDS.has(command)) {
    printUsage()
    process.exitCode = 1
    return
  }

  if (command === "extract") runExtract()
  if (command === "check") runCheck()
  if (command === "sync") runSync()
}

function printUsage() {
  console.log([
    "Usage: node scripts/i18n/index.mjs <command>",
    "",
    "Commands:",
    "  extract   Print candidate user-visible strings for review",
    "  check     Validate locale key coverage and interpolation variables",
    "  sync      Add missing locale keys and align target locale order",
    "",
    "Options:",
    "  --limit <n>          Limit extract output, default 200; use 0 for all",
    "  --json               Print extract output as JSON",
    "  --strict-hardcoded   Make check fail on hardcoded CJK candidates",
  ].join("\n"))
}

function runExtract() {
  const limit = Number(getArg("--limit") ?? 200)
  const candidates = extractCandidates()

  if (hasArg("--json")) {
    console.log(JSON.stringify(candidates, null, 2))
    return
  }

  const visible = limit === 0 ? candidates : candidates.slice(0, limit)
  console.log(`Found ${candidates.length} candidate user-visible strings.`)
  if (visible.length > 0) {
    console.log("")
    for (const item of visible) {
      console.log(
        `- ${item.file}:${item.line} [${item.kind}] ${JSON.stringify(item.text)} -> ${item.suggestedKey}`,
      )
    }
  }
  if (limit > 0 && candidates.length > limit) {
    console.log("")
    console.log(`Output limited to ${limit}. Re-run with --limit 0 to print all candidates.`)
  }
}

function runCheck() {
  const localeData = loadAllLocales()
  const source = localeData.find((item) => item.meta.source)
  const errors = []
  const warnings = []

  for (const locale of localeData) {
    for (const key of locale.duplicateKeys) {
      errors.push(`${locale.meta.id}: duplicate key ${key}`)
    }
  }

  const sourceKeys = source.keys
  const sourceKeySet = new Set(sourceKeys)
  for (const locale of localeData.filter((item) => !item.meta.source)) {
    const localeKeySet = new Set(locale.keys)
    const missing = sourceKeys.filter((key) => !localeKeySet.has(key))
    const stale = locale.keys.filter((key) => !sourceKeySet.has(key))

    for (const key of missing) errors.push(`${locale.meta.id}: missing key ${key}`)
    for (const key of stale) errors.push(`${locale.meta.id}: stale key ${key}`)

    for (const key of sourceKeys) {
      if (!locale.values.has(key)) continue
      const expected = interpolationVars(source.values.get(key))
      const actual = interpolationVars(locale.values.get(key))
      if (!sameList(expected, actual)) {
        errors.push(
          `${locale.meta.id}: interpolation mismatch for ${key}; expected {${expected.join(", ")}}, got {${actual.join(", ")}}`,
        )
      }
    }
  }

  const usedKeys = scanTranslationKeyUsage()
  for (const item of usedKeys) {
    if (!sourceKeySet.has(item.key)) {
      errors.push(`unknown translation key ${item.key} at ${item.file}:${item.line}`)
    }
  }

  const hardcodedCjk = extractCandidates().filter((item) => hasCjk(item.text))
  if (hardcodedCjk.length > 0) {
    warnings.push(
      `${hardcodedCjk.length} hardcoded CJK candidate strings remain. Run npm run i18n:extract -- --limit 50 to inspect them.`,
    )
    if (hasArg("--strict-hardcoded")) {
      for (const item of hardcodedCjk) {
        errors.push(`hardcoded CJK candidate at ${item.file}:${item.line}: ${JSON.stringify(item.text)}`)
      }
    }
  }

  for (const warning of warnings) console.warn(`Warning: ${warning}`)

  if (errors.length > 0) {
    console.error(`i18n check failed with ${errors.length} error(s):`)
    for (const error of errors) console.error(`- ${error}`)
    process.exitCode = 1
    return
  }

  console.log(`i18n check passed for ${localeData.length} locales and ${sourceKeys.length} keys.`)
}

function runSync() {
  const localeData = loadAllLocales()
  const source = localeData.find((item) => item.meta.source)
  const sourceKeys = source.keys
  let changedCount = 0

  for (const locale of localeData.filter((item) => !item.meta.source)) {
    const nextValues = new Map()
    const added = []

    for (const key of sourceKeys) {
      if (locale.values.has(key)) {
        nextValues.set(key, locale.values.get(key))
      } else {
        nextValues.set(key, source.values.get(key))
        added.push(key)
      }
    }

    const stale = locale.keys.filter((key) => !source.values.has(key))
    const nextText = formatTargetLocaleFile(locale.meta.exportName, sourceKeys, nextValues)

    if (nextText !== locale.text) {
      fs.writeFileSync(locale.filePath, nextText, "utf8")
      changedCount += 1
    }

    console.log(
      `${locale.meta.id}: ${added.length} added, ${stale.length} stale removed, ${nextText === locale.text ? "unchanged" : "updated"}`,
    )
  }

  if (changedCount === 0) {
    console.log("Locale files are already synchronized.")
  }
}

function loadAllLocales() {
  return LOCALES.map((meta) => loadLocale(meta))
}

function loadLocale(meta) {
  const filePath = path.join(LOCALE_DIR, meta.file)
  const text = fs.readFileSync(filePath, "utf8")
  const sourceFile = ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const object = findLocaleObject(sourceFile, meta.exportName)
  const values = new Map()
  const keys = []

  for (const property of object.properties) {
    if (!ts.isPropertyAssignment(property)) continue
    const key = getPropertyName(property.name)
    const value = getStringValue(property.initializer)
    if (key == null || value == null) continue
    keys.push(key)
    values.set(key, value)
  }

  return {
    meta,
    filePath,
    text,
    keys,
    values,
    duplicateKeys: findDuplicateKeys(text),
  }
}

function findLocaleObject(sourceFile, exportName) {
  let found = null

  function visit(node) {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === exportName) {
      const initializer = unwrapExpression(node.initializer)
      if (initializer && ts.isObjectLiteralExpression(initializer)) {
        found = initializer
      }
    }
    ts.forEachChild(node, visit)
  }

  visit(sourceFile)

  if (!found) {
    throw new Error(`Could not find locale export ${exportName} in ${sourceFile.fileName}`)
  }

  return found
}

function unwrapExpression(expression) {
  let current = expression
  while (
    current &&
    (ts.isAsExpression(current) ||
      ts.isTypeAssertionExpression(current) ||
      (ts.isSatisfiesExpression?.(current) ?? false))
  ) {
    current = current.expression
  }
  return current
}

function getPropertyName(name) {
  if (ts.isStringLiteral(name) || ts.isNumericLiteral(name) || ts.isIdentifier(name)) return name.text
  return null
}

function getStringValue(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text
  return null
}

function findDuplicateKeys(text) {
  const counts = new Map()
  for (const match of text.matchAll(/^\s*"([^"]+)"\s*:/gm)) {
    counts.set(match[1], (counts.get(match[1]) ?? 0) + 1)
  }
  return [...counts.entries()].filter(([, count]) => count > 1).map(([key]) => key)
}

function formatTargetLocaleFile(exportName, keys, values) {
  const lines = [
    'import type { TranslationKey } from "./zh-CN"',
    "",
    `export const ${exportName}: Record<TranslationKey, string> = {`,
  ]

  for (const key of keys) {
    lines.push(`  ${JSON.stringify(key)}: ${JSON.stringify(values.get(key) ?? "")},`)
  }

  lines.push("}", "")
  return lines.join("\n")
}

function interpolationVars(value) {
  const vars = new Set()
  for (const match of String(value ?? "").matchAll(/\{\{(\w+)\}\}/g)) {
    vars.add(match[1])
  }
  return [...vars].sort()
}

function sameList(a, b) {
  return a.length === b.length && a.every((value, index) => value === b[index])
}

function scanTranslationKeyUsage() {
  const results = []

  for (const filePath of sourceFiles()) {
    if (isLocaleFile(filePath)) continue
    if (EXTRACT_FILE_ALLOWLIST.has(relativeFile(filePath))) continue
    const text = fs.readFileSync(filePath, "utf8")
    const sourceFile = ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS)

    function visit(node) {
      if (ts.isCallExpression(node) && getCallName(node.expression) === "t") {
        const [firstArg] = node.arguments
        if (firstArg && (ts.isStringLiteral(firstArg) || ts.isNoSubstitutionTemplateLiteral(firstArg))) {
          results.push({
            key: firstArg.text,
            file: relativeFile(filePath),
            line: lineOf(sourceFile, firstArg),
          })
        }
      }
      ts.forEachChild(node, visit)
    }

    visit(sourceFile)
  }

  return results
}

function extractCandidates() {
  const candidates = []

  for (const filePath of sourceFiles()) {
    if (isLocaleFile(filePath)) continue
    if (EXTRACT_FILE_ALLOWLIST.has(relativeFile(filePath))) continue
    const text = fs.readFileSync(filePath, "utf8")
    const sourceFile = ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS)

    function add(node, kind, value) {
      const normalized = normalizeText(value)
      if (!isLikelyUserVisible(normalized)) return
      candidates.push({
        file: relativeFile(filePath),
        line: lineOf(sourceFile, node),
        kind,
        text: normalized,
        suggestedKey: suggestKey(filePath, normalized),
      })
    }

    function visit(node) {
      if (ts.isJsxText(node)) {
        if (!isInsideJsxElement(node, "code")) {
          add(node, "jsx-text", node.getText(sourceFile))
        }
      } else if (
        isStringLiteralNode(node) &&
        !isImportOrExportPath(node) &&
        !isTypeOnlyString(node) &&
        !isKeywordString(node)
      ) {
        const attrName = getJsxAttributeName(node)
        if (attrName && USER_VISIBLE_ATTRS.has(attrName)) {
          add(node, `jsx-attr:${attrName}`, node.text)
        } else if (isVisibleCallArgument(node)) {
          add(node, "call-arg", node.text)
        } else if (isVisiblePropertyValue(node)) {
          add(node, "property", node.text)
        } else if (hasCjk(node.text) && !isObviousNonUiString(node)) {
          add(node, "string", node.text)
        }
      }

      ts.forEachChild(node, visit)
    }

    visit(sourceFile)
  }

  candidates.sort((a, b) => a.file.localeCompare(b.file) || a.line - b.line || a.text.localeCompare(b.text))
  return candidates
}

function isStringLiteralNode(node) {
  return ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)
}

function isInsideJsxElement(node, tagName) {
  let current = node.parent
  while (current && !ts.isSourceFile(current)) {
    if (ts.isJsxElement(current)) {
      const name = current.openingElement.tagName.getText()
      if (name === tagName) return true
    }
    current = current.parent
  }
  return false
}

function isTypeOnlyString(node) {
  return ts.isLiteralTypeNode(node.parent)
}

function isKeywordString(node) {
  if (!ts.isArrayLiteralExpression(node.parent)) return false
  const property = node.parent.parent
  if (!ts.isPropertyAssignment(property)) return false
  return getPropertyName(property.name) === "keywords"
}

function isImportOrExportPath(node) {
  const parent = node.parent
  return (
    ts.isImportDeclaration(parent) ||
    ts.isExportDeclaration(parent) ||
    ts.isExternalModuleReference(parent)
  )
}

function getJsxAttributeName(node) {
  if (ts.isJsxAttribute(node.parent) && ts.isIdentifier(node.parent.name)) {
    return node.parent.name.text
  }
  return null
}

function isVisibleCallArgument(node) {
  if (!ts.isCallExpression(node.parent)) return false
  const [firstArg] = node.parent.arguments
  if (firstArg !== node) return false
  return ["alert", "confirm", "prompt"].includes(getCallName(node.parent.expression))
}

function isVisiblePropertyValue(node) {
  if (!ts.isPropertyAssignment(node.parent)) return false
  const name = getPropertyName(node.parent.name)
  return name != null && USER_VISIBLE_PROPS.has(name)
}

function isObviousNonUiString(node) {
  if (isSetMembershipString(node)) return true

  if (ts.isPropertyAssignment(node.parent)) {
    if (node.parent.name === node && isCssClassString(node.parent.initializer)) return true

    const name = getPropertyName(node.parent.name)
    if (name && ["id", "key", "path", "slug", "type", "value"].includes(name)) return true
  }
  if (ts.isJsxAttribute(node.parent)) {
    const name = node.parent.name.getText()
    if (["className", "href", "rel", "target", "type", "value"].includes(name)) return true
  }
  return false
}

function isSetMembershipString(node) {
  const array = node.parent
  if (!ts.isArrayLiteralExpression(array)) return false
  const expression = array.parent
  return ts.isNewExpression(expression) && getCallName(expression.expression) === "Set"
}

function isCssClassString(node) {
  const value = getStringValue(unwrapExpression(node))
  if (value == null) return false
  return /\b(?:bg|text|border|ring|dark|hover|focus|rounded|px|py|mx|my|flex|grid|size|w|h|gap|items|justify)-/.test(value)
}

function getCallName(expression) {
  if (ts.isIdentifier(expression)) return expression.text
  if (ts.isPropertyAccessExpression(expression)) return expression.name.text
  return ""
}

function sourceFiles() {
  const files = []
  walk(SRC_DIR, files)
  return files.filter((file) => file.endsWith(".ts") || file.endsWith(".tsx"))
}

function walk(dir, files) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (["dist", "node_modules"].includes(entry.name)) continue
      walk(fullPath, files)
    } else {
      files.push(fullPath)
    }
  }
}

function isLocaleFile(filePath) {
  return path.normalize(filePath).startsWith(path.normalize(LOCALE_DIR))
}

function normalizeText(text) {
  return text.replace(/\s+/g, " ").trim()
}

function isLikelyUserVisible(text) {
  if (!text) return false
  if (text.length < 2) return false
  if (/^&(?:#x?[0-9a-f]+|\w+);$/i.test(text)) return false
  if (/^https?:\/\//.test(text)) return false
  if (/^[./@#\w-]+$/.test(text) && !hasCjk(text)) return false
  return /[\p{L}\p{N}\p{Script=Han}]/u.test(text)
}

function hasCjk(text) {
  return /[\p{Script=Han}]/u.test(text)
}

function suggestKey(filePath, text) {
  const rel = relativeFile(filePath).replace(/\.(tsx|ts)$/, "")
  const parts = rel
    .replace(/^src\//, "")
    .split(/[\\/]/)
    .map((part) => camelCase(part.replace(/Page$/, "")))
    .filter(Boolean)

  const slug = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 4)
    .join(".")

  return [...parts.slice(-2), slug || `text.${hashText(text)}`].join(".")
}

function camelCase(value) {
  return value
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part, index) => {
      const lower = part.charAt(0).toLowerCase() + part.slice(1)
      return index === 0 ? lower : lower.charAt(0).toUpperCase() + lower.slice(1)
    })
    .join("")
}

function hashText(text) {
  let hash = 5381
  for (let i = 0; i < text.length; i += 1) {
    hash = ((hash << 5) + hash + text.charCodeAt(i)) >>> 0
  }
  return hash.toString(36)
}

function lineOf(sourceFile, node) {
  return sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile)).line + 1
}

function relativeFile(filePath) {
  return path.relative(FRONTEND_ROOT, filePath).replace(/\\/g, "/")
}

function getArg(name) {
  const index = process.argv.indexOf(name)
  if (index === -1) return null
  return process.argv[index + 1] ?? null
}

function hasArg(name) {
  return process.argv.includes(name)
}

main()
