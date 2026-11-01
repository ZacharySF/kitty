<!-- Browser fixture for tests/tags.browser.test.mjs: a real note page (block editor, tag chips,
     tag pages) and Settings → Tags, against a real kitty! demo server. -->
<script>
  import NoteOutline from "/src/lib/NoteOutline.svelte";
  import TagSettings from "/src/lib/TagSettings.svelte";

  let { number, opened } = $props();
</script>

<NoteOutline pageId={number} pages={[]} references={{}} counts={{}} onpage={() => {}}
  onopen={(page, block) => opened.push([page, block])} onopenpage={() => {}} oncreatepage={async () => null}
  onrevision={() => {}} onerror={(message) => console.error(message)} />
<TagSettings />
