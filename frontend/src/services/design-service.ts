/**
 * Servicio de diseño de resortes.
 * Encapsula toda la comunicación con la API de diseño en una clase
 * con métodos tipados, aislando los componentes de los detalles de red.
 */

import { BaseApiClient, NetworkError } from './api-client';
import type {
  DesignResponse,
  HealthResponse,
  StepProgress,
} from './types';

export class DesignService {
  private readonly client: BaseApiClient;

  constructor(client?: BaseApiClient) {
    this.client = client ?? new BaseApiClient();
  }

  /**
   * Inicia un nuevo proceso de diseño a partir de la entrada del usuario.
   * El backend ejecuta el grafo en background. Retorna inmediatamente
   * con status='processing'; usa getDesignStatus() para hacer polling.
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
   * Envía respuestas a preguntas de clarificación como array de strings.
   */
  async clarifyDesign(
    sessionId: string,
    answers: string[]
  ): Promise<DesignResponse> {
    return this.client.post<DesignResponse>('/api/v1/design/clarify', {
      session_id: sessionId,
      answers,
    });
  }

  /**
   * Hace polling al estado de progreso del diseño.
   */
  async getDesignStatus(sessionId: string): Promise<StepProgress> {
    return this.client.get<StepProgress>(
      `/api/v1/design/${encodeURIComponent(sessionId)}/status`
    );
  }

  /**
   * Recupera un diseño previamente completado por session_id.
   */
  async getDesign(sessionId: string): Promise<DesignResponse> {
    return this.client.get<DesignResponse>(
      `/api/v1/design/${encodeURIComponent(sessionId)}`
    );
  }

  /**
   * Verifica el estado del backend.
   */
  async getHealth(): Promise<HealthResponse> {
    return this.client.get<HealthResponse>('/health');
  }

  /**
   * Verifica si el backend está alcanzable.
   */
  async checkHealth(): Promise<boolean> {
    try {
      await this.getHealth();
      return true;
    } catch (error) {
      if (error instanceof NetworkError) {
        return false;
      }
      return true;
    }
  }
}
