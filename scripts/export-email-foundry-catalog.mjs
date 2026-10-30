import { createHash } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const sourcePath = resolve(
  repositoryRoot,
  'app/public/dependencies/@bttco/email-foundry/1.0.0/email-templates.js',
)
const outputPath = resolve(repositoryRoot, 'api/app/services/email_foundry_catalog.json')

// This projection exists because the locked Foundry dependency is an ES module
// while the production mail sender is Python. It copies no authored behavior:
// every subject, HTML byte, and merge-field key is exported from that authority.
const source = await readFile(sourcePath, 'utf8')
const sourceSha256 = createHash('sha256').update(source).digest('hex')
const moduleUrl = `${pathToFileURL(sourcePath).href}?sha256=${sourceSha256}`
const { TEMPLATES } = await import(moduleUrl)

const templates = Object.fromEntries(
  TEMPLATES.map((template) => [
    template.slug,
    {
      name: template.name,
      subject: template.subject,
      html: template.html,
      variables: (template.variables ?? []).map((variable) => variable.key),
    },
  ]),
)

const catalog = {
  dependency: '@bttco/email-foundry',
  version: '1.0.0',
  source: 'app/public/dependencies/@bttco/email-foundry/1.0.0/email-templates.js',
  source_sha256: sourceSha256,
  templates,
}

await writeFile(outputPath, `${JSON.stringify(catalog, null, 2)}\n`, 'utf8')
console.log(`Exported ${Object.keys(templates).length} Email Foundry templates to ${outputPath}`)
