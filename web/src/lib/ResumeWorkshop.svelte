<script lang="ts">
  import { onMount } from 'svelte';
  import Loader from './Loader.svelte';
  import Select from './ui/Select.svelte';
  import { api, ApiError, apiObjectUrl, downloadApi, type TaskRecord } from './api';
  import { workshop, taskResult, fileBase64, encodeText, decodeText, type WorkshopDoc, type WorkshopIndex, type Revision, type Compile, type Settings, type Fact, type Proposal, type Preview, type Research, type BatchIndex, type AtsResult } from './workshopApi';
  let { onview }: { onview: (view: 'resumes') => void } = $props();
  let index = $state<WorkshopIndex | null>(null), doc = $state<WorkshopDoc | null>(null);
  let source = $state(''), title = $state(''), assets = $state<Record<string,string>>({}), revisions = $state<Revision[]>([]);
  let error = $state(''), notice = $state(''), busy = $state(false), saving = $state(false), conflict = $state(false), loading = $state(true);
  let newTitle = $state('My resume'), template = $state(''), selectedAsset = $state(''), assetText = $state('');
  let images = $state<string[]>([]), compiled = $state<Compile | null>(null), compiling = $state(false), compileError = $state('');
  let settings = $state<Settings>({enabled:false,model_id:'',max_cost_usd:'0.10'}), facts = $state<Fact[]>([]), factText = $state(''), factSource = $state(''), factConfirmed = $state(false), editingFact = $state<Fact|null>(null);
  let company = $state(''), role = $state(''), jobId = $state(''), instruction = $state(''), researchEnabled = $state(false), urls = $state('');
  let research = $state<Research|null>(null), preview = $state<Preview|null>(null), proposals = $state<Proposal[]>([]), accepted = $state<string[]>([]);
  let batches = $state<BatchIndex>({batches:[],jobs:[],insights:{resumes:[],notice:''}}), selectedJobs = $state<number[]>([]), batchCap = $state('1.00');
  const masters = $derived((index?.documents || []).filter(d => !d.archived && d.kind === 'master'));
  const variants = $derived((index?.documents || []).filter(d => !d.archived && d.kind === 'tailored'));
  const pendingProposals = $derived(proposals.filter(p => p.status === 'pending').length);
  const engineReady = $derived((index?.capabilities.engines || []).filter(e => e.available).length);
  let preferences = $state<unknown[]>([]);
  let atsResult = $state<AtsResult|null>(null);
  let activeBatches = $state<string[]>([]);
  type Tab = 'tailor' | 'facts' | 'batches' | 'outcomes' | 'files';
  let tab = $state<Tab>('tailor'), dragging = $state(false), gutter = $state<HTMLElement>();
  /** Tailoring and files belong to an open resume; without one the page opens on the facts. */
  const shown = $derived<Tab>(!doc && (tab === 'tailor' || tab === 'files') ? 'facts' : tab);
  const lineCount = $derived(source.split('\n').length);
  const gutterText = $derived(Array.from({length: lineCount}, (_, i) => i + 1).join('\n'));
  const confirmedFacts = $derived(facts.filter(f => f.confirmed).length);
  const engines = $derived(index?.capabilities.engines || []);
  const atsTotal = $derived(atsResult?.report ? atsResult.report.findings.length : 0);
  const atsScore = $derived(atsTotal ? (atsResult!.report!.counts.pass / atsTotal) : 0);
  const FORMAT: Record<WorkshopDoc['format'], string> = {latex: 'TeX', typst: 'Typ', html: 'HTML'};
  const tabs = $derived<{id: Tab; label: string; count?: number | string; needsDoc?: boolean}[]>([
    {id: 'tailor', label: 'Tailor with Rika', count: pendingProposals || undefined, needsDoc: true},
    {id: 'facts', label: 'Facts & privacy', count: confirmedFacts || undefined},
    {id: 'batches', label: 'Many at once', count: batches.batches.length || undefined},
    {id: 'outcomes', label: 'Outcomes', count: batches.insights.resumes.length || undefined},
    {id: 'files', label: 'Files & history', count: doc ? revisions.length : undefined, needsDoc: true},
  ]);
  /** Arrow keys move along a row of tabs or tiles, as a native radio group does. */
  function roving(event: KeyboardEvent, selector: string) {
    const step = ({ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1} as Record<string, number>)[event.key];
    if (!step) return;
    const items = [...(event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>(selector)].filter(item => !item.hasAttribute('disabled'));
    const at = items.indexOf(document.activeElement as HTMLElement);
    event.preventDefault();
    const next = items[(at + step + items.length) % items.length];
    next?.focus(); next?.click();
  }
  const lifetime = new AbortController(); let selection = 0, compileToken = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const dirty = $derived(!!doc && (source !== doc.source || title !== doc.title || JSON.stringify(assets) !== JSON.stringify(doc.assets)));
  export function hasUnsavedChanges() { return dirty || saving; }
  const stale = $derived(!!compiled && (dirty || compiled.revision !== doc?.revision));
  function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause); }
  async function act(work: () => Promise<void>) { busy = true; error = ''; notice = ''; try { await work(); } catch (cause) { if (!lifetime.signal.aborted) error = message(cause); } finally { busy = false; } }
  async function refresh() { index = await workshop.index(); if (!template) template = index.templates[0]?.id || ''; }
  async function loadAI() { const data = await api<{settings:Settings;facts:Fact[];preferences:unknown[]}>('/workshop-ai'); settings = data.settings; facts = data.facts; preferences = data.preferences || []; }
  async function loadProposals(id: string) { const result = await api<{proposals:Proposal[]}>(`/workshop-ai/proposals?doc_id=${encodeURIComponent(id)}`); if (doc?.id === id) { proposals = result.proposals; if (proposals.some(p => p.status === 'pending')) tab = 'tailor'; } }
  function adopt(next: WorkshopDoc) { doc = next; source = next.source; title = next.title; assets = {...next.assets}; conflict = false; selectedAsset = ''; preview = null; accepted = []; }
  async function open(id: string) {
    if (saving || (dirty && !await save()) || dirty) return;
    const token = ++selection; const result = await workshop.get(id); if (token !== selection || lifetime.signal.aborted) return;
    ++compileToken; images.forEach(URL.revokeObjectURL); images = []; compiled = null; atsResult = null; compileError = ''; adopt(result.document); revisions = result.revisions;
    company = doc!.company; role = doc!.role; jobId = doc!.job_id == null ? '' : String(doc!.job_id); research = null;
    await Promise.all([compile(), loadProposals(id)]);
  }
  function changed() { preview = null; clearTimeout(timer); if (!conflict) timer = setTimeout(() => { void save(); }, 800); }
  async function save(): Promise<boolean> {
    clearTimeout(timer); if (!doc || !dirty) return true; if (saving || conflict) return false;
    const id = doc.id, sentSource = source, sentTitle = title, sentAssets = {...assets}; saving = true; error = '';
    try {
      const {document} = await workshop.save(id, {source:sentSource,title:sentTitle,assets:sentAssets,base_revision:doc.revision});
      if (doc?.id !== id) return false;
      doc = document; preview = null;
      revisions = (await workshop.get(id)).revisions;
      await refresh(); void compile(); return true;
    } catch (cause) {
      conflict = cause instanceof ApiError && cause.status === 409;
      error = conflict ? 'Conflict: your draft is preserved. Download your draft, then explicitly reload the server version to reconcile.' : `Save failed; your draft is preserved. ${message(cause)}`;
      return false;
    } finally { saving = false; if (dirty && !conflict && !error) changed(); }
  }
  async function compile() {
    if (!doc) return; const target = doc, token = ++compileToken; compiling = true; compileError = '';
    try {
      const result = await taskResult<Compile>(await api(`/workshop/${target.id}/compile`, {revision:target.revision}), lifetime.signal);
      if (token !== compileToken || doc?.id !== target.id || doc.revision !== target.revision) return;
      if (!result.ok) { compileError = `Revision ${target.revision} did not compile; the last good preview is kept.\n` + (result.diagnostics.join('\n') || 'No compiler diagnostics were produced.'); return; }
      const next: string[] = [];
      try { for (let page = 1; page <= result.pages; page++) next.push(await apiObjectUrl(`/workshop/${target.id}/page/${page}.png?revision=${target.revision}`)); }
      catch (cause) { next.forEach(URL.revokeObjectURL); throw cause; }
      if (token !== compileToken || doc?.revision !== target.revision || lifetime.signal.aborted) { next.forEach(URL.revokeObjectURL); return; }
      images.forEach(URL.revokeObjectURL); images = next; compiled = result;
      // The ATS check runs on every successful compile; it never blocks the preview.
      const report = await workshop.ats(target.id, target.revision).catch(() => null);
      if (token === compileToken && doc?.revision === target.revision) atsResult = report;
    } catch (cause) { if (token === compileToken && !lifetime.signal.aborted) compileError = `Preview unavailable: ${message(cause)}`; }
    finally { if (token === compileToken) compiling = false; }
  }
  async function create() { if ((dirty && !await save()) || dirty) return; const choice = index?.templates.find(t => t.id === template); if (!choice) throw new Error('Choose an available template.'); const result = await api<{document:WorkshopDoc}>('/workshop', {title:newTitle,format:choice.format,template_id:choice.id}); await refresh(); await open(result.document.id); }
  async function importFiles(event: Event, assetOnly = false) {
    const input = event.currentTarget as HTMLInputElement; const files = Array.from(input.files || []); if (!files.length) return;
    await importList(files, assetOnly); input.value = '';
  }
  async function importList(files: File[], assetOnly = false) {
    await act(async () => {
      if (files.reduce((total, file) => total + file.size, 0) > 16 * 1024 * 1024) throw new Error('Upload limit is 16 MiB.');
      const payload = await Promise.all(files.map(async file => ({name:file.name,data:await fileBase64(file)})));
      if (assetOnly) { for (const file of payload) assets = {...assets,[file.name]:file.data}; changed(); return; }
      if ((dirty && !await save()) || dirty) return;
      const result = await taskResult<{documents:WorkshopDoc[];warnings:string[]}>(await api('/workshop/import', {files:payload}), lifetime.signal);
      await refresh(); if (result.documents[0]) await open(result.documents[0].id); notice = (result.warnings || []).join('\n') || 'Import completed. Review reconstructed content and layout.';
    });
  }
  function drop(event: DragEvent) { event.preventDefault(); dragging = false; const files = Array.from(event.dataTransfer?.files || []); if (files.length && !busy) void importList(files); }
  function chooseAsset(name: string) { selectedAsset = name; try { assetText = decodeText(assets[name]); } catch { assetText = ''; error = 'Binary asset: download or replace this file rather than editing it as text.'; selectedAsset = ''; } }
  function downloadDraft() { const url = URL.createObjectURL(new Blob([JSON.stringify({title,source,assets},null,2)],{type:'application/json'})); const link = document.createElement('a'); link.href = url; link.download = 'workshop-unsaved-draft.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  async function restore(revision: number) { if (!doc || dirty || saving) throw new Error('Save or reconcile your draft before restoring.'); const result = await api<{document:WorkshopDoc}>(`/workshop/${doc.id}/restore`, {revision,base_revision:doc.revision}); adopt(result.document); revisions = (await workshop.get(doc.id)).revisions; await compile(); }
  async function exportPDF() { if (!await save() || !doc || dirty) return; const result = await api<{resume_id:number}>(`/workshop/${doc.id}/export`, {revision:doc.revision,label:title,job_id:jobId ? Number(jobId) : undefined,confirm:true}); notice = `Registered resume #${result.resume_id}. No application was created.`; await downloadApi(`/workshop/${doc.id}/pdf?revision=${doc.revision}`, `${title}.pdf`); }
  async function saveSettings() { settings = (await api<{settings:Settings}>('/workshop-ai/settings', {enabled:settings.enabled,model_id:settings.model_id,max_cost_usd:String(settings.max_cost_usd),confirm:true})).settings; preview = null; }
  async function saveFact() { await api('/workshop-ai/facts', {text:factText,source:factSource,confirmed:factConfirmed,...(editingFact ? {id:editingFact.id,revision:editingFact.revision} : {})}); editingFact = null; factText = ''; factSource = ''; factConfirmed = false; preview = null; await loadAI(); }
  async function fetchResearch() { research = (await taskResult<{research:Research}>(await api('/workshop-ai/research', {company,role,job_id:jobId ? Number(jobId) : undefined,urls:urls.split('\n').map(s=>s.trim()).filter(Boolean),confirm:true}),lifetime.signal)).research; preview = null; }
  async function prepare() { if (!await save() || !doc || dirty) return; if (researchEnabled && !research) await fetchResearch(); preview = (await api<{preview:Preview}>('/workshop-ai/preview',{doc_id:doc.id,instruction,company,role,job_id:jobId ? Number(jobId) : undefined,research_id:researchEnabled ? research?.id : undefined})).preview; }
  async function runAI() { if (!preview || !doc || dirty) throw new Error('Prepare a fresh saved-source payload first.'); const request = preview; preview = null; await taskResult(await api('/workshop-ai/run',{preview_id:request.id,digest:request.digest,confirm:true}), lifetime.signal); await loadProposals(doc.id); }
  async function review(proposal: Proposal, reject = false) { if (!doc || dirty || saving) throw new Error('Save or reconcile your draft before reviewing.'); const result = await api<{document:WorkshopDoc}>(`/workshop-ai/proposals/${proposal.id}/review`,{base_revision:proposal.base_revision,accept_ids:reject ? [] : proposal.hunks.filter(h=>accepted.includes(h.id)).map(h=>h.id),confirm:true}); adopt(result.document); await loadProposals(doc.id); await compile(); }
  async function createBatch() { if (!doc || !await save() || dirty) return; await api('/workshop-batches',{master_id:doc.id,base_revision:doc.revision,job_ids:selectedJobs,research:researchEnabled,max_cost_usd:batchCap,confirm:true}); batches = await workshop.batches(); }
  async function runBatch(id: string) {
    const started = await api<{task: TaskRecord}>(`/workshop-batches/${id}/run`, {confirm:true});
    activeBatches = [...activeBatches, id]; batches = await workshop.batches();
    void taskResult(started, lifetime.signal).then(async () => {
      if (lifetime.signal.aborted) return;
      batches = await workshop.batches(); await refresh();
      notice = 'Batch finished. Inspect each result; successful drafts still require review.';
    }).catch(cause => { if (!lifetime.signal.aborted) error = message(cause); })
      .finally(() => { activeBatches = activeBatches.filter(value => value !== id); });
  }
  function selectJob() {
    const selected = batches.jobs.find(job => String(job.id) === jobId);
    if (selected) { company = selected.company; role = selected.title; }
    preview = null; research = null;
  }
  onMount(() => {
    void act(async () => { try { await refresh(); await Promise.all([loadAI(), workshop.batches().then(result => { batches = result; })]); } finally { loading = false; } });
    const warnUnsaved = (event: BeforeUnloadEvent) => { if (dirty || saving) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warnUnsaved);
    let refreshingBatches = false;
    const poll = setInterval(() => {
      if (refreshingBatches || (!activeBatches.length && !batches.batches.some(batch => batch.status === 'running'))) return;
      refreshingBatches = true;
      void workshop.batches().then(result => { if (!lifetime.signal.aborted) batches = result; })
        .catch(cause => { if (!lifetime.signal.aborted) error = message(cause); })
        .finally(() => { refreshingBatches = false; });
    }, 2000);
    return () => { lifetime.abort(); ++selection; ++compileToken; clearTimeout(timer); clearInterval(poll); window.removeEventListener('beforeunload', warnUnsaved); images.forEach(URL.revokeObjectURL); };
  });
</script>

<section class="workshop" aria-label="Resume Workshop">
  <header class="hero rise">
    <div class="hero-text">
      <p class="eyebrow"><span class="spark" aria-hidden="true"></span>Career studio</p>
      <h1>Resume Workshop</h1>
      <p class="lede">Edit resumes live, tailor them for a company or role, and keep every version. Nothing leaves this computer unless you turn on Rika's cloud help.</p>
    </div>
    <div class="hero-side">
      <div class="chips" aria-label="Workshop status">
        <span class="status-chip" data-state={engineReady ? 'ok' : 'off'}><i aria-hidden="true"></i>{engineReady} of {engines.length} compilers ready</span>
        <span class="status-chip" data-state={settings.enabled ? 'on' : 'off'}><i aria-hidden="true"></i>Cloud help {settings.enabled ? 'on' : 'off'}</span>
      </div>
      <button class="button" onclick={() => onview('resumes')}>Existing resumes library</button>
    </div>
  </header>
  {#if error}<p class="message error" role="alert">{error}</p>{/if}
  {#if notice}<p class="message" role="status">{notice}</p>{/if}
  {#if loading}<Loader label="Loading workshop…" />{/if}
  <div class="layout">
    <aside class="rail rise" style="--rise-i: 1" aria-label="Resume library">
      <section class="card">
        <h2>Your resumes <span class="count">{masters.length}</span></h2>
        <ul class="docs">
          {#each masters as item (item.id)}
            <li><button class="document" aria-pressed={doc?.id === item.id} disabled={busy || saving} onclick={() => act(() => open(item.id))}>
              <span class="badge" data-format={item.format}>{FORMAT[item.format]}</span>
              <span class="doc-name">{item.title}<small>{item.format} · revision {item.revision}</small></span>
            </button></li>
          {:else}<li class="hint none">No resumes yet. Start one below.</li>{/each}
        </ul>
        {#if variants.length}
          <h3>Company &amp; role versions</h3>
          <ul class="docs variants">
            {#each variants as item (item.id)}
              <li><button class="document" aria-pressed={doc?.id === item.id} disabled={busy || saving} onclick={() => act(() => open(item.id))}>
                <span class="badge" data-format={item.format}>{FORMAT[item.format]}</span>
                <span class="doc-name">{item.title}<small>{[item.company, item.role].filter(Boolean).join(' · ') || 'variant'} · revision {item.revision}</small></span>
              </button></li>
            {/each}
          </ul>
        {/if}
      </section>
      <section class="card start">
        <h2>Start a resume</h2>
        <label>New resume title<input bind:value={newTitle} /></label>
        <span class="field-label" id="workshop-template">Template</span>
        <div class="tiles" role="radiogroup" aria-labelledby="workshop-template" tabindex="-1" onkeydown={event => roving(event, '[role="radio"]')}>
          {#each index?.templates || [] as item (item.id)}
            <button class="tile" role="radio" aria-checked={template === item.id} tabindex={template === item.id ? 0 : -1} title={item.description} onclick={() => template = item.id}>
              <span class="badge" data-format={item.format}>{FORMAT[item.format]}</span>
              <strong>{item.label}</strong>
              <small>{item.description}</small>
            </button>
          {/each}
        </div>
        <button class="button primary wide" disabled={busy || !template} onclick={() => act(create)}>Create resume</button>
        <label class="drop" data-over={dragging} ondragover={event => { event.preventDefault(); dragging = true; }} ondragleave={() => dragging = false} ondrop={drop}>
          <span class="drop-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg></span>
          <span class="drop-text"><strong>Import source, PDF, DOCX or ZIP</strong><small>Drop files here or browse</small></span>
          <input class="sr-only" type="file" multiple accept=".html,.htm,.typ,.tex,.zip,.pdf,.docx,.md,.json,.txt,.css,.png,.jpg,.jpeg,.svg,.woff,.woff2,.ttf,.otf" disabled={busy} onchange={event => importFiles(event)} />
        </label>
        <p class="hint">LaTeX, Typst and HTML import as editable source; Overleaf ZIPs keep their files. PDF and DOCX become plain editable text and the original is kept beside it.</p>
      </section>
      <details class="card engines">
        <summary>Compilers <span class="count">{engineReady} ready</span></summary>
        <ul>
          {#each engines as engine (engine.id)}<li data-ready={engine.available}><i aria-hidden="true"></i><span><strong>{engine.label}</strong><small>{engine.available ? 'ready' : engine.reason || 'not installed'}</small></span></li>{/each}
        </ul>
      </details>
    </aside>
    <main class="rise" style="--rise-i: 2">
      {#if doc}
        <div class="doc-bar card">
          <div class="doc-heading">
            <span class="badge big" data-format={doc.format}>{FORMAT[doc.format]}</span>
            <label class="title-field"><span class="sr-only">Resume title</span><input bind:value={title} oninput={changed} disabled={busy} /></label>
            <span class="meta">{doc.kind === 'tailored' ? [doc.company, doc.role].filter(Boolean).join(' · ') || 'Tailored version' : 'Master resume'} · revision {doc.revision}</span>
          </div>
          <div class="doc-tools">
            <span class="save-state" role="status" data-state={saving ? 'saving' : dirty ? 'dirty' : 'saved'}><i aria-hidden="true"></i>{saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'All changes saved'}</span>
            <button class="button ghost" disabled={saving || conflict || !dirty} onclick={() => save()}>Save now</button>
            <button class="button primary" disabled={busy || saving || conflict} onclick={() => act(exportPDF)}>Export PDF and register</button>
          </div>
        </div>
        {#if conflict}<div class="message error doc-actions"><span>This resume changed in another window.</span><button class="button" onclick={downloadDraft}>Download preserved draft</button><button class="button" disabled={busy} onclick={() => act(async () => { if (!doc) return; const result = await workshop.get(doc.id); adopt(result.document); revisions = result.revisions; await compile(); })}>Discard draft and reload server version</button></div>{/if}
        <div class="studio">
          <div class="pane card editor">
            <div class="pane-head">
              <label for="resume-source">Resume source</label>
              <span class="file-chip">{doc.entrypoint}</span>
              <small>{doc.format} ({doc.engine}) · revision {doc.revision} · {lineCount} lines</small>
            </div>
            <div class="code">
              <pre class="gutter" aria-hidden="true" bind:this={gutter}>{gutterText}</pre>
              <textarea id="resume-source" class="source" spellcheck="false" bind:value={source} oninput={changed} onscroll={event => { if (gutter) gutter.scrollTop = event.currentTarget.scrollTop; }} disabled={busy}></textarea>
            </div>
            <div class="pane-foot">
              <button class="button ghost" disabled={busy || dirty} onclick={() => act(() => downloadApi(`/workshop/${doc!.id}/source?revision=${doc!.revision}`,`${title}.zip`))}>Download source archive</button>
              <button class="button ghost" onclick={downloadDraft}>Download draft backup</button>
            </div>
          </div>
          <section class="preview pane card" aria-label="Compiled preview" data-compiling={compiling}>
            <div class="pane-head">
              <h2>PDF preview</h2>
              {#if compiling}<span class="pill busy" role="status"><i aria-hidden="true"></i>Compiling saved revision…</span>
              {:else if stale}<span class="pill warn">Stale · revision {compiled?.revision}</span>
              {:else if compiled}<span class="pill ok">{compiled.pages} page{compiled.pages === 1 ? '' : 's'} · revision {compiled.revision}</span>{/if}
              <button class="button ghost push" disabled={busy || saving || dirty || compiling} onclick={() => act(compile)}>Compile saved revision</button>
            </div>
            <div class="progress" aria-hidden="true"></div>
            <div class="stage">
              {#if compileError}<pre class="compile-error" role="alert">{compileError}</pre>{:else if stale}<p class="stale">Stale preview — last successful revision {compiled?.revision}; current edits are not shown.</p>{/if}
              {#each images as image, i (image)}<img class="page" style="--page-i: {i}" src={image} alt={`Resume PDF page ${i+1}`} />{:else}
                {#if !compileError}<div class="paper-ghost" aria-hidden="true"><span></span><span></span><span></span><span></span></div>{/if}
                <p class="hint center">No successful preview yet. Source editing remains available offline.</p>
              {/each}
            </div>
          </section>
        </div>

        {#if atsResult?.report || compiled}
          <div class="insights">
            {#if atsResult?.report}
              {@const counts = atsResult.report.counts}
              {#key atsResult.revision}
                <section class="ats card" aria-label="ATS check">
                  <div class="ats-head">
                    <svg class="ring" viewBox="0 0 64 64" aria-hidden="true" style="--score: {atsScore}">
                      <circle class="track" cx="32" cy="32" r="26" />
                      <circle class="value" cx="32" cy="32" r="26" pathLength="100" />
                      <text x="32" y="36" text-anchor="middle">{counts.pass}/{atsTotal}</text>
                    </svg>
                    <h3>ATS check <small>revision {atsResult.revision} · runs on every compile · {counts.pass} passed · {counts.warn} to review{counts.fail ? ` · ${counts.fail} failed` : ''}</small></h3>
                  </div>
                  <ul class="findings">
                    {#each atsResult.report.findings as finding, i}
                      <li class={finding.level} style="--row-i: {i}"><span class="mark" aria-label={finding.level}>{finding.level === 'pass' ? '✓' : finding.level === 'warn' ? '!' : '×'}</span><div><strong>{finding.title}</strong><small>{finding.detail}</small></div></li>
                    {/each}
                  </ul>
                  <dl class="parsed">
                    <dt>Name</dt><dd>{atsResult.report.contact.name || '—'}</dd>
                    <dt>Email</dt><dd>{atsResult.report.contact.email || '—'}</dd>
                    <dt>Phone</dt><dd>{atsResult.report.contact.phone || '—'}</dd>
                    <dt>Links</dt><dd>{atsResult.report.contact.links.join(' · ') || '—'}</dd>
                    <dt>Sections</dt><dd>{atsResult.report.sections.map(s => `${s.title} (${s.lines} lines)`).join(' · ') || '—'}</dd>
                  </dl>
                  {#if atsResult.job_terms}
                    {@const terms = atsResult.job_terms}
                    <div class="terms">
                      <strong>Against the linked job: {terms.company} — {terms.title}</strong>
                      {#if terms.error}<p class="hint">{terms.error}</p>{:else}
                        <p><span class="label">In your resume</span>{#each terms.found as term}<span class="chip found">{term}</span>{:else}<span class="hint">none of the posting's terms</span>{/each}</p>
                        <p><span class="label">Asked for, not found</span>{#each terms.missing as term}<span class="chip missing">{term}</span>{:else}<span class="hint">nothing missing</span>{/each}</p>
                        <p class="hint">Add a missing term only where it is true for you.</p>
                      {/if}
                    </div>
                  {/if}
                  <p class="hint">{atsResult.report.note}</p>
                </section>
              {/key}
            {/if}
            {#if compiled}
              <details class="card readable">
                <summary>Can software read this PDF? <span class="count">revision {compiled.revision}</span></summary>
                <ul class="checks">
                  <li>Selectable text: {compiled.checks.extractable_text ? 'yes' : 'no — image-only pages cannot be read by applicant systems'}</li>
                  <li>Text order vs. source: {compiled.checks.reading_order === 'source_order_matches' ? 'matches' : compiled.checks.reading_order === 'warning' ? 'differs — check columns and floats' : 'not checked for this engine'}</li>
                  <li>Pages: {compiled.pages}</li>
                  {#each (compiled.checks.warnings as string[] | undefined) || [] as warning}<li>{warning}</li>{/each}
                </ul>
                <p class="hint">Shows how a plain text extractor reads the PDF. It does not predict any employer's system.</p>
                <pre>{compiled.checks.extracted_text}</pre>
              </details>
            {/if}
          </div>
        {/if}
      {:else}
        <div class="empty card">
          <div class="sheets" aria-hidden="true"><span></span><span></span><span></span></div>
          <h2>Pick a resume or start one</h2>
          <p>Choose a template on the left or import an existing LaTeX, Typst, HTML, PDF or DOCX resume. Manual editing works offline, with no account or AI key.</p>
          <ol class="steps">
            <li><strong>Pick a template</strong><small>Jake's Resume, clean HTML, Typst or LaTeX</small></li>
            <li><strong>Edit live</strong><small>Every save recompiles and rechecks the PDF</small></li>
            <li><strong>Tailor with Rika</strong><small>Exact edits from facts you confirmed</small></li>
          </ol>
        </div>
      {/if}

      <div class="tabs" role="tablist" aria-label="Workshop tools" tabindex="-1" onkeydown={event => roving(event, '[role="tab"]')}>
        {#each tabs as item (item.id)}
          {#if !item.needsDoc || doc}
            <button role="tab" id={`workshop-tab-${item.id}`} aria-controls="workshop-panel" aria-selected={shown === item.id} tabindex={shown === item.id ? 0 : -1} onclick={() => tab = item.id}>
              {item.label}{#if item.count !== undefined}<span class="count">{item.count}</span>{/if}
            </button>
          {/if}
        {/each}
      </div>
      {#key shown}
        <div class="tab-panel card" id="workshop-panel" role="tabpanel" aria-labelledby={`workshop-tab-${shown}`}>
          {#if shown === 'tailor' && doc}
            <p class="hint">Rika proposes exact edits backed by your confirmed facts. You approve each change; nothing is applied, exported or sent to an employer automatically.</p>
            <div class="fields">
              <label>Company<input bind:value={company} oninput={() => { research = null; preview = null; }} /></label>
              <label>Role<input bind:value={role} oninput={() => { research = null; preview = null; }} /></label>
              <div class="field"><span class="field-label">Linked job</span><Select label="Linked job" kind="search" bind:value={jobId} onchange={selectJob} options={[{value: '', label: 'No linked job'}, ...batches.jobs.map(job => ({value: String(job.id), label: `${job.company} — ${job.title}`}))]} /></div>
            </div>
            <button class="button" disabled={busy || saving} onclick={() => act(async () => { if (!await save() || dirty) return; const result = await api<{document:WorkshopDoc}>(`/workshop/${doc!.id}/copy`,{title:`${title} — ${company || role || 'variant'}`.slice(0,200),company,role,job_id:jobId ? Number(jobId) : undefined}); await refresh(); await open(result.document.id); })}>Create linked variant</button>
            {#if !settings.enabled}<p class="message">Cloud help is off. Open <button class="link" onclick={() => tab = 'facts'}>Facts &amp; privacy</button> to opt in; manual editing never needs it.</p>{/if}
            <label class="check"><input type="checkbox" bind:checked={researchEnabled} onchange={() => preview = null} /> Include public company and role research</label>
            {#if researchEnabled}
              <label>Public company / posting URLs (one per line)<textarea bind:value={urls}></textarea></label>
              <button class="button" disabled={busy || !settings.enabled || !company} onclick={() => act(fetchResearch)}>Fetch cited public research</button>
              {#if research}<div class="panel"><h3>{research.company}: {research.status}</h3>{#each research.findings as finding}<p>{finding.text} <a href={finding.url} target="_blank" rel="noreferrer">Source</a></p>{/each}{#each research.sources as citation}<p class="hint"><a href={citation.url} target="_blank" rel="noreferrer">{citation.title || citation.url}</a> — {citation.status}</p>{/each}{#each research.warnings as warning}<p class="hint">{warning}</p>{/each}</div>{/if}
            {/if}
            <label>Edit instruction<textarea bind:value={instruction} oninput={() => preview = null} placeholder="e.g. Emphasize my verification work for this role, using only confirmed facts"></textarea></label>
            <button class="button primary" disabled={busy || !settings.enabled || !instruction.trim() || conflict} onclick={() => act(prepare)}>Preview redacted request and maximum cost</button>
            {#if preview}<div class="panel pop"><h3>Review exact outgoing payload</h3><p class="hint">Model: {preview.model_id} · Maximum cost: ${preview.maximum_cost_usd} · Expires: {preview.expires_at}</p><pre>{typeof preview.redacted_payload === 'string' ? preview.redacted_payload : JSON.stringify(preview.redacted_payload,null,2)}</pre><button class="button primary" disabled={busy || dirty} onclick={() => act(runAI)}>Confirm and send this payload</button></div>{/if}
            {#each proposals as proposal (proposal.id)}<article class="panel pop"><h3>{proposal.summary || 'Proposed edits'} <span class="pill" data-status={proposal.status}>{proposal.status}</span></h3><p class="hint">Based on revision {proposal.base_revision}</p>{#each proposal.warnings || [] as warning}<p class="message">{warning}</p>{/each}{#each proposal.hunks as hunk}<label class="check"><input type="checkbox" value={hunk.id} bind:group={accepted} disabled={proposal.status !== 'pending'} /> Approve this exact edit</label><div class="diff"><div class="before"><strong>Before</strong><pre>{hunk.find}</pre></div><div class="after"><strong>After</strong><pre>{hunk.replace}</pre></div></div><p>{hunk.reason}</p><p class="hint">Fact IDs: {hunk.fact_ids.join(', ')}{(hunk.flags || []).length ? ` · Check: ${hunk.flags.join(', ')}` : ''}</p>{/each}{#if proposal.status === 'pending'}<div class="doc-actions"><button class="button primary" disabled={busy || dirty || !proposal.hunks.some(h=>accepted.includes(h.id))} onclick={() => act(() => review(proposal))}>Apply selected exact edits</button><button class="button" disabled={busy || dirty} onclick={() => act(() => review(proposal,true))}>Reject proposal</button></div>{/if}</article>{/each}
          {:else if shown === 'facts'}
            <p class="hint">Hosted editing sends the payload you preview through OpenRouter to your selected model provider. Contact redaction does not anonymize work history. Public research sends company/role names and public URLs, never resume text.</p>
            <label class="check switch-row"><input type="checkbox" bind:checked={settings.enabled} /> I opt in to external research and hosted model requests</label>
            <div class="fields"><label>Model ID<input bind:value={settings.model_id} /></label><label>Maximum model request cost (USD)<input inputmode="decimal" bind:value={settings.max_cost_usd} /></label></div>
            <button class="button" disabled={busy} onclick={() => act(saveSettings)}>Confirm privacy settings</button>
            <h3>Your confirmed facts</h3>
            <p class="hint">Rika may only use facts you confirm here. A job posting asking for a skill never counts as having it.</p>
            <ul class="rows">
              {#each facts as fact (fact.id)}<li class="fact" data-confirmed={fact.confirmed}><span><span class="pill" data-status={fact.confirmed ? 'confirmed' : 'pending'}>{fact.confirmed ? 'Confirmed' : 'Unconfirmed'}</span> {fact.text} <small>{fact.source}</small></span><button class="button ghost" onclick={() => { editingFact = fact; factText = fact.text; factSource = fact.source; factConfirmed = fact.confirmed; }}>Edit fact</button></li>{:else}<li class="hint none">No facts yet. Add the first one below.</li>{/each}
            </ul>
            <div class="fact-form">
              <label>Fact text<textarea bind:value={factText}></textarea></label>
              <label>Fact evidence source<input bind:value={factSource} /></label>
              <label class="check"><input type="checkbox" bind:checked={factConfirmed} /> I confirm this fact is true</label>
              <div class="doc-actions"><button class="button" disabled={busy || !factText.trim()} onclick={() => act(saveFact)}>{editingFact ? 'Update fact' : 'Add fact'}</button>{#if editingFact}<button class="button ghost" onclick={() => { editingFact = null; factText = ''; factSource = ''; factConfirmed = false; }}>Cancel fact edit</button>{/if}</div>
            </div>
            {#if preferences.length}<details><summary>What Rika learned from your reviews</summary><pre>{JSON.stringify(preferences,null,2)}</pre></details>{/if}
          {:else if shown === 'batches'}
            <p class="hint">Choose jobs and Rika drafts one tailored copy of the open master for each. Creating a batch makes no model calls; running it sends contact-redacted source, confirmed facts and each posting within your cost limit. Every draft still needs your review.</p>
            <div class="job-list">{#each batches.jobs as job (job.id)}<label class="check job"><input type="checkbox" value={job.id} bind:group={selectedJobs} /><span><strong>{job.company}</strong> {job.title}</span></label>{:else}<p class="hint">No ApplyAI jobs yet.</p>{/each}</div>
            <div class="fields"><label>Maximum total batch cost (USD)<input bind:value={batchCap} inputmode="decimal" /></label></div>
            <p class="hint">Public research: {researchEnabled ? 'included' : 'off'} · Master: {doc?.title || 'open a master first'}{doc ? `, revision ${doc.revision}` : ''}</p>
            <div class="doc-actions"><button class="button primary" disabled={busy || !doc || doc.kind !== 'master' || !selectedJobs.length} onclick={() => act(createBatch)}>Create capped batch</button><button class="button ghost" disabled={busy} onclick={() => act(async () => { batches = await workshop.batches(); })}>Refresh batches and outcomes</button></div>
            {#each batches.batches as batch (batch.id)}
              <article class="panel" data-status={batch.status}>
                <h3>Batch <span class="pill" data-status={batch.status}>{batch.status}</span></h3>
                <p class="hint">Master revision {batch.master_revision} · Limit ${batch.max_cost_usd} · Reserved maximum ${batch.reserved_usd} · Research {batch.research ? 'on' : 'off'}. Reservations are upper bounds, not charges; interrupted requests are never retried.</p>
                <div class="doc-actions">
                  <button class="button primary" disabled={busy || !settings.enabled || batch.status !== 'queued' || activeBatches.includes(batch.id)} onclick={() => act(() => runBatch(batch.id))}>Confirm and run capped batch</button>
                  <button class="button ghost" disabled={['cancelled','done'].includes(batch.status)} onclick={() => act(async () => { await api(`/workshop-batches/${batch.id}/cancel`,{confirm:true}); batches = await workshop.batches(); })}>Cancel remaining work</button>
                </div>
                <ul class="rows">{#each batch.items as item}<li class="fact"><span>{item.company} — {item.role}: <span class="pill" data-status={item.status}>{item.status}</span> {item.error || ''}</span>{#if item.doc_id}<button class="button ghost" disabled={busy} onclick={() => act(() => open(item.doc_id!))}>Open result and review</button>{/if}</li>{/each}</ul>
              </article>
            {/each}
          {:else if shown === 'outcomes'}
            <p class="hint">{batches.insights.notice}</p>
            {#if batches.insights.resumes.length}
              <table><thead><tr><th>Resume version</th><th>Applications</th><th>Responses</th><th>Interview or later</th><th>Offers</th></tr></thead>
                <tbody>{#each batches.insights.resumes as row}<tr><td>{row.title} <small>revision {row.revision}</small></td><td>{row.applications}</td><td>{row.responses}</td><td>{row.advanced}</td><td>{row.outcomes.offer ?? 0}</td></tr>{/each}</tbody></table>
            {:else}<p class="hint none">Export a resume and use it in ApplyAI to see outcomes here.</p>{/if}
          {:else if shown === 'files' && doc}
            <label class="drop small">
              <span class="drop-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 16V4m0 0-4.5 4.5M12 4l4.5 4.5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg></span>
              <span class="drop-text"><strong>Add or replace project files</strong><small>Images, fonts, class files</small></span>
              <input class="sr-only" type="file" multiple onchange={event => importFiles(event,true)} />
            </label>
            <ul class="rows">{#each Object.keys(assets) as name (name)}<li class="fact"><span class="file-chip">{name}</span><span class="doc-actions"><button class="button ghost" onclick={() => chooseAsset(name)}>Edit</button><button class="button ghost" aria-label={`Remove asset ${name}`} onclick={() => { const next = {...assets}; delete next[name]; assets = next; selectedAsset = ''; changed(); }}>Remove</button></span></li>{/each}</ul>
            {#if selectedAsset}<label>{selectedAsset}<textarea class="asset" bind:value={assetText} oninput={() => { assets = {...assets,[selectedAsset]:encodeText(assetText)}; changed(); }}></textarea></label>{/if}
            <h3>History</h3>
            <ol class="timeline">{#each revisions as revision (revision.revision)}<li><span class="dot" aria-hidden="true"></span><span class="when"><strong>Revision {revision.revision}</strong> · {revision.author} · {revision.note}</span><button class="button ghost" disabled={dirty || busy || saving} onclick={() => act(() => restore(revision.revision))}>Restore revision {revision.revision}</button></li>{/each}</ol>
            <button class="button danger" disabled={dirty || busy} onclick={() => act(async () => { await api(`/workshop/${doc!.id}/archive`,{confirm:true}); doc = null; ++compileToken; images.forEach(URL.revokeObjectURL); images = []; await refresh(); })}>Archive this document</button>
          {/if}
        </div>
      {/key}
    </main>
  </div>
</section>

<style>
  /* Colours, radii and type come from the theme (styles.css); this file only arranges them.
     Movement is the Animations module's: it plays only under <html data-anim="on"> and never
     for people who asked for less motion (the setting or the system's). */
  .workshop {
    --ws-glow: color-mix(in srgb, var(--accent) 16%, transparent);
    --ws-line: color-mix(in srgb, var(--ink) 9%, var(--border));
    --ws-card: color-mix(in srgb, var(--surface) 88%, transparent);
    --ws-ease: cubic-bezier(0.16, 1, 0.3, 1);
    position: relative; padding: 22px 28px 48px; color: var(--ink); height: 100%; overflow: auto;
  }
  .workshop::before {
    content: ""; position: absolute; inset: 0 0 auto; height: 320px; pointer-events: none; z-index: 0;
    background: radial-gradient(closest-side at 18% 0%, var(--ws-glow), transparent), radial-gradient(closest-side at 88% 0%, color-mix(in srgb, var(--c2, var(--accent)) 10%, transparent), transparent);
    mask-image: linear-gradient(#000 30%, transparent);
  }
  .workshop > * { position: relative; z-index: 1; }
  h1 { font-family: var(--font-title, var(--font-ui)); font-size: calc(var(--fs-title) + 6px); letter-spacing: -0.02em; margin: 2px 0 0; line-height: 1.1; }
  h2 { font-size: var(--fs-heading); margin: 0 0 10px; display: flex; align-items: center; gap: 8px; }
  h3 { font-size: var(--fs-subheading); margin: 16px 0 8px; display: flex; align-items: center; gap: 8px; }
  ul, ol { list-style: none; margin: 0; padding: 0; }

  /* ── Header */
  .hero { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-bottom: 20px; }
  .eyebrow { display: inline-flex; align-items: center; gap: 8px; margin: 0; color: var(--muted); font-size: var(--fs-label); font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; }
  .spark { width: 8px; height: 8px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 4px var(--ws-glow); }
  .lede { color: var(--muted); max-width: 62ch; margin: 8px 0 0; line-height: 1.5; }
  .hero-side { display: flex; flex-direction: column; align-items: flex-end; gap: 10px; }
  .chips { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .status-chip { display: inline-flex; align-items: center; gap: 7px; height: 26px; padding: 0 11px; border-radius: 999px; border: 1px solid var(--ws-line); background: var(--ws-card); font-size: var(--fs-label); color: var(--muted); }
  .status-chip i, .save-state i, .pill.busy i { width: 7px; height: 7px; border-radius: 50%; background: var(--subtle, var(--muted)); }
  .status-chip[data-state=ok] i, .status-chip[data-state=on] i { background: var(--success); box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 22%, transparent); }

  /* ── Cards and layout */
  .layout { display: grid; grid-template-columns: minmax(230px, 280px) minmax(0, 1fr); gap: 22px; align-items: start; }
  aside, main { min-width: 0; }
  .rail { display: grid; gap: 14px; }
  .card { border: 1px solid var(--ws-line); border-radius: var(--r-panel); background: var(--ws-card); padding: 14px 16px; box-shadow: 0 1px 0 color-mix(in srgb, var(--ink) 4%, transparent) inset, 0 12px 32px -22px color-mix(in srgb, #000 60%, transparent); }
  .count { display: inline-grid; place-items: center; min-width: 20px; height: 20px; padding: 0 6px; border-radius: 999px; background: color-mix(in srgb, var(--ink) 8%, transparent); color: var(--muted); font-size: 11px; font-weight: 600; }

  /* ── Resume list */
  .docs { display: grid; gap: 4px; }
  .document { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; padding: 8px 10px; border-radius: var(--r-item); background: transparent; color: var(--ink); border: 1px solid transparent; transition: background-color 160ms, border-color 160ms; }
  .document:hover:not(:disabled) { background: var(--wash); }
  .document[aria-pressed=true] { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: color-mix(in srgb, var(--accent) 10%, var(--wash)); }
  .doc-name { min-width: 0; font-weight: 550; overflow-wrap: anywhere; }
  .doc-name small { display: block; color: var(--muted); font-weight: 400; font-size: var(--fs-label); }
  .variants { padding-left: 10px; border-left: 1px dashed var(--ws-line); margin-left: 14px; }
  .none { padding: 6px 2px; }
  .badge { flex: none; display: inline-grid; place-items: center; width: 34px; height: 34px; border-radius: 9px; font-size: 10.5px; font-weight: 700; letter-spacing: 0.02em; color: var(--ink); background: color-mix(in srgb, var(--badge, var(--accent)) 22%, transparent); border: 1px solid color-mix(in srgb, var(--badge, var(--accent)) 45%, transparent); }
  .badge[data-format=latex] { --badge: var(--c1, var(--accent)); }
  .badge[data-format=typst] { --badge: var(--c3, var(--accent)); }
  .badge[data-format=html] { --badge: var(--c5, var(--accent)); }
  .badge.big { width: 42px; height: 42px; border-radius: 11px; font-size: 12px; }

  /* ── Start a resume */
  label { display: block; margin: 10px 0; font-size: var(--fs-label); color: var(--muted); font-weight: 500; }
  .field-label { display: block; margin: 12px 0 6px; font-size: var(--fs-label); color: var(--muted); font-weight: 500; }
  .field { flex: 1; min-width: 180px; margin: 10px 0; } .field .field-label { margin: 0 0 4px; }
  label.check { display: flex; align-items: center; gap: 9px; color: var(--ink); font-size: var(--fs-control); font-weight: 400; }
  input:not([type=checkbox]):not([type=file]), textarea { width: 100%; margin-top: 5px; font-size: var(--fs-control); }
  textarea { padding: 9px 11px; border-radius: var(--r-field); line-height: 1.45; }
  .tiles { display: grid; gap: 6px; margin-bottom: 12px; }
  .tile { display: grid; grid-template-columns: auto 1fr; column-gap: 10px; align-items: center; text-align: left; padding: 8px 10px; border-radius: var(--r-field); border: 1px solid var(--ws-line); background: color-mix(in srgb, var(--ink) 3%, transparent); color: var(--ink); font-size: var(--fs-control); transition: border-color 180ms, background-color 180ms, box-shadow 180ms; }
  .tile .badge { grid-row: span 2; width: 30px; height: 30px; border-radius: 8px; font-size: 9.5px; }
  .tile small { color: var(--muted); font-size: 11px; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tile:hover { border-color: color-mix(in srgb, var(--ink) 22%, transparent); }
  .tile[aria-checked=true] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); box-shadow: 0 0 0 3px var(--ws-glow); }
  .wide { width: 100%; height: var(--control-h-lg); }
  .drop { display: flex; align-items: center; gap: 12px; margin: 14px 0 6px; padding: 14px; border-radius: var(--r-field); border: 1.5px dashed color-mix(in srgb, var(--ink) 20%, transparent); color: var(--ink); cursor: pointer; transition: border-color 180ms, background-color 180ms; }
  .drop:hover, .drop:focus-within, .drop[data-over=true] { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 9%, transparent); }
  .drop.small { margin-top: 0; }
  .drop-icon { flex: none; display: grid; place-items: center; width: 34px; height: 34px; border-radius: 50%; background: color-mix(in srgb, var(--accent) 16%, transparent); }
  .drop-icon svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .drop-text strong { display: block; font-size: var(--fs-control); } .drop-text small { color: var(--muted); }
  .hint { color: var(--muted); font-size: var(--fs-label); margin: 6px 0; line-height: 1.5; }
  .hint.center { text-align: center; }
  .engines summary { display: flex; align-items: center; gap: 8px; }
  .engines ul { display: grid; gap: 8px; margin-top: 10px; }
  .engines li { display: flex; gap: 10px; align-items: flex-start; font-size: var(--fs-label); }
  .engines li i { flex: none; width: 8px; height: 8px; margin-top: 4px; border-radius: 50%; background: color-mix(in srgb, var(--ink) 25%, transparent); }
  .engines li[data-ready=true] i { background: var(--success); box-shadow: 0 0 0 3px color-mix(in srgb, var(--success) 20%, transparent); }
  .engines li small { display: block; color: var(--muted); }

  /* ── Messages */
  .message { font-size: var(--fs-control); border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); background: color-mix(in srgb, var(--accent) 8%, transparent); padding: 9px 13px; border-radius: var(--r-field); white-space: pre-wrap; margin: 10px 0; }
  .message.error { border-color: color-mix(in srgb, var(--error) 45%, transparent); background: color-mix(in srgb, var(--error) 9%, transparent); }
  .link { background: none; border: 0; padding: 0; color: var(--accent); font: inherit; font-weight: 600; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }

  /* ── The open resume */
  .doc-bar { display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; margin-bottom: 14px; }
  .doc-heading { display: grid; grid-template-columns: auto 1fr; grid-template-rows: auto auto; column-gap: 12px; align-items: center; flex: 1; min-width: 260px; }
  .doc-heading .badge { grid-row: span 2; }
  .title-field { margin: 0; }
  .title-field input { margin: 0; height: auto; padding: 2px 6px; margin-left: -6px; font-size: 19px; font-weight: 650; letter-spacing: -0.01em; background: transparent; border-color: transparent; }
  .title-field input:hover, .title-field input:focus { border-color: var(--ws-line); background: color-mix(in srgb, var(--ink) 4%, transparent); }
  .meta { color: var(--muted); font-size: var(--fs-label); }
  .doc-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .save-state { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: var(--fs-label); padding: 0 8px; }
  .save-state[data-state=saved] i { background: var(--success); }
  .save-state[data-state=dirty] i { background: var(--warning); }
  .save-state[data-state=saving] i { background: var(--accent); }
  .button.ghost { background: transparent; border-color: transparent; }
  .button.ghost:hover:not(:disabled) { background: var(--wash); border-color: var(--ws-line); }
  .button.danger { color: var(--error); border-color: color-mix(in srgb, var(--error) 35%, transparent); background: transparent; margin-top: 14px; }
  .doc-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 10px 0; }

  /* ── Studio: source beside the paper */
  .studio { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
  .pane { min-width: 0; padding: 0; overflow: hidden; display: flex; flex-direction: column; }
  .pane-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 14px; border-bottom: 1px solid var(--ws-line); }
  .pane-head label, .pane-head h2 { margin: 0; font-size: var(--fs-control); font-weight: 650; color: var(--ink); }
  .pane-head small { color: var(--muted); font-size: var(--fs-label); }
  .pane-head .push { margin-left: auto; }
  .pane-foot { display: flex; gap: 6px; padding: 8px 10px; border-top: 1px solid var(--ws-line); }
  .file-chip { font-family: var(--font-code); font-size: 11.5px; padding: 2px 8px; border-radius: var(--r-chip); background: color-mix(in srgb, var(--ink) 8%, transparent); color: var(--ink); }
  .code { position: relative; display: grid; grid-template-columns: auto 1fr; height: 620px; background: color-mix(in srgb, var(--canvas) 55%, transparent); }
  .gutter { margin: 0; padding: 12px 10px 12px 14px; max-height: none; overflow: hidden; border-radius: 0; background: transparent; color: color-mix(in srgb, var(--muted) 70%, transparent); text-align: right; user-select: none; font-family: var(--font-code); font-size: 12.5px; line-height: 20px; white-space: pre; border-right: 1px solid var(--ws-line); }
  .source { height: 100%; margin: 0 !important; border: 0 !important; border-radius: 0; background: transparent !important; padding: 12px 14px; tab-size: 2; font-family: var(--font-code); font-size: 12.5px; line-height: 20px; resize: none; white-space: pre; overflow: auto; box-shadow: none !important; }
  .source:focus { outline: none; }
  .code:focus-within { box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--accent) 60%, transparent); }
  .preview { position: relative; }
  .pill { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px; border-radius: 999px; font-size: 11px; font-weight: 600; color: var(--muted); background: color-mix(in srgb, var(--ink) 7%, transparent); text-transform: none; }
  .pill.ok, .pill[data-status=confirmed], .pill[data-status=done], .pill[data-status=accepted], .pill[data-status=applied] { color: var(--success); background: color-mix(in srgb, var(--success) 14%, transparent); }
  .pill.warn, .pill[data-status=pending], .pill[data-status=queued] { color: var(--warning); background: color-mix(in srgb, var(--warning) 14%, transparent); }
  .pill[data-status=failed], .pill[data-status=rejected], .pill[data-status=cancelled] { color: var(--error); background: color-mix(in srgb, var(--error) 12%, transparent); }
  .pill.busy, .pill[data-status=running] { color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .pill.busy i { background: var(--accent); }
  .progress { height: 2px; background: transparent; overflow: hidden; }
  .stage { flex: 1; height: 620px; overflow: auto; padding: 18px; display: grid; align-content: start; gap: 16px; background: radial-gradient(circle at 1px 1px, color-mix(in srgb, var(--ink) 10%, transparent) 1px, transparent 0) 0 0 / 16px 16px, color-mix(in srgb, var(--canvas) 40%, transparent); }
  .page { display: block; width: 100%; max-width: 640px; justify-self: center; height: auto; background: white; border-radius: 3px; box-shadow: 0 1px 2px rgb(0 0 0 / 30%), 0 18px 40px -18px rgb(0 0 0 / 65%); transition: transform 300ms var(--ws-ease), box-shadow 300ms; }
  .preview[data-compiling=true] .page { opacity: 0.82; }
  .paper-ghost { justify-self: center; width: min(100%, 420px); aspect-ratio: 8.5 / 11; border-radius: 3px; background: color-mix(in srgb, var(--ink) 6%, transparent); padding: 34px 30px; display: grid; align-content: start; gap: 12px; }
  .paper-ghost span { height: 9px; border-radius: 4px; background: color-mix(in srgb, var(--ink) 10%, transparent); }
  .paper-ghost span:nth-child(1) { width: 45%; height: 14px; justify-self: center; } .paper-ghost span:nth-child(3) { width: 80%; } .paper-ghost span:nth-child(4) { width: 62%; }
  .stale { margin: 0; border: 1px solid color-mix(in srgb, var(--warning) 40%, transparent); padding: 8px 12px; background: color-mix(in srgb, var(--warning) 9%, transparent); border-radius: var(--r-field); font-size: var(--fs-label); }
  .compile-error { margin: 0; border: 1px solid color-mix(in srgb, var(--error) 45%, transparent); background: color-mix(in srgb, var(--error) 9%, transparent); font-family: var(--font-code); font-size: 12px; }

  /* ── Insights: the ATS check and what a parser reads */
  .insights { display: grid; gap: 14px; margin-top: 14px; }
  .readable { margin-top: 0; }
  .ats-head { display: flex; gap: 14px; align-items: center; margin-bottom: 12px; }
  .ats-head h3 { margin: 0; display: block; font-size: var(--fs-heading); } .ats-head h3 small { display: block; color: var(--muted); font-weight: 400; font-size: var(--fs-label); margin-top: 3px; }
  .ring { flex: none; width: 64px; height: 64px; transform: rotate(-90deg); }
  .ring circle { fill: none; stroke-width: 6; }
  .ring .track { stroke: color-mix(in srgb, var(--ink) 10%, transparent); }
  .ring .value { stroke: var(--success); stroke-linecap: round; stroke-dasharray: 100; stroke-dashoffset: calc(100 - var(--score) * 100); }
  .ring text { transform: rotate(90deg); transform-origin: 32px 32px; fill: var(--ink); font-size: 13px; font-weight: 700; font-family: var(--font-ui); }
  .findings { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 8px; }
  .findings li { display: flex; gap: 10px; align-items: flex-start; padding: 9px 10px; border-radius: var(--r-field); background: color-mix(in srgb, var(--ink) 3.5%, transparent); border: 1px solid transparent; }
  .findings li.warn { border-color: color-mix(in srgb, var(--warning) 30%, transparent); }
  .findings li.fail { border-color: color-mix(in srgb, var(--error) 40%, transparent); }
  .findings strong { font-size: var(--fs-control); } .findings li small { display: block; color: var(--muted); font-size: var(--fs-label); margin-top: 2px; line-height: 1.4; }
  .mark { flex: none; width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 12px; color: var(--canvas); }
  .pass .mark { background: var(--success); } .warn .mark { background: var(--warning); } .fail .mark { background: var(--error); }
  .parsed { display: grid; grid-template-columns: max-content 1fr; gap: 5px 14px; margin: 14px 0 8px; padding: 12px; border-radius: var(--r-field); background: color-mix(in srgb, var(--canvas) 45%, transparent); font-size: var(--fs-label); }
  .parsed dt { color: var(--muted); } .parsed dd { margin: 0; overflow-wrap: anywhere; }
  .terms { border-top: 1px solid var(--ws-line); padding-top: 10px; margin-top: 10px; font-size: var(--fs-label); }
  .terms p { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin: 6px 0; }
  .terms .label { color: var(--muted); margin-right: 4px; }
  .chip { border-radius: 999px; padding: 2px 9px; border: 1px solid var(--ws-line); }
  .chip.found { border-color: color-mix(in srgb, var(--success) 55%, transparent); background: color-mix(in srgb, var(--success) 10%, transparent); }
  .chip.missing { border-color: color-mix(in srgb, var(--warning) 55%, transparent); background: color-mix(in srgb, var(--warning) 10%, transparent); }
  .readable summary { display: flex; align-items: center; gap: 8px; }
  .checks { margin: 10px 0; display: grid; gap: 5px; font-size: var(--fs-control); } .checks li::before { content: "•"; color: var(--accent); margin-right: 7px; }
  details { margin-top: 10px; }
  summary { cursor: pointer; font-weight: 600; list-style-position: inside; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; background: var(--chrome); padding: 10px; border-radius: var(--r-field); max-height: 24rem; overflow: auto; }

  /* ── Empty state */
  .empty { text-align: center; padding: 34px 28px; display: grid; justify-items: center; gap: 6px; }
  .empty h2 { justify-content: center; margin: 14px 0 2px; font-size: 20px; }
  .empty > p { color: var(--muted); max-width: 56ch; margin: 0; line-height: 1.5; }
  .sheets { position: relative; width: 92px; height: 108px; }
  .sheets span { position: absolute; inset: 0; border-radius: 6px; background: color-mix(in srgb, var(--ink) 92%, transparent); box-shadow: 0 12px 30px -12px rgb(0 0 0 / 60%); }
  .sheets span:nth-child(1) { transform: rotate(-9deg) translateX(-14px); background: color-mix(in srgb, var(--c1, var(--accent)) 55%, var(--surface)); }
  .sheets span:nth-child(2) { transform: rotate(7deg) translateX(14px); background: color-mix(in srgb, var(--c3, var(--accent)) 55%, var(--surface)); }
  .sheets span:nth-child(3) { background: linear-gradient(var(--surface-raised), var(--surface-raised)) 14px 18px / 48% 7px no-repeat, linear-gradient(color-mix(in srgb, var(--ink) 14%, transparent), color-mix(in srgb, var(--ink) 14%, transparent)) 14px 36px / 70% 5px no-repeat, linear-gradient(color-mix(in srgb, var(--ink) 14%, transparent), color-mix(in srgb, var(--ink) 14%, transparent)) 14px 48px / 60% 5px no-repeat, linear-gradient(color-mix(in srgb, var(--ink) 14%, transparent), color-mix(in srgb, var(--ink) 14%, transparent)) 14px 60px / 66% 5px no-repeat, color-mix(in srgb, var(--ink) 88%, var(--surface)); }
  .steps { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; width: 100%; max-width: 720px; margin-top: 18px; counter-reset: step; }
  .steps li { counter-increment: step; text-align: left; padding: 12px; border-radius: var(--r-field); border: 1px solid var(--ws-line); background: color-mix(in srgb, var(--ink) 3%, transparent); }
  .steps li::before { content: counter(step); display: inline-grid; place-items: center; width: 22px; height: 22px; margin-bottom: 8px; border-radius: 50%; background: color-mix(in srgb, var(--accent) 20%, transparent); color: var(--ink); font-size: 11px; font-weight: 700; }
  .steps strong { display: block; font-size: var(--fs-control); } .steps small { color: var(--muted); font-size: var(--fs-label); }

  /* ── Tools: tabs and their panel */
  .tabs { display: flex; gap: 4px; flex-wrap: wrap; margin: 22px 0 10px; padding: 4px; border-radius: 999px; border: 1px solid var(--ws-line); background: var(--ws-card); width: fit-content; max-width: 100%; }
  .tabs [role=tab] { display: inline-flex; align-items: center; gap: 7px; height: 30px; padding: 0 14px; border-radius: 999px; border: 0; background: transparent; color: var(--muted); font-size: var(--fs-control); font-weight: 550; transition: color 160ms, background-color 160ms; }
  .tabs [role=tab]:hover { color: var(--ink); }
  .tabs [role=tab][aria-selected=true] { background: var(--surface-raised); color: var(--ink); box-shadow: 0 1px 3px rgb(0 0 0 / 25%); }
  .tabs [role=tab][aria-selected=true] .count { background: color-mix(in srgb, var(--accent) 22%, transparent); color: var(--ink); }
  .tab-panel { padding: 16px 18px; }
  .tab-panel > .hint:first-child { margin-top: 0; }
  .panel { border: 1px solid var(--ws-line); border-radius: var(--r-field); background: color-mix(in srgb, var(--ink) 3%, transparent); padding: 12px 14px; margin: 12px 0; }
  .panel h3:first-child { margin-top: 0; }
  .fields { display: flex; gap: 12px; flex-wrap: wrap; } .fields > label { flex: 1; min-width: 170px; }
  .rows { display: grid; gap: 6px; margin: 8px 0; }
  .fact { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 10px; border-radius: var(--r-field); background: color-mix(in srgb, var(--ink) 3%, transparent); font-size: var(--fs-control); }
  .fact small { color: var(--muted); }
  .fact-form { margin-top: 12px; padding: 12px 14px; border-radius: var(--r-field); border: 1px dashed var(--ws-line); }
  .fact-form label:first-child { margin-top: 0; }
  .job-list { max-height: 260px; overflow: auto; display: grid; gap: 4px; margin: 8px 0; }
  .job { margin: 0; padding: 8px 10px; border-radius: var(--r-field); background: color-mix(in srgb, var(--ink) 3%, transparent); }
  .job:has(input:checked) { background: color-mix(in srgb, var(--accent) 11%, transparent); }
  .diff { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; } .diff > div { min-width: 0; }
  .diff .before pre { border-left: 3px solid color-mix(in srgb, var(--error) 60%, transparent); }
  .diff .after pre { border-left: 3px solid color-mix(in srgb, var(--success) 60%, transparent); }
  .timeline { position: relative; display: grid; gap: 4px; margin: 6px 0; padding-left: 18px; }
  .timeline::before { content: ""; position: absolute; left: 5px; top: 10px; bottom: 10px; width: 1.5px; background: var(--ws-line); }
  .timeline li { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 6px 8px; border-radius: var(--r-item); font-size: var(--fs-control); }
  .timeline li:hover { background: var(--wash); }
  .timeline .dot { position: absolute; left: -17px; width: 9px; height: 9px; border-radius: 50%; background: var(--surface); border: 2px solid var(--accent); }
  .timeline li:first-child .dot { background: var(--accent); }
  .asset { min-height: 180px; font-family: var(--font-code); }
  table { width: 100%; border-collapse: collapse; font-size: var(--fs-control); }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--ws-line); }
  th { color: var(--muted); font-weight: 600; font-size: var(--fs-label); }
  tbody tr:hover { background: var(--wash); }
  td small { color: var(--muted); }

  /* ── Motion (the Animations module only, and never under Reduce motion). */
  @media (prefers-reduced-motion: no-preference) {
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .rise { animation: ws-rise 620ms var(--ws-ease) backwards; animation-delay: calc(var(--rise-i, 0) * 70ms); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .spark { animation: ws-breathe 3.2s ease-in-out infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .tile:hover:not([aria-checked=true]) { transform: translateX(3px); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .tile { transition: transform 380ms var(--anim-spring, var(--ws-ease)), border-color 180ms, background-color 180ms, box-shadow 180ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .drop[data-over=true] .drop-icon { animation: ws-bob 900ms ease-in-out infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .save-state[data-state=saving] i, :global(:root[data-anim="on"]:not([data-motion="reduced"])) .pill.busy i { animation: ws-pulse 900ms ease-in-out infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .save-state[data-state=saved] i { animation: ws-pop 460ms var(--anim-spring, var(--ws-ease)); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .preview[data-compiling=true] .progress { background: linear-gradient(90deg, transparent, var(--accent), transparent) 0 0 / 40% 100% no-repeat; animation: ws-sweep 1.1s var(--ws-ease) infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .page { animation: ws-page 560ms var(--ws-ease) backwards; animation-delay: calc(var(--page-i, 0) * 90ms); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .page:hover { transform: translateY(-3px); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .paper-ghost span { animation: ws-shimmer 1.6s ease-in-out infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .ring .value { animation: ws-ring 1100ms var(--ws-ease) 120ms backwards; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .findings li { animation: ws-row 480ms var(--ws-ease) backwards; animation-delay: calc(180ms + var(--row-i, 0) * 45ms); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .tab-panel { animation: ws-panel 380ms var(--ws-ease); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .pop { animation: ws-rise 480ms var(--anim-spring, var(--ws-ease)) backwards; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .sheets span:nth-child(1) { animation: ws-fan-left 900ms var(--anim-spring, var(--ws-ease)) backwards 120ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .sheets span:nth-child(2) { animation: ws-fan-right 900ms var(--anim-spring, var(--ws-ease)) backwards 120ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .sheets { animation: ws-float 5s ease-in-out infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .steps li { animation: ws-rise 560ms var(--ws-ease) backwards; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .steps li:nth-child(2) { animation-delay: 80ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .steps li:nth-child(3) { animation-delay: 160ms; }
  }
  @keyframes ws-rise { from { opacity: 0; transform: translateY(14px); } }
  @keyframes ws-panel { from { opacity: 0; transform: translateY(8px); } }
  @keyframes ws-row { from { opacity: 0; transform: translateY(10px) scale(0.98); } }
  @keyframes ws-page { from { opacity: 0; transform: translateY(18px) scale(0.97); } }
  @keyframes ws-ring { from { stroke-dashoffset: 100; } }
  @keyframes ws-pulse { 50% { opacity: 0.35; transform: scale(0.7); } }
  @keyframes ws-pop { 0% { transform: scale(0.3); } 60% { transform: scale(1.4); } }
  @keyframes ws-breathe { 50% { box-shadow: 0 0 0 7px transparent; } }
  @keyframes ws-bob { 50% { transform: translateY(-3px); } }
  @keyframes ws-sweep { from { background-position: -40% 0; } to { background-position: 140% 0; } }
  @keyframes ws-shimmer { 50% { opacity: 0.45; } }
  @keyframes ws-float { 50% { transform: translateY(-5px); } }
  @keyframes ws-fan-left { from { transform: rotate(0) translateX(0); } }
  @keyframes ws-fan-right { from { transform: rotate(0) translateX(0); } }

  @media (max-width: 1180px) { .studio { grid-template-columns: 1fr; } .code, .stage { height: 460px; } }
  @media (max-width: 760px) { .layout { grid-template-columns: 1fr; } .diff, .steps { grid-template-columns: 1fr; } .workshop { padding: 14px; } .hero { flex-direction: column; align-items: flex-start; } .hero-side { align-items: flex-start; } }
</style>
