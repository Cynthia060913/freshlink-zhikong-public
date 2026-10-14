// Deterministic whitespace-only cleanup after SDK generation; no API changes.
import { readdir, readFile, writeFile } from "node:fs/promises"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const clientRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../frontend/src/client")
async function normalize(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) await normalize(path)
    else if (entry.isFile() && entry.name.endsWith(".ts")) {
      const original = await readFile(path, "utf8")
      // Only indentation on otherwise empty lines; string/comment content stays intact.
      const cleaned = original.replace(/^[\t ]+(?=\r?$)/gm, "")
      if (cleaned !== original) await writeFile(path, cleaned, "utf8")
    }
  }
}
await normalize(clientRoot)
