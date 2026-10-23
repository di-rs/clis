<script lang="ts">
  import { tick } from "svelte";
  import CommandNode from "./CommandNode.svelte";
  import { compareCommand, visible, type Catalog } from "./catalog";
  import { files, shellExamples } from "./examples";
  let {
    clis,
    selectedCli = $bindable(),
    catalog,
    catalogLoading,
    connected,
    insert,
    retry,
  }: {
    clis: string[];
    selectedCli: string;
    catalog: Catalog | null;
    catalogLoading: boolean;
    connected: boolean;
    insert: (value: string) => void;
    retry: () => void;
  } = $props();
  let picker: HTMLDivElement;
  let pickerOpen = $state(false);
  let pickerQuery = $state("");
  let active = $state(0);
  let query = $state("");
  let changesOnly = $state(false);
  const choices = $derived(
    clis.filter((cli) => cli.toLowerCase().includes(pickerQuery.toLowerCase())),
  );
  const entry = $derived(catalog?.clis.find((cli) => cli.name === selectedCli));
  const tree = $derived(
    entry
      ? compareCommand(
          entry.head.command,
          entry.base?.command ?? null,
          entry.comparison,
        )
      : null,
  );
  function choose(cli: string) {
    selectedCli = cli;
    query = "";
    changesOnly = false;
    picker.querySelector("input")?.focus();
    pickerOpen = false;
  }
  function revealActive() {
    void tick().then(() =>
      document
        .getElementById(`cli-option-${active}`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }
  function keys(event: KeyboardEvent) {
    if (event.key === "Escape" && pickerOpen) {
      event.stopPropagation();
      pickerOpen = false;
      return;
    }
    if (event.key === "Tab") {
      pickerOpen = false;
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!pickerOpen) {
        pickerOpen = true;
        pickerQuery = "";
        active = 0;
      } else
        active = Math.max(
          0,
          Math.min(
            choices.length - 1,
            active + (event.key === "ArrowDown" ? 1 : -1),
          ),
        );
      revealActive();
    } else if (event.key === "Enter" && pickerOpen) {
      event.preventDefault();
      const cli = choices[active];
      if (cli) choose(cli);
    }
  }
</script>

<svelte:window
  onclick={(event) => {
    if (event.target instanceof Node && !picker?.contains(event.target))
      pickerOpen = false;
  }}
/>

<div class="explorer-controls">
  <div class="explorer-heading">
    <span class="eyebrow">COMMAND EXPLORER</span><span class="cli-total"
      >{clis.length} CLIs</span
    >
  </div>
  <div class="cli-picker" bind:this={picker}>
    <input
      role="combobox"
      aria-label="CLI"
      aria-expanded={pickerOpen}
      aria-controls="cli-options"
      aria-autocomplete="list"
      aria-activedescendant={pickerOpen && choices[active]
        ? `cli-option-${active}`
        : undefined}
      value={pickerOpen ? pickerQuery : selectedCli}
      placeholder={selectedCli}
      onfocus={() => {
        pickerOpen = true;
        pickerQuery = "";
        active = 0;
      }}
      onclick={() => {
        if (!pickerOpen) {
          pickerOpen = true;
          pickerQuery = "";
          active = 0;
        }
      }}
      oninput={(event) => {
        pickerQuery = event.currentTarget.value;
        pickerOpen = true;
        active = 0;
      }}
      onkeydown={keys}
    />
    <span class="picker-chevron" aria-hidden="true">⌄</span>
    {#if pickerOpen}
      <div
        id="cli-options"
        role="listbox"
        aria-label="Available CLIs"
        class="cli-options"
      >
        {#each choices as cli, index (cli)}
          <button
            id={`cli-option-${index}`}
            type="button"
            role="option"
            aria-selected={cli === selectedCli}
            tabindex="-1"
            class:active={active === index}
            onclick={() => choose(cli)}
            ><code>{cli}</code>{#if cli === selectedCli}<span aria-hidden="true"
                >✓</span
              >{/if}</button
          >
        {:else}<p>No matching CLI</p>{/each}
      </div>
    {/if}
  </div>
  <div class="explorer-search">
    <input
      aria-label="Search commands and flags"
      placeholder="Find a command or flag…"
      bind:value={query}
    />
    {#if query}<button
        type="button"
        class="clear-search"
        aria-label="Clear command search"
        onclick={() => {
          query = "";
          document
            .querySelector<HTMLInputElement>(
              '[aria-label="Search commands and flags"]',
            )
            ?.focus();
        }}><span aria-hidden="true">×</span></button
      >{/if}
  </div>
  <div class="explorer-filters" aria-label="Command filter">
    <button
      type="button"
      aria-pressed={!changesOnly}
      onclick={() => (changesOnly = false)}>All</button
    >
    <button
      type="button"
      aria-pressed={changesOnly}
      onclick={() => (changesOnly = true)}
      >Changes <span>{tree?.count ?? 0}</span></button
    >
  </div>
</div>
<div class="explorer-scroll">
  <div class="catalog-status" aria-live="polite">
    {#if catalogLoading}<span>Loading command reference…</span>
    {:else if !entry}<span>Command reference unavailable</span><button
        type="button"
        onclick={retry}>Retry</button
      >
    {:else if entry.comparison === "unavailable"}<span
        >Comparison unavailable</span
      >
    {:else}<span
        >Compared with
        <code>{catalog?.base_sha.slice(0, 7)}</code></span
      >{/if}
  </div>
  <div id="command-list" class="command-list">
    {#if tree}
      {#key selectedCli}
        <CommandNode
          node={tree}
          cli={selectedCli}
          {query}
          {changesOnly}
          {connected}
          {insert}
          root
        />
      {/key}
      {#if !visible(tree, query, changesOnly)}<p class="empty-list">
          {changesOnly
            ? "No matching interface changes."
            : "No matching commands or flags."}
        </p>{/if}
    {:else if !catalogLoading}<p class="empty-list">
        This build has no command catalog. You can still explore its CLIs in the
        terminal.
      </p>{/if}
  </div>
  <div class="explorer-extras">
    <details class="workspace-details">
      <summary
        >Sample files<span class="extra-count">{files.length}</span></summary
      >
      <p class="directory-label">/workspace</p>
      <ul id="file-list" class="file-list">
        {#each files as file (file)}<li>
            <button
              type="button"
              disabled={!connected}
              aria-label={`Insert command to read ${file}`}
              onclick={() => insert(`cat ${file}`)}>{file}</button
            >
          </li>{/each}
      </ul>
    </details>
    <details class="shell-details">
      <summary>Shell essentials</summary>
      {#each shellExamples as example (example.command)}<button
          type="button"
          class="shell-example"
          disabled={!connected}
          aria-label={`Insert ${example.display}: ${example.label}`}
          onclick={() => insert(example.command)}
          ><span>{example.label}</span><code>{example.display}</code></button
        >{/each}
    </details>
  </div>
  <div class="explorer-footer">
    Click to insert. <kbd>Enter</kbd> to run.
  </div>
</div>
