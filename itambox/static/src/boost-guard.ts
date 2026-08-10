/**
 * Strict non-boost policy for app-external links.
 *
 * The global `hx-boost="true"` on <body> turns every same-origin anchor into
 * an HTMX request. Links that point OUTSIDE the app shell — cross-origin
 * targets and same-origin paths that serve standalone pages or machine
 * responses (/static/docs, /api, /graphql, /admin, /accounts, /media) — must
 * always perform a full-page navigation instead: boosting them swaps a foreign
 * document (or JSON) into the app layout and breaks the UI.
 *
 * htmx 2.x has no event that can veto a single boost while preserving the
 * native navigation (`htmx:beforeRequest` fires after the click default has
 * already been prevented, so cancelling there leaves the link dead). The only
 * strict enforcement is to intercept the click in the capture phase — before
 * htmx's body-level listener — and perform the navigation ourselves. This is
 * the enforcement layer: per-link `hx-boost="false"` remains as defense in
 * depth (e.g. beta_banner.html), but new links are safe by default without
 * remembering an attribute.
 */

export const NON_APP_PATH_PREFIXES = [
  '/static/',
  '/api/',
  '/graphql',
  '/admin/',
  '/accounts/',
  '/media/',
] as const;

/**
 * Decide whether a link target is outside the app shell and therefore must
 * never be boosted. Cross-origin targets and same-origin paths under the
 * NON_APP_PATH_PREFIXES are app-external; scheme differences count as
 * cross-origin. Non-http(s) protocols (mailto:, tel:, javascript:) are left
 * untouched.
 */
export function isAppExternal(href: string, origin: string): boolean {
  let url: URL;
  try {
    url = new URL(href, origin);
  } catch {
    return false; // unparsable — let htmx/the browser handle it
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return false;
  }
  if (url.origin !== origin) {
    return true;
  }
  return NON_APP_PATH_PREFIXES.some(
    (prefix) => url.pathname === prefix || url.pathname.startsWith(prefix),
  );
}

function onDocumentClickCapture(evt: MouseEvent): void {
  if (evt.defaultPrevented) return;
  // Only plain left-clicks without modifiers: modifier clicks (new tab/window)
  // and non-left buttons must keep the browser's native handling.
  if (evt.button !== 0 || evt.metaKey || evt.ctrlKey || evt.shiftKey || evt.altKey) return;
  const target = evt.target instanceof Element ? evt.target : null;
  const anchor = target ? (target.closest('a') as HTMLAnchorElement | null) : null;
  if (!anchor || !anchor.href) return;
  if (anchor.target && anchor.target !== '_self') return; // e.g. target="_blank": htmx skips these anyway
  if (!isAppExternal(anchor.href, window.location.origin)) return;
  evt.preventDefault();
  evt.stopPropagation(); // keep htmx's body-level listener from boosting the click
  window.location.assign(anchor.href);
}

if (typeof document !== 'undefined') {
  document.addEventListener('click', onDocumentClickCapture, true);
}
