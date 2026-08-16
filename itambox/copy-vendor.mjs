/**
 * ITAMbox — Vendor asset copy script.
 *
 * Copies CSS/JS/font/image files from node_modules into static/dist/vendor/
 * so they can be served via Django's static files without exposing node_modules.
 */
import { cpSync, mkdirSync } from 'fs';
import { dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const dist = (p) => __dirname + '/static/dist/vendor/' + p;

// Ensure directories exist
mkdirSync(dist('tabler/css'), { recursive: true });
mkdirSync(dist('tabler/img'), { recursive: true });
mkdirSync(dist('tabler/js'), { recursive: true });
mkdirSync(dist('tom-select/css'), { recursive: true });
mkdirSync(dist('gridstack'), { recursive: true });
mkdirSync(dist('mdi/css'), { recursive: true });
mkdirSync(dist('mdi/fonts'), { recursive: true });
mkdirSync(dist('inter'), { recursive: true });
mkdirSync(dist('graphiql'), { recursive: true });

// --- CSS ---

// Tabler CSS
cpSync('node_modules/@tabler/core/dist/css/tabler.min.css', dist('tabler/css/tabler.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler.min.css.map', dist('tabler/css/tabler.min.css.map'));
cpSync('node_modules/@tabler/core/dist/css/tabler-flags.min.css', dist('tabler/css/tabler-flags.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler-payments.min.css', dist('tabler/css/tabler-payments.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler-vendors.min.css', dist('tabler/css/tabler-vendors.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler-vendors.min.css.map', dist('tabler/css/tabler-vendors.min.css.map'));

// Tabler images (flags, payments)
cpSync('node_modules/@tabler/core/dist/img', dist('tabler/img'), { recursive: true });

// Tom Select CSS
cpSync('node_modules/tom-select/dist/css/tom-select.bootstrap5.css', dist('tom-select/css/tom-select.bootstrap5.css'));
cpSync('node_modules/tom-select/dist/css/tom-select.bootstrap5.css.map', dist('tom-select/css/tom-select.bootstrap5.css.map'));

// GridStack CSS
cpSync('node_modules/gridstack/dist/gridstack.min.css', dist('gridstack/gridstack.min.css'));

// MDI CSS + font files
cpSync('node_modules/@mdi/font/css/materialdesignicons.min.css', dist('mdi/css/materialdesignicons.min.css'));
cpSync('node_modules/@mdi/font/fonts/materialdesignicons-webfont.woff2', dist('mdi/fonts/materialdesignicons-webfont.woff2'));
cpSync('node_modules/@mdi/font/fonts/materialdesignicons-webfont.woff', dist('mdi/fonts/materialdesignicons-webfont.woff'));
cpSync('node_modules/@mdi/font/fonts/materialdesignicons-webfont.ttf', dist('mdi/fonts/materialdesignicons-webfont.ttf'));

// Inter Variable font (referenced by @font-face in _typography.scss)
cpSync('node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2', dist('inter/inter-latin-wght-normal.woff2'));
cpSync('node_modules/@fontsource-variable/inter/files/inter-latin-ext-wght-normal.woff2', dist('inter/inter-latin-ext-wght-normal.woff2'));

// --- JS (loaded as separate <script> tags to preserve global assignments) ---

// HTMX (must load in <head> before body hx-* attributes)
cpSync('node_modules/htmx.org/dist/htmx.min.js', dist('htmx.min.js'));

// Bootstrap JS
cpSync('node_modules/bootstrap/dist/js/bootstrap.bundle.min.js', dist('bootstrap.bundle.min.js'));

// GridStack
cpSync('node_modules/gridstack/dist/gridstack-all.js', dist('gridstack-all.js'));

// Tom Select JS
cpSync('node_modules/tom-select/dist/js/tom-select.complete.min.js', dist('tom-select.complete.min.js'));

// ApexCharts
cpSync('node_modules/apexcharts/dist/apexcharts.min.js', dist('apexcharts.min.js'));

// HTML5 QR Code Scanner fallback
cpSync('node_modules/html5-qrcode/html5-qrcode.min.js', dist('html5-qrcode.min.js'));

// GraphiQL UI (served locally; no runtime CDN dependency)
cpSync('node_modules/graphiql/graphiql.min.css', dist('graphiql/graphiql.min.css'));
cpSync('node_modules/@graphiql/plugin-explorer/dist/style.css', dist('graphiql/plugin-explorer.css'));
cpSync('node_modules/whatwg-fetch/dist/fetch.umd.js', dist('graphiql/fetch.umd.js'));
cpSync('node_modules/react/umd/react.production.min.js', dist('graphiql/react.production.min.js'));
cpSync('node_modules/react-dom/umd/react-dom.production.min.js', dist('graphiql/react-dom.production.min.js'));
cpSync('node_modules/graphiql/graphiql.min.js', dist('graphiql/graphiql.min.js'));
cpSync('node_modules/graphql-ws/umd/graphql-ws.min.js', dist('graphiql/graphql-ws.min.js'));
cpSync(
  'node_modules/@graphiql/plugin-explorer/dist/graphiql-plugin-explorer.umd.js',
  dist('graphiql/plugin-explorer.umd.js'),
);

// --- Brand assets ---
mkdirSync(__dirname + '/static/dist/brand', { recursive: true });
cpSync(__dirname + '/static/src/brand/itambox-mark.svg', __dirname + '/static/dist/brand/itambox-mark.svg');
cpSync(__dirname + '/static/src/brand/itambox-logo.svg', __dirname + '/static/dist/brand/itambox-logo.svg');
cpSync(__dirname + '/static/src/brand/favicon.svg', __dirname + '/static/dist/brand/favicon.svg');

console.log('[itambox] Vendor assets copied to static/dist/vendor/');

