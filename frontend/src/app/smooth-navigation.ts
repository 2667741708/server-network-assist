// Typed adapter to the same navigation engine used by the subscription windows.
export interface NavigationController {
  navigate(view: string, options?: { focus?: boolean }): Promise<boolean>;
  warm(view: string): void;
  observe(): void;
  url(view: string): string;
  readonly current: string;
  restoreScroll(): void;
  destroy(): void;
}
export interface ReadCache {
  read(key: string, options?: { fresh?: boolean; stale?: boolean }): Promise<unknown>;
  invalidate(): void;
}
declare global {
  interface Window {
    SmoothNavigation?: {
      create(options: {
        views: string[];
        initial: string;
        render: (view: string) => void;
        canNavigate?: (view: string) => boolean;
        prefetch?: (view: string) => Promise<unknown> | void;
        onError?: (error: Error) => void;
        focusTarget?: (view: string) => HTMLElement | null;
      }): NavigationController;
      createReadCache(options: {
        allowed: string[];
        load: (path: string, signal: AbortSignal) => Promise<unknown>;
        maxAge?: number;
      }): ReadCache;
    };
  }
}
