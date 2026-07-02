/**
 * Cliente HTTP base con tipado estricto.
 * Todas las llamadas a la API pasan por esta clase para centralizar
 * el manejo de errores, timeouts y configuración de red.
 */

const DEFAULT_BASE_URL = 'http://localhost:8000';
const DEFAULT_TIMEOUT_MS = 30_000;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly body: unknown
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export class NetworkError extends Error {
  constructor(
    message: string,
    public readonly cause: unknown
  ) {
    super(message);
    this.name = 'NetworkError';
  }
}

export class BaseApiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;

  constructor(baseUrl?: string, timeoutMs?: number) {
    this.baseUrl = (baseUrl ?? process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, '');
    this.timeoutMs = timeoutMs ?? DEFAULT_TIMEOUT_MS;
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          ...options.headers,
        },
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        let body: unknown;
        try {
          body = await response.json();
        } catch {
          body = await response.text().catch(() => null);
        }
        throw new ApiError(
          `HTTP ${response.status}: ${response.statusText}`,
          response.status,
          body
        );
      }

      const data: T = await response.json();
      return data;
    } catch (error) {
      clearTimeout(timeoutId);

      if (error instanceof ApiError) {
        throw error;
      }

      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new NetworkError(`Request timed out after ${this.timeoutMs}ms`, error);
      }

      if (error instanceof TypeError && error.message.includes('fetch')) {
        throw new NetworkError(
          'No se pudo conectar con el servidor. Verificá que el backend esté corriendo en ' +
            `${this.baseUrl}`,
          error
        );
      }

      throw new NetworkError('Error de red inesperado', error);
    }
  }

  async get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: 'GET' });
  }

  async post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  }
}
