const REQUEST_TIMEOUT_MS = 600_000;

function readToken(): string {
  try {
    const fromHash = window.location.hash.slice(1);
    const stored = window.sessionStorage.getItem('desktop-token') || '';
    const token = fromHash || stored;
    if (fromHash) {
      window.sessionStorage.setItem('desktop-token', token);
      window.history.replaceState(null, '', window.location.pathname + window.location.search);
    }
    return token;
  } catch {
    return window.location.hash.slice(1);
  }
}

let desktopToken = readToken();

export function hasDesktopToken(): boolean {
  return Boolean(desktopToken);
}

export function resetDesktopToken(): void {
  desktopToken = readToken();
}

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
  }
}

async function responseBody(response: Response): Promise<Record<string, unknown>> {
  try {
    const body: unknown = await response.json();
    return body && typeof body === 'object' ? body as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

export async function requestJson<T>(path: string, body?: unknown): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const response = await window.fetch(`/api/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      signal: controller.signal,
      headers: {
        'X-Desktop-Token': desktopToken,
        ...(body === undefined ? {} : {'Content-Type': 'application/json'}),
      },
      ...(body === undefined ? {} : {body: JSON.stringify(body)}),
    });
    const value = await responseBody(response);
    if (!response.ok) {
      const message = typeof value.error === 'string' ? value.error : `请求失败 (${response.status})`;
      throw new ApiRequestError(message, response.status);
    }
    return value as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('请求超时。远端操作可能仍在执行，请刷新方案状态后再决定是否重试。');
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}
