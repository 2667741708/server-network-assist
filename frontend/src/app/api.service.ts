import { HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { Injectable, signal } from '@angular/core';
import { catchError, Observable, tap, throwError } from 'rxjs';
import { SessionInfo } from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  readonly csrf = signal('');

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
    return this.http.get<T>(path, { withCredentials: true }).pipe(catchError(this.failure));
  }

  post<T>(path: string, body: unknown, protectedRequest = true): Observable<T> {
    const headers = protectedRequest ? new HttpHeaders({ 'X-CSRF-Token': this.csrf() }) : undefined;
    return this.http
      .post<T>(path, body, { headers, withCredentials: true })
      .pipe(catchError(this.failure));
  }

  private failure(error: HttpErrorResponse) {
    const message =
      typeof error.error?.error === 'string'
        ? error.error.error
        : `请求失败（HTTP ${error.status || 0}）`;
    return throwError(() => new Error(message));
  }
}
