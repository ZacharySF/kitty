// Runs in the page (pass `textContrast.toString()` to page.evaluate and rebuild it there): the
// WCAG contrast of `element`'s text over every background composited between it and <html>,
// worst case over `grounds` (CSS colours laid under the page, such as a theme's void and panel).
export function textContrast(element, grounds) {
  const canvas = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  const rgba = (css) => {
    canvas.clearRect(0, 0, 1, 1);
    canvas.fillStyle = "#000";
    canvas.fillStyle = css;
    canvas.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
    return [r, g, b, a / 255];
  };
  const over = (top, bottom) => top.slice(0, 3).map((value, index) => value * top[3] + bottom[index] * (1 - top[3]));
  const lin = (value) => ((value /= 255) <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
  const chain = [];
  for (let node = element; node; node = node.parentElement) chain.unshift(getComputedStyle(node));
  const text = rgba(getComputedStyle(element).color);
  let worst = Infinity;
  for (const ground of grounds) {
    let bg = rgba(ground).slice(0, 3);
    for (const style of chain) bg = over(rgba(style.backgroundColor), bg);
    worst = Math.min(worst, ratio(over(text, bg), bg));
  }
  return Math.round(worst * 100) / 100;
}
