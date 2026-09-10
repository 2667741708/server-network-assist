import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { FormControl, FormGroup, ReactiveFormsModule, Validators } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ApiService } from './api.service';
import { decodeRequestOptions, serializeCredential } from './passkey';

@Component({
  selector: 'app-login',
  imports: [
    ReactiveFormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressSpinnerModule,
  ],
  template: `
    <main class="login-page">
      <mat-card class="login-card">
        <div class="brand-mark" aria-hidden="true">↗</div>
        <p class="eyebrow">SERVER NETWORK ASSIST</p>
        <h1>服务器网络协作台</h1>
        <p class="muted">探测、借网、终端和安全设置都集中在一个可审计的入口。</p>
        <form [formGroup]="form" (ngSubmit)="submit()" class="stack-form">
          <mat-form-field appearance="outline"
            ><mat-label>管理员账号</mat-label
            ><input matInput formControlName="username" autocomplete="username"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>密码</mat-label
            ><input
              matInput
              type="password"
              formControlName="password"
              autocomplete="current-password"
          /></mat-form-field>
          <mat-form-field appearance="outline"
            ><mat-label>恢复密钥（可选）</mat-label
            ><input matInput type="password" formControlName="key" autocomplete="off"
          /></mat-form-field>
          <mat-checkbox formControlName="remember">在此设备保持登录 7 天</mat-checkbox>
          @if (error()) {
            <div class="alert error" role="alert">{{ error() }}</div>
          }
          <button mat-flat-button color="primary" type="submit" [disabled]="form.invalid || busy()">
            @if (busy()) {
              <mat-spinner diameter="20" />
            } @else {
              登录
            }
          </button>
          @if (passkeys && webauthn) {
            <button mat-stroked-button type="button" (click)="passkeyLogin()" [disabled]="busy()">
              使用通行密钥登录
            </button>
          }
        </form>
      </mat-card>
    </main>
  `,
})
export class LoginComponent {
  @Input() passkeys = false;
  @Output() authenticated = new EventEmitter<void>();
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = new FormGroup(
    {
      username: new FormControl('admin', { nonNullable: true, validators: Validators.required }),
      password: new FormControl('', { nonNullable: true }),
      key: new FormControl('', { nonNullable: true }),
      remember: new FormControl(false, { nonNullable: true }),
    },
    {
      validators: (group) =>
        group.value.password || group.value.key ? null : { credential: true },
    },
  );

  constructor(private readonly api: ApiService) {}
  readonly webauthn = !!window.PublicKeyCredential;

  submit() {
    if (this.form.invalid || this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    const value = this.form.getRawValue();
    this.api
      .login({ ...value, remember: value.remember ? 7 : 0, device: navigator.userAgent })
      .subscribe({
        next: () => {
          this.busy.set(false);
          this.authenticated.emit();
        },
        error: (error) => {
          this.busy.set(false);
          this.error.set(error.message);
        },
      });
  }

  passkeyLogin() {
    this.busy.set(true);
    this.error.set('');
    this.api
      .post<PublicKeyCredentialRequestOptionsJSON>('/api/passkey/options', {}, false)
      .subscribe({
        next: async (options) => {
          try {
            const credential = (await navigator.credentials.get({
              publicKey: decodeRequestOptions(options),
            })) as PublicKeyCredential | null;
            if (!credential) throw new Error('未选择通行密钥');
            this.api
              .post<{ csrf: string }>(
                '/api/passkey/login',
                {
                  credential: serializeCredential(credential),
                  remember: 7,
                  device: navigator.userAgent,
                },
                false,
              )
              .subscribe({
                next: (value) => {
                  this.api.csrf.set(value.csrf);
                  this.busy.set(false);
                  this.authenticated.emit();
                },
                error: (error) => {
                  this.busy.set(false);
                  this.error.set(error.message);
                },
              });
          } catch (error) {
            this.busy.set(false);
            this.error.set(error instanceof Error ? error.message : '通行密钥操作失败');
          }
        },
        error: (error) => {
          this.busy.set(false);
          this.error.set(error.message);
        },
      });
  }
}
