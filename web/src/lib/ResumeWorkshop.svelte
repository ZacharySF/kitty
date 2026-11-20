<script lang="ts">
  import { onMount } from 'svelte';
  import Loader from './Loader.svelte';
  import Select from './ui/Select.svelte';
  import { api, ApiError, apiObjectUrl, downloadApi, type TaskRecord } from './api';
  import { workshop, taskResult, fileBase64, encodeText, decodeText, type WorkshopDoc, type WorkshopIndex, type Revision, type Compile, type Settings, type Fact, type Proposal, type Preview, type Research, type BatchIndex, type AtsResult } from './workshopApi';
  import AskRika from './rika/AskRika.svelte';
  import { useFocus } from './rika/focus.svelte';
  import RikaRemembers from './rika/RikaRemembers.svelte';
  import { takeTailorJob, type TailorJob } from './workshopHandoff';
  let { onview }: { onview: (view: 'resumes' | 'rika') => void } = $props();
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
  type Tab = 'check' | 'tailor' | 'facts' | 'history';
  let tab = $state<Tab>('check'), dragging = $state(false), gutter = $state<HTMLElement>();
  /** The template gallery fills the middle when nothing is open or "New resume" was pressed. */
  let starting = $state(true);
  /** Tailor one copy by hand, or many: one draft per selected job (a batch). */
  let tailorMode = $state<'one' | 'many'>('one');
  const lineCount = $derived(source.split('\n').length);
  const gutterText = $derived(Array.from({length: lineCount}, (_, i) => i + 1).join('\n'));
  const confirmedFacts = $derived(facts.filter(f => f.confirmed).length);
  const engines = $derived(index?.capabilities.engines || []);
  const atsTotal = $derived(atsResult?.report ? atsResult.report.findings.length : 0);
  const atsScore = $derived(atsTotal ? (atsResult!.report!.counts.pass / atsTotal) : 0);
  const FORMAT: Record<WorkshopDoc['format'], string> = {latex: 'TeX', typst: 'Typ', html: 'HTML'};
  const tabs = $derived<{id: Tab; label: string; count?: number | string}[]>([
    {id: 'check', label: 'Check', count: atsResult?.report ? `${atsResult.report.counts.pass}/${atsTotal}` : undefined},
    {id: 'tailor', label: 'Tailor', count: pendingProposals || undefined},
    {id: 'facts', label: 'Facts', count: confirmedFacts || undefined},
    {id: 'history', label: 'History', count: revisions.length || undefined},
  ]);
  const issues = $derived((atsResult?.report?.findings || []).filter(f => f.level !== 'pass'));
  const passes = $derived((atsResult?.report?.findings || []).filter(f => f.level === 'pass'));
  const chosen = $derived(index?.templates.find(t => t.id === template));
  /** The engines that compile a format (render.py's _ENGINE_IDS); a LaTeX template may name one. */
  const ENGINES: Record<WorkshopDoc['format'], string[]> = {html: ['chromium'], typst: ['typst'], latex: ['tectonic', 'pdflatex', 'lualatex']};
  const ready = (format: WorkshopDoc['format']) => engines.some(e => e.available && ENGINES[format].includes(e.id));
  async function startNew() { if (saving || (dirty && !await save()) || dirty) return; starting = true; }
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
  useFocus(() => doc ? { kind: 'resume_document', id: doc.id, title: title.trim() || doc.title,
    detail: doc.kind === 'tailored' ? [doc.company, doc.role].filter(Boolean).join(' · ') || 'Tailored version' : 'Master resume' } : null);
  function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause); }
  async function act(work: () => Promise<void>) { busy = true; error = ''; notice = ''; try { await work(); } catch (cause) { if (!lifetime.signal.aborted) error = message(cause); } finally { busy = false; } }
  async function refresh() { index = await workshop.index(); if (!template) template = index.templates[0]?.id || ''; }
  async function loadAI() { const data = await api<{settings:Settings;facts:Fact[];preferences:unknown[]}>('/workshop-ai'); settings = data.settings; facts = data.facts; preferences = data.preferences || []; }
  async function loadProposals(id: string) { const result = await api<{proposals:Proposal[]}>(`/workshop-ai/proposals?doc_id=${encodeURIComponent(id)}`); if (doc?.id === id) { proposals = result.proposals; if (proposals.some(p => p.status === 'pending')) tab = 'tailor'; } }
  function adopt(next: WorkshopDoc) { doc = next; source = next.source; title = next.title; assets = {...next.assets}; conflict = false; selectedAsset = ''; preview = null; accepted = []; }
  async function open(id: string) {
    if (saving || (dirty && !await save()) || dirty) return;
    const token = ++selection; const result = await workshop.get(id); if (token !== selection || lifetime.signal.aborted) return;
    ++compileToken; images.forEach(URL.revokeObjectURL); images = []; compiled = null; atsResult = null; compileError = ''; adopt(result.document); revisions = result.revisions; starting = false; tab = 'check';
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
  /** Find's "Tailor resume": this job's tailored copy (else the master) on Tailor, with the job linked. */
  async function tailorFor(job: TailorJob) {
    const current = (index?.documents || []).filter(d => !d.archived);
    const target = current.find(d => d.kind === 'tailored' && d.job_id === job.id) || current.find(d => d.kind === 'master');
    if (!target) { notice = `Create or import a master resume, then tailor it for ${job.title} at ${job.company}.`; return; }
    await open(target.id);
    company = job.company; role = job.title; jobId = String(job.id); preview = null; research = null; tab = 'tailor';
  }
  onMount(() => {
    const handed = takeTailorJob();
    void act(async () => { try { await refresh(); await Promise.all([loadAI(), workshop.batches().then(result => { batches = result; })]); if (handed) await tailorFor(handed); } finally { loading = false; } });
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


<section class="workshop" aria-label="Resume Workshop" data-open={!!doc && !starting}>
  <header class="top">
    <h1>Resume Workshop</h1>
    <span class="privacy" data-on={settings.enabled} title={settings.enabled ? 'Rika may send previewed, contact-redacted requests you confirm.' : 'Nothing leaves this computer.'}><i aria-hidden="true"></i>{settings.enabled ? 'Cloud help on' : 'Stays on this computer'}</span>
    <AskRika push />
    <button class="button ghost" onclick={() => onview('resumes')}>Existing resumes library</button>
  </header>
  {#if error}<p class="message error" role="alert">{error}</p>{/if}
  {#if notice}<p class="message" role="status">{notice}</p>{/if}

  <div class="studio">
    <aside class="rail" aria-label="Resume library">
      <button class="button primary new" disabled={busy || saving} aria-pressed={starting} onclick={() => act(startNew)}><span aria-hidden="true">＋</span> New resume</button>
      {#if loading}<Loader label="Loading workshop…" />{/if}
      <ul class="docs">
        {#each masters as item (item.id)}
          <li>
            <button class="document" aria-pressed={!starting && doc?.id === item.id} disabled={busy || saving} onclick={() => act(() => open(item.id))}>
              <span class="badge" data-format={item.format}>{FORMAT[item.format]}</span>
              <span class="doc-name">{item.title}<small>Master · rev {item.revision}</small></span>
            </button>
            {#if variants.some(v => v.base_doc_id === item.id)}
              <ul class="versions">
                {#each variants.filter(v => v.base_doc_id === item.id) as version (version.id)}
                  <li><button class="document version" aria-pressed={!starting && doc?.id === version.id} disabled={busy || saving} onclick={() => act(() => open(version.id))}>
                    <span class="doc-name">{[version.company, version.role].filter(Boolean).join(' · ') || version.title}<small>rev {version.revision}</small></span>
                  </button></li>
                {/each}
              </ul>
            {/if}
          </li>
        {/each}
        {#each variants.filter(v => !masters.some(m => m.id === v.base_doc_id)) as version (version.id)}
          <li><button class="document" aria-pressed={!starting && doc?.id === version.id} disabled={busy || saving} onclick={() => act(() => open(version.id))}>
            <span class="badge" data-format={version.format}>{FORMAT[version.format]}</span>
            <span class="doc-name">{version.title}<small>{[version.company, version.role].filter(Boolean).join(' · ') || 'Tailored'} · rev {version.revision}</small></span>
          </button></li>
        {/each}
      </ul>
      {#if !loading && !masters.length && !variants.length}<p class="quiet">Your resumes will appear here.</p>{/if}
      <details class="engines">
        <summary><i data-ok={engineReady > 0} aria-hidden="true"></i>{engineReady} of {engines.length} compilers ready</summary>
        <ul>{#each engines as engine (engine.id)}<li data-ready={engine.available}><strong>{engine.label}</strong><small>{engine.available ? 'Ready' : engine.reason || 'Not installed'}</small></li>{/each}</ul>
      </details>
    </aside>

    {#if !doc || starting}
      <div class="gallery">
        <div class="gallery-head">
          <h2>Start a resume</h2>
          <p>Pick a layout. You edit the source on the left and see the PDF on the right.</p>
        </div>
        <div class="templates" role="radiogroup" aria-label="Template" tabindex="-1" onkeydown={event => roving(event, '[role="radio"]')}>
          {#each index?.templates || [] as item (item.id)}
            <button class="template" role="radio" aria-checked={template === item.id} tabindex={template === item.id ? 0 : -1} onclick={() => template = item.id}>
              <span class="thumb" data-look={item.id} aria-hidden="true">
                <b></b><i></i>
                <s></s><u></u><u></u>
                <s></s><u></u><u></u><u></u>
                <s></s><u></u>
              </span>
              <span class="template-name"><strong>{item.label.replace(/ \((LaTeX|Typst|HTML)\)$/, '')}</strong><span class="tag" data-format={item.format}>{item.format === 'latex' ? 'LaTeX' : item.format === 'typst' ? 'Typst' : 'HTML'}</span></span>
              <small>{ready(item.format) ? item.description : 'Needs a compiler — see the list on the left'}</small>
            </button>
          {/each}
        </div>
        <div class="create">
          <label>New resume title<input bind:value={newTitle} /></label>
          <button class="button primary" disabled={busy || !template} onclick={() => act(create)}>Create resume</button>
        </div>
        <label class="drop" data-over={dragging} ondragover={event => { event.preventDefault(); dragging = true; }} ondragleave={() => dragging = false} ondrop={drop}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V4m0 0L8 8m4-4 4 4M5 14v4a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" /></svg>
          <span><strong>Or import a resume you have</strong><small>LaTeX, Typst, HTML, Overleaf ZIP, PDF or DOCX — drop it here or click to browse</small></span>
          <input class="sr-only" type="file" multiple accept=".html,.htm,.typ,.tex,.zip,.pdf,.docx,.md,.json,.txt,.css,.png,.jpg,.jpeg,.svg,.woff,.woff2,.ttf,.otf" disabled={busy} onchange={event => importFiles(event)} />
        </label>
      </div>
    {:else}
      <div class="desk">
        <div class="toolbar">
          <span class="badge" data-format={doc.format}>{FORMAT[doc.format]}</span>
          <div class="title-block">
            <label class="title-field"><span class="sr-only">Resume title</span><input bind:value={title} oninput={changed} disabled={busy} /></label>
            <small>{doc.kind === 'tailored' ? [doc.company, doc.role].filter(Boolean).join(' · ') || 'Tailored version' : 'Master'} · revision {doc.revision} · {doc.engine}</small>
          </div>
          <span class="save-state" role="status" data-state={saving ? 'saving' : dirty ? 'dirty' : 'saved'}><i aria-hidden="true"></i>{saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'All changes saved'}</span>
          {#if dirty && !saving}<button class="button ghost" disabled={conflict} onclick={() => save()}>Save now</button>{/if}
          <button class="button primary" disabled={busy || saving || conflict} onclick={() => act(exportPDF)}>Export PDF and register</button>
        </div>
        {#if conflict}<div class="message error conflict"><span>This resume changed in another window.</span><button class="button" onclick={downloadDraft}>Download preserved draft</button><button class="button" disabled={busy} onclick={() => act(async () => { if (!doc) return; const result = await workshop.get(doc.id); adopt(result.document); revisions = result.revisions; await compile(); })}>Discard draft and reload server version</button></div>{/if}
        <div class="split">
          <div class="pane editor">
            <div class="pane-head"><label for="resume-source">Resume source</label><span class="file">{doc.entrypoint}</span><small class="push">{lineCount} lines</small></div>
            <div class="code">
              <pre class="gutter" aria-hidden="true" bind:this={gutter}>{gutterText}</pre>
              <textarea id="resume-source" class="source" spellcheck="false" bind:value={source} oninput={changed} onscroll={event => { if (gutter) gutter.scrollTop = event.currentTarget.scrollTop; }} disabled={busy}></textarea>
            </div>
          </div>
          <section class="pane preview" aria-label="Compiled preview" data-compiling={compiling}>
            <div class="pane-head">
              <h2>Preview</h2>
              {#if compiling}<span class="state busy" role="status"><i aria-hidden="true"></i>Compiling saved revision…</span>
              {:else if compileError}<span class="state bad">Didn't compile</span>
              {:else if stale}<span class="state warn">Out of date</span>
              {:else if compiled}<span class="state ok">{compiled.pages} page{compiled.pages === 1 ? '' : 's'}</span>{/if}
              {#if atsResult?.report}<button class="score push" title="Open the ATS check" onclick={() => tab = 'check'}>ATS {atsResult.report.counts.pass}/{atsTotal}</button>{/if}
              <button class="button ghost icon" class:push={!atsResult?.report} title="Compile saved revision" aria-label="Compile saved revision" disabled={busy || saving || dirty || compiling} onclick={() => act(compile)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5" /></svg></button>
            </div>
            <div class="progress" aria-hidden="true"></div>
            <div class="paper">
              {#if compileError}<pre class="compile-error" role="alert">{compileError}</pre>{:else if stale}<p class="note warn">Showing revision {compiled?.revision}; your newest edits appear after the next save.</p>{/if}
              {#each images as image, i (image)}<img class="page" style="--i: {i}" src={image} alt={`Resume PDF page ${i+1}`} />{:else}
                {#if !compileError}<div class="blank" aria-hidden="true"></div>{/if}
                <p class="quiet center">No successful preview yet. Source editing remains available offline.</p>
              {/each}
            </div>
          </section>
        </div>
      </div>

      <aside class="inspector">
        <div class="tabs" role="tablist" aria-label="Resume tools" tabindex="-1" onkeydown={event => roving(event, '[role="tab"]')}>
          {#each tabs as item (item.id)}
            <button role="tab" id={`workshop-tab-${item.id}`} aria-controls="workshop-panel" aria-selected={tab === item.id} tabindex={tab === item.id ? 0 : -1} onclick={() => tab = item.id}>{item.label}{#if item.count !== undefined}<span class="count">{item.count}</span>{/if}</button>
          {/each}
        </div>
        {#key tab}
          <div class="panel-body" id="workshop-panel" role="tabpanel" aria-labelledby={`workshop-tab-${tab}`}>
            {#if tab === 'check'}
              {#if atsResult?.report}
                {@const report = atsResult.report}
                {#key atsResult.revision}
                  <section class="ats" aria-label="ATS check">
                    <div class="verdict">
                      <svg class="ring" viewBox="0 0 64 64" aria-hidden="true" style="--score: {atsScore}"><circle class="track" cx="32" cy="32" r="27" /><circle class="value" cx="32" cy="32" r="27" pathLength="100" /></svg>
                      <div><strong>{report.counts.pass} of {atsTotal} checks pass</strong><small>How a resume parser reads revision {atsResult.revision}</small></div>
                    </div>
                    {#if issues.length}
                      <h3>To review</h3>
                      <ul class="findings">{#each issues as finding, i}<li class={finding.level} style="--i: {i}"><span class="mark" aria-label={finding.level}>{finding.level === 'warn' ? '!' : '×'}</span><div><strong>{finding.title}</strong><small>{finding.detail}</small></div></li>{/each}</ul>
                    {/if}
                    <details class="passed"><summary>{passes.length} passed</summary><ul class="findings compact">{#each passes as finding}<li class="pass"><span class="mark" aria-label="pass">✓</span><div><strong>{finding.title}</strong><small>{finding.detail}</small></div></li>{/each}</ul></details>
                    <h3>What the parser read</h3>
                    <dl class="parsed">
                      <dt>Name</dt><dd>{report.contact.name || '—'}</dd>
                      <dt>Email</dt><dd>{report.contact.email || '—'}</dd>
                      <dt>Phone</dt><dd>{report.contact.phone || '—'}</dd>
                      <dt>Links</dt><dd>{report.contact.links.join(' · ') || '—'}</dd>
                      <dt>Sections</dt><dd>{report.sections.map(s => s.title).join(' · ') || '—'}</dd>
                    </dl>
                    {#if atsResult.job_terms}
                      {@const terms = atsResult.job_terms}
                      <h3>Against {terms.company} — {terms.title}</h3>
                      {#if terms.error}<p class="quiet">{terms.error}</p>{:else}
                        <div class="chips">{#each terms.found as term}<span class="chip found">{term}</span>{/each}{#each terms.missing as term}<span class="chip missing" title="Asked for, not found">{term}</span>{/each}</div>
                        <p class="quiet">Outlined terms are missing. Add one only where it is true for you.</p>
                      {/if}
                    {/if}
                    {#if compiled}
                      <details><summary>Plain text a parser sees</summary>
                        <p class="quiet">Selectable text: {compiled.checks.extractable_text ? 'yes' : 'no'} · reading order: {compiled.checks.reading_order === 'source_order_matches' ? 'matches the source' : compiled.checks.reading_order === 'warning' ? 'differs — check columns' : 'not checked'}{#each (compiled.checks.warnings as string[] | undefined) || [] as warning} · {warning}{/each}</p>
                        <pre>{compiled.checks.extracted_text}</pre>
                      </details>
                    {/if}
                    <p class="quiet fine">{report.note}</p>
                  </section>
                {/key}
              {:else}
                <p class="quiet center pad">{compiling ? 'Checking after this compile…' : 'The check runs after each successful compile.'}</p>
              {/if}

            {:else if tab === 'tailor'}
              <RikaRemembers onopen={() => onview('rika')} />
              <div class="segmented" role="radiogroup" aria-label="Tailor mode">
                <button role="radio" aria-checked={tailorMode === 'one'} onclick={() => tailorMode = 'one'}>This resume</button>
                <button role="radio" aria-checked={tailorMode === 'many'} onclick={() => tailorMode = 'many'}>Many jobs at once</button>
              </div>
              {#if !settings.enabled}<p class="note">Rika's edits need cloud help. <button class="link" onclick={() => tab = 'facts'}>Turn it on in Facts</button>. Manual editing never needs it.</p>{/if}
              {#if tailorMode === 'one'}
                <div class="group">
                  <label>Company<input bind:value={company} oninput={() => { research = null; preview = null; }} /></label>
                  <label>Role<input bind:value={role} oninput={() => { research = null; preview = null; }} /></label>
                  <div class="field"><span class="field-label">Linked job</span><Select label="Linked job" kind="search" bind:value={jobId} onchange={selectJob} options={[{value: '', label: 'No linked job'}, ...batches.jobs.map(job => ({value: String(job.id), label: `${job.company} — ${job.title}`}))]} /></div>
                  <button class="button" disabled={busy || saving} onclick={() => act(async () => { if (!await save() || dirty) return; const result = await api<{document:WorkshopDoc}>(`/workshop/${doc!.id}/copy`,{title:`${title} — ${company || role || 'variant'}`.slice(0,200),company,role,job_id:jobId ? Number(jobId) : undefined}); await refresh(); await open(result.document.id); })}>Create linked variant</button>
                </div>
                <div class="group">
                  <label>What should Rika change?<textarea bind:value={instruction} oninput={() => preview = null} placeholder="Emphasize my verification work for this role"></textarea></label>
                  <label class="check"><input type="checkbox" bind:checked={researchEnabled} onchange={() => preview = null} /> Use public research on the company</label>
                  {#if researchEnabled}
                    <label>Company or posting URLs, one per line<textarea bind:value={urls}></textarea></label>
                    <button class="button" disabled={busy || !settings.enabled || !company} onclick={() => act(fetchResearch)}>Fetch cited public research</button>
                    {#if research}<div class="card"><h3>{research.company} · {research.status}</h3>{#each research.findings as finding}<p>{finding.text} <a href={finding.url} target="_blank" rel="noreferrer">Source</a></p>{/each}{#each research.warnings as warning}<p class="quiet">{warning}</p>{/each}</div>{/if}
                  {/if}
                  <button class="button primary" disabled={busy || !settings.enabled || !instruction.trim() || conflict} onclick={() => act(prepare)}>Preview redacted request and maximum cost</button>
                  <p class="quiet">You see exactly what would be sent, and its cost limit, before anything goes out.</p>
                </div>
                {#if preview}<div class="card pop"><h3>Outgoing request</h3><p class="quiet">{preview.model_id} · at most ${preview.maximum_cost_usd} · expires {preview.expires_at}</p><pre>{typeof preview.redacted_payload === 'string' ? preview.redacted_payload : JSON.stringify(preview.redacted_payload,null,2)}</pre><button class="button primary" disabled={busy || dirty} onclick={() => act(runAI)}>Confirm and send this payload</button></div>{/if}
                {#each proposals as proposal (proposal.id)}
                  <article class="card pop proposal">
                    <h3>{proposal.summary || 'Proposed edits'}<span class="state" data-status={proposal.status}>{proposal.status}</span></h3>
                    {#each proposal.warnings || [] as warning}<p class="note">{warning}</p>{/each}
                    {#each proposal.hunks as hunk}
                      <div class="hunk">
                        <label class="check"><input type="checkbox" value={hunk.id} bind:group={accepted} disabled={proposal.status !== 'pending'} /> Approve this exact edit</label>
                        <pre class="del">{hunk.find}</pre><pre class="ins">{hunk.replace}</pre>
                        <p class="quiet">{hunk.reason} · facts {hunk.fact_ids.join(', ')}{(hunk.flags || []).length ? ` · check: ${hunk.flags.join(', ')}` : ''}</p>
                      </div>
                    {/each}
                    {#if proposal.status === 'pending'}<div class="row"><button class="button primary" disabled={busy || dirty || !proposal.hunks.some(h=>accepted.includes(h.id))} onclick={() => act(() => review(proposal))}>Apply selected exact edits</button><button class="button ghost" disabled={busy || dirty} onclick={() => act(() => review(proposal,true))}>Reject proposal</button></div>{/if}
                  </article>
                {/each}
              {:else}
                <p class="quiet">Rika drafts one tailored copy of this master per job. Creating the batch costs nothing; running it stays under your total limit, and every draft waits for your review.</p>
                <div class="jobs">{#each batches.jobs as job (job.id)}<label class="check job"><input type="checkbox" value={job.id} bind:group={selectedJobs} /><span><strong>{job.company}</strong><small>{job.title}</small></span></label>{:else}<p class="quiet">No ApplyAI jobs yet.</p>{/each}</div>
                <label>Maximum total batch cost (USD)<input bind:value={batchCap} inputmode="decimal" /></label>
                <div class="row"><button class="button primary" disabled={busy || doc.kind !== 'master' || !selectedJobs.length} onclick={() => act(createBatch)}>Create capped batch</button><button class="button ghost" disabled={busy} onclick={() => act(async () => { batches = await workshop.batches(); })}>Refresh batches and outcomes</button></div>
                {#each batches.batches as batch (batch.id)}
                  <article class="card">
                    <h3>Batch · {batch.items.length} job{batch.items.length === 1 ? '' : 's'}<span class="state" data-status={batch.status}>{batch.status}</span></h3>
                    <p class="quiet">Limit ${batch.max_cost_usd} · reserved at most ${batch.reserved_usd} · research {batch.research ? 'on' : 'off'}</p>
                    <ul class="lines">{#each batch.items as item}<li><span>{item.company} — {item.role} <span class="state" data-status={item.status}>{item.status}</span>{item.error ? ` ${item.error}` : ''}</span>{#if item.doc_id}<button class="button ghost" disabled={busy} onclick={() => act(() => open(item.doc_id!))}>Open result and review</button>{/if}</li>{/each}</ul>
                    <div class="row">
                      <button class="button primary" disabled={busy || !settings.enabled || batch.status !== 'queued' || activeBatches.includes(batch.id)} onclick={() => act(() => runBatch(batch.id))}>Confirm and run capped batch</button>
                      <button class="button ghost" disabled={['cancelled','done'].includes(batch.status)} onclick={() => act(async () => { await api(`/workshop-batches/${batch.id}/cancel`,{confirm:true}); batches = await workshop.batches(); })}>Cancel remaining work</button>
                    </div>
                  </article>
                {/each}
              {/if}

            {:else if tab === 'facts'}
              <div class="card cloud" data-on={settings.enabled}>
                <label class="check strong"><input type="checkbox" bind:checked={settings.enabled} /> I opt in to external research and hosted model requests</label>
                <div class="pair"><label>Model ID<input bind:value={settings.model_id} /></label><label>Max per request (USD)<input inputmode="decimal" bind:value={settings.max_cost_usd} /></label></div>
                <button class="button" disabled={busy} onclick={() => act(saveSettings)}>Confirm privacy settings</button>
                <details><summary>What leaves this computer</summary><p class="quiet">Hosted editing sends the payload you preview through OpenRouter to your selected model provider. Contact redaction does not anonymize work history. Public research sends company/role names and public URLs, never resume text.</p></details>
              </div>
              <h3>Confirmed facts</h3>
              <p class="quiet">Rika only writes what is backed by a fact you confirmed. A posting asking for a skill never counts as having it.</p>
              <ul class="lines facts">{#each facts as fact (fact.id)}<li data-confirmed={fact.confirmed}><span><i aria-hidden="true"></i>{fact.text}{#if fact.source}<small>{fact.source}</small>{/if}</span><button class="button ghost" onclick={() => { editingFact = fact; factText = fact.text; factSource = fact.source; factConfirmed = fact.confirmed; }}>Edit fact</button></li>{/each}</ul>
              <div class="group">
                <label>Fact text<textarea bind:value={factText} placeholder="Led the migration of 40 services to Kubernetes"></textarea></label>
                <label>Fact evidence source<input bind:value={factSource} placeholder="Performance review, 2025" /></label>
                <label class="check"><input type="checkbox" bind:checked={factConfirmed} /> I confirm this fact is true</label>
                <div class="row"><button class="button" disabled={busy || !factText.trim()} onclick={() => act(saveFact)}>{editingFact ? 'Update fact' : 'Add fact'}</button>{#if editingFact}<button class="button ghost" onclick={() => { editingFact = null; factText = ''; factSource = ''; factConfirmed = false; }}>Cancel fact edit</button>{/if}</div>
              </div>
              {#if preferences.length}<details><summary>What Rika learned from your reviews</summary><pre>{JSON.stringify(preferences,null,2)}</pre></details>{/if}

            {:else if tab === 'history'}
              <ol class="timeline">{#each revisions as revision (revision.revision)}<li><i aria-hidden="true"></i><span><strong>Revision {revision.revision}</strong><small>{revision.note} · {revision.author}</small></span><button class="button ghost" disabled={dirty || busy || saving || revision.revision === doc.revision} onclick={() => act(() => restore(revision.revision))}>Restore revision {revision.revision}</button></li>{/each}</ol>
              <h3>Project files</h3>
              <ul class="lines">{#each Object.keys(assets) as name (name)}<li><span class="file">{name}</span><span class="row tight"><button class="button ghost" onclick={() => chooseAsset(name)}>Edit</button><button class="button ghost" aria-label={`Remove asset ${name}`} onclick={() => { const next = {...assets}; delete next[name]; assets = next; selectedAsset = ''; changed(); }}>Remove</button></span></li>{/each}</ul>
              {#if selectedAsset}<label>{selectedAsset}<textarea class="asset" bind:value={assetText} oninput={() => { assets = {...assets,[selectedAsset]:encodeText(assetText)}; changed(); }}></textarea></label>{/if}
              <label class="button ghost file-button">Add or replace project files<input class="sr-only" type="file" multiple onchange={event => importFiles(event,true)} /></label>
              <div class="row">
                <button class="button ghost" disabled={busy || dirty} onclick={() => act(() => downloadApi(`/workshop/${doc!.id}/source?revision=${doc!.revision}`,`${title}.zip`))}>Download source archive</button>
                <button class="button ghost" onclick={downloadDraft}>Download draft backup</button>
              </div>
              <h3>How exported versions did</h3>
              {#if batches.insights.resumes.length}
                <table><thead><tr><th>Version</th><th title="Applications">Sent</th><th title="Responses">Replies</th><th title="Interview or later">Interviews</th><th>Offers</th></tr></thead>
                  <tbody>{#each batches.insights.resumes as row}<tr><td>{row.title}<small>rev {row.revision}</small></td><td>{row.applications}</td><td>{row.responses}</td><td>{row.advanced}</td><td>{row.outcomes.offer ?? 0}</td></tr>{/each}</tbody></table>
                <p class="quiet fine">{batches.insights.notice}</p>
              {:else}<p class="quiet">Export a version and apply with it in ApplyAI to see replies here.</p>{/if}
              <button class="button danger" disabled={dirty || busy} onclick={() => act(async () => { await api(`/workshop/${doc!.id}/archive`,{confirm:true}); doc = null; starting = true; ++compileToken; images.forEach(URL.revokeObjectURL); images = []; await refresh(); })}>Archive this document</button>
            {/if}
          </div>
        {/key}
      </aside>
    {/if}
  </div>
</section>

<style>
  /* One studio: the library on the left, the open resume in the middle (source beside its PDF),
     and the tools for that resume on the right. Colours, radii and type are the theme's
     (styles.css); movement is the Animations module's and stops under Reduce motion. */
  .workshop {
    --line: color-mix(in srgb, var(--ink) 10%, var(--border));
    --soft: color-mix(in srgb, var(--ink) 4%, transparent);
    --softer: color-mix(in srgb, var(--ink) 2.5%, transparent);
    --ease: cubic-bezier(0.16, 1, 0.3, 1);
    --spring: var(--anim-spring, cubic-bezier(0.22, 1.25, 0.36, 1));
    display: flex; flex-direction: column; padding: 4px 0 18px; gap: 12px; color: var(--ink);
  }
  h1 { font-family: var(--font-title, var(--font-ui)); font-size: 22px; letter-spacing: -0.015em; margin: 0; }
  h2 { font-size: var(--fs-heading); margin: 0; }
  h3 { font-size: var(--fs-label); font-weight: 650; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 20px 0 8px; display: flex; align-items: center; gap: 8px; }
  ul, ol { list-style: none; margin: 0; padding: 0; }
  p { margin: 0; }
  label { display: block; font-size: var(--fs-label); color: var(--muted); font-weight: 500; }
  input:not([type=checkbox]):not([type=file]), textarea { display: block; box-sizing: border-box; width: 100%; max-width: 100%; margin-top: 5px; font-size: var(--fs-control); }
  textarea { min-height: 72px; padding: 8px 10px; border-radius: var(--r-field); line-height: 1.45; resize: vertical; }
  label.check { display: flex; align-items: center; gap: 9px; color: var(--ink); font-size: var(--fs-control); font-weight: 400; }
  .field-label { display: block; font-size: var(--fs-label); color: var(--muted); font-weight: 500; margin-bottom: 5px; }
  .quiet { color: var(--muted); font-size: var(--fs-label); line-height: 1.5; }
  .quiet.center { text-align: center; } .quiet.pad { padding: 28px 8px; } .quiet.fine { font-size: 11px; margin-top: 14px; }
  .push { margin-left: auto; }
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; } .row.tight { gap: 2px; flex-wrap: nowrap; }
  .button.ghost { background: transparent; border-color: transparent; }
  .button.ghost:hover:not(:disabled) { background: var(--soft); border-color: var(--line); }
  .button.icon { width: var(--control-h); padding: 0; }
  .button svg, .drop svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .button.danger { color: var(--error); background: transparent; border-color: color-mix(in srgb, var(--error) 30%, transparent); margin-top: 22px; }
  .link { background: none; border: 0; padding: 0; color: var(--ink); font: inherit; font-weight: 600; text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
  .file { font-family: var(--font-code); font-size: 11.5px; color: var(--muted); }
  .count { display: inline-grid; place-items: center; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 999px; background: var(--soft); color: var(--muted); font-size: 10.5px; font-weight: 650; }
  .message { font-size: var(--fs-control); padding: 8px 12px; border-radius: var(--r-field); border: 1px solid var(--line); background: var(--soft); white-space: pre-wrap; }
  .message.error { border-color: color-mix(in srgb, var(--error) 45%, transparent); background: color-mix(in srgb, var(--error) 9%, transparent); }
  .conflict { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .note { font-size: var(--fs-label); line-height: 1.5; padding: 8px 10px; border-radius: var(--r-field); background: var(--soft); border: 1px solid var(--line); margin-bottom: 12px; }
  .note.warn { border-color: color-mix(in srgb, var(--warning) 40%, transparent); background: color-mix(in srgb, var(--warning) 8%, transparent); margin: 0; }

  /* ── Top line */
  .top { display: flex; align-items: center; gap: 12px; }
  .privacy { display: inline-flex; align-items: center; gap: 7px; height: 24px; padding: 0 10px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 11.5px; }
  .privacy i { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }
  .privacy[data-on=true] i { background: var(--warning); }

  /* ── The studio */
  .studio { height: clamp(560px, calc(100dvh - 150px), 1400px); display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 12px; }
  .workshop[data-open=true] .studio { grid-template-columns: 232px minmax(0, 1fr) 340px; }
  .rail, .gallery, .desk, .inspector { min-height: 0; min-width: 0; border: 1px solid var(--line); border-radius: var(--r-panel); background: var(--surface); }

  /* Library */
  .rail { display: flex; flex-direction: column; gap: 10px; padding: 10px; overflow: auto; }
  .new { width: 100%; height: var(--control-h-lg); }
  .docs { display: grid; gap: 2px; }
  .document { display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; padding: 7px 8px; border-radius: var(--r-item); border: 0; background: transparent; color: var(--ink); transition: background-color 140ms; }
  .document:hover:not(:disabled) { background: var(--soft); }
  .document[aria-pressed=true] { background: color-mix(in srgb, var(--ink) 9%, transparent); }
  .doc-name { min-width: 0; font-size: var(--fs-control); font-weight: 550; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; }
  .doc-name small { display: block; color: var(--muted); font-weight: 400; font-size: 11px; margin-top: 1px; }
  .versions { margin: 1px 0 4px 21px; padding-left: 12px; border-left: 1px solid var(--line); display: grid; gap: 1px; }
  .version { padding: 5px 8px; }
  .badge { flex: none; display: inline-grid; place-items: center; width: 28px; height: 28px; border-radius: 7px; font-size: 9.5px; font-weight: 750; letter-spacing: 0.02em; color: var(--ink); background: color-mix(in srgb, var(--tone) 20%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--tone) 40%, transparent); }
  [data-format=latex] { --tone: var(--c1, var(--accent)); } [data-format=typst] { --tone: var(--c3, var(--accent)); } [data-format=html] { --tone: var(--c5, var(--accent)); }
  .quiet:where(.rail > *) { padding: 4px 8px; }
  .engines { margin-top: auto; padding: 8px; border-radius: var(--r-item); font-size: 11.5px; color: var(--muted); }
  .engines summary { cursor: pointer; display: flex; align-items: center; gap: 8px; list-style: none; }
  .engines summary::-webkit-details-marker { display: none; }
  .engines summary i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); } .engines summary i[data-ok=true] { background: var(--success); }
  .engines ul { display: grid; gap: 6px; margin-top: 8px; }
  .engines li strong { display: block; color: var(--ink); font-weight: 550; } .engines li[data-ready=false] strong { color: var(--muted); }
  .engines li small { display: block; line-height: 1.35; }

  /* Template gallery */
  .gallery { overflow: auto; padding: 28px 32px; display: grid; align-content: start; gap: 22px; }
  .gallery-head p { color: var(--muted); margin-top: 4px; font-size: var(--fs-control); }
  .templates { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 14px; }
  .template { display: grid; gap: 8px; align-content: start; padding: 0; border: 0; background: transparent; color: var(--ink); text-align: left; cursor: pointer; }
  .template small { color: var(--muted); font-size: 11.5px; line-height: 1.4; }
  .template-name { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: var(--fs-control); }
  .tag { font-size: 10px; font-weight: 650; padding: 2px 6px; border-radius: 5px; color: var(--ink); background: color-mix(in srgb, var(--tone) 18%, transparent); }
  .thumb { position: relative; display: grid; align-content: start; gap: 5px; aspect-ratio: 8.5 / 11; padding: 16% 13%; border-radius: 6px; background: #fbfbf9; box-shadow: 0 0 0 1px var(--line), 0 10px 24px -14px rgb(0 0 0 / 70%); transition: box-shadow 220ms, transform 360ms var(--spring); overflow: hidden; }
  .template:hover .thumb { box-shadow: 0 0 0 1px color-mix(in srgb, var(--ink) 30%, transparent), 0 14px 30px -14px rgb(0 0 0 / 75%); }
  .template[aria-checked=true] .thumb { box-shadow: 0 0 0 2px var(--ink), 0 0 0 6px color-mix(in srgb, var(--ink) 14%, transparent), 0 14px 30px -14px rgb(0 0 0 / 75%); }
  .template:focus-visible { outline: none; } .template:focus-visible .thumb { outline: 2px solid var(--accent); outline-offset: 4px; }
  /* A sketch of each layout: name (b), contact (i), section rule (s), lines (u). */
  .thumb b { height: 7%; min-height: 6px; width: 52%; border-radius: 2px; background: #2b2b2b; }
  .thumb i { height: 3px; width: 70%; border-radius: 2px; background: #b9b9b4; margin-bottom: 6%; }
  .thumb s { height: 3px; width: 32%; background: #444; margin-top: 5%; text-decoration: none; }
  .thumb u { height: 3px; width: 92%; border-radius: 2px; background: #d6d6d1; text-decoration: none; }
  .thumb u:nth-of-type(2n) { width: 78%; }
  .thumb[data-look=latex-jake] b, .thumb[data-look=latex-jake] i, .thumb[data-look=latex-clean] b, .thumb[data-look=latex-clean] i { justify-self: center; }
  .thumb[data-look=latex-jake] s { width: 100%; height: 1.5px; background: #333; box-shadow: 0 -4px 0 -0.5px #333 inset; position: relative; }
  .thumb[data-look=latex-jake] s::before { content: ""; position: absolute; left: 0; top: -5px; width: 28%; height: 3px; background: #333; border-radius: 1px; }
  .thumb[data-look=latex-jake] u { width: 100%; background: linear-gradient(90deg, #d6d6d1 0 62%, transparent 62% 80%, #d6d6d1 80%); }
  .thumb[data-look=latex-clean] s { width: 100%; height: 1.5px; background: #666; }
  .thumb[data-look=typst-clean] { padding-top: 0; }
  .thumb[data-look=typst-clean] b { margin: 0 -18% 0; width: auto; height: 18%; border-radius: 0; background: color-mix(in srgb, var(--c3, #555) 55%, #222); }
  .thumb[data-look=typst-clean] i { margin-top: 4%; }
  .thumb[data-look=typst-clean] s { background: color-mix(in srgb, var(--c3, #555) 70%, #222); }
  .thumb[data-look=html-clean] { border-left: 6px solid color-mix(in srgb, var(--c5, #666) 70%, #333); }
  .thumb[data-look=html-clean] s { background: color-mix(in srgb, var(--c5, #666) 70%, #333); width: 26%; }
  /* Classic, Modern and Compact (LaTeX and HTML share a sketch): centred name over a contact line, a small-caps label on a full-width rule. */
  .thumb:is([data-look$=-classic], [data-look$=-modern], [data-look$=-compact]) :is(b, i) { justify-self: center; }
  .thumb:is([data-look$=-classic], [data-look$=-modern], [data-look$=-compact]) s { position: relative; width: 100%; height: 1.5px; margin-top: 9%; background: #333; }
  .thumb:is([data-look$=-classic], [data-look$=-modern], [data-look$=-compact]) s::before { content: ""; position: absolute; left: 0; bottom: 3px; width: 26%; height: 3px; border-radius: 1px; background: #333; }
  .thumb[data-look$=-classic] b { width: 50%; background: #1e1e1e; }
  .thumb[data-look$=-classic] u { width: 100%; background: linear-gradient(90deg, #d6d6d1 0 58%, transparent 58% 80%, #d6d6d1 80%); }
  .thumb[data-look$=-modern] { gap: 6px; }
  .thumb[data-look$=-modern] b { width: 58%; height: 8.5%; background: #111; }
  .thumb[data-look$=-modern] i { width: 64%; background: #c6c6c1; }
  .thumb[data-look$=-modern] s { height: 1px; background: #9a9a96; }
  .thumb[data-look$=-modern] s::before { width: 24%; height: 2.5px; background: #555; }
  .thumb[data-look$=-modern] s + u { width: 100%; background: linear-gradient(90deg, #6a6a66 0 30%, transparent 30% 84%, #c6c6c1 84%); }
  .thumb[data-look$=-compact] { gap: 3px; padding: 11% 9%; }
  .thumb[data-look$=-compact] b { width: 40%; height: 5%; min-height: 5px; }
  .thumb[data-look$=-compact] i { width: 78%; height: 2px; margin-bottom: 3%; }
  .thumb[data-look$=-compact] s { height: 1px; margin-top: 5%; }
  .thumb[data-look$=-compact] s::before { bottom: 2px; width: 22%; height: 2.5px; }
  .thumb[data-look$=-compact] u { width: 100%; height: 6px; border-radius: 0; background: linear-gradient(90deg, transparent 0 70%, #fbfbf9 70% 86%, transparent 86%), repeating-linear-gradient(180deg, #d6d6d1 0 2px, transparent 2px 4px); }
  .create { display: flex; align-items: flex-end; gap: 10px; max-width: 560px; }
  .create label { flex: 1; }
  .create .button { height: var(--control-h-lg); padding: 0 18px; }
  .drop { display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: var(--r-field); border: 1.5px dashed var(--line); color: var(--ink); cursor: pointer; transition: border-color 160ms, background-color 160ms; }
  .drop:hover, .drop:focus-within, .drop[data-over=true] { border-color: color-mix(in srgb, var(--ink) 40%, transparent); background: var(--softer); }
  .drop svg { flex: none; width: 20px; height: 20px; color: var(--muted); }
  .drop strong { display: block; font-size: var(--fs-control); font-weight: 600; } .drop small { color: var(--muted); font-size: var(--fs-label); }

  /* Desk: the open resume */
  .desk { display: flex; flex-direction: column; overflow: hidden; }
  .toolbar { display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .title-block { flex: 1; min-width: 200px; }
  .title-block small { display: block; color: var(--muted); font-size: 11px; padding-left: 6px; }
  .title-field input { margin: 0; height: 26px; padding: 0 6px; font-size: 15px; font-weight: 650; background: transparent; border-color: transparent; }
  .title-field input:hover, .title-field input:focus { background: var(--soft); border-color: var(--line); }
  .save-state { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); font-size: 11.5px; white-space: nowrap; }
  .save-state i { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }
  .save-state[data-state=dirty] i { background: var(--warning); } .save-state[data-state=saving] i { background: var(--accent); }
  .split { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .pane { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
  .editor { border-right: 1px solid var(--line); }
  .pane-head { display: flex; align-items: center; gap: 8px; height: 38px; padding: 0 10px 0 12px; border-bottom: 1px solid var(--line); flex: none; }
  .pane-head label, .pane-head h2 { font-size: var(--fs-label); font-weight: 650; color: var(--ink); }
  .pane-head small { color: var(--muted); font-size: 11px; }
  .code { flex: 1; min-height: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); background: color-mix(in srgb, var(--canvas) 50%, transparent); }
  .gutter { margin: 0; padding: 10px 8px 10px 12px; overflow: hidden; background: transparent; color: color-mix(in srgb, var(--muted) 60%, transparent); text-align: right; user-select: none; font-family: var(--font-code); font-size: 12px; line-height: 19px; white-space: pre; max-height: none; border-radius: 0; }
  .source { height: 100%; min-height: 0; margin: 0 !important; border: 0 !important; border-radius: 0; background: transparent !important; padding: 10px 12px; tab-size: 2; font-family: var(--font-code); font-size: 12px; line-height: 19px; resize: none; white-space: pre; overflow: auto; box-shadow: none !important; }
  .source:focus { outline: none; }
  .state { display: inline-flex; align-items: center; gap: 6px; height: 20px; padding: 0 8px; border-radius: 999px; font-size: 10.5px; font-weight: 650; color: var(--muted); background: var(--soft); text-transform: capitalize; }
  .state i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .state.ok, .state[data-status=done], .state[data-status=applied], .state[data-status=accepted] { color: var(--success); background: color-mix(in srgb, var(--success) 12%, transparent); }
  .state.warn, .state[data-status=pending], .state[data-status=queued] { color: var(--warning); background: color-mix(in srgb, var(--warning) 12%, transparent); }
  .state.bad, .state[data-status=failed], .state[data-status=rejected], .state[data-status=cancelled] { color: var(--error); background: color-mix(in srgb, var(--error) 12%, transparent); }
  .state.busy, .state[data-status=running] { color: var(--ink); }
  h3 .state { margin-left: auto; text-transform: capitalize; letter-spacing: 0; }
  .score { height: 24px; padding: 0 10px; border-radius: 999px; border: 1px solid var(--line); background: transparent; color: var(--ink); font-size: 11px; font-weight: 650; cursor: pointer; }
  .score:hover { background: var(--soft); }
  .progress { height: 2px; flex: none; }
  .paper { flex: 1; min-height: 0; overflow: auto; padding: 22px; display: grid; align-content: start; justify-items: center; gap: 18px; background: color-mix(in srgb, var(--canvas) 70%, transparent); }
  .page { display: block; width: 100%; max-width: 620px; height: auto; background: white; border-radius: 2px; box-shadow: 0 0 0 1px rgb(0 0 0 / 20%), 0 22px 44px -20px rgb(0 0 0 / 80%); }
  .preview[data-compiling=true] .page { opacity: 0.75; transition: opacity 200ms; }
  .blank { width: min(100%, 420px); aspect-ratio: 8.5 / 11; border-radius: 2px; background: var(--soft); box-shadow: inset 0 0 0 1px var(--line); }
  .compile-error { width: 100%; margin: 0; border: 1px solid color-mix(in srgb, var(--error) 45%, transparent); background: color-mix(in srgb, var(--error) 8%, transparent); font-family: var(--font-code); font-size: 11.5px; }

  /* Inspector: the open resume's tools */
  .inspector { display: flex; flex-direction: column; overflow: hidden; }
  .tabs { display: flex; gap: 2px; padding: 6px; border-bottom: 1px solid var(--line); flex: none; }
  .tabs [role=tab] { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 28px; padding: 0 6px; border: 0; border-radius: var(--r-item); background: transparent; color: var(--muted); font-size: var(--fs-label); font-weight: 600; white-space: nowrap; transition: color 140ms, background-color 140ms; }
  .tabs [role=tab]:hover { color: var(--ink); }
  .tabs [role=tab][aria-selected=true] { color: var(--ink); background: var(--soft); }
  .panel-body { flex: 1; min-height: 0; overflow: auto; padding: 14px; }
  .panel-body > h3:first-child { margin-top: 0; }
  .group { display: grid; gap: 10px; padding: 12px; border-radius: var(--r-field); background: var(--softer); border: 1px solid var(--line); margin-bottom: 12px; }
  .group .button { justify-self: start; max-width: 100%; white-space: normal; height: auto; min-height: var(--control-h); padding-block: 5px; text-align: left; }
  .card { padding: 12px; border-radius: var(--r-field); border: 1px solid var(--line); background: var(--softer); margin: 10px 0; display: grid; gap: 8px; }
  .card h3 { margin: 0; color: var(--ink); text-transform: none; letter-spacing: 0; font-size: var(--fs-control); }
  .cloud .button { justify-self: start; }
  .cloud[data-on=true] { border-color: color-mix(in srgb, var(--warning) 35%, transparent); }
  .check.strong { font-weight: 600; }
  .pair { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .segmented { display: flex; padding: 3px; border-radius: 999px; background: var(--soft); margin-bottom: 12px; }
  .segmented button { flex: 1; height: 26px; border: 0; border-radius: 999px; background: transparent; color: var(--muted); font-size: var(--fs-label); font-weight: 600; }
  .segmented button[aria-checked=true] { background: var(--surface-raised); color: var(--ink); box-shadow: 0 1px 3px rgb(0 0 0 / 30%); }
  .lines { display: grid; gap: 2px; margin: 4px 0 8px; }
  .lines li { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 8px; border-radius: var(--r-item); font-size: var(--fs-control); }
  .lines li:hover { background: var(--soft); }
  .lines small { display: block; color: var(--muted); font-size: 11px; }
  .facts li > span { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .facts li i { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--muted); transform: translateY(-1px); }
  .facts li[data-confirmed=true] i { background: var(--success); }
  .jobs { display: grid; gap: 2px; max-height: 240px; overflow: auto; margin: 10px 0; }
  .job { padding: 7px 8px; border-radius: var(--r-item); } .job:hover { background: var(--soft); }
  .job:has(input:checked) { background: color-mix(in srgb, var(--ink) 8%, transparent); }
  .job small { display: block; color: var(--muted); }
  .hunk { display: grid; gap: 4px; padding-top: 8px; border-top: 1px solid var(--line); }
  pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--font-code); font-size: 11.5px; background: var(--chrome); padding: 8px 10px; border-radius: var(--r-item); max-height: 22rem; overflow: auto; }
  pre.del { background: color-mix(in srgb, var(--error) 10%, var(--chrome)); text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--error) 60%, transparent); }
  pre.ins { background: color-mix(in srgb, var(--success) 10%, var(--chrome)); }
  details { margin-top: 10px; }
  summary { cursor: pointer; font-size: var(--fs-label); font-weight: 600; color: var(--ink); }
  details > :not(summary) { margin-top: 8px; }

  /* ATS check */
  .verdict { display: flex; align-items: center; gap: 14px; padding: 4px 2px 6px; }
  .verdict strong { display: block; font-size: 15px; } .verdict small { color: var(--muted); font-size: var(--fs-label); }
  .ring { flex: none; width: 52px; height: 52px; transform: rotate(-90deg); }
  .ring circle { fill: none; stroke-width: 7; }
  .ring .track { stroke: var(--soft); }
  .ring .value { stroke: var(--success); stroke-linecap: round; stroke-dasharray: 100; stroke-dashoffset: calc(100 - var(--score) * 100); }
  .findings { display: grid; gap: 6px; }
  .findings li { display: flex; gap: 10px; align-items: flex-start; padding: 9px 10px; border-radius: var(--r-field); background: var(--softer); border: 1px solid var(--line); }
  .findings li.warn { border-color: color-mix(in srgb, var(--warning) 35%, transparent); background: color-mix(in srgb, var(--warning) 6%, transparent); }
  .findings li.fail { border-color: color-mix(in srgb, var(--error) 40%, transparent); background: color-mix(in srgb, var(--error) 7%, transparent); }
  .findings strong { font-size: var(--fs-control); font-weight: 600; } .findings small { display: block; color: var(--muted); font-size: 11.5px; line-height: 1.4; margin-top: 1px; }
  .findings.compact li { padding: 5px 4px; border: 0; background: transparent; }
  .mark { flex: none; width: 18px; height: 18px; border-radius: 50%; display: grid; place-items: center; font-weight: 800; font-size: 11px; color: var(--canvas); margin-top: 1px; }
  .pass .mark { background: var(--success); } .warn .mark { background: var(--warning); } .fail .mark { background: var(--error); }
  .passed summary { color: var(--muted); }
  .parsed { display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; font-size: var(--fs-label); }
  .parsed dt { color: var(--muted); } .parsed dd { margin: 0; overflow-wrap: anywhere; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip { border-radius: 999px; padding: 2px 9px; font-size: 11.5px; }
  .chip.found { background: color-mix(in srgb, var(--success) 14%, transparent); }
  .chip.missing { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 60%, transparent); }

  /* History */
  .timeline { position: relative; display: grid; gap: 2px; }
  .timeline::before { content: ""; position: absolute; left: 10px; top: 14px; bottom: 14px; width: 1px; background: var(--line); }
  .timeline li { position: relative; display: flex; align-items: center; gap: 12px; padding: 6px 4px; border-radius: var(--r-item); font-size: var(--fs-control); }
  .timeline li > span { flex: 1; min-width: 0; } .timeline small { display: block; color: var(--muted); font-size: 11px; }
  .timeline i { flex: none; width: 9px; height: 9px; margin-left: 6px; border-radius: 50%; background: var(--surface); box-shadow: 0 0 0 1.5px var(--muted); }
  .timeline li:first-child i { background: var(--ink); box-shadow: 0 0 0 1.5px var(--ink); }
  .file-button { cursor: pointer; margin: 4px 0 8px; color: var(--ink); font-size: var(--fs-control); }
  .asset { min-height: 160px; font-family: var(--font-code); }
  table { width: 100%; border-collapse: collapse; font-size: var(--fs-label); }
  th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 600; } td small { display: block; color: var(--muted); }
  td:not(:first-child), th:not(:first-child) { text-align: right; }

  /* ── Motion: the Animations module only, never under Reduce motion. */
  @media (prefers-reduced-motion: no-preference) {
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .studio > * { animation: ws-in 520ms var(--ease) backwards; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .studio > :nth-child(2) { animation-delay: 60ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .studio > :nth-child(3) { animation-delay: 120ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .template { animation: ws-in 520ms var(--ease) backwards; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .template:nth-child(2) { animation-delay: 50ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .template:nth-child(3) { animation-delay: 100ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .template:nth-child(4) { animation-delay: 150ms; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .template:hover .thumb { transform: translateY(-4px); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .template[aria-checked=true] .thumb { transform: translateY(-4px) scale(1.01); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .page { animation: ws-page 600ms var(--ease) backwards; animation-delay: calc(var(--i, 0) * 90ms); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .preview[data-compiling=true] .progress { background: linear-gradient(90deg, transparent, var(--ink), transparent) 0 0 / 35% 100% no-repeat; animation: ws-sweep 1.2s var(--ease) infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .state.busy i, :global(:root[data-anim="on"]:not([data-motion="reduced"])) .save-state[data-state=saving] i { animation: ws-pulse 900ms ease-in-out infinite; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .save-state[data-state=saved] i { animation: ws-pop 500ms var(--spring); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .panel-body { animation: ws-panel 320ms var(--ease); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .ring .value { animation: ws-ring 1000ms var(--ease) 100ms backwards; }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .findings li { animation: ws-in 420ms var(--ease) backwards; animation-delay: calc(160ms + var(--i, 0) * 50ms); }
    :global(:root[data-anim="on"]:not([data-motion="reduced"])) .pop { animation: ws-in 460ms var(--spring) backwards; }
  }
  @keyframes ws-in { from { opacity: 0; transform: translateY(10px); } }
  @keyframes ws-panel { from { opacity: 0; transform: translateY(6px); } }
  @keyframes ws-page { from { opacity: 0; transform: translateY(16px) scale(0.98); } }
  @keyframes ws-ring { from { stroke-dashoffset: 100; } }
  @keyframes ws-pulse { 50% { opacity: 0.3; } }
  @keyframes ws-pop { 0% { transform: scale(0.2); } 60% { transform: scale(1.5); } }
  @keyframes ws-sweep { from { background-position: -35% 0; } to { background-position: 135% 0; } }

  @media (max-width: 1320px) {
    .workshop[data-open=true] .studio { grid-template-columns: 220px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) minmax(0, 0.8fr); }
    .workshop[data-open=true] .rail { grid-row: span 2; }
  }
  @media (max-width: 860px) {
    .studio { height: auto; }
    .studio, .workshop[data-open=true] .studio { grid-template-columns: 1fr; grid-template-rows: none; }
    .workshop[data-open=true] .rail { grid-row: auto; }
    .split { grid-template-columns: 1fr; } .code, .paper { min-height: 420px; }
    .editor { border-right: 0; border-bottom: 1px solid var(--line); }
  }
</style>
