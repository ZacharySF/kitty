<script lang="ts">
  /** Kitty's empty state: a cat asleep on the last bench of the platform, in text. */
  const ART = String.raw`        z
      z
   /\_/\
  ( -.- )
  (  u u )
 ==================
  ||            ||`;
</script>

<pre class="aero-empty" aria-hidden="true">{ART}</pre>

<style>
  .aero-empty {
    margin: 0 auto 6px;
    width: max-content;
    color: var(--accent);
    font-family: var(--font-code);
    font-size: 13px;
    line-height: 1.2;
    opacity: 0.85;
    text-shadow: 0 0 12px color-mix(in srgb, var(--accent) 45%, transparent);
  }
</style>
