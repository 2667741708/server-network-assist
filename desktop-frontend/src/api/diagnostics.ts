import { requestJson } from './client';
import type { DiagnosticResponse } from './types';

export const getDiagnostics = () => requestJson<DiagnosticResponse>('diagnostics');
