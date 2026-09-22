import type { SharingProfile } from '../api/types';
import type { DataFreshness } from './types';

export type SharingAction = 'enable' | 'disable' | 'delete';

export interface SharingActions {
  enable: boolean;
  disable: boolean;
  delete: boolean;
  recovery: boolean;
  message: string;
}

export function sharingActions(profile: Pick<SharingProfile, 'state' | 'cleanup_pending'>, freshness: DataFreshness): SharingActions {
  if (freshness !== 'fresh') {
    return {enable: false, disable: false, delete: false, recovery: false, message: '服务端状态未知或已过期，请先刷新后再执行共享操作。'};
  }
  const state = profile.state;
  const cleanupPending = profile.cleanup_pending === true;
  if (cleanupPending) {
    return {enable: false, disable: true, delete: false, recovery: true, message: '清理尚未确认完成，只允许继续执行恢复。'};
  }
  if (state === 'disabled') return {enable: true, disable: false, delete: true, recovery: false, message: ''};
  if (state === 'enabled' || state === 'enabling' || state === 'disabling') {
    return {enable: false, disable: true, delete: false, recovery: state !== 'enabled', message: state === 'enabled' ? '' : '方案仍在变更中，只允许继续执行安全恢复。'};
  }
  if (state === 'error') return {enable: true, disable: false, delete: true, recovery: false, message: ''};
  return {enable: false, disable: false, delete: false, recovery: false, message: '未知方案状态，不能安全推断下一步操作。'};
}

export function sharingActionAllowed(actions: SharingActions, action: SharingAction): boolean {
  return actions[action];
}
