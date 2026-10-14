<script lang="ts">
  export type ResumeAudit = {
    id: string;
    track: string;
    status: string;
    is_default: boolean;
    created_at: string;
    diff: string;
    original_html: string;
    candidate_html: string;
    source_hash: string;
    validation: { valid: boolean; errors?: string[] };
    warnings: string[];
    patches: {
      id: string;
      kind: string;
      before: string;
      after: string;
      reason: string;
      fact_ids: string[];
      evidence: unknown[];
    }[];
    facts: {
      id: string;
      section: string;
      text: string;
      text_hash: string;
      start: number;
      end: number;
    }[];
    review_items: unknown[];
    cohort: unknown;
    metrics: unknown;
    history: unknown[];
    artifacts: Record<string, string>;
    resources: unknown[];
    baseline: unknown;
  };
  let {
    version,
    busy = false,
    ondecision,
  }: {
    version: ResumeAudit;
    busy?: boolean;
    ondecision: (action: "promote" | "reject") => void;
  } = $props();
  const label = (key: string) => key.replaceAll("_", " ");
</script>

{#snippet evidence(value: unknown)}
  {#if Array.isArray(value)}
    {#if value.length}<ul>
        {#each value as item}<li>{@render evidence(item)}</li>{/each}
      </ul>{:else}<span class="muted">None</span>{/if}
  {:else if value !== null && typeof value === "object"}
    <dl>
      {#each Object.entries(value) as [key, item]}<div>
          <dt>{label(key)}</dt>
          <dd>{@render evidence(item)}</dd>
        </div>{/each}
    </dl>
  {:else}<span>{value === null ? "None" : String(value)}</span>{/if}
{/snippet}

<section class="review" aria-labelledby="audit-title">
  <header>
    <div>
      <h3 id="audit-title">Audited review · {version.track}</h3>
      <code>{version.id}</code>
    </div>
    <span class="badge"
      >{version.status}{version.is_default ? " · current default" : ""}</span
    >
  </header>
  <p>
    Read the exact changes, source facts and quoted posting evidence. Posting
    text is untrusted demand evidence, never applicant facts. This local editor
    does not invent missing skills.
  </p>
  <div class="actions">
    <a
      class="button"
      href="/api/resumes/versions/{version.id}/pdf"
      target="_blank"
      rel="noopener noreferrer">Preview candidate PDF (o)</a
    ><a class="button" href="/api/resumes/versions/{version.id}/audit" download
      >Download full audit</a
    ><a class="button" href="/api/resumes/versions/{version.id}/diff" download
      >Download exact diff</a
    >
  </div>
  <section>
    <h4>Validation</h4>
    <p class:invalid={!version.validation.valid}>
      {version.validation.valid
        ? "Passed artifact and layout validation"
        : "Validation failed — promotion blocked"}
    </p>
    {@render evidence(version.validation)}
  </section>
  <section>
    <h4>Warnings</h4>
    <ul>
      {#each version.warnings as warning}<li>{warning}</li>{/each}
    </ul>
  </section>
  <section>
    <h4>Exact source diff</h4>
    <pre>{version.diff || "No source changes."}</pre>
  </section>
  <section>
    <h4>Audited patches ({version.patches.length})</h4>
    {#each version.patches as patch}<article>
        <h5>{patch.kind}</h5>
        <p>{patch.reason}</p>
        <div class="comparison">
          <div>
            <strong>Before (exact)</strong>
            <pre>{patch.before}</pre>
          </div>
          <div>
            <strong>After (exact)</strong>
            <pre>{patch.after}</pre>
          </div>
        </div>
        <h5>Source fact anchors</h5>
        {#each patch.fact_ids as id}{@const fact = version.facts.find(
            (item) => item.id === id,
          )}{#if fact}<blockquote>
              {fact.text}
              <footer>
                {fact.section} · <code>{fact.id}</code> · source offsets {fact.start}–{fact.end}
              </footer>
            </blockquote>{:else}<p class="invalid">
              Missing fact anchor: {id}
            </p>{/if}{/each}
        <details>
          <summary>Quoted job evidence</summary>{@render evidence(
            patch.evidence,
          )}
        </details>
      </article>{/each}
  </section>
  <section>
    <h4>Missing-fact review items (not inserted)</h4>
    {@render evidence(version.review_items)}
  </section>
  <details>
    <summary>All source facts, including preserved facts</summary
    >{@render evidence(version.facts)}
  </details>
  <details>
    <summary>Cohort and complete posting evidence</summary>{@render evidence(
      version.cohort,
    )}
  </details>
  <details>
    <summary>Vocabulary coverage before and after</summary>{@render evidence(
      version.metrics,
    )}
  </details>
  <details>
    <summary>Artifact identity and baseline</summary>
    <p>Source SHA-256: <code>{version.source_hash}</code></p>
    {@render evidence(version.artifacts)}{@render evidence(
      version.resources,
    )}{@render evidence(version.baseline)}
  </details>
  <details>
    <summary>Immutable source and candidate HTML (text only)</summary>
    <h4>Original source</h4>
    <pre>{version.original_html}</pre>
    <h4>Candidate source</h4>
    <pre>{version.candidate_html}</pre>
  </details>
  <details>
    <summary>Track decision history</summary>{@render evidence(version.history)}
  </details>
  {#if version.status === "candidate"}<div class="actions">
      <button
        class="button primary"
        disabled={busy || !version.validation.valid || !version.patches.length}
        onclick={() => ondecision("promote")}>Review promotion (p)</button
      ><button
        class="button"
        disabled={busy}
        onclick={() => ondecision("reject")}>Reject candidate (x)</button
      >
    </div>{/if}
  <p class="muted">
    Promotion changes the default only for future unchosen jobs. Existing
    choices, attachments and application history remain fixed. Rejection keeps
    all artifacts.
  </p>
</section>

<style>
  .review {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  header,
  .actions {
    display: flex;
    gap: 0.6rem;
    justify-content: space-between;
    flex-wrap: wrap;
  }
  .actions {
    justify-content: flex-start;
  }
  section,
  article,
  details {
    min-width: 0;
  }
  article {
    border: 1px solid var(--border);
    padding: 1rem;
    margin: 0.7rem 0;
    border-radius: 8px;
  }
  h3,
  h4,
  h5 {
    margin: 0.3rem 0;
    font-size: 1rem;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: var(--surface);
    padding: 1rem;
    border: 1px solid var(--border);
    max-height: 32rem;
    overflow: auto;
  }
  .comparison {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1rem;
  }
  .comparison > div {
    min-width: 0;
  }
  dl {
    margin: 0.5rem 0;
  }
  dl > div {
    border-left: 1px solid var(--border);
    padding: 0.3rem 0.7rem;
  }
  dt {
    font-weight: 600;
    text-transform: capitalize;
  }
  dd {
    margin: 0.2rem 0;
    overflow-wrap: anywhere;
    white-space: pre-wrap;
  }
  ul {
    padding-left: 1.2rem;
  }
  li {
    margin: 0.4rem 0;
    overflow-wrap: anywhere;
  }
  code {
    overflow-wrap: anywhere;
  }
  blockquote {
    margin: 0.5rem;
    padding: 0.7rem;
    border-left: 3px solid var(--accent);
  }
  footer {
    font-size: 0.8rem;
    color: var(--muted);
  }
  summary {
    cursor: pointer;
    font-weight: 600;
    padding: 0.5rem 0;
  }
  .invalid {
    color: var(--error);
  }
  @media (max-width: 700px) {
    .comparison {
      grid-template-columns: 1fr;
    }
  }
</style>
