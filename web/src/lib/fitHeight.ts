/**
 * Svelte action: publishes `--fit-h` on the node, the height that makes it end at the bottom of
 * the window above the dock, whatever bars or notices sit above it (the workbench bar, a demo
 * banner, an error line). Canvas-like views (the Notes stage, the mind-map editor) size from it
 * so the page itself never scrolls. Remeasures on resize and when anything above changes size.
 */
export function fitHeight(node: HTMLElement, minimum = 320) {
  let frame = 0;
  const root = document.documentElement;
  const set = (value: number) => node.style.setProperty("--fit-h", `${Math.max(minimum, Math.floor(value))}px`);
  const measure = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const top = node.getBoundingClientRect().top + window.scrollY;
      const page = node.closest<HTMLElement>(".workbench") ?? document.body;
      const room = window.innerHeight - top - (parseFloat(getComputedStyle(page).paddingBottom) || 0);
      set(room);
      // Whatever still sits under the node (a border, a margin) comes off the same height.
      const over = root.scrollHeight - window.innerHeight;
      if (over > 0) set(room - over);
    });
  };
  const observer = new ResizeObserver(measure);
  observer.observe(document.body);
  window.addEventListener("resize", measure);
  measure();
  return {
    destroy() {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", measure);
      node.style.removeProperty("--fit-h");
    },
  };
}
