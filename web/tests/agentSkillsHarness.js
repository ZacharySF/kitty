// Browser fixture for tests/agentSkills.browser.test.mjs. Mounts the real Rika bar and
// history drawer, the herdr view's new-agent strip and the All agent skills view; the
// test serves /api/rika/* and /api/terminal/* with a scripted stand-in. The look and the
// coding-agent choice come from a stand-in build, as in every harness on the platform stack.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { loadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import Rika from "/src/lib/rika/Rika.svelte";
import TerminalAgents from "/src/lib/TerminalAgents.svelte";
import AgentSkillsView from "/src/lib/AgentSkillsView.svelte";
import { rika } from "/src/lib/rika/session.svelte.ts";

const build = createBuildStub();
const served = window.fetch.bind(window);
window.fetch = (url, init = {}) => build.fetch(url, init) ?? served(url, init);
await loadBuild();
const navigations = [];
mount(TerminalAgents, { target: document.getElementById("agents") });
mount(AgentSkillsView, { target: document.getElementById("skills") });
mount(Rika, { target: document.getElementById("rika"), props: { onnavigate: (view) => navigations.push(view) } });
window.skillsHarness = { navigations, connected: () => rika.connected };
