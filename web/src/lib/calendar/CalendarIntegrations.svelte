<!-- Imports & Google: ICS/CSV import and export, reminder notifications, Google Calendar sync and the
     reviewed resource editor. Mounted with the calendar so a running Google task keeps polling (and
     refreshing the calendar) while another section is open; the panels render only when `visible`. -->
<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api, type TaskRecord } from "../api";
  import GoogleConflict, { type ConflictEvent } from "../GoogleConflict.svelte";
  import { notifier } from "../notifications.svelte";
  import type { ThemeTokens } from "../themes/contract";
  import Select from "../ui/Select.svelte";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import { addDays } from "./dates";
  import { blockText } from "./palette";
  import type { Cal, CalendarEvent } from "./types";

  type Painted = Pick<CalendarEvent, "calendar_id" | "subject_id" | "color">;
  let { visible, active, demo, tz, anchor, theme, paint, run, load, onerror, onnotice, onundo }: {
    visible: boolean;
    /** The calendar is the active pane: Escape closes a pending operation review only then. */
    active: boolean;
    demo: boolean;
    tz: string;
    anchor: string;
    theme: ThemeTokens;
    paint: (item: Painted) => string;
    /** The calendar's save wrapper: clears the error and notice, counts pending work, reports failures. */
    run: (work: () => Promise<unknown>) => Promise<unknown>;
    load: () => Promise<void>;
    onerror: (message: string) => void;
    onnotice: (message: string) => void;
    onundo: (id: number | null) => void;
  } = $props();
  const pane = useWorkspaceContext();
  type GoogleStatus = {
    client_present: boolean;
    authorized: boolean;
    tasks_authorized: boolean;
    steps: string[];
    selected: Cal[];
    notice: string;
    limits: string[];
    public_operations: Record<string, string[]>;
    calendar_conflicts: {
      calendar_id: string;
      local: {
        name: string;
        timezone: string;
        color: string;
        revision: number;
      };
      remote: { values: { name: string; timezone: string; color: string } };
    }[];
    capabilities: {
      google: string[];
      account_dependent: string[];
      unsupported: Record<string, string>;
    };
  };
  type Preview = {
    row?: number;
    event?: Record<string, unknown>;
    exceptions?: unknown[];
    duplicate?: boolean;
    error?: string;
    selected?: boolean;
  };
  let importRows = $state<Preview[]>([]);
  let importNotice = $state("");
  let googleStatus = $state<GoogleStatus | null>(null);
  let googleResult = $state<unknown>(null);
  let googleTask = $state<TaskRecord | null>(null);
  let linked = $state<string[]>([]);
  let includeTasks = $state(false);
  let syncDirection = $state("pull");
  let sendUpdates = $state("none");
  let syncApproved = $state(false);
  /** A proposed Google write (kitty effects kernel): approving sends back exactly this id and digest. */
  type ProposedEffect = { id: string; digest: string; expires_at: string };
  type SyncPreview = {
    calendars: Record<string, unknown>[];
    events: Record<string, unknown>[];
    effect: ProposedEffect;
  };
  let syncPreview = $state<SyncPreview | null>(null);
  let pendingGoogle = $state<{
    resource?: string;
    action?: string;
    calendar_id?: string;
    id?: string;
  }>({});
  let pendingPath = $state("");
  let remoteCalendars = $state<Record<string, unknown>[]>([]);
  let googleConflicts = $state<
    {
      id: number;
      local_revision: number;
      local: CalendarEvent & { deleted?: boolean };
      remote: ConflictEvent | null;
    }[]
  >([]);
  let resource = $state("calendarList");
  let action = $state("list");
  let remoteCalendarId = $state("primary");
  let remoteId = $state("");
  let remoteEtag = $state("");
  let remoteData = $state("{}");
  let remoteParams = $state("{}");
  let meet = $state(false);
  let notifySharing = $state(false);
  let webhookConfirmed = $state(false);
  type OperationReview = {
    resource: string;
    action: string;
    method: string;
    url: string;
    params: Record<string, unknown>;
    body: unknown;
    if_match: string | null;
    current: Record<string, unknown> | null;
    recipients: string[];
    items: Record<string, unknown>[];
    notes: string[];
    effect: ProposedEffect;
  };
  let operationRequest = $state<Record<string, unknown> | null>(null);
  let operationReview = $state<OperationReview | null>(null);
  let operationApproved = $state(false);
  let clearConfirmed = $state(false);
  const resourceActions: Record<string, string[]> = $derived(
    googleStatus?.public_operations || {
      calendarList: ["list", "get", "create", "update", "delete"],
      calendars: ["get", "create", "update", "delete"],
      events: [
        "list",
        "get",
        "create",
        "update",
        "delete",
        "rsvp",
        "instances",
      ],
      colors: ["list", "get"],
      settings: ["list", "get"],
      acl: ["list", "get", "create", "update", "delete"],
      freebusy: ["freebusy"],
      tasklists: ["list", "get", "create", "update", "delete"],
      tasks: ["list", "get", "create", "update", "delete"],
    },
  );
  const actionLabels: Record<string, string> = {
    list: "List resources",
    get: "Read resource + ETag",
    create: "Create resource",
    update: "Patch selected fields",
    replace: "Replace full resource (PUT)",
    delete: "Delete resource",
    rsvp: "Update reviewed RSVP responses",
    instances: "List recurring instances",
    freebusy: "Check provider availability",
    move: "Move event/task (explicit source & target)",
    import: "Import a private event copy (iCalUID)",
    quickAdd: "Create event from Google-parsed text",
    watch: "Register a public HTTPS change channel",
    clear: "Clear primary events / completed tasks",
    stop: "Stop a registered change channel",
  };
  async function exportFile() {
    await run(async () => {
      const result = await api<{ content: string; filename: string }>(
        "/calendar/export",
      );
      const url = URL.createObjectURL(
        new Blob([result.content], { type: "text/calendar" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = result.filename;
      a.click();
      URL.revokeObjectURL(url);
    });
  }
  async function importFile(event: EventTarget | null, format: string) {
    const input = event as HTMLInputElement;
    if (!input.files?.[0]) return;
    await run(async () => {
      const result = await api<{ items: Preview[]; notice: string }>(
        "/calendar/import/preview",
        { content: await input.files![0].text(), format, timezone: tz },
      );
      importRows = result.items.map((v) => ({
        ...v,
        selected: !!v.event && !v.duplicate && !v.error,
      }));
      importNotice = result.notice;
    });
    input.value = "";
  }
  async function applyImport() {
    await run(async () => {
      const result = await api<{
        created: string[];
        duplicates: string[];
        undo_ids: number[];
      }>("/calendar/import", {
        confirmed: true,
        items: importRows.filter((v) => v.selected),
      });
      onnotice(`Imported ${result.created.length}; skipped ${result.duplicates.length} duplicates.`);
      onundo(result.undo_ids.at(-1) || null);
      importRows = [];
      await load();
    });
  }
  async function refreshSources() {
    await run(async () => {
      const result = await api<{ changed: number; review_errors: unknown[] }>(
        "/calendar/import-jobs",
        {},
      );
      onnotice(`Refreshed ${result.changed} linked dates. ${result.review_errors.length} source dates need review.`);
      if (result.review_errors.length) googleResult = result.review_errors;
      await load();
    });
  }
  async function loadGoogle() {
    await run(async () => {
      googleStatus = await api<GoogleStatus>("/calendar/google/status");
      googleConflicts = (
        await api<{ items: typeof googleConflicts }>(
          "/calendar/google/conflicts",
        )
      ).items;
      linked = googleStatus.selected.map((c) => c.id);
    });
  }
  async function googleWork(path: string, body: unknown) {
    pendingGoogle =
      body && typeof body === "object" ? (body as typeof pendingGoogle) : {};
    pendingPath = path;
    await run(async () => {
      const result = await api<{ task: TaskRecord }>(path, body);
      googleTask = result.task;
      syncApproved = false;
      syncPreview = null;
      googleResult = null;
    });
  }
  /** A sync request; a push approves the frozen preview's proposal by its id and digest (single use). */
  function syncRequest(full = false) {
    return {
      calendar_ids: linked,
      direction: syncDirection,
      send_updates: sendUpdates,
      confirmed: true,
      external_approved: syncApproved,
      ...(syncDirection === "both" && syncPreview
        ? { effect_id: syncPreview.effect.id, digest: syncPreview.effect.digest }
        : {}),
      ...(full ? { full: true } : {}),
    };
  }
  async function pollTask() {
    if (!googleTask || googleTask.status !== "running") return;
    await run(async () => {
      const result = await api<TaskRecord>(`/tasks/${googleTask!.id}`);
      googleTask = result;
      if (
        result.status === "completed" &&
        pendingPath === "/calendar/google/operation/preview"
      ) {
        operationReview = result.result as OperationReview;
        operationApproved = false;
        clearConfirmed = false;
      } else if (result.status === "completed") {
        googleResult = result.result;
        const items = (result.result as { items?: Record<string, unknown>[] })
          ?.items;
        if (
          pendingGoogle.resource === "calendarList" &&
          pendingGoogle.action === "list" &&
          items
        )
          remoteCalendars = items;
        await loadGoogle();
        await load();
      }
      if (result.status === "failed")
        onerror(result.error || "Google operation failed");
    });
  }
  async function uploadClient(target: EventTarget | null) {
    const input = target as HTMLInputElement;
    if (!input.files?.[0]) return;
    await run(async () => {
      await api("/calendar/google/client", {
        confirmed: true,
        client: await input.files![0].text(),
      });
      await loadGoogle();
    });
    input.value = "";
  }
  function template() {
    const allowed = resourceActions[resource];
    if (!allowed.includes(action)) action = allowed[0];
    meet = false;
    notifySharing = false;
    if (resource === "calendars" && !remoteId) remoteId = "primary";
    if (resource === "settings" && action === "get" && !remoteId)
      remoteId = "timezone";
    if (resource === "tasks" && remoteCalendarId === "primary")
      remoteCalendarId = "@default";
    const values: Record<string, unknown> = {
      calendarList:
        action === "create"
          ? { id: "calendar-id@example.com" }
          : action === "update"
            ? { selected: true }
            : {},
      calendars:
        action === "create" || action === "update"
          ? { summary: "My calendar", timeZone: tz }
          : {},
      events:
        action === "create" || action === "import"
          ? {
              summary: "New event",
              start: { date: anchor },
              end: { date: addDays(anchor, 1) },
              ...(action === "import"
                ? {
                    iCalUID: `${crypto.randomUUID()}@kitty.local`,
                    eventType: "default",
                  }
                : {}),
            }
          : action === "rsvp"
            ? {
                attendees: [
                  { email: "you@example.com", responseStatus: "accepted" },
                ],
              }
            : {},
      acl:
        action === "create" || action === "update"
          ? {
              scope: { type: "user", value: "person@example.com" },
              role: "reader",
            }
          : {},
      freebusy: {
        timeMin: new Date().toISOString(),
        timeMax: new Date(Date.now() + 86400000).toISOString(),
        timeZone: tz,
        items: [{ id: "primary" }],
      },
      tasks:
        action === "create"
          ? { title: "New task", due: anchor + "T00:00:00Z" }
          : {},
      tasklists: action === "create" ? { title: "New task list" } : {},
    };
    remoteData = JSON.stringify(values[resource] || {}, null, 2);
    if (action === "watch")
      remoteData = JSON.stringify(
        {
          id: crypto.randomUUID(),
          type: "web_hook",
          address: "",
          params: { ttl: "604800" },
        },
        null,
        2,
      );
    if (resource === "events" && action === "quickAdd") {
      remoteData = JSON.stringify(
        { text: `Study block on ${anchor} from 9am to 10am` },
        null,
        2,
      );
      sendUpdates = "none";
    }
    if (resource === "channels")
      remoteData = JSON.stringify({ id: "", resourceId: "" }, null, 2);
    remoteParams = JSON.stringify(
      action === "move"
        ? resource === "events"
          ? { destination: "" }
          : { destinationTasklist: remoteCalendarId }
        : {},
      null,
      2,
    );
    operationRequest = null;
    operationReview = null;
    operationApproved = false;
    clearConfirmed = false;
    webhookConfirmed = false;
  }
  async function reviewOperation() {
    let request: Record<string, unknown>;
    try {
      const data = JSON.parse(remoteData);
      const params = JSON.parse(remoteParams);
      if (
        !data ||
        typeof data !== "object" ||
        Array.isArray(data) ||
        !params ||
        typeof params !== "object" ||
        Array.isArray(params)
      )
        throw new Error("Expected JSON objects");
      if (action === "watch" && !webhookConfirmed)
        throw new Error(
          "Configure and confirm your own public HTTPS receiver before preparing a watch.",
        );
      if (resource === "events" && action === "move" && !params.destination)
        throw new Error(
          "Enter the exact target calendar ID in params.destination.",
        );
      if (
        resource === "events" &&
        action === "quickAdd" &&
        sendUpdates !== "none"
      )
        throw new Error(
          "Safe quick add requires notification policy none; use structured create for reviewed attendees.",
        );
      request = {
        resource,
        action,
        calendar_id: remoteCalendarId,
        id: remoteId,
        etag: remoteEtag || undefined,
        data,
        params: {
          ...params,
          ...(resource === "events" &&
          [
            "create",
            "update",
            "replace",
            "delete",
            "rsvp",
            "move",
            "quickAdd",
          ].includes(action)
            ? { sendUpdates }
            : {}),
        },
        meet,
        notify: notifySharing,
        webhook_confirmed: webhookConfirmed,
      };
    } catch (e) {
      onerror(
        e instanceof SyntaxError
          ? "Resource data and query parameters must be valid JSON objects."
          : e instanceof Error
            ? e.message
            : String(e),
      );
      return;
    }
    operationReview = null;
    operationApproved = false;
    clearConfirmed = false;
    if (["list", "get", "instances", "freebusy"].includes(action)) {
      operationRequest = null;
      await googleWork("/calendar/google/operation", request);
      return;
    }
    operationRequest = request;
    // The server reads the live target and returns the exact frozen request to approve.
    await googleWork("/calendar/google/operation/preview", request);
  }
  async function sendReviewedOperation() {
    if (!operationRequest || !operationReview) return;
    // The server sends the reviewed request only if this proposal is unchanged and unused.
    const body = {
      ...operationRequest,
      effect_id: operationReview.effect.id,
      digest: operationReview.effect.digest,
      confirmed: true,
      external_approved: true,
      clear_confirmed: clearConfirmed,
    };
    operationRequest = null;
    operationReview = null;
    operationApproved = false;
    clearConfirmed = false;
    await googleWork("/calendar/google/operation", body);
  }
  function useGoogleResult() {
    if (
      !googleResult ||
      typeof googleResult !== "object" ||
      Array.isArray(googleResult) ||
      resource !== pendingGoogle.resource
    )
      return;
    const result = googleResult as Record<string, unknown>;
    if (typeof result.id !== "string" || typeof result.etag !== "string")
      return;
    remoteId = result.id;
    remoteEtag = result.etag;
    if (pendingGoogle.calendar_id) remoteCalendarId = pendingGoogle.calendar_id;
    if (resource === "calendars" || resource === "tasklists")
      remoteCalendarId = result.id;
    operationRequest = null;
    operationReview = null;
    operationApproved = false;
    onnotice(
      "Copied the returned ID and ETag. Prepare the operation to review the exact request and live recipients.",
    );
  }
  $effect(() => {
    syncDirection;
    sendUpdates;
    linked;
    untrack(() => {
      syncApproved = false;
      syncPreview = null;
    });
  });

  onMount(() => {
    void loadGoogle();
    const timer = setInterval(() => void pollTask(), 1200);
    return () => clearInterval(timer);
  });
</script>

<svelte:window onkeydown={(e) => {
  if (active && e.key === "Escape") operationReview = null;
}} />

{#if visible}
    <section class="panel">
      <h2>Import / export</h2>
      <div class="controls">
        <label class="file"
          >Preview ICS<input
            type="file"
            accept=".ics,text/calendar"
            onchange={(e) => importFile(e.target, "ics")}
          /></label
        ><label class="file" title="CSV headers: title,date; optional job_id,kind,timezone,url,description"
          >Preview CSV<input
            type="file"
            accept=".csv,text/csv"
            onchange={(e) => importFile(e.target, "csv")}
          /></label
        ><button onclick={exportFile}>Export ICS</button><button
          onclick={refreshSources}
          >Refresh linked application / opportunity dates</button
        >
      </div>
      {#if importRows.length}<p>{importNotice}</p>
        <div class="import-review">
          {#each importRows as row}<label
              ><input
                type="checkbox"
                bind:checked={row.selected}
                disabled={!!row.error || !!row.duplicate}
              /><span
                >{row.row ? `Row ${row.row}: ` : ""}{row.event?.title ||
                  row.error}{row.duplicate
                  ? " · existing UID/source, skipped"
                  : ""}<small
                  >{String(row.event?.start || "")} → {String(
                    row.event?.end || "",
                  )}
                  {String(row.event?.timezone || "")}
                  {String(row.event?.rrule || "")}</small
                ></span
              ></label
            >{/each}
        </div>
        <button
          onclick={applyImport}
          disabled={!importRows.some((v) => v.selected)}
          >Confirm selected import</button
        ><button onclick={() => (importRows = [])}>Cancel</button>{/if}
    </section>
    <section class="panel">
      <h2 title="Due reminders arrive as toasts and in the bell while rika! runs; quiet hours live in Settings">Reminder notifications</h2>
      <label
        ><input
          type="checkbox"
          checked={notifier.system}
          onchange={(event) => {
            event.currentTarget.checked = notifier.system;
            void notifier.toggleSystem();
          }}
        />Also show system notifications</label
      >
    </section>
    <section class="panel">
      <h2>Google Calendar · optional</h2>
      {#if demo}<p class="notice">Demo mode: Google calls are blocked.</p>{/if}{#if googleStatus}<p>{googleStatus.notice}</p>
        <p>
          Client: {googleStatus.client_present
            ? "stored privately"
            : "not configured"} · Account: {googleStatus.authorized
            ? "authorized"
            : "not authorized"} · Tasks: {googleStatus.tasks_authorized
            ? "authorized"
            : "not authorized"}
        </p>
        <ol>
          {#each googleStatus.steps as step}<li>{step}</li>{/each}
        </ol>
        <div class="controls">
          <label class="file"
            >Upload Desktop client JSON<input
              type="file"
              accept=".json,application/json"
              disabled={demo || googleStatus.authorized}
              onchange={(e) => uploadClient(e.target)}
            /></label
          ><label
            ><input type="checkbox" bind:checked={includeTasks} />Also request
            Google Tasks scope</label
          ><button
            disabled={demo ||
              !googleStatus.client_present ||
              googleTask?.status === "running"}
            onclick={() =>
              googleWork("/calendar/google/authorize", {
                confirmed: true,
                include_tasks: includeTasks,
              })}>Review browser consent & authorize</button
          ><button
            disabled={demo || !googleStatus.authorized}
            onclick={() =>
              run(async () => {
                if (
                  window.confirm(
                    "Remove the separate Calendar token? Local data and Gmail token stay unchanged.",
                  )
                ) {
                  await api("/calendar/google/disconnect", {
                    confirmed: true,
                    revoke: false,
                  });
                  await loadGoogle();
                }
              })}>Disconnect locally</button
          ><button
            disabled={demo || !googleStatus.authorized}
            onclick={() =>
              run(async () => {
                if (
                  window.confirm(
                    "Revoke this separate Calendar authorization at Google and remove its token?",
                  )
                ) {
                  await api("/calendar/google/disconnect", {
                    confirmed: true,
                    revoke: true,
                  });
                  await loadGoogle();
                }
              })}>Revoke access</button
          >
        </div>
        <details>
          <summary>Capability inventory & account blockers</summary>
          <p>
            Available API resources: {googleStatus.capabilities.google.join(
              ", ",
            )}.
          </p>
          <p>
            Account/permission dependent: {googleStatus.capabilities.account_dependent.join(
              ", ",
            )}. Google denial is reported, never simulated.
          </p>
          {#each Object.entries(googleStatus.capabilities.unsupported) as [name, reason]}<p
            >
              <strong>{name}:</strong>
              {reason}
            </p>{/each}
          {#each googleStatus.limits as limit}<p>{limit}</p>{/each}
        </details>{/if}
      <h3>Select calendars & synchronize</h3>
      <button
        disabled={demo ||
          !googleStatus?.authorized ||
          googleTask?.status === "running"}
        onclick={() =>
          googleWork("/calendar/google/operation", {
            resource: "calendarList",
            action: "list",
          })}>List Google calendars</button
      >{#each remoteCalendars as c}<div class="free-row">
          <span>{String(c.summary)} · {String(c.accessRole || "")}</span><button
            disabled={demo ||
              !googleStatus?.authorized ||
              googleTask?.status === "running" ||
              googleStatus?.selected.some(
                (selected) => selected.google_id === c.id,
              )}
            onclick={() =>
              googleWork("/calendar/google/select", {
                google_id: c.id,
                confirmed: true,
              })}>Link locally</button
          >
        </div>{/each}
      <div class="controls">
        {#each googleStatus?.selected || [] as c}<label
            ><input
              type="checkbox"
              checked={linked.includes(c.id)}
              onchange={() =>
                (linked = linked.includes(c.id)
                  ? linked.filter((id) => id !== c.id)
                  : [...linked, c.id])}
            />{c.name}</label
          ><button
            disabled={demo || googleTask?.status === "running"}
            onclick={() =>
              run(async () => {
                if (
                  !window.confirm(
                    `Unlink ${c.name}? Local events remain; no Google deletion is made.`,
                  )
                )
                  return;
                await api("/calendar/google/unselect", {
                  calendar_id: c.id,
                  revision: c.revision,
                  confirmed: true,
                });
                await loadGoogle();
                await load();
              })}>Unlink {c.name}</button
          >{/each}
      </div>
      <div class="controls">
        <label title="Pull reviews local conflicts; Pull + push also sends local changes"
          >Direction<Select label="Direction" bind:value={syncDirection}
            options={[{ value: "pull", label: "Pull from Google" }, { value: "both", label: "Pull + push" }]} /></label
        ><label title="Google may still notify on its own"
          >Attendee email updates<Select label="Attendee email updates" bind:value={sendUpdates}
            options={[{ value: "none", label: "None" }, { value: "all", label: "All attendees" }, { value: "externalOnly", label: "External only" }]} /></label
        >
      </div>
      <button
        disabled={demo ||
          !googleStatus?.authorized ||
          !linked.length ||
          googleTask?.status === "running"}
        onclick={() =>
          run(async () => {
            syncPreview = await api<SyncPreview>("/calendar/google/preview", {
              calendar_ids: linked,
              send_updates: sendUpdates,
            });
            syncApproved = false;
          })}>Prepare all pending writes & recipients for review</button
      >
      {#if syncPreview}<details open>
          <summary
            >Frozen sync preview · all dates, including deleted events</summary
          >
          <pre class="results">{JSON.stringify(
              { calendars: syncPreview.calendars, events: syncPreview.events },
              null,
              2,
            )}</pre>
        </details>{/if}
      <label
        ><input
          type="checkbox"
          bind:checked={syncApproved}
          disabled={syncDirection === "both" && !syncPreview}
        />{syncDirection === "both"
          ? "I reviewed every pending write/recipient above and approve this notification policy."
          : "I approve fetching Google data. Local conflicts require review; no external changes are made."}</label
      >
      <div class="controls">
        <button
          disabled={demo ||
            !googleStatus?.authorized ||
            (syncDirection === "both" && !syncPreview) ||
            !linked.length ||
            !syncApproved ||
            googleTask?.status === "running"}
          onclick={() =>
            googleWork("/calendar/google/sync", syncRequest())}
          >Run reviewed sync</button
        ><button
          disabled={demo ||
            !googleStatus?.authorized ||
            (syncDirection === "both" && !syncPreview) ||
            !linked.length ||
            !syncApproved ||
            googleTask?.status === "running"}
          onclick={() =>
            googleWork("/calendar/google/sync", syncRequest(true))}
          >Full refresh</button
        >
      </div>
      {#each googleStatus?.calendar_conflicts || [] as conflict}<article
          class="conflict"
        >
          <h3>Calendar settings conflict</h3>
          <ul class="setting-diff" aria-label="Settings that differ">
            {#each [["Name", "name"], ["Timezone", "timezone"], ["Colour", "color"]] as const as [label, key]}
              {#if conflict.local[key] !== conflict.remote.values[key]}<li>
                  <span>{label}</span><span>kitty: {conflict.local[key]}</span><span>Google: {conflict.remote.values[key]}</span>
                </li>{/if}
            {/each}
          </ul>
          {#each [["local", "Keep local settings (push later)"], ["remote", "Use Google settings"]] as [choice, title]}
            <button
              disabled={demo || googleTask?.status === "running"}
              onclick={() => {
                if (window.confirm(`${title}?`))
                  void googleWork("/calendar/google/resolve-calendar", {
                    calendar_id: conflict.calendar_id,
                    revision: conflict.local.revision,
                    choice,
                    confirmed: true,
                  });
              }}>{title}</button
            >{/each}
        </article>{/each}
      {#if googleConflicts.length}<h3>
          Conflicts · never silently overwritten
        </h3>
        {#each googleConflicts as c (c.id)}{@const fill = paint(c.local)}<GoogleConflict
            conflict={c}
            {fill}
            ink={blockText(theme, fill)}
            disabled={demo || googleTask?.status === "running"}
            onresolve={(choice) =>
              void googleWork("/calendar/google/resolve", {
                id: c.id,
                revision: c.local.revision,
                choice,
                confirmed: true,
              })}
          />{/each}{/if}
      <details class="google-tools">
        <summary title="List or get first, copy the returned ID and ETag, edit exact JSON, then review"
          >Google resource editor</summary
        >
        <div class="controls">
          <label
            >Resource<Select kind="list" label="Resource" bind:value={resource} onchange={template}
              options={Object.keys(resourceActions).map((r) => ({ value: r, label: r }))} /></label
          ><label
            >Action<Select kind="list" label="Action" bind:value={action} onchange={template}
              options={(resourceActions[resource] ?? []).map((a) => ({ value: a, label: actionLabels[a] || a }))} /></label
          ><button onclick={template}>Fill example for resource</button>
        </div>
        <div class="form-grid">
          <label
            >Source calendar ID / source Tasklist ID<input
              bind:value={remoteCalendarId}
            /></label
          ><label
            >Exact resource / event / task / ACL ID<input
              bind:value={remoteId}
            /></label
          ><label>ETag from latest read<input bind:value={remoteEtag} /></label
          ><label
            ><input
              type="checkbox"
              bind:checked={meet}
              disabled={resource !== "events" ||
                !["create", "update", "replace", "import"].includes(action)}
            />Generate Google Meet (event create/update/replace/import)</label
          ><label
            ><input
              type="checkbox"
              bind:checked={notifySharing}
              disabled={resource !== "acl" || ["list", "get"].includes(action)}
            />Send sharing notifications (ACL changes)</label
          >
        </div>
        {#if googleResult && typeof googleResult === "object" && !Array.isArray(googleResult) && "etag" in googleResult && pendingGoogle.resource === resource}
          <button onclick={useGoogleResult}
            >Copy returned ID & ETag into this editor</button
          >{/if}
        <p class="hint">Changes are previewed against live Google and sent only after approval.</p>
        {#if action === "watch"}<p class="hint">Needs your own public HTTPS webhook; channels expire unrenewed.</p>
          <label
            ><input type="checkbox" bind:checked={webhookConfirmed} />
            I control and configured the public HTTPS receiver in data.address and
            reviewed what Google may deliver.</label
          >{/if}
        {#if resource === "events" && action === "quickAdd"}<p class="hint">Google has no dry run; sent without attendees or notifications.</p>{/if}
        <label title="Move: params.destination (Calendar) or destinationTasklist/parent/previous (Tasks). Import needs iCalUID, start and end. Clear is not undoable locally."
          >Exact resource data (JSON)<textarea
            rows="10"
            bind:value={remoteData}
            spellcheck="false"
          ></textarea></label
        ><label
          >Query parameters (JSON)<textarea
            rows="3"
            bind:value={remoteParams}
            spellcheck="false"
          ></textarea></label
        ><button
          disabled={demo ||
            !googleStatus?.authorized ||
            googleTask?.status === "running"}
          onclick={reviewOperation}
          >{["list", "get", "instances", "freebusy"].includes(action)
            ? "Run read-only request"
            : "Prepare exact change for review"}</button
        >{#if !googleStatus?.authorized}<p class="hint">
            {demo
              ? "External Google calls are disabled in demo mode."
              : "Not connected: authorize Calendar above first."}
          </p>{/if}{#if operationReview && operationRequest}<div
            class="review"
            role="group"
            aria-labelledby={`${pane.id}-operation-review-title`}
          >
            <h4 id={`${pane.id}-operation-review-title`}>
              Frozen request prepared from live Google state
            </h4>
            <dl>
              <dt>Request</dt>
              <dd>
                <code>{operationReview.method} {operationReview.url}</code>
              </dd>
              <dt>Query parameters</dt>
              <dd><code>{JSON.stringify(operationReview.params)}</code></dd>
              <dt>If-Match ETag</dt>
              <dd>
                {operationReview.if_match ||
                  "none (not an ETag-checked change)"}
              </dd>
              <dt>Target</dt>
              <dd>
                {operationReview.current
                  ? JSON.stringify(operationReview.current)
                  : "new resource"}
              </dd>
              <dt>
                Recipients Google may notify ({operationReview.recipients
                  .length})
              </dt>
              <dd>
                {#if operationReview.recipients.length}<ul>
                    {#each operationReview.recipients as email}<li>
                        {email}
                      </li>{/each}
                  </ul>{:else}none listed{/if}
              </dd>
            </dl>
            <p>Exact request body:</p>
            <pre>{operationReview.body === null
                ? "(empty body)"
                : JSON.stringify(operationReview.body, null, 2)}</pre>
            {#each operationReview.notes as note}<p class="hint">
                {note}
              </p>{/each}
            {#if operationReview.action === "clear"}<p>
                {operationReview.items.length}
                {operationReview.resource === "calendars"
                  ? "events"
                  : "completed tasks"} affected:
              </p>
              {#if operationReview.items.length}<ol class="results">
                  {#each operationReview.items as item}<li>
                      {String(item.summary ?? item.title ?? "(untitled)")} ·
                      <code>{String(item.id)}</code>
                    </li>{/each}
                </ol>
                <label
                  ><input type="checkbox" bind:checked={clearConfirmed} />I
                  reviewed the full list above and confirm clearing all {operationReview
                    .items.length} items.</label
                >{/if}{/if}
            <label
              ><input type="checkbox" bind:checked={operationApproved} />I
              approve this exact external request, recipients, sharing
              role/scope and possible Google emails. Paid services/attachment
              permissions remain provider-controlled.</label
            ><button
              disabled={!operationApproved ||
                googleTask?.status === "running" ||
                (operationReview.items.length > 0 && !clearConfirmed)}
              onclick={() => void sendReviewedOperation()}
              >Send reviewed request</button
            >
          </div>{/if}
      </details>
      {#if googleTask}<p role="status">
          Google operation: {googleTask.kind} · {googleTask.status}{googleTask.status ===
          "running"
            ? " · waiting for Google / browser consent"
            : ""}
        </p>{/if}{#if googleResult}<details open>
          <summary>Google / import operation result</summary>
          <pre class="results">{JSON.stringify(googleResult, null, 2)}</pre>
        </details>{/if}
      <a
        href="https://calendar.google.com/calendar/u/0/r"
        target="_blank"
        rel="noreferrer"
      >
        Open Google Calendar for consumer-only features such as appointment
        schedules</a
      >
    </section>
{/if}

<style>
  h2,
  h3,
  h4,
  p {
    margin: 0 0 0.65rem;
  }
  h2 {
    font-size: 1.1rem;
  }
  h3 {
    margin-top: 1.2rem;
  }
  button,
  input,
  textarea {
    font: inherit;
    color: var(--ink);
  }
  button {
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0.45rem;
    padding: 0.45rem 0.65rem;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  button:hover:not(:disabled) {
    border-color: var(--accent);
  }
  button:focus-visible,
  input:focus-visible,
  textarea:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  input,
  textarea {
    border: 1px solid var(--border);
    background: var(--surface);
    border-radius: 0.4rem;
    padding: 0.5rem;
    max-width: 100%;
    box-sizing: border-box;
  }
  label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .google-tools > label {
    display: flex;
    align-items: stretch;
    flex-direction: column;
    margin: 0.75rem 0;
  }
  textarea {
    resize: vertical;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    flex-wrap: wrap;
  }
  .hint {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .notice,
  .panel {
    padding: 1rem;
    border-radius: 0.65rem;
    border: 1px solid var(--border);
    background: var(--surface);
  }
  .free-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.7rem;
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--border);
  }
  .conflict {
    padding: 0.8rem;
    border: 1px solid var(--warning);
    border-radius: 0.5rem;
    margin: 0.6rem 0;
  }
  .file {
    border: 1px solid var(--border);
    padding: 0.5rem;
    border-radius: 0.4rem;
    cursor: pointer;
    flex-wrap: wrap;
  }
  .file input {
    max-width: 15rem;
    font-size: 0.8rem;
  }
  .import-review {
    max-height: 24rem;
    overflow: auto;
  }
  .import-review label {
    padding: 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  small {
    display: block;
    color: var(--muted);
    margin-top: 0.3rem;
  }
  .panel ol {
    font-size: 0.9rem;
    padding-left: 1.3rem;
  }
  .panel li {
    margin: 0.45rem 0;
  }
  details {
    margin: 0.8rem 0;
  }
  summary {
    cursor: pointer;
    font-weight: 600;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: var(--canvas);
    padding: 0.8rem;
    border-radius: 0.4rem;
    font-size: 13px;
    max-height: 28rem;
    overflow: auto;
  }
  .setting-diff {
    display: grid;
    gap: 0.3rem;
    margin: 0.5rem 0;
    padding: 0;
    list-style: none;
  }
  .setting-diff li {
    display: grid;
    grid-template-columns: 6rem 1fr 1fr;
    gap: 0.6rem;
    font-size: 0.85rem;
  }
  .review {
    border: 1px solid var(--accent);
    padding: 1rem;
    margin-top: 1rem;
    border-radius: 0.5rem;
  }
  .review button {
    margin-top: 0.8rem;
  }
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.7rem;
  }
  .form-grid label {
    flex-direction: column;
    align-items: stretch;
  }
  .form-grid label:has(input[type="checkbox"]) {
    flex-direction: row;
    align-items: center;
  }
  a {
    color: var(--accent);
  }
  @media (max-width: 760px) {
    .form-grid {
      grid-template-columns: 1fr;
    }
    .controls label {
      flex-wrap: wrap;
    }
  }
</style>
