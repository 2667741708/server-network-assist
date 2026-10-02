import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Injectable, signal } from '@angular/core';
import { catchError, defer, finalize, Observable, tap, throwError } from 'rxjs';
import { ReadCache } from './smooth-navigation';
import { SessionInfo } from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  readonly csrf = signal('');
  private readonly basePath = new URL(document.baseURI).pathname.replace(/\/$/, '');
  private readonly navigationReads?: ReadCache;

  constructor(private readonly http: HttpClient) {
    this.navigationReads = window.SmoothNavigation?.createReadCache({
      allowed:['/api/client-service'], maxAge:1500,
      load:(path,signal) => new Promise((resolve,reject) => {
        const cancel = () => { subscription.unsubscribe(); reject(new DOMException('Obsolete read','AbortError')); };
        const subscription = this.http.get(this.url(path), {withCredentials:true}).pipe(catchError(this.failure)).subscribe({
          next:value => resolve(value), error:error => reject(error),
          complete:() => signal.removeEventListener('abort',cancel)
        });
        signal.addEventListener('abort',cancel,{once:true});
        subscription.add(() => signal.removeEventListener('abort',cancel));
        if(signal.aborted) cancel();
      })
    });
  }
  async prefetch(path: string) { await this.navigationReads?.read(path); }
  invalidateNavigation() { this.navigationReads?.invalidate(); }

  session(): Observable<SessionInfo> {
    return this.get<SessionInfo>('/api/session').pipe(
      tap((value) => this.csrf.set(value.csrf || '')),
    );
  }

  login(payload: Record<string, unknown>): Observable<{ csrf: string }> {
    return this.post<{ csrf: string }>('/api/login', payload, false).pipe(
      tap((value) => this.csrf.set(value.csrf)),
    );
  }

  get<T>(path: string): Observable<T> {
    if(path === '/api/client-service' && this.navigationReads) return defer(() => this.navigationReads!.read(path) as Promise<T>);
    return this.http.get<T>(this.url(path), { withCredentials: true }).pipe(catchError(this.failure));
  }

  post<T>(path: string, body: unknown, protectedRequest = true): Observable<T> {
    const headers = protectedRequest ? new HttpHeaders({ 'X-CSRF-Token': this.csrf() }) : undefined;
    return defer(() => {
      this.invalidateNavigation();
      return this.http.post<T>(this.url(path), body, { headers, withCredentials: true })
        .pipe(catchError(this.failure),finalize(() => this.invalidateNavigation()));
    });
  }

  url(path: string) {
    return `${this.basePath}${path}`;
  }

  private readonly failure = (error: HttpErrorResponse) => {
    if(error.status === 401 || error.status === 403) this.invalidateNavigation();
    const message =
      typeof error.error?.error === 'string'
        ? error.error.error
        : `请求失败（HTTP ${error.status || 0}）`;
    return throwError(() => new Error(message));
  };
}
