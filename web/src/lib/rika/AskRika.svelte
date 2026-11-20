<script lang="ts">
  import Icon from "../Icon.svelte";
  import { navigation } from "../workspace";
  import { askRika, rikaFocus } from "./focus.svelte";

  /**
   * Every view's one "Ask Rika": opens her beside the page with what is open as the context.
   * `push` sits it at the end of its row; `compact` is the same pill as just the mark, for a
   * header row that is already full (its name stays "Ask Rika").
   */
  let { prompt, push = false, compact = false }: { prompt?: string; push?: boolean; compact?: boolean } = $props();
  const about = $derived(rikaFocus.item?.title || navigation.find((item) => item.id === rikaFocus.view)?.label || "");
</script>

<button type="button" class="button ask-rika" class:push class:compact data-ask-rika aria-label="Ask Rika"
  title={about ? `Ask Rika about ${about}` : "Ask Rika"}
  onclick={() => askRika(prompt)}><Icon name="ai" size={15} />{#if !compact}<span>Ask Rika</span>{/if}</button>

<style>
  /* One quiet pill, the same in every view's header row. */
  .ask-rika {
    flex: none;
    height: var(--control-h);
    padding: 0 12px 0 10px;
    border-radius: 999px;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    color: var(--ink);
    font-size: max(13px, var(--fs-control));
    font-weight: 500;
    gap: 6px;
  }
  .compact {
    width: var(--control-h);
    padding: 0;
  }
  .push {
    margin-left: auto;
  }
</style>
