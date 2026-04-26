/**
 * ITAMbox static asset bundler — esbuild-based build pipeline.
 *
 * Entry point:
 *   static/src/index.ts → static/dist/itambox.js  (JS bundle, IIFE)
 *
 * Usage:
 *   node bundle.mjs           Build (production minified)
 *   node bundle.mjs --watch   Watch mode (development)
 */
import * as esbuild from 'esbuild';
import { dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const isWatch = process.argv.includes('--watch');

const config = {
  entryPoints: [__dirname + '/static/src/index.ts'],
  bundle: true,
  minify: !isWatch,
  sourcemap: isWatch,
  outfile: __dirname + '/static/dist/itambox.js',
  format: 'iife',
  target: ['es2020'],
  logLevel: 'info',
};

if (isWatch) {
  const ctx = await esbuild.context(config);
  await ctx.watch();
  console.log('[itambox] Watching JS files for changes...');
} else {
  await esbuild.build(config);
  console.log('[itambox] JS build complete — static/dist/itambox.js');
}
