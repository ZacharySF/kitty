/**
 * Schema-depth breadcrumb shown in the App top bar:
 * `kitty › <Section> › <node> › …`. The App owns `kitty` and the section crumb;
 * the active section page pushes the nodes below it.
 */
export type Crumb = { label: string; onclick?: () => void };

