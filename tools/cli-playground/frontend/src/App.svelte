<script lang="ts">
  import { onMount } from 'svelte';
  import {
    EnvironmentError, environmentIdFromPath, loadEnvironment, runnable,
    type Environment, type RunnableEnvironment,
  } from './environment';
  import { examplesFor, files, shellExamples, type Example } from './examples';
  import { createBrowserTerminal, type BrowserTerminal } from './terminal/browser';
  import type { SessionState } from './terminal/session';

  let environment = $state<RunnableEnvironment | null>(null);
  let latest = $state<Environment | null>(null);
  let selectedCli = $state('');
  let failure = $state<EnvironmentError | null>(null);
  let loading = $state(true);
  let controller = $state.raw<BrowserTerminal | null>(null);
  let session = $state<SessionState>({ phase: 'connecting', label: 'Connecting', message: null, canReconnect: false });
  let request: AbortController | undefined;
  let pollRequest: AbortController | undefined;
  const environmentId = environmentIdFromPath(window.location.pathname);
  const connected = $derived(session.phase === 'connected');
  const examples = $derived(selectedCli ? examplesFor(selectedCli) : []);
  const updateAvailable = $derived(
    latest !== null && runnable(latest) && environment !== null && latest.revision !== environment.revision,
  );
  const buildTitle = $derived.by(() => {
    switch (latest?.build?.state) {
      case 'building': return 'This preview is building.';
      case 'failed': return 'The latest preview failed.';
      case 'empty': return 'No affected CLIs.';
      case 'cancelled': return 'The latest build was cancelled.';
      default: return 'No runnable build is available.';
    }
  });

  function selectEnvironment(result: Environment) {
    environment = runnable(result) ? result : null;
    if (!result.clis.includes(selectedCli)) selectedCli = result.clis[0] ?? '';
  }

  async function load() {
    request?.abort();
    pollRequest?.abort();
    const current = new AbortController();
    request = current;
    loading = true;
    failure = null;
    try {
      const result = await loadEnvironment(environmentId, current.signal);
      if (current.signal.aborted) return;
      latest = result;
      selectEnvironment(result);
    } catch (error) {
      if (current.signal.aborted) return;
      failure = error instanceof EnvironmentError ? error : new EnvironmentError(
        "Couldn't open the playground.",
        'Check your connection and try again. If the issue continues, open a newer preview from the pull request.',
        true,
      );
    } finally {
      if (!current.signal.aborted) loading = false;
    }
  }

  async function checkForUpdate() {
    if (loading || failure || pollRequest || !latest) return;
    const current = new AbortController();
    pollRequest = current;
    try {
      const result = await loadEnvironment(environmentId, current.signal);
      if (current.signal.aborted) return;
      latest = result;
      // Polling never replaces a terminal that already owns a workspace.
      if (!environment && runnable(result)) selectEnvironment(result);
    } catch {
      // A transient metadata failure must not interrupt a working terminal.
    } finally {
      if (pollRequest === current) pollRequest = undefined;
    }
  }

  function terminalAction(node: HTMLDivElement) {
    if (!environmentId || !environment) return;
    let terminal: BrowserTerminal;
    try {
      terminal = createBrowserTerminal(node, environmentId, environment.revision, (next) => { session = next; });
      controller = terminal;
      terminal.connect();
    } catch {
      failure = new EnvironmentError(
        "The terminal couldn't load.",
        'A required terminal feature is unavailable. Try again to reopen the playground.',
        true,
      );
      return;
    }
    return {
      destroy() {
        terminal.dispose();
        if (controller === terminal) controller = null;
      },
    };
  }

  onMount(() => {
    void load();
    const interval = window.setInterval(() => void checkForUpdate(), 10_000);
    return () => {
      request?.abort();
      pollRequest?.abort();
      window.clearInterval(interval);
    };
  });
</script>

<svelte:head>
  <title>{selectedCli ? `${selectedCli} / CLI playground` : 'CLI playground'}</title>
</svelte:head>

{#snippet commandButton(example: Example)}
  <button type="button" class="command-button" disabled={!connected}
    aria-label={`Insert ${example.display}: ${example.label}`}
    onclick={() => controller?.paste(example.command)}>
    <span class="command-description"><span>{example.label}</span><span aria-hidden="true">↗</span></span>
    <code>{example.display}</code>
  </button>
{/snippet}

{#if environment && !failure && !loading}
  <a class="skip-link" href="#terminal">Skip to terminal</a>
{:else}
  <a class="skip-link" href="#main">Skip to content</a>
{/if}
<header class="masthead">
  <div class="wordmark" aria-label="CLIs playground">
    <span class="brand">clis<span aria-hidden="true">_</span></span>
    <span class="masthead-divider" aria-hidden="true">/</span><span>playground</span>
  </div>
</header>

<main id="main" tabindex="-1">
  {#if loading}
    <section id="loading-view" class="state-view" aria-live="polite" aria-busy="true">
      <span class="eyebrow">ONE MOMENT</span>
      <h1>Opening your playground<span class="loading-dot" aria-hidden="true">.</span></h1>
      <p>Getting this build ready.</p>
    </section>
  {:else if failure}
    <section id="error-view" class="state-view" role="alert">
      <span class="eyebrow">PLAYGROUND UNAVAILABLE</span>
      <h1 id="error-title">{failure.title}</h1>
      <p id="error-message">{failure.message}</p>
      {#if failure.retryable}
        <button id="retry-button" class="button button-primary" type="button" onclick={() => void load()}>
          Try again <span aria-hidden="true">↗</span>
        </button>
      {/if}
    </section>
  {:else if latest && !environment}
    <section id="preview-state" class="state-view" aria-live="polite">
      <span class="eyebrow">PULL REQUEST #{latest.pr}</span>
      <h1>{buildTitle}</h1>
      {#if latest.build}
        <p>Commit <code>{latest.build.sha.slice(0, 7)}</code>.
          {#if latest.build.state === 'building'}This page will open a terminal when the build is ready.
          {:else if latest.build.state === 'empty'}This revision does not include CLI changes to try.
          {:else}Check the build details on the pull request. This page checks for updates automatically.{/if}
        </p>
      {/if}
      <button type="button" class="button button-primary" onclick={() => void load()}>Refresh status</button>
    </section>
  {:else if environment}
    <div id="playground-view" class="playground">
      <section class="intro" aria-labelledby="cli-name">
        <div class="intro-title">
          {#if environment.clis.length > 1}
            <h1 id="cli-name" class="visually-hidden">{selectedCli}</h1>
            <div class="cli-picker">
              <span class="prompt-mark" aria-hidden="true">&gt;</span>
              <select aria-label="CLI" title="Choose starter commands — all CLIs share this workspace"
                bind:value={selectedCli}>
                {#each environment.clis as cli (cli)}
                  <option value={cli}>{cli}</option>
                {/each}
              </select>
              <span class="cli-count">{environment.clis.length} CLIs</span>
            </div>
          {:else}
            <h1><span class="prompt-mark" aria-hidden="true">&gt;</span> <span id="cli-name">{selectedCli}</span><span class="title-cursor" aria-hidden="true">_</span></h1>
          {/if}
        </div>
        <dl class="build-meta" aria-label="Build information">
          <div><dt>Pull request</dt><dd id="meta-pr">#{environment.pr}</dd></div>
          <div><dt>Commit</dt><dd><abbr id="meta-sha" title={environment.sha}>{environment.sha.slice(0, 7)}</abbr></dd></div>
          <div><dt>Link expires</dt><dd><time id="meta-expiry" datetime={environment.expiresAt.toISOString()} title={environment.expiresAt.toLocaleString()}>{environment.expiresAt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</time></dd></div>
          {#if session.absoluteDeadline}<div><dt>Session expires</dt><dd><time id="session-expiry" datetime={session.absoluteDeadline}>{new Date(session.absoluteDeadline).toLocaleString()}</time></dd></div>{/if}
        </dl>
      </section>

      {#if latest?.build && latest.build.state !== 'ready' || updateAvailable}
        <div id="build-banner" class="build-banner" role="status">
          <p>
            {#if latest?.build?.state === 'building'}
              Building commit <code>{latest.build.sha.slice(0, 7)}</code>.
              {#if latest.sha}The last successful build is <code>{latest.sha.slice(0, 7)}</code>.{/if}
              Your terminal remains on <code>{environment.sha.slice(0, 7)}</code>.
            {:else if latest?.build?.state === 'failed'}
              Latest preview failed for <code>{latest.build.sha.slice(0, 7)}</code>.
              {#if latest.sha}The last successful build is <code>{latest.sha.slice(0, 7)}</code>.{/if}
              Your terminal remains on <code>{environment.sha.slice(0, 7)}</code>.
            {:else if latest?.build?.state === 'cancelled'}
              Latest build was cancelled for <code>{latest.build.sha.slice(0, 7)}</code>.
              {#if latest.sha}The last successful build is <code>{latest.sha.slice(0, 7)}</code>.{/if}
              Your terminal remains on <code>{environment.sha.slice(0, 7)}</code>.
            {:else if latest?.build?.state === 'empty'}
              No affected CLIs in commit <code>{latest.build.sha.slice(0, 7)}</code>.
              Your existing session remains on <code>{environment.sha.slice(0, 7)}</code>.
            {:else if updateAvailable && latest?.sha}
              A new build is ready for <code>{latest.sha.slice(0, 7)}</code>.
              Your current terminal and files are unchanged.
            {/if}
          </p>
          {#if updateAvailable}
            <button type="button" class="banner-button" onclick={() => void load()}
              title="Discard this workspace and start a new terminal with the latest runnable build">
              Switch to latest build
            </button>
          {/if}
        </div>
      {/if}

      <div class="workbench">
        <aside class="sidebar" aria-label="Workspace and command examples">
          <section class="sidebar-section file-section">
            <div class="section-heading"><h2>Workspace</h2><span class="section-index">01</span></div>
            <p class="directory-label"><span aria-hidden="true">↳</span> /workspace</p>
            <ul id="file-list" class="file-list" aria-label="Sample files">
              {#each files as file (file)}
                <li><button type="button" class="file-button" disabled={!connected}
                  aria-label={`Insert command to read ${file}`} onclick={() => controller?.paste(`cat ${file}`)}>
                  <span>{file}</span><span class="file-arrow" aria-hidden="true">↗</span>
                </button></li>
              {/each}
            </ul>
            <p class="section-note">Click a file to insert a command to read it.</p>
          </section>
          <section class="sidebar-section example-section">
            <div class="section-heading"><h2>Try a command</h2><span class="section-index">02</span></div>
            <div id="cli-examples" class="examples">
              {#each examples as example (example.command)}{@render commandButton(example)}{/each}
            </div>
            {#if selectedCli === 'kara'}
              <p id="cli-note" class="section-note">i to edit · Esc to navigate · Ctrl Q to quit.</p>
            {/if}
            <details class="shell-details">
              <summary>Shell essentials <span aria-hidden="true">+</span></summary>
              <div id="shell-examples" class="examples">
                {#each shellExamples as example (example.command)}{@render commandButton(example)}{/each}
              </div>
            </details>
            <p class="section-note">Inserted at your cursor.<br />Press <kbd>Enter</kbd> when you're ready.</p>
          </section>
        </aside>

        <section class="terminal-panel" aria-labelledby="terminal-heading">
          <div class="terminal-toolbar">
            <div class="terminal-title"><span class="terminal-icon" aria-hidden="true">&gt;_</span><h2 id="terminal-heading">Terminal</h2><span class="shell-label">bash</span></div>
            <div class="terminal-actions">
              <button id="disconnect-button" class="toolbar-button" type="button" disabled={!connected} onclick={() => controller?.disconnect()}>Disconnect</button>
              <button id="reset-button" class="toolbar-button reset-button" type="button" disabled={session.phase === 'connecting'}
                title="Discard this session and open a fresh workspace" onclick={() => void load()}>
                <span aria-hidden="true">↻</span> Reset session
              </button>
            </div>
          </div>
          {#if session.message}
            <div id="session-banner" class="session-banner" role="status">
              <p id="session-message">{session.message}</p>
              {#if session.canReconnect}
                <button id="reconnect-button" type="button" class="banner-button" onclick={() => void load()}>Start new session <span aria-hidden="true">↗</span></button>
              {/if}
            </div>
          {/if}
          <div class="terminal-stage">
            <div id="terminal" role="region" aria-label="Interactive Bash terminal" tabindex="-1" use:terminalAction></div>
          </div>
          <div class="terminal-statusbar">
            <div id="connection-status" class="connection-status" role="status" aria-live="polite" data-state={session.phase}>
              <span class="status-dot" aria-hidden="true"></span><span id="status-label">{session.label}</span>
            </div>
            <div class="keyboard-hints"><span><kbd>↑</kbd> history</span><span><kbd>Ctrl C</kbd> stop</span><span><kbd>Tab</kbd> complete</span></div>
          </div>
        </section>
      </div>
      <footer class="page-footer"><p><span aria-hidden="true">◇</span> Your session is temporary. Reset restores the sample files.</p><span>60 MIN / SESSION</span></footer>
    </div>
  {/if}
</main>
