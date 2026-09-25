// Driver for test_text_motion.py: runs legacyToStack() of src/textFx.ts — the
// migration the browser applies when it opens an old project — so the test can
// compare it with legacy_to_stack() in backend/app/text_motion.py.
//
//   node --experimental-strip-types text_fx_migration.mjs < cases.json
//
// textFx.ts is imported as-is (Node strips the types). Two loader hooks give
// it what Vite gives it in the app: JSON modules and extensionless imports.
import { readFileSync } from 'node:fs'
import { register } from 'node:module'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const hooks = `
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
export async function resolve(specifier, context, next) {
  if (/^\\.\\.?\\//.test(specifier) && !/\\.(m?[jt]sx?|json)$/.test(specifier)) {
    try { return await next(specifier + '.ts', context) } catch { /* fall through */ }
  }
  return next(specifier, context)
}
export async function load(url, context, next) {
  if (url.startsWith('file:') && url.endsWith('.json')) {
    return { format: 'module', shortCircuit: true, source: 'export default ' + await readFile(fileURLToPath(url), 'utf8') }
  }
  return next(url, context)
}
`
register(`data:text/javascript,${encodeURIComponent(hooks)}`)

const here = path.dirname(fileURLToPath(import.meta.url))
const { legacyToStack } = await import(pathToFileURL(path.join(here, '..', '..', 'src', 'textFx.ts')).href)
const input = JSON.parse(readFileSync(0, 'utf8'))
process.stdout.write(JSON.stringify(input.cases.map(c => legacyToStack(c.item, c.defaults || {}))))
