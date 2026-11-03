<script lang="ts">
  /** half blood's empty state: a crescent moon asleep on a squiggle cloud, two sparks. */
</script>

<svg class="halfblood-empty" viewBox="0 0 124 80" aria-hidden="true">
  <path d="M70 10 A26 26 0 1 0 82 56 A20 20 0 1 1 70 10 Z" fill="#c9dcff" stroke="#0b1250" stroke-width="2" stroke-linejoin="round" />
  <path d="M55 32 q2.5 2.5 5 0" fill="none" stroke="#0b1250" stroke-width="1.8" stroke-linecap="round" />
  <g class="zs" fill="none" stroke="#f7f1e3" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
    <path d="M86 18 h5 l-5 6 h5" />
    <path d="M96 8 h4 l-4 5 h4" />
  </g>
  <path
    d="M22 66 q0 -12 12 -11 q3 -10 14 -8 q6 -8 16 -3 q10 -4 14 5 q12 -1 12 10 q8 2 6 7 Z"
    fill="#f7f1e3"
    stroke="#0b1250"
    stroke-width="2"
    stroke-linejoin="round"
  />
  <path d="M36 62 q3 -4 6 0 q3 -4 6 0 M62 60 q3 -4 6 0 q3 -4 6 0" fill="none" stroke="#1b2fa0" stroke-width="1.6" stroke-linecap="round" />
  <path d="M14 22 Q14 27 19 27 Q14 27 14 32 Q14 27 9 27 Q14 27 14 22 Z" fill="#ffd23a" />
  <path d="M108 44 Q108 47.5 111.5 47.5 Q108 47.5 108 51 Q108 47.5 104.5 47.5 Q108 47.5 108 44 Z" fill="#ffa31a" />
</svg>

<style>
  .halfblood-empty {
    width: 132px;
    height: auto;
    overflow: visible;
  }
  .zs {
    animation: halfblood-doze calc(var(--motion-ms) * 22) ease-in-out infinite alternate;
  }
  @keyframes halfblood-doze {
    from { opacity: 0.35; transform: translateY(2px); }
    to { opacity: 1; transform: translateY(-2px); }
  }
</style>
