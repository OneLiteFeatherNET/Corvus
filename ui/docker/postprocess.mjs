// Runs in the Docker build stage on .output/public.
//  1. Makes the asset references of the HTML shells relative ("/_nuxt/x.js" -> "./_nuxt/x.js"). nginx adds
//     <base href="<base path>"> at runtime, so the same build works at the root and under a path prefix.
//  2. Drops the prerendered per-route copies of the shell; nginx falls back to index.html for every route.
//  3. Writes the CSP hashes of the inline scripts (importmap, Nuxt config) to csp-script-hashes.txt.
import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const root = process.argv[2] ?? '.output/public'
const keep = new Set(['index.html', '200.html', '404.html', '_nuxt', 'config.json', 'logo.png'])

for (const entry of readdirSync(root)) {
  if (!keep.has(entry) && statSync(join(root, entry)).isDirectory()) rmSync(join(root, entry), { recursive: true })
}

const hashes = new Set()
for (const file of ['index.html', '200.html', '404.html']) {
  const path = join(root, file)
  const html = readFileSync(path, 'utf8').replaceAll('"/_nuxt/', '"./_nuxt/')
  writeFileSync(path, html)
  for (const m of html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)) {
    hashes.add(`'sha256-${createHash('sha256').update(m[1]).digest('base64')}'`)
  }
}
writeFileSync(join(root, '..', 'csp-script-hashes.txt'), `${[...hashes].join(' ')}\n`)
console.log(`postprocess: ${hashes.size} inline script hashes`)
