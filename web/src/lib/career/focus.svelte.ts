/**
 * What a Career rail link asks the page it opens to show first: the rail's Interviews link
 * opens Track with `stage` "interview"; the board scrolls to that column and clears it.
 */
export const careerFocus = $state<{ stage: string | null }>({ stage: null });
