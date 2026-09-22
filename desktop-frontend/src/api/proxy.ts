import { requestJson } from './client';
import type { ProxyResponse } from './types';

export const getProxy = () => requestJson<ProxyResponse>('proxy');
export const saveProxy = (payload: {enabled: boolean; server: string; bypass: string}) =>
  requestJson<ProxyResponse>('proxy/save', payload);
export const restoreProxy = (id: string) => requestJson<ProxyResponse>('proxy/restore', {id});
