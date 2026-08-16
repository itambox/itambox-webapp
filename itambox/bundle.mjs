/**
 * ITAMbox static asset bundler — esbuild-based build pipeline.
 *
 * Entry points:
 *   static/src/index.ts       → static/dist/itambox.js
 *   static/src/graphql-ui.js  → static/dist/vendor/graphiql/graphiql-ui.js
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

const graphiqlConfig = {
  entryPoints: [__dirname + '/static/src/graphql-ui.js'],
  bundle: true,
  minify: !isWatch,
  sourcemap: isWatch,
  outfile: __dirname + '/static/dist/vendor/graphiql/graphiql-ui.js',
  format: 'iife',
  target: ['es2020'],
  // GraphiQL CSS is copied and served as a normal static asset. Do not inject
  // it from the JS bundle, because that would violate the response CSP.
  loader: { '.css': 'empty' },
  logLevel: 'info',
};

if (isWatch) {
  const ctx = await esbuild.context(config);
  const graphiqlContext = await esbuild.context(graphiqlConfig);
  await Promise.all([ctx.watch(), graphiqlContext.watch()]);
  console.log('[itambox] Watching JS files for changes...');
} else {
  await esbuild.build(config);
  await esbuild.build(graphiqlConfig);
  console.log('[itambox] JS bundles complete — static/dist/');
}
