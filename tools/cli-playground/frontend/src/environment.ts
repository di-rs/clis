import { z } from "zod";

// Validate the fields the UI consumes; the backend owns artifact and lifecycle rules.
const environmentSchema = z
  .object({
    id: z.string(),
    clis: z.array(z.string()),
    sha: z.string().nullable(),
    revision: z.string().nullable(),
    build: z
      .object({
        sha: z.string(),
        state: z.enum(["building", "ready", "failed", "empty", "cancelled"]),
      })
      .nullable(),
    pr: z.int().positive(),
    expires_at: z.iso.datetime({ offset: true }),
  })
  .transform(({ expires_at, ...environment }) => ({
    ...environment,
    expiresAt: new Date(expires_at),
  }));

export type Environment = z.infer<typeof environmentSchema>;

export type RunnableEnvironment = Environment & { sha: string; revision: string };

export function runnable(environment: Environment): environment is RunnableEnvironment {
  return environment.sha !== null && environment.revision !== null && environment.clis.length > 0;
}

export class EnvironmentError extends Error {
  constructor(
    public title: string,
    message: string,
    public retryable: boolean,
  ) {
    super(message);
    this.name = "EnvironmentError";
  }
}

export function environmentIdFromPath(path: string): string | null {
  return /^\/p\/([a-f0-9]{32})\/?$/.exec(path)?.[1] ?? null;
}

function invalid(): EnvironmentError {
  return new EnvironmentError(
    "Couldn't open the playground.",
    "This build returned invalid metadata. Try again or open a newer preview from the pull request.",
    true,
  );
}

export function parseEnvironment(value: unknown): Environment {
  const result = environmentSchema.safeParse(value);
  if (!result.success) throw invalid();
  return result.data;
}

export async function loadEnvironment(
  identifier: string | null,
  signal: AbortSignal,
  fetcher: (url: string, options?: RequestInit) => Promise<Response> = fetch,
): Promise<Environment> {
  if (!identifier) {
    throw new EnvironmentError(
      "This link is incomplete.",
      "Open a playground link from a pull request to try its CLI.",
      false,
    );
  }
  const response = await fetcher(`/api/environments/${encodeURIComponent(identifier)}`, {
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    if (response.status === 404 || response.status === 410) {
      throw new EnvironmentError(
        response.status === 410 ? "This playground has expired." : "This playground wasn't found.",
        "Open the latest playground link on the pull request. Preview links are available for seven days.",
        false,
      );
    }
    throw new EnvironmentError(
      "The playground is unavailable.",
      "The server could not load this build. Try again in a moment.",
      true,
    );
  }
  const data: unknown = await response.json().catch((error: unknown) => {
    if (error instanceof SyntaxError) throw invalid();
    throw error;
  });
  const environment = parseEnvironment(data);
  if (environment.id !== identifier) throw invalid();
  return environment;
}

const sessionSchema = z.strictObject({
  id: z.uuid(),
  preview_id: z.string().regex(/^[a-f0-9]{32}$/),
  revision: z.string().regex(/^[a-f0-9]{40}(?:-[1-9][0-9]*-[1-9][0-9]*)?$/),
  credential: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  absolute_deadline: z.iso.datetime({ offset: true }),
  state: z.enum(["accepted", "provisioning", "ready", "terminating", "terminated", "failed"]),
  reason: z.string().nullable(),
});
export type SessionBinding = z.infer<typeof sessionSchema>;

export function parseSession(value: unknown, preview: string, revision: string): SessionBinding {
  const parsed = sessionSchema.parse(value);
  if (parsed.preview_id !== preview || parsed.revision !== revision) throw invalid();
  return parsed;
}

export async function createFreshBinding(
  preview: string,
  revision: string,
): Promise<SessionBinding> {
  const response = await fetch(`/api/environments/${encodeURIComponent(preview)}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ revision }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error("Session admission failed");
  return parseSession(await response.json(), preview, revision);
}

export async function stopBinding(binding: SessionBinding): Promise<void> {
  await fetch(`/api/environments/${binding.preview_id}/sessions/${binding.id}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${binding.credential}` },
    keepalive: true,
  });
}
