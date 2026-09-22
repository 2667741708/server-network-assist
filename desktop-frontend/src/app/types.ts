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

export interface NavigationItem {
  id: SectionId;
  label: string;
  description: string;
  icon: ReactNode;
}
