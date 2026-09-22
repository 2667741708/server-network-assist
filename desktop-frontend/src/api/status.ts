import { requestJson } from './client';
import type { HealthResponse, StatusResponse } from './types';

export const getStatus = () => requestJson<StatusResponse>('status');
export const getHealth = () => requestJson<HealthResponse>('health');

export type TunnelAction = 'connect' | 'disconnect' | 'pause-sharing' | 'restore-startup' | 'disable-proxy';

export const runTunnelAction = (action: TunnelAction, tunnel?: string) =>
  requestJson<{ok?: boolean}>('action', {action, ...(tunnel === undefined ? {} : {tunnel})});
