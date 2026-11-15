<!-- Kitty's view loader: a train passing, as three smeared bars. -->
<div class="aero-loader" aria-hidden="true"><i></i><i></i><i></i></div>

<style>
  .aero-loader {
    display: grid;
    gap: 5px;
    width: 120px;
    margin: 10px auto;
    overflow: hidden;
  }
  .aero-loader i {
    height: 4px;
    background: linear-gradient(90deg, transparent, var(--ink) 35%, var(--ink) 65%, transparent);
    translate: -110% 0;
    animation: aero-pass 1.15s cubic-bezier(0.5, 0, 0.2, 1) infinite;
  }
  .aero-loader i:nth-child(2) {
    height: 9px;
    background: linear-gradient(90deg, transparent, var(--accent) 35%, var(--accent) 65%, transparent);
    animation-delay: 0.09s;
  }
  .aero-loader i:nth-child(3) {
    animation-delay: 0.18s;
  }
  @keyframes aero-pass {
    to { translate: 110% 0; }
  }
  :global(:root[data-theme-motion="none"]) .aero-loader i {
    animation: none;
    translate: 0 0;
  }
</style>
