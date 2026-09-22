import { requestJson } from './client';
import type { UpdateResponse } from './types';

export const getUpdates = () => requestJson<UpdateResponse>('updates');
