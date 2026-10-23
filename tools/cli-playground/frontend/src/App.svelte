<script lang="ts">
  import { onMount, tick } from "svelte";
  import {
    EnvironmentError,
    environmentIdFromPath,
    loadEnvironment,
    runnable,
    type Environment,
    type RunnableEnvironment,
  } from "./environment";
  import Explorer from "./Explorer.svelte";
  import { loadCatalog, type Catalog } from "./catalog";
  import {
    createBrowserTerminal,
    type BrowserTerminal,
  } from "./terminal/browser";
  import type { SessionState } from "./terminal/session";

  let environment = $state<RunnableEnvironment | null>(null);
  let latest = $state<Environment | null>(null);
  let selectedCli = $state("");
  let failure = $state<EnvironmentError | null>(null);
  let loading = $state(true);
  let controller = $state.raw<BrowserTerminal | null>(null);
  let session = $state<SessionState>({
    phase: "connecting",
    label: "Connecting",
    message: null,
    canReconnect: false,
  });
  let catalog = $state<Catalog | null>(null);
  let catalogLoading = $state(false);
  let catalogRequest: AbortController | undefined;
  let drawerOpen = $state(false);
  let mobile = $state(window.matchMedia("(max-width: 799px)").matches);
  let request: AbortController | undefined;
  let pollRequest: AbortController | undefined;
  const environmentId = environmentIdFromPath(window.location.pathname);
  const connected = $derived(session.phase === "connected");

  const updateAvailable = $derived(
    latest !== null &&
      runnable(latest) &&
      environment !== null &&
      latest.revision !== environment.revision,
  );
  const buildTitle = $derived.by(() => {
    switch (latest?.build?.state) {
      case "building":
        return "This preview is building.";
      case "failed":
        return "The latest preview failed.";
      case "empty":
        return "No affected CLIs.";
      case "cancelled":
        return "The latest build was cancelled.";
      default:
        return "No runnable build is available.";
    }
  });

  function selectEnvironment(result: Environment) {
    environment = runnable(result) ? result : null;
    if (!result.clis.includes(selectedCli)) selectedCli = result.clis[0] ?? "";
  }

  async function load() {
    request?.abort();
    pollRequest?.abort();
    const current = new AbortController();
    request = current;
    loading = true;
    catalogRequest?.abort();
    catalog = null;
    failure = null;
    try {
      const result = await loadEnvironment(environmentId, current.signal);
      if (current.signal.aborted) return;
      latest = result;
      selectEnvironment(result);
      void refreshCatalog();
    } catch (error) {
      if (current.signal.aborted) return;
      failure =
        error instanceof EnvironmentError
          ? error
          : new EnvironmentError(
              "Couldn't open the playground.",
              "Check your connection and try again. If the issue continues, open a newer preview from the pull request.",
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
      if (!environment && runnable(result)) {
        selectEnvironment(result);
        void refreshCatalog();
      }
    } catch {
      // A transient metadata failure must not interrupt a working terminal.
    } finally {
      if (pollRequest === current) pollRequest = undefined;
    }
  }

  async function refreshCatalog() {
    catalogRequest?.abort();
    const active = environment;
    if (!active || !environmentId) return;
    const current = new AbortController();
    catalogRequest = current;
    catalogLoading = true;
    try {
      const result = await loadCatalog(
        environmentId,
        active.revision,
        active.sha,
        active.clis,
        current.signal,
      );
      if (!current.signal.aborted && environment === active) catalog = result;
    } catch {
      if (!current.signal.aborted) catalog = null;
    } finally {
      if (!current.signal.aborted) catalogLoading = false;
    }
  }

  async function insert(value: string) {
    if (!connected) return;
    const active = controller;
    drawerOpen = false;
    // Let the drawer release its focus boundary before focusing xterm.
    await tick();
    if (connected && controller === active) active?.paste(value);
  }

  async function closeDrawer() {
    drawerOpen = false;
    await tick();
    if (!drawerOpen) document.getElementById("explorer-button")?.focus();
  }

  function terminalAction(node: HTMLDivElement) {
    if (!environmentId || !environment) return;
    let terminal: BrowserTerminal;
    try {
      terminal = createBrowserTerminal(
        node,
        environmentId,
        environment.revision,
        (next) => {
          session = next;
        },
      );
      controller = terminal;
      terminal.startFresh();
    } catch {
      failure = new EnvironmentError(
        "The terminal couldn't load.",
        "A required terminal feature is unavailable. Try again to reopen the playground.",
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
    const media = window.matchMedia("(max-width: 799px)");
    const resize = () => {
      mobile = media.matches;
    };
    media.addEventListener("change", resize);
    const interval = window.setInterval(() => void checkForUpdate(), 10_000);
    return () => {
      media.removeEventListener("change", resize);
      catalogRequest?.abort();
      request?.abort();
      pollRequest?.abort();
      window.clearInterval(interval);
    };
  });
</script>

<svelte:head
  ><title
    >{selectedCli ? `${selectedCli} / cli-playground` : "cli-playground"}</title
  ></svelte:head
>
<svelte:window
  onkeydown={(event) => {
    if (event.key === "Escape" && drawerOpen) closeDrawer();
  }}
/>
<a
  class="skip-link"
  href={environment && !loading && !failure ? "#terminal" : "#main"}
  >Skip to {environment && !loading && !failure ? "terminal" : "content"}</a
>
<header class="masthead">
  <div class="wordmark">
    <span class="brand">Sanctuary<span aria-hidden="true">_</span></span><span
      class="masthead-divider">/</span
    ><span class="workspace-name">playground</span>
  </div>
  {#if environment}
    <div class="build-meta" aria-label="Build information">
      <a
        id="meta-pr"
        href={`https://github.com/di-rs/sanctuary/pull/${environment.pr}`}
        target="_blank"
        rel="noreferrer">PR #{environment.pr} ↗</a
      ><span class="meta-divider">/</span><abbr
        id="meta-sha"
        title={environment.sha}>{environment.sha.slice(0, 7)}</abbr
      ><span class="build-status">Build {latest?.build?.state ?? "ready"}</span
      ><span class="meta-expiry"
        >Link expires <time
          id="meta-expiry"
          datetime={environment.expiresAt.toISOString()}
          title={environment.expiresAt.toLocaleString()}
          >{environment.expiresAt.toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          })}</time
        ></span
      >
    </div>
  {/if}
</header>
<main id="main" tabindex="-1">
  {#if loading}
    <section
      id="loading-view"
      class="state-view"
      aria-live="polite"
      aria-busy="true"
    >
      <span class="eyebrow">PREPARING WORKSPACE</span>
      <h1>Opening your playground<span class="loading-dot">.</span></h1>
      <p>Getting this build ready.</p>
    </section>
  {:else if failure}
    <section id="error-view" class="state-view" role="alert">
      <span class="eyebrow">PLAYGROUND UNAVAILABLE</span>
      <h1 id="error-title">{failure.title}</h1>
      <p id="error-message">{failure.message}</p>
      {#if failure.retryable}<button
          id="retry-button"
          class="button button-primary"
          type="button"
          onclick={() => void load()}>Try again ↗</button
        >{/if}
    </section>
  {:else if latest && !environment}
    <section id="preview-state" class="state-view" aria-live="polite">
      <span class="eyebrow">PULL REQUEST #{latest.pr}</span>
      <h1>{buildTitle}</h1>
      {#if latest.build}<p>
          Commit <code>{latest.build.sha.slice(0, 7)}</code>. {#if latest.build.state === "building"}This
            page will open a terminal when the build is ready.{:else if latest.build.state === "empty"}This
            revision does not include CLI changes to try.{:else}Check the build
            details on the pull request. This page checks for updates
            automatically.{/if}
        </p>{/if}<button
        type="button"
        class="button"
        onclick={() => void load()}>Refresh status</button
      >
    </section>
  {:else if environment}
    <div id="playground-view" class="playground-shell">
      {#if (latest?.build && latest.build.state !== "ready") || updateAvailable}
        <div id="build-banner" class="build-banner" role="status">
          <p>
            {#if latest?.build?.state === "building"}Building commit <code
                >{latest.build.sha.slice(0, 7)}</code
              >.
            {:else if latest?.build?.state === "failed"}Latest preview failed
              for <code>{latest.build.sha.slice(0, 7)}</code>.
            {:else if latest?.build?.state === "cancelled"}Latest build was
              cancelled for <code>{latest.build.sha.slice(0, 7)}</code>.
            {:else if latest?.build?.state === "empty"}No affected CLIs in
              commit <code>{latest.build.sha.slice(0, 7)}</code>.
            {:else if updateAvailable && latest?.sha}A new build is ready for <code
                >{latest.sha.slice(0, 7)}</code
              >.{/if}
            {#if latest?.build?.state !== "ready" && latest?.build?.state !== "building"}
              {#if updateAvailable && latest?.sha}The last successful build is <code
                  >{latest.sha.slice(0, 7)}</code
                >.
              {:else}You're using the last successful build.{/if}
            {/if}
            Your terminal remains on <code>{environment.sha.slice(0, 7)}</code>.
          </p>
          {#if updateAvailable}<button
              type="button"
              class="banner-button"
              onclick={() => void load()}
              title="Discard this workspace and start a new terminal with the latest runnable build"
              >Switch to latest build</button
            >{/if}
        </div>
      {/if}
      <div class="workbench">
        {#if mobile && drawerOpen}<button
            class="drawer-backdrop"
            type="button"
            aria-label="Close command explorer"
            onclick={closeDrawer}
          ></button>{/if}
        <aside
          id="explorer"
          class="sidebar"
          class:drawer-open={drawerOpen}
          aria-label="Command explorer"
          tabindex="-1"
          inert={mobile && !drawerOpen}
        >
          {#if mobile}<button
              class="drawer-close"
              aria-label="Close explorer ×"
              type="button"
              onclick={closeDrawer}
              >Close explorer <span aria-hidden="true">×</span></button
            >{/if}
          <Explorer
            clis={environment.clis}
            bind:selectedCli
            {catalog}
            {catalogLoading}
            {connected}
            {insert}
            retry={() => void refreshCatalog()}
          />
        </aside>
        <section
          class="terminal-panel"
          aria-labelledby="terminal-heading"
          inert={mobile && drawerOpen}
        >
          <div class="terminal-toolbar">
            <div class="terminal-title">
              <button
                id="explorer-button"
                aria-label="Explore"
                type="button"
                class="toolbar-button explorer-toggle"
                aria-controls="explorer"
                aria-expanded={drawerOpen}
                onclick={async () => {
                  drawerOpen = !drawerOpen;
                  await tick();
                  if (drawerOpen) document.getElementById("explorer")?.focus();
                }}>Explore</button
              >
              <h1 id="terminal-heading">Terminal</h1>
              <span class="shell-label">bash</span>
            </div>
            <div class="terminal-actions">
              <button
                id="disconnect-button"
                class="toolbar-button"
                type="button"
                disabled={!connected}
                onclick={() => controller?.disconnect()}>Disconnect</button
              ><button
                id="reset-button"
                class="toolbar-button"
                type="button"
                disabled={session.phase === "connecting"}
                title="Discard this session and open a fresh workspace"
                onclick={() => void load()}>Reset session</button
              >
            </div>
          </div>
          {#if session.message}<div
              id="session-banner"
              class="session-banner"
              role="status"
            >
              <p id="session-message">{session.message}</p>
              {#if session.canReconnect}<button
                  id="reconnect-button"
                  type="button"
                  class="banner-button"
                  onclick={() => controller?.resumeExisting()}
                  >Reconnect <span aria-hidden="true">↗</span></button
                >{:else if session.phase === "disconnected"}<button
                  type="button"
                  class="banner-button"
                  onclick={() => void load()}>Start new session</button
                >{/if}
            </div>{/if}
          <div class="terminal-stage">
            <div
              id="terminal"
              role="region"
              aria-label="Interactive Bash terminal"
              tabindex="-1"
              use:terminalAction
            ></div>
          </div>
          <div class="terminal-statusbar">
            <div
              id="connection-status"
              class="connection-status"
              role="status"
              aria-live="polite"
              data-state={session.phase}
            >
              <span class="status-dot" aria-hidden="true"></span><span
                id="status-label">{session.label}</span
              >
            </div>
            <span class="workspace-label">/workspace</span
            >{#if session.absoluteDeadline}<time
                id="session-expiry"
                datetime={session.absoluteDeadline}
                title={new Date(session.absoluteDeadline).toLocaleString()}
                >Temporary session</time
              >{/if}
            <div class="keyboard-hints">
              <span><kbd>↑</kbd> history</span><span
                ><kbd>Ctrl C</kbd> stop</span
              >
            </div>
          </div>
        </section>
      </div>
    </div>
  {/if}
</main>
