// Browser fixture for tests/tags.browser.test.mjs: mounts a note page (?page=<number>) and
// Settings → Tags against the real rika! demo server the test started (synthetic data only).
import { mount } from "svelte";
import "/src/styles.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import TagsHarness from "./TagsHarness.svelte";

const opened = [];
await loadBuild();
mount(TagsHarness, { target: document.getElementById("view"), props: { number: Number(new URLSearchParams(location.search).get("page")), opened } });
window.tagsHarness = { opened };
