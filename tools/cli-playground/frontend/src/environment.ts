export interface Environment {
  id: string;
  clis: string[];
  sha: string;
  pr: number;
  expiresAt: Date;
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

export function parseEnvironment(value: unknown, now = Date.now()): Environment {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw invalid();
  const data = value as Record<string, unknown>;
  if (
    typeof data.id !== "string" ||
    !/^[a-f0-9]{32}$/.test(data.id) ||
    !Array.isArray(data.clis) ||
    data.clis.length === 0 ||
    !data.clis.every(
      (cli: unknown): cli is string =>
        typeof cli === "string" && /^[a-z][a-z0-9_-]{0,63}$/.test(cli),
    ) ||
    new Set(data.clis).size !== data.clis.length ||
    typeof data.sha !== "string" ||
    !/^[a-f0-9]{40}$/.test(data.sha) ||
    typeof data.pr !== "number" ||
    !Number.isSafeInteger(data.pr) ||
    data.pr < 1 ||
    typeof data.expires_at !== "string"
  )
    throw invalid();
  const expiresAt = new Date(data.expires_at);
  if (Number.isNaN(expiresAt.getTime())) throw invalid();
  if (expiresAt.getTime() <= now) {
    throw new EnvironmentError(
      "This playground has expired.",
      "Open the latest playground link on the pull request to try a current build.",
      false,
    );
  }
  return { id: data.id, clis: data.clis, sha: data.sha, pr: data.pr, expiresAt };
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
