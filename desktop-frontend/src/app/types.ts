import type { ReactNode } from 'react';

export type SectionId =
  | 'overview'
  | 'tunnel-page'
  | 'sharing'
  | 'hosts-page'
  | 'proxy-page'
  | 'diagnostics'
  | 'settings';

export type ThemeMode = 'light' | 'dark' | 'system';

export type DataFreshness = 'fresh' | 'unknown';

export interface Confirmation {
  title: string;
  body: string;
}

export type RunTask = (
  operation: () => Promise<void>,
  confirmation?: Confirmation,
  sync?: () => Promise<unknown>,
) => Promise<boolean>;

export interface NavigationItem {
  id: SectionId;
  label: string;
  description: string;
  icon: ReactNode;
}
