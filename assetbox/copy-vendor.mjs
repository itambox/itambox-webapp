/**
 * AssetBox — Vendor asset copy script.
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

// --- CSS ---

// Tabler CSS
cpSync('node_modules/@tabler/core/dist/css/tabler.min.css', dist('tabler/css/tabler.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler-flags.min.css', dist('tabler/css/tabler-flags.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler-payments.min.css', dist('tabler/css/tabler-payments.min.css'));
cpSync('node_modules/@tabler/core/dist/css/tabler-vendors.min.css', dist('tabler/css/tabler-vendors.min.css'));

// Tabler images (flags, payments)
cpSync('node_modules/@tabler/core/dist/img', dist('tabler/img'), { recursive: true });

// Tom Select CSS
cpSync('node_modules/tom-select/dist/css/tom-select.bootstrap5.css', dist('tom-select/css/tom-select.bootstrap5.css'));

// GridStack CSS
cpSync('node_modules/gridstack/dist/gridstack.min.css', dist('gridstack/gridstack.min.css'));

// MDI CSS + font files
cpSync('node_modules/@mdi/font/css/materialdesignicons.min.css', dist('mdi/css/materialdesignicons.min.css'));
cpSync('node_modules/@mdi/font/fonts/materialdesignicons-webfont.woff2', dist('mdi/fonts/materialdesignicons-webfont.woff2'));
cpSync('node_modules/@mdi/font/fonts/materialdesignicons-webfont.woff', dist('mdi/fonts/materialdesignicons-webfont.woff'));
cpSync('node_modules/@mdi/font/fonts/materialdesignicons-webfont.ttf', dist('mdi/fonts/materialdesignicons-webfont.ttf'));

// --- JS (loaded as separate <script> tags to preserve global assignments) ---

// HTMX (must load in <head> before body hx-* attributes)
cpSync('node_modules/htmx.org/dist/htmx.min.js', dist('htmx.min.js'));

// Bootstrap JS
cpSync('node_modules/bootstrap/dist/js/bootstrap.bundle.min.js', dist('bootstrap.bundle.min.js'));

// GridStack
cpSync('node_modules/gridstack/dist/gridstack-all.js', dist('gridstack-all.js'));

// Tom Select JS
cpSync('node_modules/tom-select/dist/js/tom-select.complete.min.js', dist('tom-select.complete.min.js'));

console.log('[assetbox] Vendor assets copied to static/dist/vendor/');
