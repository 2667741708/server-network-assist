import { requestJson } from './client';
import type { CampusLoginResponse, CampusStatus } from './types';

export const getCampus = () => requestJson<CampusStatus>('campus');
export const getCampusStatus = () => requestJson<CampusStatus>('campus/status');
export const loginCampus = (payload: {username: string; password: string; service: string; physical_network_confirmed: boolean}) =>
  requestJson<CampusLoginResponse>('campus/login', payload);
