<script lang="ts">
  import CommandNode from "./CommandNode.svelte";
  import {
    commandText,
    flagText,
    visible,
    type CommandView,
    type FieldChange,
  } from "./catalog";
  let {
    node,
    cli,
    query,
    changesOnly,
    connected,
    insert,
    root = false,
  }: {
    node: CommandView;
    cli: string;
    query: string;
    changesOnly: boolean;
    connected: boolean;
    insert: (value: string) => void;
    root?: boolean;
  } = $props();
  const removed = $derived(node.change === "removed");
  const args = $derived(
    node.args.filter(
      (row) =>
        row.change !== "removed" &&
        (!changesOnly || row.change !== "unchanged") &&
        (!query.trim() ||
          [
            row.argument.help,
            row.argument.id,
            row.argument.long ?? "",
            row.argument.short ?? "",
            ...row.argument.aliases,
            node.command.name,
            ...node.path,
          ]
            .join(" ")
            .toLowerCase()
            .includes(query.toLowerCase().trim())),
    ),
  );
  const removedArgs = $derived(
    node.args.filter((row) => row.change === "removed"),
  );
  const children = $derived(
    node.children.filter(
      (child) =>
        child.change !== "removed" && visible(child, query, changesOnly),
    ),
  );
  function paste(value: () => string) {
    try {
      insert(value());
    } catch {
      /* Invalid metadata never reaches terminal input. */
    }
  }
</script>

{#snippet differences(changes: FieldChange[])}
  <dl class="field-changes">
    {#each changes as change (change.label)}
      <div>
        <dt>{change.label}</dt>
        <dd>
          <del>{change.before || "None"}</del><ins>{change.after || "None"}</ins
          >
        </dd>
      </div>
    {/each}
  </dl>
{/snippet}

{#if visible(node, query, changesOnly)}
  <details
    class="command-node"
    class:root
    class:removed
    open={root || Boolean(query) || changesOnly}
  >
    <summary class="command-heading">
      <span class="chevron" aria-hidden="true">›</span>
      <span class="command-summary"
        ><code>{root ? cli : node.command.name}</code></span
      >
      {#if node.change !== "unchanged"}<span class="badge"
          >{node.change === "added"
            ? "+ Added"
            : node.change === "removed"
              ? "− Removed"
              : "~ Changed"}</span
        >{/if}
      {#if node.count > 0}<span class="change-count" title="Interface changes"
          >{node.count}</span
        >{/if}
    </summary>
    <div class="command-content">
      <p class="command-about">{node.command.about}</p>
      <div class="invocation">
        <button
          class="insert-command"
          type="button"
          disabled={!connected || removed}
          aria-label={`Insert ${[cli, ...node.path].join(" ")}`}
          onclick={() => paste(() => commandText(cli, node.path))}
        >
          <code>{[cli, ...node.path].join(" ")}</code><span
            class="insert-label"
            aria-hidden="true">+</span
          >
        </button>
      </div>
      <details class="command-reference">
        <summary>Usage &amp; details</summary>
        {#if node.command.long_about}<p>{node.command.long_about}</p>{/if}
        <pre>{node.command.usage}</pre>
        {#if node.command.aliases.length}<p>
            Aliases: {node.command.aliases.join(", ")}
          </p>{/if}
        {@render differences(node.fields)}
      </details>
      {#if !node.command.args.length && !node.children.length}<p
          class="section-note"
        >
          No arguments or options.
        </p>{/if}
      {#if children.length}
        <div class="command-group">
          {#each children as child (child.command.name)}
            <CommandNode
              node={child}
              {cli}
              {query}
              {changesOnly}
              {connected}
              {insert}
            />
          {/each}
        </div>
      {/if}
      {#each args as row (row.argument.id)}
        {@const arg = row.argument}
        <div class="argument-row" data-change={row.change}>
          <div class="argument-title">
            <div class="argument-signature">
              {#if arg.long || arg.short}
                <button
                  type="button"
                  class="flag-button"
                  disabled={!connected || removed}
                  aria-label={`Insert ${arg.long ? `--${arg.long}` : `-${arg.short}`}`}
                  onclick={() => paste(() => flagText(arg))}
                >
                  <code
                    >{[
                      arg.short ? `-${arg.short}` : "",
                      arg.long ? `--${arg.long}` : "",
                    ]
                      .filter(Boolean)
                      .join(", ")}</code
                  >
                </button>
              {:else}<code class="operand"
                  >{arg.value_names.join(" ") || arg.id}</code
                >{/if}
              {#if arg.num_args.max !== 0 && (arg.long || arg.short)}<span
                  class="value-label"
                  >{arg.value_names.join(" ") || "VALUE"}</span
                >{/if}
            </div>
            {#if row.change !== "unchanged"}<span class="badge"
                >{row.change === "added"
                  ? "+ Added"
                  : row.change === "removed"
                    ? "− Removed"
                    : "~ Changed"}</span
              >{/if}
          </div>
          <p class="argument-help">{arg.help.split("\n")[0]}</p>
          <details class="argument-details">
            <summary>Details{arg.required ? " · required" : ""}</summary>
            <p>{arg.long_help ?? arg.help}</p>
            <dl>
              {#if arg.default_values.length}<div>
                  <dt>Default</dt>
                  <dd><code>{arg.default_values.join(", ")}</code></dd>
                </div>{/if}
              {#if arg.possible_values.length}<div>
                  <dt>Choices</dt>
                  <dd><code>{arg.possible_values.join(" · ")}</code></dd>
                </div>{/if}
              {#if arg.aliases.length}<div>
                  <dt>Aliases</dt>
                  <dd><code>{arg.aliases.join(", ")}</code></dd>
                </div>{/if}
              <div>
                <dt>Required</dt>
                <dd>{arg.required ? "Yes" : "No"}</dd>
              </div>
              {#if arg.num_args.max !== 0}<div>
                  <dt>Values</dt>
                  <dd>{arg.num_args.min}–{arg.num_args.max ?? "any"}</dd>
                </div>{/if}
              {#if arg.global}<div>
                  <dt>Available from</dt>
                  <dd><code>{[cli, ...arg.owner].join(" ")}</code></dd>
                </div>{/if}
            </dl>
            {@render differences(row.fields)}
          </details>
        </div>
      {/each}
      {#if removedArgs.length || node.children.some((child) => child.change === "removed")}
        <details class="removed-section">
          <summary
            >Removed in this PR <span
              >{removedArgs.length +
                node.children.filter((child) => child.change === "removed")
                  .length}</span
            ></summary
          >
          {#each removedArgs as row (row.argument.id)}
            <div class="argument-row removed">
              <code
                >{row.argument.long
                  ? `--${row.argument.long}`
                  : row.argument.short
                    ? `-${row.argument.short}`
                    : row.argument.id}</code
              ><span class="badge">− Removed</span>
              <p>{row.argument.help}</p>
            </div>
          {/each}
          {#each node.children.filter((child) => child.change === "removed") as child (child.command.name)}
            <CommandNode
              node={child}
              {cli}
              {query}
              {changesOnly}
              {connected}
              {insert}
            />
          {/each}
        </details>
      {/if}
    </div>
  </details>
{/if}
