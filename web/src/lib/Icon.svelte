<script lang="ts">
  let { name, size = 20 }: { name: string; size?: number } = $props();
  const paths: Record<string, string> = {
    today: "M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z",
    atlas:
      "M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18ZM3.5 9.5c4 2 13 2 17 0M3.5 14.5c4-2 13-2 17 0M12 3c-3.5 4-3.5 14 0 18M12 3c3.5 4 3.5 14 0 18",
    calendar: "M4 5h16v16H4ZM4 10h16M8 3v4M16 3v4M8 14h2M14 14h2M8 18h2",
    opportunities: "m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3ZM19 3v4M17 5h4",
    study: "M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3ZM12 6v16M6 9h3M15 9h3",
    flashcards: "M7 3h14v14H7ZM3 7v14h14M10 8h8M10 12h5",
    notes: "M4 3h16v18H4ZM8 7h8M8 11h8M8 15h4m3 3 4-4",
    schema: "M10 4h4v4h-4ZM3 16h4v4H3ZM17 16h4v4h-4ZM12 8v4M5 16v-4h14v4",
    kanban:
      "M3 3h18v18H3ZM9 3v18M15 3v18M5 7h2M5 11h2M11 7h2M17 7h2M17 11h2M17 15h2",
    ai: "M12 3v3M9 3h6M5 7h14v13H5ZM8 11h.01M16 11h.01M8 16h8M2 10v6M22 10v6",
    kitty:
      "M4 10V3l5 4a10 10 0 0 1 6 0l5-4v7a8 8 0 1 1-16 0ZM8 12h.01M16 12h.01m-6 4 2 1 2-1",
    jobs: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0",
    queue: "M4 5h16M4 12h10M4 19h10m3-10 4 3-4 3",
    applications: "M8 5V3h8v2M3 7h18v14H3ZM3 12h18M10 12v3h4v-3",
    favorites:
      "m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z",
    resumes: "M14 2H5v20h14V7Zm0 0v5h5M8 12h8M8 16h8",
    sources: "M12 8v8M8 12h8M20 12a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    inbox: "M4 3h16l2 13v5H2v-5Zm-2 13h6l2 3h4l2-3h6",
    settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
    reports: "M4 3v18h17M8 16v-4M13 16V7M18 16v-7",
    mail: "M3 5h18v14H3Zm0 0 9 8 9-8",
    questions: "M8 8a4 4 0 1 1 7 3c-2 1-3 2-3 4M12 19h.01",
    arrow: "M4 12h16m-6-6 6 6-6 6",
    external: "M14 3h7v7m0-7L10 14M10 3H3v18h18v-7",
    close: "m6 6 12 12M6 18 18 6",
    refresh:
      "M20 7v5h-5M4 17v-5h5M5.2 7a8 8 0 0 1 13.2-2L20 8M4 16l1.6 3A8 8 0 0 0 19 17",
    check: "m5 12 4 4L19 6",
    download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
    location:
      "M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0",
    menu: "M4 6h16M4 12h16M4 18h16",
    moon: "M20 14a8.5 8.5 0 0 1-10-10 8.5 8.5 0 1 0 10 10",
    shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Zm-4 9 3 3 5-6",
  };
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.6"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"><path d={paths[name] || paths.jobs} /></svg
>
