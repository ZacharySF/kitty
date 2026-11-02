// Browser fixture for tests/blockEditor.browser.test.mjs. Mounts kitty!'s block editor with an
// in-memory stand-in for the block backend (tests/fakeBlockServer.js); every record is synthetic.
import { mount } from "svelte";
import "/src/styles.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import { keysBetween } from "/src/lib/blockKeys.ts";
import { createBlockServer } from "./fakeBlockServer.js";
import BlockEditorHarness from "./BlockEditorHarness.svelte";

const PAGE = "10000000-0000-4000-8000-000000000001";
const OTHER = "10000000-0000-4000-8000-000000000002";
const id = (n) => `20000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
function blocks(page, items) {
  const keys = keysBetween(null, null, items.length);
  return items.map(([n, type, text, props = {}], index) => ({ id: id(n), type, parent_id: page, order_key: keys[index], properties: props, text }));
}

const server = createBlockServer({
  pages: [
    { page_id: PAGE, number: 1, title: "Synthetic page", blocks: blocks(PAGE, [
      [1, "text", "Alpha block about hash rings."],
      [2, "text", "Bravo block on virtual nodes."],
      [3, "text", "Charlie block for replication."],
      [4, "text", "Delta block closes the page."],
    ]) },
    { page_id: OTHER, number: 2, title: "Second page", blocks: blocks(OTHER, [[9, "text", "Already on the second page."]]) },
  ],
});
window.WebSocket = server.FakeSocket;
const build = createBuildStub();
window.fetch = async (input, init = {}) => {
  const answer = build.fetch(input, init);
  if (answer) return answer;
  const url = new URL(String(input), location.href);
  const handled = server.fetch(url, init.method ?? "GET", init.body ? JSON.parse(init.body) : undefined);
  return handled ?? new Response(JSON.stringify({ error: "not in harness" }), { status: 404 });
};

const opened = [];
await loadBuild();
mount(BlockEditorHarness, { target: document.getElementById("view"), props: { opened } });
window.blockHarness = {
  server, opened, PAGE, OTHER, id,
  /** The page as the server holds it: [type, text, depth] in reading order. */
  outline(page = PAGE) {
    const state = server.state(page);
    const depth = new Map([[page, -1]]);
    return state.blocks.map((block) => {
      depth.set(block.id, depth.get(block.parent_id) + 1);
      return [block.type, block.text, depth.get(block.id)];
    });
  },
  block: (blockId, page = PAGE) => server.state(page).blocks.find((block) => block.id === blockId),
};
