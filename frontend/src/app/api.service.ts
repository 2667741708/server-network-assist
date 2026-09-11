import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Injectable, signal } from '@angular/core';
import { catchError, Observable, tap, throwError } from 'rxjs';
import { SessionInfo } from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  readonly csrf = signal('');
  private readonly basePath = new URL(document.baseURI).pathname.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

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
    return this.http.get<T>(this.url(path), { withCredentials: true }).pipe(catchError(this.failure));
  }

  post<T>(path: string, body: unknown, protectedRequest = true): Observable<T> {
    const headers = protectedRequest ? new HttpHeaders({ 'X-CSRF-Token': this.csrf() }) : undefined;
    return this.http
      .post<T>(this.url(path), body, { headers, withCredentials: true })
      .pipe(catchError(this.failure));
  }

  url(path: string) {
    return `${this.basePath}${path}`;
  }

  private failure(error: HttpErrorResponse) {
    const message =
      typeof error.error?.error === 'string'
        ? error.error.error
        : `请求失败（HTTP ${error.status || 0}）`;
    return throwError(() => new Error(message));
  }
}
