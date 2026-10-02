// Bundles three.js and the post-processing add-ons the prototype uses into one minified ES module.
// Usage: node vendor/build-vendor.mjs <node_modules directory containing three and esbuild>
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const modules = process.argv[2];
if (!modules) {
  console.error('Usage: node vendor/build-vendor.mjs <path to node_modules with three and esbuild>');
  process.exit(1);
}
const require = createRequire(path.join(modules, 'noop.js'));
const esbuild = require('esbuild');
const here = path.dirname(fileURLToPath(import.meta.url));

await esbuild.build({
  entryPoints: [path.join(here, 'three-entry.js')],
  bundle: true,
  minify: true,
  format: 'esm',
  target: 'es2020',
  nodePaths: [modules],
  outfile: path.join(here, 'three.bundle.min.js'),
  legalComments: 'eof',
});
console.log('wrote vendor/three.bundle.min.js');
