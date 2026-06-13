/**
 * Global Ctrl/Cmd+K — focus the quick search input (command-palette affordance).
 * The visual chip lives in global_includes/_topbar.html (.global-search-kbd).
 */
document.addEventListener('keydown', (e: KeyboardEvent): void => {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        const input = document.getElementById('global-search-input') as HTMLInputElement | null;
        if (input) {
            e.preventDefault();
            input.focus();
            input.select();
        }
    }
});
