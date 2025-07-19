/**
 * AssetBox static asset bundler — esbuild-based build pipeline.
 *
 * Bundles all JS modules from static/js/ into a single assetbox.js.
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
    entryPoints: [dirname + '/index.js'],
    bundle: true,
    minify: !isWatch,
    sourcemap: isWatch,
    outfile: __dirname + '/static/dist/assetbox.js',
    format: 'iife',
    target: ['es2020'],
    logLevel: 'info',
};

if (isWatch) {
    const ctx = await esbuild.context(config);
    await ctx.watch();
    console.log('[assetbox] Watching JS files for changes...');
} else {
    await esbuild.build(config);
    console.log('[assetbox] Build complete — static/dist/assetbox.js');
}
