/**
 * Servicio de diseño de resortes.
 * Encapsula toda la comunicación con la API de diseño en una clase
 * con métodos tipados, aislando los componentes de los detalles de red.
 */

import { BaseApiClient, NetworkError } from './api-client';
import type { DesignResponse, HealthResponse } from './types';

export class DesignService {
  private readonly client: BaseApiClient;

  constructor(client?: BaseApiClient) {
    this.client = client ?? new BaseApiClient();
  }

  /**
   * Inicia un nuevo proceso de diseño a partir de la entrada del usuario.
   * @param userInput - Descripción natural del resorte deseado
   * @param maxIterations - Iteraciones máximas (default: 5)
   * @param sessionId - ID de sesión opcional para continuar una existente
   */
  async startDesign(
    userInput: string,
    maxIterations: number = 5,
    sessionId?: string | null
  ): Promise<DesignResponse> {
    return this.client.post<DesignResponse>('/api/v1/design/', {
      user_input: userInput,
      max_iterations: maxIterations,
      session_id: sessionId ?? null,
    });
  }

  /**
   * Envía respuestas a preguntas de clarificación.
   */
  async clarifyDesign(sessionId: string, answers: string): Promise<DesignResponse> {
    return this.client.post<DesignResponse>('/api/v1/design/clarify', {
      session_id: sessionId,
      answers,
    });
  }

  /**
   * Recupera un diseño previamente cacheados por session_id.
   */
  async getDesign(sessionId: string): Promise<DesignResponse> {
    return this.client.get<DesignResponse>(`/api/v1/design/${encodeURIComponent(sessionId)}`);
  }

  /**
   * Verifica el estado del backend.
   */
  async getHealth(): Promise<HealthResponse> {
    return this.client.get<HealthResponse>('/health');
  }

  /**
   * Verifica si el backend está alcanzable.
   * Útil para mostrar estado "backend offline" sin romper la UX.
   */
  async checkHealth(): Promise<boolean> {
    try {
      await this.getHealth();
      return true;
    } catch (error) {
      if (error instanceof NetworkError) {
        return false;
      }
      // Si el health check responde pero con error inesperado,
      // consideramos que el backend está funcionando
      return true;
    }
  }
}
