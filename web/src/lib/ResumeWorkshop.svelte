<script lang="ts">
  import { onMount } from 'svelte';
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
  const lifetime = new AbortController(); let selection = 0, compileToken = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const dirty = $derived(!!doc && (source !== doc.source || title !== doc.title || JSON.stringify(assets) !== JSON.stringify(doc.assets)));
  export function hasUnsavedChanges() { return dirty || saving; }
  const stale = $derived(!!compiled && (dirty || compiled.revision !== doc?.revision));
  function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause); }
  async function act(work: () => Promise<void>) { busy = true; error = ''; notice = ''; try { await work(); } catch (cause) { if (!lifetime.signal.aborted) error = message(cause); } finally { busy = false; } }
  async function refresh() { index = await workshop.index(); if (!template) template = index.templates[0]?.id || ''; }
  async function loadAI() { const data = await api<{settings:Settings;facts:Fact[];preferences:unknown[]}>('/workshop-ai'); settings = data.settings; facts = data.facts; preferences = data.preferences || []; }
  async function loadProposals(id: string) { const result = await api<{proposals:Proposal[]}>(`/workshop-ai/proposals?doc_id=${encodeURIComponent(id)}`); if (doc?.id === id) proposals = result.proposals; }
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
    await act(async () => {
      if (files.reduce((total, file) => total + file.size, 0) > 16 * 1024 * 1024) throw new Error('Upload limit is 16 MiB.');
      const payload = await Promise.all(files.map(async file => ({name:file.name,data:await fileBase64(file)})));
      if (assetOnly) { for (const file of payload) assets = {...assets,[file.name]:file.data}; changed(); return; }
      if ((dirty && !await save()) || dirty) return;
      const result = await taskResult<{documents:WorkshopDoc[];warnings:string[]}>(await api('/workshop/import', {files:payload}), lifetime.signal);
      await refresh(); if (result.documents[0]) await open(result.documents[0].id); notice = (result.warnings || []).join('\n') || 'Import completed. Review reconstructed content and layout.';
    }); input.value = '';
  }
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
  <header class="page-head">
    <div><h1>Resume Workshop</h1><p class="lede">Edit resumes live, tailor them for a company or role, and keep every version. Nothing leaves this computer unless you turn on Rika's cloud help.</p></div>
    <button class="button" onclick={() => onview('resumes')}>Existing resumes library</button>
  </header>
  {#if error}<p class="message error" role="alert">{error}</p>{/if}
  {#if notice}<p class="message" role="status">{notice}</p>{/if}
  {#if loading}<p class="message">Loading workshop…</p>{/if}
  <div class="layout">
    <aside aria-label="Resume library">
      <h2>Master resumes</h2>
      {#each masters as item}<button class="document" aria-pressed={doc?.id === item.id} disabled={busy || saving} onclick={() => act(() => open(item.id))}>{item.title}<small>{item.format} · revision {item.revision}</small></button>{:else}<p class="hint">No resumes yet.</p>{/each}
      {#if variants.length}<h2>Company &amp; role versions</h2>{#each variants as item}<button class="document" aria-pressed={doc?.id === item.id} disabled={busy || saving} onclick={() => act(() => open(item.id))}>{item.title}<small>{[item.company, item.role].filter(Boolean).join(' · ') || 'variant'} · revision {item.revision}</small></button>{/each}{/if}
      <div class="panel">
        <h3>Start a resume</h3>
        <label>New resume title<input bind:value={newTitle} /></label>
        <label>Template<select bind:value={template}>{#each index?.templates || [] as item}<option value={item.id}>{item.label} ({item.format})</option>{/each}</select></label>
        <button class="button primary wide" disabled={busy || !template} onclick={() => act(create)}>Create resume</button>
        <label class="upload">Import source, PDF, DOCX or ZIP<input type="file" multiple accept=".html,.htm,.typ,.tex,.zip,.pdf,.docx,.md,.json,.txt,.css,.png,.jpg,.jpeg,.svg,.woff,.woff2,.ttf,.otf" disabled={busy} onchange={event => importFiles(event)} /></label>
        <p class="hint">LaTeX, Typst and HTML import as editable source; Overleaf ZIPs keep their files. PDF and DOCX become plain editable text and the original is kept beside it.</p>
      </div>
      <details><summary>Compilers ({engineReady} ready)</summary>{#each index?.capabilities.engines || [] as engine}<p class="hint"><strong>{engine.label}:</strong> {engine.available ? 'ready' : engine.reason || 'not installed'}</p>{/each}</details>
    </aside>
    <main>
      {#if doc}
        <div class="doc-bar">
          <label class="title-field">Resume title<input bind:value={title} oninput={changed} disabled={busy} /></label>
          <span class="save-state" role="status">{saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'All changes saved'}</span>
          <button class="button" disabled={saving || conflict || !dirty} onclick={() => save()}>Save now</button>
          <button class="button primary" disabled={busy || saving || conflict} onclick={() => act(exportPDF)}>Export PDF and register</button>
        </div>
        {#if conflict}<div class="message error doc-actions"><span>This resume changed in another window.</span><button class="button" onclick={downloadDraft}>Download preserved draft</button><button class="button" disabled={busy} onclick={() => act(async () => { if (!doc) return; const result = await workshop.get(doc.id); adopt(result.document); revisions = result.revisions; await compile(); })}>Discard draft and reload server version</button></div>{/if}
        <div class="editor-preview">
          <div class="pane"><label for="resume-source">Resume source</label><small>{doc.entrypoint} · {doc.format} ({doc.engine}) · revision {doc.revision} · {source.split('\n').length} lines</small><textarea id="resume-source" class="source" spellcheck="false" bind:value={source} oninput={changed} disabled={busy}></textarea></div>
          <section class="preview pane" aria-label="Compiled preview">
            <h2>PDF preview</h2>
            {#if compiling}<p class="hint" role="status">Compiling saved revision…</p>{/if}
            {#if compileError}<pre class="compile-error" role="alert">{compileError}</pre>{:else if stale}<p class="stale">Stale preview — last successful revision {compiled?.revision}; current edits are not shown.</p>{/if}
            {#each images as image, i}<img src={image} alt={`Resume PDF page ${i+1}`} />{:else}<p class="hint">No successful preview yet. Source editing remains available offline.</p>{/each}
            {#if atsResult?.report}
              {@const counts = atsResult.report.counts}
              <section class="ats" aria-label="ATS check">
                <h3>ATS check <small>revision {atsResult.revision} · runs on every compile · {counts.pass} passed · {counts.warn} to review{counts.fail ? ` · ${counts.fail} failed` : ''}</small></h3>
                <ul class="findings">
                  {#each atsResult.report.findings as finding}
                    <li class={finding.level}><span class="mark" aria-label={finding.level}>{finding.level === 'pass' ? '✓' : finding.level === 'warn' ? '!' : '×'}</span><div><strong>{finding.title}</strong><small>{finding.detail}</small></div></li>
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
            {/if}
            {#if compiled}
              <details>
                <summary>Can software read this PDF? (revision {compiled.revision})</summary>
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
          </section>
        </div>
        <div class="doc-actions">
          <button class="button" disabled={busy || saving || dirty || compiling} onclick={() => act(compile)}>Compile saved revision</button>
          <button class="button" disabled={busy || dirty} onclick={() => act(() => downloadApi(`/workshop/${doc!.id}/source?revision=${doc!.revision}`,`${title}.zip`))}>Download source archive</button>
          <button class="button" onclick={downloadDraft}>Download draft backup</button>
        </div>

        <details class="section" open={pendingProposals > 0}>
          <summary>Tailor with Rika{pendingProposals ? ` — ${pendingProposals} proposal${pendingProposals === 1 ? '' : 's'} to review` : ''}</summary>
          <p class="hint">Rika proposes exact edits backed by your confirmed facts. You approve each change; nothing is applied, exported or sent to an employer automatically.</p>
          <div class="fields"><label>Company<input bind:value={company} oninput={() => { research = null; preview = null; }} /></label><label>Role<input bind:value={role} oninput={() => { research = null; preview = null; }} /></label><label>Linked job<select bind:value={jobId} onchange={selectJob}><option value="">No linked job</option>{#each batches.jobs as job}<option value={String(job.id)}>{job.company} — {job.title}</option>{/each}</select></label></div>
          <button class="button" disabled={busy || saving} onclick={() => act(async () => { if (!await save() || dirty) return; const result = await api<{document:WorkshopDoc}>(`/workshop/${doc!.id}/copy`,{title:`${title} — ${company || role || 'variant'}`.slice(0,200),company,role,job_id:jobId ? Number(jobId) : undefined}); await refresh(); await open(result.document.id); })}>Create linked variant</button>
          {#if !settings.enabled}<p class="message">Cloud help is off. Open <strong>Rika settings and confirmed facts</strong> below to opt in; manual editing never needs it.</p>{/if}
          <label class="check"><input type="checkbox" bind:checked={researchEnabled} onchange={() => preview = null} /> Include public company and role research</label>
          {#if researchEnabled}
            <label>Public company / posting URLs (one per line)<textarea bind:value={urls}></textarea></label>
            <button class="button" disabled={busy || !settings.enabled || !company} onclick={() => act(fetchResearch)}>Fetch cited public research</button>
            {#if research}<div class="panel"><h3>{research.company}: {research.status}</h3>{#each research.findings as finding}<p>{finding.text} <a href={finding.url} target="_blank" rel="noreferrer">Source</a></p>{/each}{#each research.sources as citation}<p class="hint"><a href={citation.url} target="_blank" rel="noreferrer">{citation.title || citation.url}</a> — {citation.status}</p>{/each}{#each research.warnings as warning}<p class="hint">{warning}</p>{/each}</div>{/if}
          {/if}
          <label>Edit instruction<textarea bind:value={instruction} oninput={() => preview = null} placeholder="e.g. Emphasize my verification work for this role, using only confirmed facts"></textarea></label>
          <button class="button primary" disabled={busy || !settings.enabled || !instruction.trim() || conflict} onclick={() => act(prepare)}>Preview redacted request and maximum cost</button>
          {#if preview}<div class="panel"><h3>Review exact outgoing payload</h3><p class="hint">Model: {preview.model_id} · Maximum cost: ${preview.maximum_cost_usd} · Expires: {preview.expires_at}</p><pre>{typeof preview.redacted_payload === 'string' ? preview.redacted_payload : JSON.stringify(preview.redacted_payload,null,2)}</pre><button class="button primary" disabled={busy || dirty} onclick={() => act(runAI)}>Confirm and send this payload</button></div>{/if}
          {#each proposals as proposal}<article class="panel"><h3>{proposal.summary || 'Proposed edits'} — {proposal.status}</h3><p class="hint">Based on revision {proposal.base_revision}</p>{#each proposal.warnings || [] as warning}<p class="message">{warning}</p>{/each}{#each proposal.hunks as hunk}<label class="check"><input type="checkbox" value={hunk.id} bind:group={accepted} disabled={proposal.status !== 'pending'} /> Approve this exact edit</label><div class="diff"><div><strong>Before</strong><pre>{hunk.find}</pre></div><div><strong>After</strong><pre>{hunk.replace}</pre></div></div><p>{hunk.reason}</p><p class="hint">Fact IDs: {hunk.fact_ids.join(', ')}{(hunk.flags || []).length ? ` · Check: ${hunk.flags.join(', ')}` : ''}</p>{/each}{#if proposal.status === 'pending'}<div class="doc-actions"><button class="button primary" disabled={busy || dirty || !proposal.hunks.some(h=>accepted.includes(h.id))} onclick={() => act(() => review(proposal))}>Apply selected exact edits</button><button class="button" disabled={busy || dirty} onclick={() => act(() => review(proposal,true))}>Reject proposal</button></div>{/if}</article>{/each}
        </details>
      {:else}
        <div class="empty"><h2>Pick a resume or start one</h2><p>Choose a template on the left or import an existing LaTeX, Typst, HTML, PDF or DOCX resume. Manual editing works offline, with no account or AI key.</p></div>
      {/if}

      <details class="section">
        <summary>Rika settings and confirmed facts ({facts.filter(f => f.confirmed).length} confirmed)</summary>
        <p class="hint">Hosted editing sends the payload you preview through OpenRouter to your selected model provider. Contact redaction does not anonymize work history. Public research sends company/role names and public URLs, never resume text.</p>
        <label class="check"><input type="checkbox" bind:checked={settings.enabled} /> I opt in to external research and hosted model requests</label>
        <div class="fields"><label>Model ID<input bind:value={settings.model_id} /></label><label>Maximum model request cost (USD)<input inputmode="decimal" bind:value={settings.max_cost_usd} /></label></div>
        <button class="button" disabled={busy} onclick={() => act(saveSettings)}>Confirm privacy settings</button>
        <h3>Your confirmed facts</h3>
        <p class="hint">Rika may only use facts you confirm here. A job posting asking for a skill never counts as having it.</p>
        {#each facts as fact}<p class="fact"><span><strong>{fact.confirmed ? 'Confirmed' : 'Unconfirmed'}:</strong> {fact.text} <small>{fact.source}</small></span><button class="button" onclick={() => { editingFact = fact; factText = fact.text; factSource = fact.source; factConfirmed = fact.confirmed; }}>Edit fact</button></p>{/each}
        <label>Fact text<textarea bind:value={factText}></textarea></label>
        <label>Fact evidence source<input bind:value={factSource} /></label>
        <label class="check"><input type="checkbox" bind:checked={factConfirmed} /> I confirm this fact is true</label>
        <div class="doc-actions"><button class="button" disabled={busy || !factText.trim()} onclick={() => act(saveFact)}>{editingFact ? 'Update fact' : 'Add fact'}</button>{#if editingFact}<button class="button" onclick={() => { editingFact = null; factText = ''; factSource = ''; factConfirmed = false; }}>Cancel fact edit</button>{/if}</div>
        {#if preferences.length}<details><summary>What Rika learned from your reviews</summary><pre>{JSON.stringify(preferences,null,2)}</pre></details>{/if}
      </details>

      <details class="section">
        <summary>Many resumes at once{batches.batches.length ? ` (${batches.batches.length} batch${batches.batches.length === 1 ? '' : 'es'})` : ''}</summary>
        <p class="hint">Choose jobs and Rika drafts one tailored copy of the open master for each. Creating a batch makes no model calls; running it sends contact-redacted source, confirmed facts and each posting within your cost limit. Every draft still needs your review.</p>
        <div class="job-list">{#each batches.jobs as job}<label class="check"><input type="checkbox" value={job.id} bind:group={selectedJobs} />{job.company} — {job.title}</label>{:else}<p class="hint">No ApplyAI jobs yet.</p>{/each}</div>
        <div class="fields"><label>Maximum total batch cost (USD)<input bind:value={batchCap} inputmode="decimal" /></label></div>
        <p class="hint">Public research: {researchEnabled ? 'included' : 'off'} · Master: {doc?.title || 'open a master first'}{doc ? `, revision ${doc.revision}` : ''}</p>
        <div class="doc-actions"><button class="button primary" disabled={busy || !doc || doc.kind !== 'master' || !selectedJobs.length} onclick={() => act(createBatch)}>Create capped batch</button><button class="button" disabled={busy} onclick={() => act(async () => { batches = await workshop.batches(); })}>Refresh batches and outcomes</button></div>
        {#each batches.batches as batch}
          <article class="panel">
            <h3>Batch — {batch.status}</h3>
            <p class="hint">Master revision {batch.master_revision} · Limit ${batch.max_cost_usd} · Reserved maximum ${batch.reserved_usd} · Research {batch.research ? 'on' : 'off'}. Reservations are upper bounds, not charges; interrupted requests are never retried.</p>
            <div class="doc-actions">
              <button class="button primary" disabled={busy || !settings.enabled || batch.status !== 'queued' || activeBatches.includes(batch.id)} onclick={() => act(() => runBatch(batch.id))}>Confirm and run capped batch</button>
              <button class="button" disabled={['cancelled','done'].includes(batch.status)} onclick={() => act(async () => { await api(`/workshop-batches/${batch.id}/cancel`,{confirm:true}); batches = await workshop.batches(); })}>Cancel remaining work</button>
            </div>
            {#each batch.items as item}<p class="fact"><span>{item.company} — {item.role}: <strong>{item.status}</strong> {item.error || ''}</span>{#if item.doc_id}<button class="button" disabled={busy} onclick={() => act(() => open(item.doc_id!))}>Open result and review</button>{/if}</p>{/each}
          </article>
        {/each}
      </details>

      <details class="section">
        <summary>ApplyAI outcomes ({batches.insights.resumes.length} exported version{batches.insights.resumes.length === 1 ? '' : 's'})</summary>
        <p class="hint">{batches.insights.notice}</p>
        {#if batches.insights.resumes.length}
          <table><thead><tr><th>Resume version</th><th>Applications</th><th>Responses</th><th>Interview or later</th><th>Offers</th></tr></thead>
            <tbody>{#each batches.insights.resumes as row}<tr><td>{row.title} <small>revision {row.revision}</small></td><td>{row.applications}</td><td>{row.responses}</td><td>{row.advanced}</td><td>{row.outcomes.offer ?? 0}</td></tr>{/each}</tbody></table>
        {:else}<p class="hint">Export a resume and use it in ApplyAI to see outcomes here.</p>{/if}
      </details>

      {#if doc}
        <details class="section">
          <summary>Files and history ({revisions.length} revisions)</summary>
          <label>Add or replace project files<input type="file" multiple onchange={event => importFiles(event,true)} /></label>
          {#each Object.keys(assets) as name}<p class="fact"><span>{name}</span><span class="doc-actions"><button class="button" onclick={() => chooseAsset(name)}>Edit</button><button class="button" aria-label={`Remove asset ${name}`} onclick={() => { const next = {...assets}; delete next[name]; assets = next; selectedAsset = ''; changed(); }}>Remove</button></span></p>{/each}
          {#if selectedAsset}<label>{selectedAsset}<textarea class="asset" bind:value={assetText} oninput={() => { assets = {...assets,[selectedAsset]:encodeText(assetText)}; changed(); }}></textarea></label>{/if}
          {#each revisions as revision}<p class="fact"><span>Revision {revision.revision} · {revision.author} · {revision.note}</span><button class="button" disabled={dirty || busy || saving} onclick={() => act(() => restore(revision.revision))}>Restore revision {revision.revision}</button></p>{/each}
          <button class="button" disabled={dirty || busy} onclick={() => act(async () => { await api(`/workshop/${doc!.id}/archive`,{confirm:true}); doc = null; ++compileToken; images.forEach(URL.revokeObjectURL); images = []; await refresh(); })}>Archive this document</button>
        </details>
      {/if}
    </main>
  </div>
</section>
<style>
  .workshop { padding: 20px 24px 40px; color: var(--ink); height: 100%; overflow: auto; }
  .page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 14px; }
  h1 { font-family: var(--font-title, var(--font-ui)); font-size: var(--fs-title); margin: 0; }
  h2 { font-size: var(--fs-heading); margin: 18px 0 8px; }
  h3 { font-size: var(--fs-subheading); margin: 12px 0 6px; }
  .lede { color: var(--muted); max-width: 70ch; margin: 4px 0 0; }
  .layout { display: grid; grid-template-columns: minmax(200px, 250px) minmax(0, 1fr); gap: 22px; }
  aside, main { min-width: 0; }
  aside h2:first-child { margin-top: 0; }
  .document { display: block; width: 100%; text-align: left; margin: 4px 0; padding: 8px 10px; border-radius: var(--r-item); background: transparent; color: var(--ink); border: 1px solid transparent; }
  .document:hover:not(:disabled) { background: var(--wash); }
  .document[aria-pressed=true] { border-color: var(--accent); background: var(--wash); }
  .document small { display: block; color: var(--muted); }
  .panel { border: 1px solid var(--border); border-radius: var(--r-panel); background: var(--surface); padding: 12px 14px; margin: 12px 0; }
  .panel h3:first-child { margin-top: 0; }
  label { display: block; margin: 8px 0; font-size: var(--fs-label); color: var(--muted); }
  label.check { display: flex; align-items: center; gap: 8px; color: var(--ink); font-size: var(--fs-control); }
  /* Colors, borders and heights come from the app theme (styles.css); only layout here. */
  input:not([type=checkbox]):not([type=file]), select, textarea { width: 100%; margin-top: 4px; font-size: var(--fs-control); }
  textarea { padding: 8px 10px; border-radius: var(--r-field); }
  input[type=file] { display: block; max-width: 100%; margin-top: 6px; color: var(--muted); }
  .wide { width: 100%; margin-top: 4px; }
  .hint { color: var(--muted); font-size: var(--fs-label); margin: 6px 0; }
  .message { border-left: 3px solid var(--accent); background: var(--wash); padding: 8px 12px; border-radius: var(--r-chip); white-space: pre-wrap; }
  .message.error { border-left-color: var(--error); }
  .doc-bar { display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
  .title-field { flex: 1; min-width: 220px; margin: 0; }
  .save-state { color: var(--muted); font-size: var(--fs-label); padding-bottom: 7px; }
  .doc-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 10px 0; }
  .editor-preview { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
  .pane { min-width: 0; }
  .pane > small { display: block; color: var(--muted); margin-bottom: 4px; }
  .source { height: 600px; tab-size: 2; font-family: var(--font-mono); font-size: 12.5px; resize: vertical; white-space: pre; }
  .asset { min-height: 180px; font-family: var(--font-mono); }
  .preview { max-height: 720px; overflow: auto; }
  .preview h2 { margin-top: 0; }
  .preview img { display: block; width: 100%; height: auto; margin-bottom: 12px; background: white; border-radius: 4px; box-shadow: 0 2px 14px color-mix(in srgb, var(--shadow-tint, #000) 30%, transparent); }
  .stale { border-left: 3px solid var(--warning); padding: 8px 12px; background: var(--wash); border-radius: var(--r-chip); }
  .compile-error { border-left: 3px solid var(--error); background: color-mix(in srgb, var(--error) 10%, transparent); font-family: var(--font-mono); font-size: 12px; }
  .checks { margin: 8px 0; padding-left: 18px; } .checks li { margin: 4px 0; }
  .ats { margin-top: 14px; padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--r-field); background: var(--surface); }
  .ats h3 { margin: 0 0 8px; font-size: var(--fs-control); } .ats h3 small { display: block; color: var(--muted); font-weight: normal; margin-top: 2px; }
  .findings { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .findings li { display: flex; gap: 10px; align-items: flex-start; }
  .findings li small { display: block; color: var(--muted); }
  .mark { flex: none; width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; font-size: 12px; color: var(--canvas); }
  .pass .mark { background: var(--success); } .warn .mark { background: var(--warning); } .fail .mark { background: var(--error); }
  .parsed { display: grid; grid-template-columns: max-content 1fr; gap: 3px 12px; margin: 12px 0 8px; font-size: var(--fs-label); }
  .parsed dt { color: var(--muted); } .parsed dd { margin: 0; overflow-wrap: anywhere; }
  .terms { border-top: 1px solid var(--border); padding-top: 8px; margin-top: 8px; font-size: var(--fs-label); }
  .terms p { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin: 6px 0; }
  .terms .label { color: var(--muted); margin-right: 4px; }
  .chip { border-radius: 999px; padding: 1px 8px; border: 1px solid var(--border); }
  .chip.found { border-color: var(--success); } .chip.missing { border-color: var(--warning); }
  details { margin-top: 10px; }
  details.section { border: 1px solid var(--border); border-radius: var(--r-panel); padding: 10px 14px; margin-top: 14px; background: var(--surface); }
  summary { cursor: pointer; font-weight: 600; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; background: var(--chrome); padding: 10px; border-radius: var(--r-field); max-height: 24rem; overflow: auto; }
  .fields { display: flex; gap: 10px; flex-wrap: wrap; } .fields > label { flex: 1; min-width: 160px; }
  .fact { display: flex; justify-content: space-between; align-items: center; gap: 10px; border-top: 1px solid var(--border); padding-top: 8px; }
  .fact small { color: var(--muted); }
  .job-list { max-height: 240px; overflow: auto; }
  .diff { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; } .diff > div { min-width: 0; }
  .empty { border: 1px dashed var(--border); border-radius: var(--r-panel); padding: 28px; text-align: center; color: var(--muted); }
  .empty h2 { color: var(--ink); margin-top: 0; }
  table { width: 100%; border-collapse: collapse; font-size: var(--fs-control); }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: var(--fs-label); }
  td small { color: var(--muted); }
  @media (max-width: 1100px) { .editor-preview { grid-template-columns: 1fr; } .source { height: 380px; } }
  @media (max-width: 720px) { .layout { grid-template-columns: 1fr; } .diff { grid-template-columns: 1fr; } .workshop { padding: 12px; } .page-head { flex-direction: column; } }
</style>
