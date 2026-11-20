/**
 * "Open Rika's Memory" from elsewhere (the Workshop's "Rika remembers" strip): the request
 * waits here until Rika's page shows it, whether that page is open already or opens next.
 */
export const memoryRequest = $state({ pending: false });

export function requestMemory() {
  memoryRequest.pending = true;
}
