/**
 * Page edit mode's page widgets while they are being arranged: PageEdit.svelte writes them,
 * every PageWidgets.svelte draws them. `widgets` is null outside edit mode (the build's
 * `ui.pages.widgets` are drawn).
 */
export const pageDraft = $state<{ widgets: string[] | null }>({ widgets: null });
