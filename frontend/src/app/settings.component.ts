import { DatePipe } from '@angular/common';
import { Component, EventEmitter, Input, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { ApiService } from './api.service';
import { SecurityInfo } from './models';
import { decodeCreationOptions, serializeCredential } from './passkey';

@Component({
  selector: 'app-settings',
  imports: [
    DatePipe,
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  template: `<header class="page-head">
      <div>
        <p class="eyebrow">SECURITY</p>
        <h1>安全设置</h1>
        <p>高风险操作需要最近 5 分钟内重新验证管理员密码。</p>
      </div>
    </header>
    @if (message()) {
      <div class="alert" [class.error]="failed()">{{ message() }}</div>
    }
    <div class="settings-grid">
      <mat-card
        ><h2>管理员密码</h2>
        <p>修改后将撤销所有现有登录会话。</p>
        <mat-form-field appearance="outline"
          ><mat-label>当前密码</mat-label
          ><input matInput type="password" [(ngModel)]="currentPassword" /></mat-form-field
        ><mat-form-field appearance="outline"
          ><mat-label>新密码（至少 12 位）</mat-label
          ><input matInput type="password" [(ngModel)]="newPassword" /></mat-form-field
        ><button mat-flat-button (click)="changePassword()">修改密码</button></mat-card
      >
      <mat-card
        ><h2>恢复密钥</h2>
        <p>
          {{
            security.key_enabled ? '恢复密钥已启用。轮换会撤销全部会话。' : '恢复密钥当前未启用。'
          }}
        </p>
        <mat-form-field appearance="outline"
          ><mat-label>管理员密码</mat-label
          ><input matInput type="password" [(ngModel)]="keyPassword"
        /></mat-form-field>
        <div class="button-row">
          <button mat-flat-button (click)="rotateKey(false)">生成新密钥</button
          ><button mat-stroked-button class="danger" (click)="rotateKey(true)">撤销密钥</button>
        </div>
        @if (newKey()) {
          <pre class="secret-output">{{ newKey() }}</pre>
        }
      </mat-card>
    </div>
    <section class="panel-section">
      <div class="section-title">
        <div>
          <h2>通行密钥</h2>
          <p>需要固定 HTTPS 来源和支持 WebAuthn 的浏览器。</p>
        </div>
        <button mat-stroked-button (click)="addPasskey()" [disabled]="!webauthn">
          添加通行密钥
        </button>
      </div>
      <div class="chip-list">
        @for (item of security.passkeys; track item.id) {
          <span class="soft-chip"
            >{{ item.name }}
            <button type="button" aria-label="删除通行密钥" (click)="deletePasskey(item.id)">
              ×
            </button></span
          >
        } @empty {
          <span class="muted">尚未添加</span>
        }
      </div>
    </section>
    <section class="panel-section">
      <div class="section-title">
        <div>
          <h2>已登录设备</h2>
          <p>可撤销任一会话；当前设备撤销后需要重新登录。</p>
        </div>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>设备</th>
              <th>来源</th>
              <th>过期时间</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (device of security.devices; track device.token_hash) {
              <tr>
                <td class="long-value">
                  {{ device.user_agent }}{{ device.current ? '（当前）' : '' }}
                </td>
                <td class="mono long-value">{{ device.remote_ip }}</td>
                <td>{{ device.expires_at * 1000 | date: 'yyyy-MM-dd HH:mm' }}</td>
                <td><button mat-button (click)="revoke(device.token_hash)">撤销</button></td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </section>`,
})
export class SettingsComponent {
  @Input({ required: true }) security!: SecurityInfo;
  @Output() changed = new EventEmitter<void>();
  currentPassword = '';
  newPassword = '';
  keyPassword = '';
  readonly message = signal('');
  readonly failed = signal(false);
  readonly newKey = signal('');
  readonly webauthn = !!window.PublicKeyCredential;
  constructor(private readonly api: ApiService) {}
  notify(v: string, f = false) {
    this.message.set(v);
    this.failed.set(f);
  }
  verify(password: string, next: () => void) {
    this.api
      .post('/api/reauth', { password })
      .subscribe({ next, error: (e) => this.notify(e.message, true) });
  }
  changePassword() {
    this.verify(this.currentPassword, () =>
      this.api.post('/api/password', { new_password: this.newPassword }).subscribe({
        next: () => {
          this.notify('密码已修改，请重新登录');
          location.reload();
        },
        error: (e) => this.notify(e.message, true),
      }),
    );
  }
  rotateKey(revoke: boolean) {
    this.verify(this.keyPassword, () =>
      this.api.post<{ key: string }>('/api/key', { revoke }).subscribe({
        next: (value) => {
          this.newKey.set(value.key);
          this.notify(revoke ? '恢复密钥已撤销' : '新密钥只显示这一次，请立即安全保存');
          this.changed.emit();
        },
        error: (e) => this.notify(e.message, true),
      }),
    );
  }
  revoke(id: string) {
    if (!confirm('确定撤销这个登录会话吗？')) return;
    this.api
      .post('/api/device/revoke', { id })
      .subscribe({ next: () => this.changed.emit(), error: (e) => this.notify(e.message, true) });
  }
  addPasskey() {
    const password = prompt('请输入管理员密码以添加通行密钥：');
    if (password === null) return;
    this.verify(password, () =>
      this.api
        .post<PublicKeyCredentialCreationOptionsJSON>('/api/passkey/register-options', {})
        .subscribe({
          next: async (options) => {
            try {
              const credential = (await navigator.credentials.create({
                publicKey: decodeCreationOptions(options),
              })) as PublicKeyCredential | null;
              if (!credential) throw new Error('未创建通行密钥');
              const name = prompt('给这个通行密钥命名：', '我的设备') || '通行密钥';
              this.api
                .post('/api/passkey/register', {
                  name,
                  credential: serializeCredential(credential),
                })
                .subscribe({
                  next: () => {
                    this.notify('通行密钥已添加');
                    this.changed.emit();
                  },
                  error: (e) => this.notify(e.message, true),
                });
            } catch (error) {
              this.notify(error instanceof Error ? error.message : '通行密钥操作失败', true);
            }
          },
          error: (e) => this.notify(e.message, true),
        }),
    );
  }
  deletePasskey(id: string) {
    if (!confirm('删除通行密钥会撤销所有会话，继续吗？')) return;
    this.api
      .post('/api/passkey-delete', { id })
      .subscribe({ next: () => location.reload(), error: (e) => this.notify(e.message, true) });
  }
}
